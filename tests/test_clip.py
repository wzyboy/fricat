import shutil
import subprocess
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from fricat import clip
from fricat.media import probe_duration


def test_parse_local_timestamp_converts_to_utc() -> None:
    result = clip.parse_local_timestamp(
        '2026-07-26 08:55:00',
        ZoneInfo('America/Vancouver'),
    )

    assert result == datetime(2026, 7, 26, 15, 55, tzinfo=UTC)


@pytest.mark.parametrize(
    'value',
    [
        '2026-03-08 02:30:00',
        '2025-11-02 01:30:00',
    ],
)
def test_parse_local_timestamp_rejects_dst_edge_cases(value: str) -> None:
    with pytest.raises(ValueError):
        clip.parse_local_timestamp(value, ZoneInfo('America/Vancouver'))


def test_clip_filename_includes_end_date_across_midnight() -> None:
    timezone = ZoneInfo('UTC')

    result = clip.clip_filename(
        datetime(2026, 7, 26, 23, 55, tzinfo=UTC),
        datetime(2026, 7, 27, 0, 5, tzinfo=UTC),
        'CAM1',
        timezone,
    )

    assert result == '2026-07-26_23-55-00_to_2026-07-27_00-05-00_CAM1.mp4'


def test_plan_media_slices_prefers_archive_then_uses_raw() -> None:
    start = datetime(2026, 7, 26, 8, 59, 50, tzinfo=UTC)
    boundary = datetime(2026, 7, 26, 9, tzinfo=UTC)
    end = boundary + timedelta(seconds=20)
    sources = [
        clip.MediaSource(
            Path('/archive/08_CAM1.mkv'),
            datetime(2026, 7, 26, 8, tzinfo=UTC),
            boundary,
            priority=0,
        ),
        clip.MediaSource(
            Path('/segments/09.00.00.mp4'),
            boundary,
            boundary + timedelta(seconds=10),
            priority=1,
        ),
        clip.MediaSource(
            Path('/segments/08.59.50.mp4'),
            start,
            boundary,
            priority=1,
        ),
        clip.MediaSource(
            Path('/segments/09.00.10.mp4'),
            boundary + timedelta(seconds=10),
            end,
            priority=1,
        ),
    ]

    slices = clip.plan_media_slices(sources, start, end)

    assert slices == [
        clip.MediaSlice(Path('/archive/08_CAM1.mkv'), 3590.0, 10.0),
        clip.MediaSlice(Path('/segments/09.00.00.mp4'), 0.0, 10.0),
        clip.MediaSlice(Path('/segments/09.00.10.mp4'), 0.0, 10.0),
    ]


def test_plan_media_slices_reports_first_gap() -> None:
    start = datetime(2026, 7, 26, 8, tzinfo=UTC)
    source_end = start + timedelta(seconds=10)
    requested_end = start + timedelta(seconds=30)
    sources = [
        clip.MediaSource(Path('/segment.mp4'), start, source_end, priority=1),
    ]

    with pytest.raises(clip.CoverageError) as exc_info:
        clip.plan_media_slices(sources, start, requested_end)

    assert exc_info.value.start_utc == source_end
    assert exc_info.value.end_utc == requested_end


def test_resolve_media_slices_combines_archive_and_raw(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / 'archive'
    segments_root = tmp_path / 'segments'
    archive = archive_root / '2026-07-26' / '08_CAM1.mkv'
    first_segment = segments_root / '2026-07-26' / '09' / 'CAM1' / '00.00.mp4'
    second_segment = segments_root / '2026-07-26' / '09' / 'CAM1' / '00.10.mp4'
    archive.parent.mkdir(parents=True)
    first_segment.parent.mkdir(parents=True)
    for path in (archive, first_segment, second_segment):
        path.write_bytes(b'media')

    durations = {archive: 3600.0, first_segment: 10.0, second_segment: 10.0}
    monkeypatch.setattr(clip, 'probe_duration', lambda path: durations[path])
    start = datetime(2026, 7, 26, 8, 59, 50, tzinfo=UTC)
    end = datetime(2026, 7, 26, 9, 0, 20, tzinfo=UTC)

    slices = clip.resolve_media_slices(
        archive_root,
        segments_root,
        'CAM1',
        start,
        end,
        ZoneInfo('UTC'),
    )

    assert [media_slice.path for media_slice in slices] == [
        archive,
        first_segment,
        second_segment,
    ]


def test_resolve_media_slices_skips_raw_discovery_when_archive_covers_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 7, 26, 8, 10, tzinfo=UTC)
    end = datetime(2026, 7, 26, 8, 20, tzinfo=UTC)
    archive = clip.MediaSource(
        Path('/archive/08_CAM1.mkv'),
        datetime(2026, 7, 26, 8, tzinfo=UTC),
        datetime(2026, 7, 26, 9, tzinfo=UTC),
        priority=0,
    )
    monkeypatch.setattr(clip, 'find_archive_sources', lambda *args: [archive])

    def fail_raw_discovery(*args: object) -> list[clip.MediaSource]:
        raise AssertionError('raw segments should not be scanned')

    monkeypatch.setattr(clip, 'find_segment_sources_for_ranges', fail_raw_discovery)

    slices = clip.resolve_media_slices(
        Path('/archive'),
        Path('/segments'),
        'CAM1',
        start,
        end,
        ZoneInfo('UTC'),
    )

    assert slices == [
        clip.MediaSlice(Path('/archive/08_CAM1.mkv'), 600.0, 600.0),
    ]


