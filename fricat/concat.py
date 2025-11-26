import shlex
import itertools
import subprocess
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from fricat.utils import format_size


def ffmpeg_concat(src_files: list[Path], dst_file: Path) -> int:
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


def frigate(src_root: Path, dst_root: Path) -> None:
    # /media/frigate/recordings/2025-11-18/14/CAM2/56.31.mp4
    #                                      %H      %M %S
    # /media/frigate/archive/2025-11-18/14_CAM2.mp4

    def group_key(p: Path) -> tuple[str, str, str]:
        p = p.resolve().absolute()
        date_str, hour_str, cam_name, _ = p.parts[-4:]
        return (date_str, hour_str, cam_name)

    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)

    recordings = sorted(src_root.rglob('*.mp4'))
    total_size = 0
    for key, recordings in itertools.groupby(recordings, key=group_key):
        date_str, hour_str, cam_name = key
        recordings = list(recordings)

        # Only process finished files
        # If it's 10:00 UTC now, only process up to dir 08, which contains
        # files from 08:00 UTC to 08:59 UTC. Should be safe...
        dir_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
        if dir_dt >= two_hours_ago:
            continue

        dst_dir = dst_root / date_str
        dst_file = dst_dir / f'{hour_str}_{cam_name}.mkv'
        if dst_file.exists():
            continue
        total_size += ffmpeg_concat(recordings, dst_file)

    print(f'Total size: {format_size(total_size)}')


def rtsp_record(src_root: Path, dst_root: Path) -> None:
    # /media/public/NVR/2024-11-30/CAM1_2024-11-30_00-00-01.mkv
    # /media/public/NVR/2024-11-30/00_CAM1.mkv

    def group_key(p: Path) -> tuple[str, str, str]:
        cam_name, date_str, time_str = p.stem.split('_')
        hour_str = time_str.split('-')[0]
        return (date_str, hour_str, cam_name)

    recordings = sorted(src_root.rglob('*.mkv'))
    for key, recordings in itertools.groupby(recordings, key=group_key):
        date_str, hour_str, cam_name = key
        recordings = list(recordings)

        dst_dir = dst_root / date_str
        dst_file = dst_dir / f'{hour_str}_{cam_name}.mkv'
        if dst_file.exists():
            continue
        ffmpeg_concat(recordings, dst_file)
        for r in recordings:
            r.unlink()
