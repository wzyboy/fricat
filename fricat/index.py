from datetime import datetime
from pathlib import Path

import click
from tqdm import tqdm

from fricat.sidecar import build_sidecar
from fricat.sidecar import enrich_segments_with_audio
from fricat.sidecar import fetch_segments
from fricat.sidecar import write_sidecar
from fricat.utils import parse_recording_path


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
def main(
    archive_root: Path,
    db_path: Path,
    overwrite: bool,
) -> None:
    """Generate sidecar JSON files for existing archive recordings."""
    if not db_path.exists():
        raise click.ClickException(f'Database not found at {db_path}')

    archive_root = archive_root.resolve()
    recordings = sorted(archive_root.rglob('*.mkv'))
    if not recordings:
        click.echo('No recordings found.')
        return

    written = 0
    skipped = 0
    for recording in tqdm(recordings):
        parsed = parse_recording_path(archive_root, recording)
        if not parsed:
            continue
        date_str, hour_str, camera = parsed
        sidecar_path = recording.with_suffix('.json')
        if sidecar_path.exists() and not overwrite:
            skipped += 1
            continue
        start_utc = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
        start_ts = start_utc.timestamp()
        end_ts = start_ts + 3600
        segments = fetch_segments(
            db_path=db_path,
            camera=camera,
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if not segments:
            skipped += 1
            continue
        segments = enrich_segments_with_audio(segments, recording)
        sidecar = build_sidecar(
            camera=camera,
            start_utc=start_utc,
            segments=segments,
        )
        write_sidecar(sidecar_path, sidecar)
        written += 1

    click.echo(f'Wrote {written} sidecars, skipped {skipped}.')
