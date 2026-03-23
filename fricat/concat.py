import shlex
import itertools
import subprocess
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time
from time import perf_counter

import click
from fricat.utils import format_size
from fricat.metrics import write_metrics_file
from fricat.sidecar import build_sidecar
from fricat.sidecar import fetch_segments
from fricat.sidecar import write_sidecar


def ffmpeg(src_files: list[Path], dst_file: Path) -> int:
    """Return the size of dst_file"""
    with NamedTemporaryFile('wt', encoding='utf-8') as list_file:
        for r in src_files:
            list_file.write(f"file '{r}'\n")
        list_file.flush()
        _input = shlex.quote(list_file.name)
        _output = shlex.quote(str(dst_file))
        _ffmpeg = f'''
            ffmpeg -hide_banner -loglevel warning
            -f concat -safe 0
            -i {_input}
            -c copy
            {_output}
        '''
        ffmpeg = shlex.split(_ffmpeg, comments=True)
        print(shlex.join(ffmpeg))
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(ffmpeg, check=True)
    return dst_file.stat().st_size


@click.command()
@click.argument('src_root', type=click.Path(path_type=Path))
@click.argument('dst_root', type=click.Path(path_type=Path))
@click.option(
    '--metrics-file',
    type=click.Path(path_type=Path),
    default=Path('/var/lib/node_exporter/fricat_concat.prom'),
    show_default=True,
    help='Write Prometheus textfile metrics to this path',
)
@click.option(
    '--db-path',
    type=click.Path(path_type=Path),
    default=Path('/var/lib/frigate/frigate.db'),
    show_default=True,
    help='Frigate sqlite db path for segment metadata',
)
@click.option(
    '--write-sidecar/--no-write-sidecar',
    'write_sidecar_flag',
    default=True,
    show_default=True,
    help='Write per-hour JSON sidecar with segment metadata',
)
def main(
    src_root: Path,
    dst_root: Path,
    metrics_file: Path,
    db_path: Path,
    write_sidecar_flag: bool,
) -> None:
    # /fastpool/frigate/recordings/2025-11-18/14/CAM2/56.31.mp4
    #                                         %H      %M %S
    # /media/public/NVR/2025-11-18/14_CAM2.mp4

    def group_key(p: Path) -> tuple[str, str, str]:
        date_str, hour_str, cam_name, _ = p.parts[-4:]
        return (date_str, hour_str, cam_name)

    started_at = perf_counter()
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    sidecar_enabled = write_sidecar_flag
    if sidecar_enabled and not db_path.exists():
        click.echo(f'Sidecar disabled, db not found at {db_path}')
        sidecar_enabled = False

    recordings = sorted(src_root.rglob('*.mp4'))
    total_inputs = 0
    total_size = 0
    for key, group in itertools.groupby(recordings, key=group_key):
        date_str, hour_str, cam_name = key
        grouped_recordings = list(group)

        # Only process finished files
        # If it's 10:00 UTC now, only process up to dir 08, which contains
        # files from 08:00 UTC to 08:59 UTC. Should be safe...
        dir_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
        if dir_dt >= two_hours_ago:
            continue

        dst_dir = dst_root / date_str
        dst_file = dst_dir / f'{hour_str}_{cam_name}.mkv'
        sidecar_path = dst_file.with_suffix('.json')
        if dst_file.exists():
            if sidecar_enabled and not sidecar_path.exists():
                start_utc = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
                start_ts = start_utc.timestamp()
                end_ts = start_ts + 3600
                segments = fetch_segments(
                    db_path=db_path,
                    camera=cam_name,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
                sidecar = build_sidecar(
                    camera=cam_name,
                    start_utc=start_utc,
                    segments=segments,
                    db_path=db_path,
                )
                write_sidecar(sidecar_path, sidecar)
            continue

        total_inputs += len(grouped_recordings)
        total_size += ffmpeg(grouped_recordings, dst_file)
        if sidecar_enabled:
            start_utc = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
            start_ts = start_utc.timestamp()
            end_ts = start_ts + 3600
            segments = fetch_segments(
                db_path=db_path,
                camera=cam_name,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            sidecar = build_sidecar(
                camera=cam_name,
                start_utc=start_utc,
                segments=segments,
                db_path=db_path,
            )
            write_sidecar(sidecar_path, sidecar)

    print(f'Total size: {format_size(total_size)}')
    duration = perf_counter() - started_at
    timestamp = time()

    write_metrics_file(
        metrics_file,
        metrics={
            'fricat_concat_processed_bytes': total_size,
            'fricat_concat_processed_files': total_inputs,
            'fricat_concat_duration_seconds': duration,
            'fricat_concat_last_run_timestamp_seconds': timestamp,
        },
    )
