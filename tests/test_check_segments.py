import os
from pathlib import Path

import pytest
from click.testing import CliRunner
from prometheus_client.parser import text_string_to_metric_families

from fricat import check_segments
from fricat.media import AudioHealth


def _metric_samples(path: Path) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    samples: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
    for family in text_string_to_metric_families(path.read_text()):
        for sample in family.samples:
            key = sample.name, tuple(sorted(sample.labels.items()))
            samples[key] = sample.value
    return samples


def _segments(root: Path, camera: str, now: float, ages: list[float]) -> list[Path]:
    camera_dir = root / '2026-07-21' / '22' / camera
    camera_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    for index, age in enumerate(ages):
        segment = camera_dir / f'00.{index:02d}.mp4'
        segment.write_bytes(b'media')
        os.utime(segment, (now - age, now - age))
        segments.append(segment)
    return segments


def test_check_segments_discovers_healthy_cameras(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    _segments(tmp_path, 'CAM1', now, [20, 30, 40])
    _segments(tmp_path, 'CAM2', now, [20, 30, 40])
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)
    monkeypatch.setattr(
        check_segments,
        'probe_audio_health',
        lambda path, max_gap: AudioHealth(True, 78),
    )
    metrics_file = tmp_path / 'metrics.prom'

    result = CliRunner().invoke(
        check_segments.main,
        [str(tmp_path), '--metrics-file', str(metrics_file)],
    )

    assert result.exit_code == 0
    assert 'HEALTHY   CAM1: 3 segment(s)' in result.output
    assert 'HEALTHY   CAM2: 3 segment(s)' in result.output
    assert '2 healthy, 0 unhealthy, 0 errors' in result.output
    metrics = _metric_samples(metrics_file)
    assert metrics[('fricat_check_segments_camera_healthy', (('camera', 'CAM1'),))] == 1
    assert metrics[('fricat_check_segments_camera_healthy', (('camera', 'CAM2'),))] == 1
    assert metrics[
        ('fricat_check_segments_latest_segment_timestamp_seconds', (('camera', 'CAM1'),))
    ] == now - 20
    assert metrics[('fricat_check_segments_checked_cameras', ())] == 2
    assert metrics[('fricat_check_segments_healthy_cameras', ())] == 2
    assert metrics[('fricat_check_segments_unhealthy_cameras', ())] == 0
    assert metrics[('fricat_check_segments_errors', ())] == 0
    assert metrics[('fricat_check_segments_last_run_timestamp_seconds', ())] == now


def test_check_segments_reports_corruption_and_exit_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    _segments(tmp_path, 'CAM2', now, [20, 30, 40])
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)
    monkeypatch.setattr(
        check_segments,
        'probe_audio_health',
        lambda path, max_gap: AudioHealth(False, 1, 'only 1 audio packet(s)'),
    )

    metrics_file = tmp_path / 'metrics.prom'
    result = CliRunner().invoke(
        check_segments.main,
        [
            str(tmp_path),
            '--camera',
            'CAM2',
            '--metrics-file',
            str(metrics_file),
        ],
    )

    assert result.exit_code == 1
    assert 'UNHEALTHY CAM2:' in result.output
    assert 'only 1 audio packet(s)' in result.output
    metrics = _metric_samples(metrics_file)
    assert metrics[('fricat_check_segments_camera_healthy', (('camera', 'CAM2'),))] == 0
    assert metrics[('fricat_check_segments_unhealthy_cameras', ())] == 1


def test_check_segments_ignores_active_file_and_detects_stale_camera(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    active, stale = _segments(tmp_path, 'CAM5', now, [5, 90])
    checked: list[Path] = []
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)

    def fake_probe(path: Path, max_gap: float) -> AudioHealth:
        checked.append(path)
        return AudioHealth(True, 78)

    monkeypatch.setattr(check_segments, 'probe_audio_health', fake_probe)

    metrics_file = tmp_path / 'metrics.prom'
    result = CliRunner().invoke(
        check_segments.main,
        [
            str(tmp_path),
            '--camera',
            'CAM5',
            '--metrics-file',
            str(metrics_file),
        ],
    )

    assert result.exit_code == 1
    assert 'newest completed segment is 90.0s old' in result.output
    assert active not in checked
    assert stale not in checked
    metrics = _metric_samples(metrics_file)
    assert metrics[('fricat_check_segments_camera_healthy', (('camera', 'CAM5'),))] == 0
    assert metrics[
        ('fricat_check_segments_latest_segment_timestamp_seconds', (('camera', 'CAM5'),))
    ] == now - 90


def test_check_segments_returns_two_when_checker_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    _segments(tmp_path, 'CAM2', now, [20])
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)
    monkeypatch.setattr(
        check_segments,
        'probe_audio_health',
        lambda path, max_gap: (_ for _ in ()).throw(FileNotFoundError('ffprobe')),
    )

    metrics_file = tmp_path / 'metrics.prom'
    result = CliRunner().invoke(
        check_segments.main,
        [
            str(tmp_path),
            '--camera',
            'CAM2',
            '--metrics-file',
            str(metrics_file),
        ],
    )

    assert result.exit_code == 2
    assert 'ERROR     CAM2: checker failed: ffprobe' in result.output
    metrics = _metric_samples(metrics_file)
    assert metrics[('fricat_check_segments_camera_healthy', (('camera', 'CAM2'),))] == 0
    assert metrics[('fricat_check_segments_errors', ())] == 1


def test_check_segments_reports_expected_camera_without_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    _segments(tmp_path, 'CAM1', now, [20])
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)

    metrics_file = tmp_path / 'metrics.prom'
    result = CliRunner().invoke(
        check_segments.main,
        [
            str(tmp_path),
            '--camera',
            'CAM8',
            '--metrics-file',
            str(metrics_file),
        ],
    )

    assert result.exit_code == 1
    assert 'UNHEALTHY CAM8: no completed segments found' in result.output
    metrics = _metric_samples(metrics_file)
    assert metrics[('fricat_check_segments_camera_healthy', (('camera', 'CAM8'),))] == 0
    assert metrics[
        ('fricat_check_segments_latest_segment_timestamp_seconds', (('camera', 'CAM8'),))
    ] == 0


def test_check_segments_returns_two_when_metrics_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    _segments(tmp_path, 'CAM1', now, [20])
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)
    monkeypatch.setattr(
        check_segments,
        'probe_audio_health',
        lambda path, max_gap: AudioHealth(True, 78),
    )
    monkeypatch.setattr(
        check_segments,
        'write_check_metrics',
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError('read-only')),
    )

    result = CliRunner().invoke(check_segments.main, [str(tmp_path), '--camera', 'CAM1'])

    assert result.exit_code == 2
    assert 'ERROR: unable to write metrics: read-only' in result.output
