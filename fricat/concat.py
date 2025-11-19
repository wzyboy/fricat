import shlex
import itertools
import subprocess
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile


def concat_to_mkv(src_root: Path, dst_root: Path) -> None:
    # /media/frigate/recordings/2025-11-18/14/CAM2/56.31.mp4
    #                                      %H      %M %S
    # /media/frigate/archive/2025-11-18/14_CAM2.mp4

    def group_key(p: Path) -> tuple[str, str, str]:
        p = p.resolve().absolute()
        date_str, hour_str, cam_name, _ = p.parts[-4:]
        return (date_str, hour_str, cam_name)

    recordings = sorted(src_root.rglob('*.mp4'))
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
        out_file = dst_dir / f'{hour_str}_{cam_name}.mkv'
        if out_file.exists():
            continue

        with NamedTemporaryFile('wt', encoding='utf-8') as list_file:
            for r in recordings:
                list_file.write(f"file '{r}'\n")
            list_file.flush()
            cmd = [
                'ffmpeg',
                '-hide_banner', '-loglevel', 'warning',
                '-f', 'concat',
                '-safe', '0',
                '-i', list_file.name,
                '-c', 'copy',
                str(out_file),
            ]
            print(shlex.join(cmd))
            dst_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(cmd, check=True)
