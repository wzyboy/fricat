import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import click

from fricat.media import MAX_ARCHIVE_DURATION_SECONDS
from fricat.media import remux
from fricat.media import probe_duration
from fricat.media import duration_is_valid
from fricat.media import copy_file_ownership_and_mode
from fricat.utils import parse_recording_path


def repair_recording(recording: Path, max_duration: float) -> None:
    with TemporaryDirectory(prefix=f'.{recording.stem}.repair.', dir=recording.parent) as temp_dir_name:
        repaired = Path(temp_dir_name) / recording.name
        remux(recording, repaired)
        duration = probe_duration(repaired)
        if not duration_is_valid(duration, max_duration):
            raise ValueError(
                f'Remuxed duration is invalid: {duration:.3f}s (maximum {max_duration:.3f}s)'
            )
        copy_file_ownership_and_mode(recording, repaired)
        os.replace(repaired, recording)


@click.command()
@click.argument(
    'archive_root',
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    '--apply',
    is_flag=True,
    help='Repair malformed recordings. Without this option, only scan and report.',
)
@click.option(
    '--max-duration',
    type=click.FloatRange(min=0, min_open=True),
    default=MAX_ARCHIVE_DURATION_SECONDS,
    show_default=True,
    help='Maximum valid duration for an hourly archive, in seconds.',
)
def main(archive_root: Path, apply: bool, max_duration: float) -> None:
    """Scan and optionally repair malformed hourly MKV archives."""
    archive_root = archive_root.resolve()
    recordings = [
        path
        for path in sorted(archive_root.rglob('*.mkv'))
        if parse_recording_path(archive_root, path) is not None
    ]
    if not recordings:
        click.echo('No recordings found.')
        return

    valid = 0
    malformed = 0
    repaired = 0
    failed = 0
    for recording in recordings:
        relative_path = recording.relative_to(archive_root)
        try:
            duration = probe_duration(recording)
        except (OSError, subprocess.CalledProcessError, ValueError) as err:
            failed += 1
            click.echo(f'ERROR     {relative_path}: {err}', err=True)
            continue

        if duration_is_valid(duration, max_duration):
            valid += 1
            continue

        malformed += 1
        click.echo(f'MALFORMED {relative_path}: {duration:.3f}s')
        if not apply:
            continue

        try:
            repair_recording(recording, max_duration)
        except (OSError, subprocess.CalledProcessError, ValueError) as err:
            failed += 1
            click.echo(f'FAILED    {relative_path}: {err}', err=True)
            continue
        repaired += 1
        click.echo(f'REPAIRED  {relative_path}')

    click.echo(
        f'Scanned {len(recordings)} recordings: {valid} valid, {malformed} malformed, '
        f'{repaired} repaired, {failed} failed.'
    )
    if failed:
        raise click.ClickException(f'{failed} recording(s) could not be processed')
