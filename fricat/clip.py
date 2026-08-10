import os
import math
import subprocess
from bisect import bisect_left
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from fricat.media import probe_duration
from fricat.utils import parse_recording_path

DEFAULT_ARCHIVE_TIMEZONE = 'America/Vancouver'
LEGACY_FILENAME_CUTOFF = datetime(2025, 11, 18)
COVERAGE_TOLERANCE_SECONDS = 1.0


class ClipExportError(Exception):
    pass


class CoverageError(ClipExportError):
    def __init__(self, start_utc: datetime, end_utc: datetime) -> None:
        self.start_utc = start_utc
        self.end_utc = end_utc
        super().__init__(
            f'No recording coverage from {start_utc.isoformat()} to {end_utc.isoformat()}'
        )


@dataclass(frozen=True)
class MediaSource:
    path: Path
    start_utc: datetime
    end_utc: datetime
    priority: int


@dataclass(frozen=True)
class MediaSlice:
    path: Path
    start: float
    duration: float


def recording_start_utc(
    date_str: str,
    hour_str: str,
    archive_tz: ZoneInfo,
) -> datetime:
    filename_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00')
    if filename_dt < LEGACY_FILENAME_CUTOFF:
        return filename_dt.replace(tzinfo=archive_tz).astimezone(UTC)
    return filename_dt.replace(tzinfo=UTC)


def parse_local_timestamp(value: str, timezone: ZoneInfo) -> datetime:
    try:
        local_dt = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f'Invalid local timestamp: {value!r}') from err
    if local_dt.tzinfo is not None:
        raise ValueError('Timestamps must not include a UTC offset or timezone')

    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        aware = local_dt.replace(tzinfo=timezone, fold=fold)
        utc_dt = aware.astimezone(UTC)
        round_trip = utc_dt.astimezone(timezone)
        if round_trip.replace(tzinfo=None) == local_dt and round_trip.fold == fold:
            candidates[utc_dt] = aware

    if not candidates:
        raise ValueError(f'Local timestamp does not exist in {timezone.key}: {value}')
    if len(candidates) > 1:
        raise ValueError(f'Local timestamp is ambiguous in {timezone.key}: {value}')
    return next(iter(candidates)).astimezone(UTC)


def clip_filename(
    start_utc: datetime,
    end_utc: datetime,
    camera: str,
    timezone: ZoneInfo,
) -> str:
    start_dt = start_utc.astimezone(timezone)
    end_dt = end_utc.astimezone(timezone)
    if start_dt.date() == end_dt.date():
        end_text = f'{end_dt:%H-%M-%S}'
    else:
        end_text = f'{end_dt:%Y-%m-%d_%H-%M-%S}'
    return f'{start_dt:%Y-%m-%d_%H-%M-%S}_to_{end_text}_{camera}.mp4'


def _date_strings(start_utc: datetime, end_utc: datetime) -> list[str]:
    current = (start_utc - timedelta(days=1)).date()
    last = (end_utc + timedelta(days=1)).date()
    values: list[str] = []
    while current <= last:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return values


