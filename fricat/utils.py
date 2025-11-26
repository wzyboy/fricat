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
