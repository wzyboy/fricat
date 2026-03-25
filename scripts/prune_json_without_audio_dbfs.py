#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path

_AUDIO_DBFS_RE = re.compile(r'"audio_dbfs"\s*:')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Delete JSON files that do not contain the "audio_dbfs" field.'
    )
    parser.add_argument(
        'root',
        type=Path,
        help='Root directory to scan recursively for JSON files',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show which files would be deleted without deleting them',
    )
    return parser.parse_args()


def has_audio_dbfs(path: Path) -> bool:
    try:
        with path.open('r', encoding='utf-8') as file_obj:
            content = file_obj.read()
    except OSError as exc:
        print(f'SKIP   {path} ({exc})')
        return True

    return _AUDIO_DBFS_RE.search(content) is not None


def iter_json_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob('*.json') if path.is_file())


def main() -> int:
    args = parse_args()
    root = args.root

    if not root.exists():
        print(f'Root path does not exist: {root}')
        return 1

    if not root.is_dir():
        print(f'Root path is not a directory: {root}')
        return 1

    deleted = 0
    kept = 0

    for path in iter_json_files(root):
        if has_audio_dbfs(path):
            kept += 1
            print(f'KEEP   {path}')
            continue

        deleted += 1
        if args.dry_run:
            print(f'DELETE {path} (dry-run)')
            continue

        path.unlink()
        print(f'DELETE {path}')

    print(f'\nScanned: {deleted + kept}')
    print(f'Kept:    {kept}')
    print(f'Deleted: {deleted}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
