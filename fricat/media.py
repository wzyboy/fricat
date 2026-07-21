import os
import json
import math
import shlex
import subprocess
from pathlib import Path
from dataclasses import dataclass

MAX_ARCHIVE_DURATION_SECONDS = 3700.0
MAX_AUDIO_GAP_SECONDS = 1.0


@dataclass(frozen=True)
class AudioHealth:
    healthy: bool
    packet_count: int
    reason: str | None = None


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


def probe_audio_health(
    path: Path,
    max_gap: float = MAX_AUDIO_GAP_SECONDS,
) -> AudioHealth:
    command = [
        'ffprobe',
        '-v',
        'error',
        '-select_streams',
        'a:0',
        '-show_streams',
        '-show_packets',
        '-show_entries',
        'stream=index:packet=pts_time,duration_time',
        '-of',
        'json',
        str(path),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as err:
        raise ValueError(f'Invalid ffprobe output for {path}') from err

    if not data.get('streams'):
        return AudioHealth(False, 0, 'audio stream is missing')

    packets = data.get('packets')
    if not isinstance(packets, list):
        raise ValueError(f'Invalid packet data reported for {path}')
    if len(packets) < 2:
        return AudioHealth(False, len(packets), f'only {len(packets)} audio packet(s)')

    previous_pts: float | None = None
    for index, packet in enumerate(packets):
        try:
            pts = float(packet['pts_time'])
            duration = float(packet['duration_time'])
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f'Invalid audio packet {index} reported for {path}') from err
        if not math.isfinite(pts) or not math.isfinite(duration):
            return AudioHealth(False, len(packets), f'audio packet {index} has non-finite timing')
        if duration <= 0 or duration > max_gap:
            return AudioHealth(
                False,
                len(packets),
                f'audio packet {index} duration is {duration:.3f}s',
            )
        if previous_pts is not None:
            gap = pts - previous_pts
            if gap < 0 or gap > max_gap:
                return AudioHealth(
                    False,
                    len(packets),
                    f'audio PTS gap is {gap:.3f}s at packet {index}',
                )
        previous_pts = pts

    return AudioHealth(True, len(packets))


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