def test_find_segment_sources_probes_only_requested_minute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / 'segments'
    for hour in (8, 9):
        camera_dir = root / '2026-07-26' / f'{hour:02d}' / 'CAM1'
        camera_dir.mkdir(parents=True)
        for index in range(360):
            minute, second_index = divmod(index, 6)
            second = second_index * 10
            (camera_dir / f'{minute:02d}.{second:02d}.mp4').write_bytes(b'media')

    probed: list[Path] = []

    def fake_probe(path: Path) -> float:
        probed.append(path)
        return 10.0

    monkeypatch.setattr(clip, 'probe_duration', fake_probe)
    start = datetime(2026, 7, 26, 9, 55, tzinfo=UTC)
    end = datetime(2026, 7, 26, 9, 56, tzinfo=UTC)

    sources = clip.find_segment_sources(root, 'CAM1', start, end)

    assert [source.path.name for source in sources] == [
        '55.00.mp4',
        '55.10.mp4',
        '55.20.mp4',
        '55.30.mp4',
        '55.40.mp4',
        '55.50.mp4',
    ]
    assert [path.name for path in probed] == [
        '54.50.mp4',
        '55.00.mp4',
        '55.10.mp4',
        '55.20.mp4',
        '55.30.mp4',
        '55.40.mp4',
        '55.50.mp4',
    ]


def test_find_uncovered_ranges_returns_only_archive_gaps() -> None:
    start = datetime(2026, 7, 26, 8, tzinfo=UTC)
    end = datetime(2026, 7, 26, 10, tzinfo=UTC)
    sources = [
        clip.MediaSource(
            Path('/archive/08_CAM1.mkv'),
            start,
            start + timedelta(hours=1),
            priority=0,
        ),
        clip.MediaSource(
            Path('/archive/09_CAM1.mkv'),
            start + timedelta(hours=1, minutes=5),
            end,
            priority=0,
        ),
    ]

    ranges = clip.find_uncovered_ranges(sources, start, end)

    assert ranges == [
        (
            datetime(2026, 7, 26, 9, tzinfo=UTC),
            datetime(2026, 7, 26, 9, 5, tzinfo=UTC),
        )
    ]


def test_export_media_slices_publishes_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / 'source.mkv'
    source.write_bytes(b'media')
    output = tmp_path / 'result.mp4'
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        Path(command[-1]).write_bytes(b'clip')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(clip.subprocess, 'run', fake_run)

    clip.export_media_slices(
        [clip.MediaSlice(source, 5.0, 10.0)],
        10.0,
        output,
    )

    assert output.read_bytes() == b'clip'
    assert commands[0][commands[0].index('-ss') + 1] == '5.0'
    assert not any(path.name.startswith('.result.') for path in tmp_path.iterdir())


def test_export_media_slices_does_not_overwrite(tmp_path: Path) -> None:
    output = tmp_path / 'result.mp4'
    output.write_bytes(b'existing')

    with pytest.raises(clip.ClipExportError, match='already exists'):
        clip.export_media_slices(
            [clip.MediaSlice(tmp_path / 'source.mkv', 0.0, 1.0)],
            1.0,
            output,
        )

    assert output.read_bytes() == b'existing'


@pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg and ffprobe are required',
)
def test_export_media_slices_combines_multiple_sources(tmp_path: Path) -> None:
    sources: list[Path] = []
    for index, color in enumerate(('red', 'blue')):
        source = tmp_path / f'{index}.mp4'
        subprocess.run(
            [
                'ffmpeg',
                '-hide_banner',
                '-loglevel',
                'error',
                '-f',
                'lavfi',
                '-i',
                f'color=c={color}:s=160x90:r=10:d=2',
                '-f',
                'lavfi',
                '-i',
                f'sine=frequency={440 + index * 440}:sample_rate=8000:duration=2',
                '-c:v',
                'mpeg4',
                '-c:a',
                'aac',
                str(source),
            ],
            check=True,
        )
        sources.append(source)

    output = tmp_path / 'combined.mp4'
    clip.export_media_slices(
        [
            clip.MediaSlice(sources[0], 1.0, 1.0),
            clip.MediaSlice(sources[1], 0.0, 1.0),
        ],
        2.0,
        output,
    )

    assert output.is_file()
    assert probe_duration(output) == pytest.approx(2.0, abs=0.25)
