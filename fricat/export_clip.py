from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

import click

from fricat.clip import DEFAULT_ARCHIVE_TIMEZONE
from fricat.clip import CoverageError
from fricat.clip import ClipExportError
from fricat.clip import clip_filename
from fricat.clip import export_media_slices
from fricat.clip import resolve_media_slices
from fricat.clip import parse_local_timestamp


def _format_local(value: datetime, timezone: ZoneInfo) -> str:
    return value.astimezone(timezone).isoformat()


@click.command(name='export-clip')
@click.argument('camera')
@click.argument('start')
@click.argument('end')
@click.option(
    '--archive-root',
    type=click.Path(file_okay=False, path_type=Path),
    default='/media/public/NVR',
    help='Root containing YYYY-MM-DD/HH_CAMERA.mkv hourly archives.',
)
@click.option(
    '--segments-root',
    type=click.Path(file_okay=False, path_type=Path),
    default='/fastpool/frigate/recordings',
    help='Root containing raw YYYY-MM-DD/HH/CAMERA/MM.SS.mp4 segments.',
)
@click.option(
    '--timezone',
    'timezone_name',
    default=DEFAULT_ARCHIVE_TIMEZONE,
    show_default=True,
    help='Timezone used to interpret START and END.',
)
@click.option(
    '-o',
    '--output',
    type=click.Path(dir_okay=False, path_type=Path),
    help='Output MP4 path. Defaults to a timestamp-based filename.',
)
def main(
    camera: str,
    start: str,
    end: str,
    archive_root: Path,
    segments_root: Path,
    timezone_name: str,
    output: Path | None,
) -> None:
    """Export CAMERA recordings between local ISO timestamps START and END."""
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as err:
        raise click.ClickException(f'Unknown timezone: {timezone_name}') from err

    try:
        start_utc = parse_local_timestamp(start, timezone)
        end_utc = parse_local_timestamp(end, timezone)
    except ValueError as err:
        raise click.ClickException(str(err)) from err
    if start_utc >= end_utc:
        raise click.ClickException('START must be earlier than END')
    if not camera or Path(camera).name != camera or camera in {'.', '..'}:
        raise click.ClickException('CAMERA must be a single path component')

    if output is None:
        output = Path(clip_filename(start_utc, end_utc, camera, timezone))
    output = output.resolve()

    try:
        slices = resolve_media_slices(
            archive_root.resolve(),
            segments_root.resolve(),
            camera,
            start_utc,
            end_utc,
            timezone,
        )
        export_media_slices(
            slices,
            (end_utc - start_utc).total_seconds(),
            output,
        )
    except CoverageError as err:
        missing_start = _format_local(err.start_utc, timezone)
        missing_end = _format_local(err.end_utc, timezone)
        raise click.ClickException(
            f'Recording coverage is unavailable from {missing_start} to {missing_end}'
        ) from err
    except ClipExportError as err:
        raise click.ClickException(f'Failed to export clip: {err}') from err

    click.echo(f'Exported clip to {output}')