def _probe_source_duration(path: Path) -> float | None:
    try:
        duration = probe_duration(path)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def find_archive_sources(
    root: Path,
    camera: str,
    start_utc: datetime,
    end_utc: datetime,
    archive_tz: ZoneInfo,
) -> list[MediaSource]:
    sources: list[MediaSource] = []
    for date_str in _date_strings(start_utc, end_utc):
        day_dir = root if root.name == date_str else root / date_str
        for path in sorted(day_dir.glob('*.mkv')):
            parsed = parse_recording_path(root, path)
            if not parsed:
                continue
            parsed_date, hour_str, parsed_camera = parsed
            if parsed_camera != camera:
                continue
            try:
                source_start = recording_start_utc(parsed_date, hour_str, archive_tz)
            except ValueError:
                continue
            nominal_end = source_start + timedelta(hours=1)
            if nominal_end <= start_utc or source_start >= end_utc:
                continue
            duration = _probe_source_duration(path)
            if duration is None:
                continue
            source_end = source_start + timedelta(seconds=min(duration, 3600.0))
            if source_end > start_utc:
                sources.append(MediaSource(path, source_start, source_end, priority=0))
    return sources


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _segment_candidates(
    root: Path,
    camera: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[tuple[datetime, Path]]:
    candidates: list[tuple[datetime, Path]] = []
    current_hour = _floor_hour(start_utc) - timedelta(hours=1)
    last_hour = _floor_hour(end_utc)
    while current_hour <= last_hour:
        camera_dir = root / f'{current_hour:%Y-%m-%d}' / f'{current_hour:%H}' / camera
        for path in sorted(camera_dir.glob('*.mp4')):
            parts = path.stem.split('.')
            if len(parts) != 2:
                continue
            try:
                minute, second = (int(part) for part in parts)
                source_start = current_hour.replace(minute=minute, second=second)
            except ValueError:
                continue
            if source_start >= end_utc:
                continue
            candidates.append((source_start, path))
        current_hour += timedelta(hours=1)

    candidates.sort()
    starts = [source_start for source_start, _ in candidates]
    first_index = max(0, bisect_left(starts, start_utc) - 1)
    return candidates[first_index:]


def find_segment_sources_for_ranges(
    root: Path,
    camera: str,
    ranges: list[tuple[datetime, datetime]],
) -> list[MediaSource]:
    candidates: dict[Path, datetime] = {}
    for start_utc, end_utc in ranges:
        for source_start, path in _segment_candidates(root, camera, start_utc, end_utc):
            candidates[path] = source_start

    sources: list[MediaSource] = []
    for path, source_start in sorted(candidates.items(), key=lambda item: (item[1], item[0])):
        duration = _probe_source_duration(path)
        if duration is None:
            continue
        source_end = source_start + timedelta(seconds=duration)
        if any(
            source_end > range_start and source_start < range_end
            for range_start, range_end in ranges
        ):
            sources.append(MediaSource(path, source_start, source_end, priority=1))
    return sources


def find_segment_sources(
    root: Path,
    camera: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[MediaSource]:
    return find_segment_sources_for_ranges(root, camera, [(start_utc, end_utc)])


def find_uncovered_ranges(
    sources: list[MediaSource],
    start_utc: datetime,
    end_utc: datetime,
) -> list[tuple[datetime, datetime]]:
    cursor = start_utc
    ranges: list[tuple[datetime, datetime]] = []
    tolerance = timedelta(seconds=COVERAGE_TOLERANCE_SECONDS)

    for source in sorted(sources, key=lambda item: (item.start_utc, item.end_utc)):
        if source.end_utc <= cursor or source.start_utc >= end_utc:
            continue
        if source.start_utc > cursor + tolerance:
            ranges.append((cursor, min(source.start_utc, end_utc)))
        cursor = max(cursor, min(source.end_utc, end_utc))
        if cursor >= end_utc:
            break

    if cursor < end_utc:
        ranges.append((cursor, end_utc))
    return ranges


def plan_media_slices(
    sources: list[MediaSource],
    start_utc: datetime,
    end_utc: datetime,
) -> list[MediaSlice]:
    if start_utc >= end_utc:
        raise ValueError('Clip start must be earlier than clip end')

    ordered = sorted(sources, key=lambda source: (source.priority, source.start_utc, source.path))
    cursor = start_utc
    slices: list[MediaSlice] = []
    tolerance = timedelta(seconds=COVERAGE_TOLERANCE_SECONDS)

    while cursor < end_utc:
        candidates = [
            source
            for source in ordered
            if source.start_utc <= cursor + tolerance and source.end_utc > cursor
        ]
        if not candidates:
            next_start = min(
                (source.start_utc for source in ordered if source.start_utc > cursor),
                default=end_utc,
            )
            raise CoverageError(cursor, min(next_start, end_utc))

        priority = min(source.priority for source in candidates)
        candidate = max(
            (source for source in candidates if source.priority == priority),
            key=lambda source: source.end_utc,
        )
        slice_start = max(cursor, candidate.start_utc)
        slice_end = min(candidate.end_utc, end_utc)
        slices.append(
            MediaSlice(
                path=candidate.path,
                start=(slice_start - candidate.start_utc).total_seconds(),
                duration=(slice_end - slice_start).total_seconds(),
            )
        )
        cursor = slice_end

    return slices


def resolve_media_slices(
    archive_root: Path,
    segments_root: Path,
    camera: str,
    start_utc: datetime,
    end_utc: datetime,
    archive_tz: ZoneInfo,
) -> list[MediaSlice]:
    archive_sources = find_archive_sources(
        archive_root,
        camera,
        start_utc,
        end_utc,
        archive_tz,
    )
    uncovered_ranges = find_uncovered_ranges(archive_sources, start_utc, end_utc)
    if not uncovered_ranges:
        return plan_media_slices(archive_sources, start_utc, end_utc)

    segment_sources = find_segment_sources_for_ranges(
        segments_root,
        camera,
        uncovered_ranges,
    )
    return plan_media_slices(
        [*archive_sources, *segment_sources],
        start_utc,
        end_utc,
    )


def _ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except OSError as err:
        raise ClipExportError(str(err)) from err
    except subprocess.CalledProcessError as err:
        detail = err.stderr.strip() or str(err)
        raise ClipExportError(detail) from err


def export_source_clip(source: Path, start: float, duration: float, output: Path) -> None:
    command = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel',
        'error',
        '-ss',
        str(start),
        '-i',
        str(source),
        '-t',
        str(duration),
        '-map',
        '0:v:0',
        '-map',
        '0:a:0?',
        '-c:v',
        'copy',
        '-af',
        'aresample=async=1:first_pts=0,apad',
        '-c:a',
        'aac',
        '-shortest',
        '-avoid_negative_ts',
        'make_zero',
        '-movflags',
        '+faststart',
        '-y',
        str(output),
    ]
    _run(command)
    if not output.is_file():
        raise ClipExportError('FFmpeg did not create the clip output')


def _assemble_slices(slices: list[MediaSlice], output: Path, temp_dir: Path) -> None:
    manifest = temp_dir / 'sources.ffconcat'
    lines = ['ffconcat version 1.0']
    for media_slice in slices:
        lines.extend(
            [
                f"file '{_ffconcat_quote(media_slice.path)}'",
                f'inpoint {media_slice.start}',
                f'outpoint {media_slice.start + media_slice.duration}',
            ]
        )
    manifest.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    command = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel',
        'error',
        '-f',
        'concat',
        '-safe',
        '0',
        '-i',
        str(manifest),
        '-map',
        '0',
        '-c',
        'copy',
        '-avoid_negative_ts',
        'make_zero',
        '-y',
        str(output),
    ]
    _run(command)
    if not output.is_file():
        raise ClipExportError('FFmpeg did not assemble the clip sources')


def export_media_slices(
    slices: list[MediaSlice],
    duration: float,
    output: Path,
) -> None:
    if not slices:
        raise ClipExportError('No media sources were selected')
    if output.exists():
        raise ClipExportError(f'Output already exists: {output}')
    if not output.parent.is_dir():
        raise ClipExportError(f'Output directory does not exist: {output.parent}')

    with TemporaryDirectory(prefix=f'.{output.stem}.', dir=output.parent) as temp_name:
        temp_dir = Path(temp_name)
        temporary_output = temp_dir / output.name
        if len(slices) == 1:
            media_slice = slices[0]
            export_source_clip(
                media_slice.path,
                media_slice.start,
                media_slice.duration,
                temporary_output,
            )
        else:
            assembled = temp_dir / 'assembled.mkv'
            _assemble_slices(slices, assembled, temp_dir)
            export_source_clip(assembled, 0.0, duration, temporary_output)
        os.replace(temporary_output, output)
