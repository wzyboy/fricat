import math
import time
import subprocess
from pathlib import Path
from datetime import datetime

import click

from fricat.media import MAX_AUDIO_GAP_SECONDS
from fricat.media import probe_audio_health


def _archive_hour(path: Path) -> tuple[str, int] | None:
    try:
        datetime.fromisoformat(path.parent.name)
        hour = int(path.name)
    except ValueError:
        return None
    if not 0 <= hour <= 23:
        return None
    return path.parent.name, hour


def find_latest_hour_directories(recordings_root: Path, count: int = 2) -> list[Path]:
    hour_directories: list[Path] = []
    for day_directory in recordings_root.iterdir():
        if not day_directory.is_dir():
            continue
        for hour_directory in day_directory.iterdir():
            if hour_directory.is_dir() and _archive_hour(hour_directory) is not None:
                hour_directories.append(hour_directory)
    hour_directories.sort(key=lambda path: _archive_hour(path) or ('', -1))
    return hour_directories[-count:]


def discover_cameras(hour_directories: list[Path]) -> list[str]:
    return sorted(
        {
            directory.name
            for hour_directory in hour_directories
            for directory in hour_directory.iterdir()
            if directory.is_dir()
        }
    )


def recent_completed_segments(
    hour_directories: list[Path],
    camera: str,
    now: float,
    settle_seconds: float,
    samples: int,
) -> list[Path]:
    segments = [
        path
        for hour_directory in hour_directories
        for path in (hour_directory / camera).glob('*.mp4')
        if path.is_file() and path.stat().st_mtime <= now - settle_seconds
    ]
    segments.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return segments[:samples]


@click.command(name='check-segments')
@click.argument(
    'recordings_root',
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option('--camera', 'cameras', multiple=True, help='Expected camera name. May be repeated.')
@click.option(
    '--samples',
    type=click.IntRange(min=1),
    default=3,
    show_default=True,
    help='Recent completed segments to inspect per camera.',
)
@click.option(
    '--settle-seconds',
    type=click.FloatRange(min=0),
    default=15.0,
    show_default=True,
    help='Minimum file age before a segment is considered complete.',
)
@click.option(
    '--max-age',
    type=click.FloatRange(min=0, min_open=True),
    default=60.0,
    show_default=True,
    help='Maximum age of the newest completed segment.',
)
@click.option(
    '--max-audio-gap',
    type=click.FloatRange(min=0, min_open=True),
    default=MAX_AUDIO_GAP_SECONDS,
    show_default=True,
    help='Maximum audio packet duration or PTS gap.',
)
@click.pass_context
def main(
    context: click.Context,
    recordings_root: Path,
    cameras: tuple[str, ...],
    samples: int,
    settle_seconds: float,
    max_age: float,
    max_audio_gap: float,
) -> None:
    """Check recent Frigate segments for corrupt or stale audio."""
    recordings_root = recordings_root.resolve()
    try:
        hour_directories = find_latest_hour_directories(recordings_root)
    except OSError as err:
        click.echo(f'ERROR: unable to inspect recordings: {err}', err=True)
        context.exit(2)

    if not hour_directories:
        click.echo('ERROR: no recording hour directories found', err=True)
        context.exit(2)

    try:
        selected_cameras = list(dict.fromkeys(cameras)) if cameras else discover_cameras(hour_directories)
    except OSError as err:
        click.echo(f'ERROR: unable to discover cameras: {err}', err=True)
        context.exit(2)
    if not selected_cameras:
        click.echo('ERROR: no cameras found', err=True)
        context.exit(2)

    now = time.time()
    unhealthy = 0
    errors = 0
    for camera in selected_cameras:
        try:
            segments = recent_completed_segments(
                hour_directories,
                camera,
                now,
                settle_seconds,
                samples,
            )
        except OSError as err:
            errors += 1
            click.echo(f'ERROR     {camera}: unable to inspect segments: {err}')
            continue

        if not segments:
            unhealthy += 1
            click.echo(f'UNHEALTHY {camera}: no completed segments found')
            continue

        try:
            age = now - segments[0].stat().st_mtime
        except OSError as err:
            errors += 1
            click.echo(f'ERROR     {camera}: unable to inspect newest segment: {err}')
            continue
        if not math.isfinite(age) or age > max_age:
            unhealthy += 1
            click.echo(f'UNHEALTHY {camera}: newest completed segment is {age:.1f}s old')
            continue

        camera_reason: str | None = None
        for segment in segments:
            try:
                health = probe_audio_health(segment, max_audio_gap)
            except OSError as err:
                errors += 1
                camera_reason = f'checker failed: {err}'
                break
            except (subprocess.CalledProcessError, ValueError) as err:
                camera_reason = f'{segment.name}: unable to probe audio: {err}'
                break
            if not health.healthy:
                camera_reason = f'{segment.name}: {health.reason}'
                break

        if camera_reason is not None:
            if camera_reason.startswith('checker failed:'):
                click.echo(f'ERROR     {camera}: {camera_reason}')
            else:
                unhealthy += 1
                click.echo(f'UNHEALTHY {camera}: {camera_reason}')
            continue

        click.echo(f'HEALTHY   {camera}: {len(segments)} segment(s), latest {age:.1f}s old')

    click.echo(
        f'Checked {len(selected_cameras)} camera(s): '
        f'{len(selected_cameras) - unhealthy - errors} healthy, {unhealthy} unhealthy, {errors} errors.'
    )
    if errors:
        context.exit(2)
    if unhealthy:
        context.exit(1)
