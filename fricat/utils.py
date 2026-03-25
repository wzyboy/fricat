from datetime import datetime
from pathlib import Path


def dir_size(path: Path) -> int:
    """Recursively calculate *path* size in bytes."""
    return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())


def format_size(bytes_: int) -> str:
    """Human-readable binary size (GiB, MiB, …)."""
    units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
    size = float(bytes_)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f'{size:,.1f} {unit}'
        size /= 1024
    return f'{size:,.1f} B'


def parse_recording_path(root: Path, path: Path) -> tuple[str, str, str] | None:
    """Parse archive paths like YYYY-MM-DD/HH_CAMERA.mkv.

    Example:
        root=/archive, path=/archive/2026-03-29/11_CAM1.mkv
        -> ('2026-03-29', '11', 'CAM1')

    Also supports day roots:
        root=/archive/2026-03-29, path=/archive/2026-03-29/11_CAM1.mkv
        -> ('2026-03-29', '11', 'CAM1')
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    if len(rel.parts) == 2:
        date_str = rel.parts[0]
        file_name = rel.parts[1]
    elif len(rel.parts) == 1:
        date_str = root.name
        file_name = rel.parts[0]
    else:
        return None
    try:
        datetime.fromisoformat(f'{date_str} 00:00:00')
    except ValueError:
        return None
    if not file_name.endswith('.mkv'):
        return None
    base = file_name[:-4]
    if '_' not in base:
        return None
    hour_str, camera = base.split('_', 1)
    if len(hour_str) != 2 or not hour_str.isdigit():
        return None
    return date_str, hour_str, camera
