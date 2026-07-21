from pathlib import Path

import pytest
from click.testing import CliRunner

from fricat import concat
from fricat.media import AudioHealth


def _fake_concat(src_files: list[Path], destination: Path) -> int:
    destination.write_bytes(b'concatenated')
    return destination.stat().st_size


def test_build_validated_archive_publishes_valid_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / 'archive' / '00_CAM1.mkv'
    monkeypatch.setattr(concat, 'ffmpeg', _fake_concat)
    monkeypatch.setattr(concat, 'probe_duration', lambda path: 3605.0)
    remux_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(concat, 'remux', lambda source, target: remux_calls.append((source, target)))

    size, repaired = concat.build_validated_archive([tmp_path / 'segment.mp4'], destination)

    assert destination.read_bytes() == b'concatenated'
    assert size == len(b'concatenated')
    assert repaired is False
    assert remux_calls == []
    assert list(destination.parent.iterdir()) == [destination]


def test_build_validated_archive_remuxes_bad_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / 'archive' / '00_CAM2.mkv'
    monkeypatch.setattr(concat, 'ffmpeg', _fake_concat)

    def fake_probe(path: Path) -> float:
        return 3605.0 if path.name == 'remuxed.mkv' else 99029.0

    def fake_remux(source: Path, target: Path) -> None:
        assert source.read_bytes() == b'concatenated'
        target.write_bytes(b'repaired')

    monkeypatch.setattr(concat, 'probe_duration', fake_probe)
    monkeypatch.setattr(concat, 'remux', fake_remux)

    size, repaired = concat.build_validated_archive([tmp_path / 'segment.mp4'], destination)

    assert destination.read_bytes() == b'repaired'
    assert size == len(b'repaired')
    assert repaired is True
    assert list(destination.parent.iterdir()) == [destination]


def test_build_validated_archive_does_not_publish_invalid_remux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    destination = tmp_path / 'archive' / '00_CAM2.mkv'
    monkeypatch.setattr(concat, 'ffmpeg', _fake_concat)
    monkeypatch.setattr(concat, 'probe_duration', lambda path: 99029.0)
    monkeypatch.setattr(concat, 'remux', lambda source, target: target.write_bytes(b'still-bad'))

    with pytest.raises(ValueError, match='Remuxed archive duration is invalid'):
        concat.build_validated_archive([tmp_path / 'segment.mp4'], destination)

    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_concat_reports_automatic_repairs_in_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / 'source' / '2025-01-01' / '00' / 'CAM2'
    source.mkdir(parents=True)
    (source / '00.00.mp4').write_bytes(b'segment')
    destination = tmp_path / 'archive'
    captured_metrics: dict[str, float | int] = {}

    def fake_build(src_files: list[Path], dst_file: Path) -> tuple[int, bool]:
        dst_file.parent.mkdir(parents=True)
        dst_file.write_bytes(b'archive')
        return len(b'archive'), True

    def fake_write_metrics(path: Path, metrics: dict[str, float | int]) -> None:
        captured_metrics.update(metrics)

    monkeypatch.setattr(concat, 'build_validated_archive', fake_build)
    monkeypatch.setattr(concat, 'find_audio_issue', lambda src_files: None)
    monkeypatch.setattr(concat, 'generate_sidecar', lambda **kwargs: None)
    monkeypatch.setattr(concat, 'write_metrics_file', fake_write_metrics)

    result = CliRunner().invoke(
        concat.main,
        [
            str(tmp_path / 'source'),
            str(destination),
            '--metrics-file',
            str(tmp_path / 'metrics.prom'),
        ],
    )

    assert result.exit_code == 0
    assert captured_metrics['fricat_concat_processed_files'] == 1
    assert captured_metrics['fricat_concat_repaired_files'] == 1
    assert captured_metrics['fricat_concat_malformed_audio_archives'] == 0


def test_representative_segments_selects_start_middle_and_end(tmp_path: Path) -> None:
    segments = [tmp_path / f'{index}.mp4' for index in range(10)]

    assert concat.representative_segments(segments) == [segments[0], segments[5], segments[9]]


def test_find_audio_issue_reports_sparse_audio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    segments = [tmp_path / f'{index}.mp4' for index in range(3)]
    monkeypatch.setattr(
        concat,
        'probe_audio_health',
        lambda path: AudioHealth(False, 1, 'only 1 audio packet')
        if path == segments[1]
        else AudioHealth(True, 78),
    )

    issue = concat.find_audio_issue(segments)

    assert issue == (segments[1], 'only 1 audio packet')


def test_concat_reports_malformed_audio_metric(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / 'source' / '2025-01-01' / '00' / 'CAM2'
    source.mkdir(parents=True)
    segment = source / '00.00.mp4'
    segment.write_bytes(b'segment')
    captured_metrics: dict[str, float | int] = {}

    def fake_build(src_files: list[Path], dst_file: Path) -> tuple[int, bool]:
        dst_file.parent.mkdir(parents=True)
        dst_file.write_bytes(b'archive')
        return len(b'archive'), False

    monkeypatch.setattr(concat, 'build_validated_archive', fake_build)
    monkeypatch.setattr(concat, 'find_audio_issue', lambda src_files: (segment, 'only 1 audio packet'))
    monkeypatch.setattr(concat, 'generate_sidecar', lambda **kwargs: None)
    monkeypatch.setattr(
        concat,
        'write_metrics_file',
        lambda path, metrics: captured_metrics.update(metrics),
    )

    result = CliRunner().invoke(
        concat.main,
        [
            str(tmp_path / 'source'),
            str(tmp_path / 'archive'),
            '--metrics-file',
            str(tmp_path / 'metrics.prom'),
        ],
    )

    assert result.exit_code == 0
    assert 'WARNING: malformed audio for 2025-01-01/00_CAM2' in result.output
    assert captured_metrics['fricat_concat_malformed_audio_archives'] == 1
