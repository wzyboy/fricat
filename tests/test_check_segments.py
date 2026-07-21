import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from fricat import check_segments
from fricat.media import AudioHealth


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

    result = CliRunner().invoke(check_segments.main, [str(tmp_path)])

    assert result.exit_code == 0
    assert 'HEALTHY   CAM1: 3 segment(s)' in result.output
    assert 'HEALTHY   CAM2: 3 segment(s)' in result.output
    assert '2 healthy, 0 unhealthy, 0 errors' in result.output


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

    result = CliRunner().invoke(check_segments.main, [str(tmp_path), '--camera', 'CAM2'])

    assert result.exit_code == 1
    assert 'UNHEALTHY CAM2:' in result.output
    assert 'only 1 audio packet(s)' in result.output


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

    result = CliRunner().invoke(check_segments.main, [str(tmp_path), '--camera', 'CAM5'])

    assert result.exit_code == 1
    assert 'newest completed segment is 90.0s old' in result.output
    assert active not in checked
    assert stale not in checked


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

    result = CliRunner().invoke(check_segments.main, [str(tmp_path), '--camera', 'CAM2'])

    assert result.exit_code == 2
    assert 'ERROR     CAM2: checker failed: ffprobe' in result.output


def test_check_segments_reports_expected_camera_without_segments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    now = 1_800_000_000.0
    _segments(tmp_path, 'CAM1', now, [20])
    monkeypatch.setattr(check_segments.time, 'time', lambda: now)

    result = CliRunner().invoke(check_segments.main, [str(tmp_path), '--camera', 'CAM8'])

    assert result.exit_code == 1
    assert 'UNHEALTHY CAM8: no completed segments found' in result.output
