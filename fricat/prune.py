"""
Prune date-named backup directories with a simple GFS (grandfather-father-son)
retention policy inspired by borg-prune(1).

Supported retention options
---------------------------
    --keep-daily N
    --keep-weekly N
    --keep-monthly N
    --keep-yearly N

The rules are processed in that order; once a directory is retained by an earlier
rule it is ignored by the later ones.
"""

import re
import shutil
import datetime
from pathlib import Path
from collections import OrderedDict

import click

# Period formats used to group dates
PRUNING_PATTERNS: OrderedDict[str, str] = OrderedDict(
    [
        ('daily', '%Y-%m-%d'),
        ('weekly', '%G-%V'),  # ISO week number
        ('monthly', '%Y-%m'),
        ('yearly', '%Y'),
    ]
)

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')  # YYYY-MM-DD

DirEntry = tuple[datetime.date, Path, str]  # (date, path object, name)


def scan_directories(base: Path) -> list[DirEntry]:
    """Return a descending list of dated sub-directories inside base."""
    items: list[DirEntry] = []
    for entry in base.iterdir():
        if entry.is_dir():
            try:
                ts = datetime.date.fromisoformat(entry.name)
            except ValueError:
                continue
            items.append((ts, entry, entry.name))
    items.sort(reverse=True)
    return items


def prune_split(
    items: list[DirEntry],
    rule: str,
    n: int,
    kept: set[str],
    kept_because: dict[str, tuple[str, int]],
) -> None:
    """Populate *kept* with directory names chosen by *rule*."""
    if n <= 0:
        return
    pattern = PRUNING_PATTERNS[rule]
    last_period = None
    counter = 0
    for ts, _, name in items:
        period = ts.strftime(pattern)
        if period != last_period:
            last_period = period
            if name not in kept:
                counter += 1
                kept.add(name)
                kept_because[name] = (rule, counter)
                if counter == n:
                    break


def choose_kept(items: list[DirEntry], counts: dict[str, int]) -> tuple[set[str], dict[str, tuple[str, int]]]:
    """Return (kept_names, reason_dict)."""
    kept: set[str] = set()
    because: dict[str, tuple[str, int]] = {}
    for rule in PRUNING_PATTERNS:
        prune_split(items, rule, counts.get(rule, 0), kept, because)
    return kept, because


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


@click.command()
@click.argument('base', type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option('-d', '--keep-daily', 'daily', type=int, default=0, help='Number of daily backups to keep')
@click.option('-w', '--keep-weekly', 'weekly', type=int, default=0, help='Number of weekly backups to keep')
@click.option('-m', '--keep-monthly', 'monthly', type=int, default=0, help='Number of monthly backups to keep')
@click.option('-y', '--keep-yearly', 'yearly', type=int, default=0, help='Number of yearly backups to keep')
@click.option('-n', '--dry-run', is_flag=True, help='Show what would be removed without deleting')
def main(base: Path, daily: int, weekly: int, monthly: int, yearly: int, dry_run: bool) -> None:
    """Prune dated directories (YYYY-MM-DD) with GFS retention."""
    if not any((daily, weekly, monthly, yearly)):
        raise click.UsageError('At least one of --keep-* rules should be provided')

    dirs = scan_directories(base)
    kept, because = choose_kept(
        dirs,
        {'daily': daily, 'weekly': weekly, 'monthly': monthly, 'yearly': yearly},
    )
    total_bytes = 0

    prefix = '(DRYRUN) ' if dry_run else ''
    for _, path, name in dirs:
        if name in kept:
            rule, num = because[name]
            click.echo(f'{prefix}KEEP   {name}   (rule: {rule} #{num})')
        else:
            size = dir_size(path)
            total_bytes += size
            size_h = format_size(size)
            click.echo(f'{prefix}PRUNE  {name}   ({size_h})')
            if not dry_run:
                shutil.rmtree(path)

    click.echo(f'\n{prefix}Removed {len(dirs) - len(kept)} directories totalling {format_size(total_bytes)}.')
