import shlex
import itertools
import subprocess
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile


def ffmpeg_concat(src_files: list[Path], dst_file: Path) -> None:
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


def frigate(src_root: Path, dst_root: Path) -> None:
    # /media/frigate/recordings/2025-11-18/14/CAM2/56.31.mp4
    #                                      %H      %M %S
    # /media/frigate/archive/2025-11-18/14_CAM2.mp4

    def group_key(p: Path) -> tuple[str, str, str]:
        p = p.resolve().absolute()
        date_str, hour_str, cam_name, _ = p.parts[-4:]
        return (date_str, hour_str, cam_name)

    def old_file(p: Path) -> bool:
        mtime_threshold = datetime.now() - timedelta(hours=24)
        return p.stat().st_mtime < mtime_threshold.timestamp()

    recordings = sorted(src_root.rglob('*.mp4'))
    recordings = filter(old_file, recordings)
    for key, recordings in itertools.groupby(recordings, key=group_key):
        date_str, hour_str, cam_name = key
        recordings = list(recordings)

        # Validate path
        for r in recordings:
            try:
                min_str, sec_str = r.stem.split('.')
                dt_str = f'{date_str} {hour_str}:{min_str}:{sec_str}'
                datetime.fromisoformat(dt_str)
            except ValueError:
                raise AssertionError(f'Invalid recording: {r}') from None

        dst_dir = dst_root / date_str
        dst_file = dst_dir / f'{hour_str}_{cam_name}.mkv'
        if dst_file.exists():
            continue
        ffmpeg_concat(recordings, dst_file)


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
