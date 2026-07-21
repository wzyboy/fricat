import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from fricat import repair


def _recording(root: Path, camera: str = 'CAM2') -> Path:
    day = root / '2026-07-20'
    day.mkdir(parents=True)
    recording = day / f'23_{camera}.mkv'
    recording.write_bytes(b'original')
    return recording


def test_repair_defaults_to_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recording = _recording(tmp_path)
    monkeypatch.setattr(repair, 'probe_duration', lambda path: 99029.0)
    remux_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(repair, 'remux', lambda source, target: remux_calls.append((source, target)))

    result = CliRunner().invoke(repair.main, [str(tmp_path)])

    assert result.exit_code == 0
    assert 'MALFORMED 2026-07-20/23_CAM2.mkv: 99029.000s' in result.output
    assert '1 malformed, 0 repaired, 0 failed' in result.output
    assert 'Finding recordings:' in result.stderr
    assert 'Scanning recordings:' in result.stderr
    assert 'Repairing recordings:' not in result.stderr
    assert recording.read_bytes() == b'original'
    assert remux_calls == []


def test_repair_apply_preserves_permissions_but_updates_mtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recording = _recording(tmp_path)
    recording.chmod(0o640)
    timestamp_ns = 1_700_000_000_123_456_789
    os.utime(recording, ns=(timestamp_ns, timestamp_ns))

    def fake_probe(path: Path) -> float:
        return 3605.0 if '.repair.' in str(path.parent) else 99029.0

    monkeypatch.setattr(repair, 'probe_duration', fake_probe)
    monkeypatch.setattr(repair, 'remux', lambda source, target: target.write_bytes(b'repaired'))

    result = CliRunner().invoke(repair.main, [str(tmp_path), '--apply'])

    assert result.exit_code == 0
    assert 'REPAIRED  2026-07-20/23_CAM2.mkv' in result.output
    assert 'Repairing recordings:' in result.stderr
    assert recording.read_bytes() == b'repaired'
    assert recording.stat().st_mode & 0o777 == 0o640
    assert recording.stat().st_mtime_ns > timestamp_ns
    assert not any('.repair.' in path.name for path in recording.parent.iterdir())


def test_repair_failure_leaves_original_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recording = _recording(tmp_path)
    monkeypatch.setattr(repair, 'probe_duration', lambda path: 99029.0)
    monkeypatch.setattr(repair, 'remux', lambda source, target: target.write_bytes(b'still-bad'))

    result = CliRunner().invoke(repair.main, [str(tmp_path), '--apply'])

    assert result.exit_code != 0
    assert 'FAILED    2026-07-20/23_CAM2.mkv' in result.output
    assert recording.read_bytes() == b'original'
    assert not any('.repair.' in path.name for path in recording.parent.iterdir())


def test_repair_skips_valid_files_and_honors_custom_threshold(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recording = _recording(tmp_path, camera='CAM1')
    monkeypatch.setattr(repair, 'probe_duration', lambda path: 3605.0)

    normal = CliRunner().invoke(repair.main, [str(tmp_path)])
    strict = CliRunner().invoke(repair.main, [str(tmp_path), '--max-duration', '3600'])

    assert normal.exit_code == 0
    assert '1 valid, 0 malformed' in normal.output
    assert strict.exit_code == 0
    assert '0 valid, 1 malformed' in strict.output
    assert recording.read_bytes() == b'original'
