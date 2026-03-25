import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime
from pathlib import Path

import click
from tqdm import tqdm

from fricat.sidecar import generate_sidecar
from fricat.sidecar import write_sidecar
from fricat.utils import parse_recording_path


def _default_jobs() -> int:
    return max(1, os.cpu_count() or 1)


def _backfill_recording(
    archive_root: Path,
    db_path: Path,
    recording: Path,
) -> tuple[str, Path | None, dict[str, object] | None]:
    parsed = parse_recording_path(archive_root, recording)
    if not parsed:
        return 'ignored', None, None

    date_str, hour_str, camera = parsed
    start_utc = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
    sidecar = generate_sidecar(
        db_path=db_path,
        recording_path=recording,
        camera=camera,
        start_utc=start_utc,
    )
    if sidecar is None:
        return 'skipped', None, None

    return 'written', recording.with_suffix('.json'), sidecar


@click.command()
@click.argument('archive_root', type=click.Path(path_type=Path))
@click.option(
    '--db-path',
    type=click.Path(path_type=Path),
    default=Path('/var/lib/frigate/frigate.db'),
    show_default=True,
    help='Frigate sqlite db path for segment metadata',
)
@click.option(
    '--overwrite',
    is_flag=True,
    help='Overwrite existing sidecar files',
)
@click.option(
    '--jobs',
    type=click.IntRange(min=1),
    default=_default_jobs,
    show_default='CPU count',
    help='Number of recordings to process in parallel',
)
def main(
    archive_root: Path,
    db_path: Path,
    overwrite: bool,
    jobs: int,
) -> None:
    """Backfill sidecar JSON files for existing archive recordings."""
    if not db_path.exists():
        raise click.ClickException(f'Database not found at {db_path}')

    archive_root = archive_root.resolve()
    recordings = sorted(archive_root.rglob('*.mkv'))
    if not recordings:
        click.echo('No recordings found.')
        return

    eligible_recordings: list[Path] = []
    skipped = 0
    for recording in recordings:
        sidecar_path = recording.with_suffix('.json')
        if sidecar_path.exists() and not overwrite:
            skipped += 1
            continue
        eligible_recordings.append(recording)

    written = 0
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = [
            executor.submit(_backfill_recording, archive_root, db_path, recording)
            for recording in eligible_recordings
        ]
        for future in tqdm(as_completed(futures), total=len(futures)):
            status, sidecar_path, sidecar = future.result()
            if status == 'written' and sidecar_path is not None and sidecar is not None:
                write_sidecar(sidecar_path, sidecar)
                written += 1
                continue
            if status == 'skipped':
                skipped += 1

    click.echo(f'Wrote {written} sidecars, skipped {skipped}.')
