from pathlib import Path
from datetime import UTC
from datetime import datetime

import pytest
from click.testing import CliRunner

from fricat import clip
from fricat import export_clip


def test_export_clip_uses_default_filename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / 'archive'
    segments_root = tmp_path / 'segments'
    archive_root.mkdir()
    segments_root.mkdir()
    source = tmp_path / 'source.mkv'
    slices = [clip.MediaSlice(source, 0.0, 600.0)]
    captured: dict[str, object] = {}

    def fake_resolve(*args: object) -> list[clip.MediaSlice]:
        captured['resolve'] = args
        return slices

    def fake_export(
        selected: list[clip.MediaSlice],
        duration: float,
        output: Path,
    ) -> None:
        captured['export'] = (selected, duration, output)

    monkeypatch.setattr(export_clip, 'resolve_media_slices', fake_resolve)
    monkeypatch.setattr(export_clip, 'export_media_slices', fake_export)
    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(
            export_clip.main,
            [
                'CAM1',
                '2026-07-26 08:55:00',
                '2026-07-26 09:05:00',
                '--timezone',
                'UTC',
                '--archive-root',
                str(archive_root),
                '--segments-root',
                str(segments_root),
            ],
        )
        expected = Path.cwd() / '2026-07-26_08-55-00_to_09-05-00_CAM1.mp4'

    assert result.exit_code == 0
    assert captured['export'] == (slices, 600.0, expected)
    assert f'Exported clip to {expected}' in result.output


def test_export_clip_reports_missing_coverage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / 'archive'
    segments_root = tmp_path / 'segments'
    archive_root.mkdir()
    segments_root.mkdir()

    def fail_resolve(*args: object) -> list[clip.MediaSlice]:
        raise clip.CoverageError(
            datetime(2026, 7, 26, 9, tzinfo=UTC),
            datetime(2026, 7, 26, 9, 5, tzinfo=UTC),
        )

    monkeypatch.setattr(export_clip, 'resolve_media_slices', fail_resolve)

    result = CliRunner().invoke(
        export_clip.main,
        [
            'CAM1',
            '2026-07-26 08:55:00',
            '2026-07-26 09:05:00',
            '--timezone',
            'UTC',
            '--archive-root',
            str(archive_root),
            '--segments-root',
            str(segments_root),
        ],
    )

    assert result.exit_code == 1
    assert 'Recording coverage is unavailable from' in result.output


def test_export_clip_rejects_reversed_range(tmp_path: Path) -> None:
    archive_root = tmp_path / 'archive'
    segments_root = tmp_path / 'segments'
    archive_root.mkdir()
    segments_root.mkdir()

    result = CliRunner().invoke(
        export_clip.main,
        [
            'CAM1',
            '2026-07-26 09:05:00',
            '2026-07-26 08:55:00',
            '--archive-root',
            str(archive_root),
            '--segments-root',
            str(segments_root),
        ],
    )

    assert result.exit_code == 1
    assert 'START must be earlier than END' in result.output


def test_export_clip_rejects_camera_path(tmp_path: Path) -> None:
    archive_root = tmp_path / 'archive'
    segments_root = tmp_path / 'segments'
    archive_root.mkdir()
    segments_root.mkdir()

    result = CliRunner().invoke(
        export_clip.main,
        [
            '../CAM1',
            '2026-07-26 08:55:00',
            '2026-07-26 09:05:00',
            '--archive-root',
            str(archive_root),
            '--segments-root',
            str(segments_root),
        ],
    )

    assert result.exit_code == 1
    assert 'CAMERA must be a single path component' in result.output
