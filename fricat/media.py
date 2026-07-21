import os
import math
import shlex
import subprocess
from pathlib import Path

MAX_ARCHIVE_DURATION_SECONDS = 3700.0


def probe_duration(path: Path) -> float:
    command = [
        'ffprobe',
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        str(path),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as err:
        raise ValueError(f'Invalid duration reported for {path}: {result.stdout.strip()!r}') from err
    return duration


def duration_is_valid(duration: float, max_duration: float = MAX_ARCHIVE_DURATION_SECONDS) -> bool:
    return math.isfinite(duration) and 0 < duration <= max_duration


def remux(source: Path, destination: Path) -> None:
    command = [
        'ffmpeg',
        '-nostdin',
        '-hide_banner',
        '-loglevel',
        'warning',
        '-i',
        str(source),
        '-map',
        '0',
        '-c',
        'copy',
        str(destination),
    ]
    print(shlex.join(command))
    subprocess.run(command, check=True)


def copy_file_ownership_and_mode(source: Path, destination: Path) -> None:
    source_stat = source.stat()
    destination_stat = destination.stat()
    if (destination_stat.st_uid, destination_stat.st_gid) != (source_stat.st_uid, source_stat.st_gid):
        os.chown(destination, source_stat.st_uid, source_stat.st_gid)
    os.chmod(destination, source_stat.st_mode)
