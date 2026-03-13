from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


@dataclass(frozen=True)
class Recording:
    camera: str
    start_utc: datetime
    path: Path
    meta_path: Path | None


RECORDING_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})/(\d{2})_(.+)\.mkv$')


def get_archive_root() -> Path:
    root = os.environ.get('FRICAT_ARCHIVE_ROOT')
    if not root:
        raise RuntimeError('FRICAT_ARCHIVE_ROOT is not set')
    return Path(root).resolve()


def scan_recordings(root: Path) -> list[Recording]:
    recordings: list[Recording] = []
    for path in root.rglob('*.mkv'):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        match = RECORDING_RE.match(rel)
        if not match:
            continue
        date_str, hour_str, camera = match.groups()
        try:
            start_utc = datetime.fromisoformat(f'{date_str} {hour_str}:00:00Z')
        except ValueError:
            continue
        meta_path = path.with_suffix('.json')
        recordings.append(
            Recording(
                camera=camera,
                start_utc=start_utc,
                path=path,
                meta_path=meta_path if meta_path.exists() else None,
            )
        )
    recordings.sort(key=lambda rec: (rec.start_utc, rec.camera))
    return recordings


def serialize_recording(rec: Recording) -> dict[str, str | bool]:
    rel = rec.path.relative_to(get_archive_root()).as_posix()
    return {
        'camera': rec.camera,
        'start_utc': rec.start_utc.replace(tzinfo=UTC).isoformat(),
        'path': rel,
        'has_meta': rec.meta_path is not None,
    }


app = FastAPI(title='fricat archive')

static_dir = Path(__file__).resolve().parent / 'static'
app.mount('/static', StaticFiles(directory=static_dir), name='static')


@app.get('/')
async def index() -> FileResponse:
    index_path = static_dir / 'index.html'
    return FileResponse(index_path)


@app.get('/api/cameras')
async def cameras() -> JSONResponse:
    root = get_archive_root()
    recordings = scan_recordings(root)
    cameras = sorted({rec.camera for rec in recordings})
    return JSONResponse(content=cameras)


@app.get('/api/recordings')
async def recordings(start: float, end: float, camera: str | None = None) -> JSONResponse:
    if start >= end:
        raise HTTPException(status_code=400, detail='start must be less than end')
    root = get_archive_root()
    recordings = scan_recordings(root)
    start_dt = datetime.fromtimestamp(start, tz=UTC)
    end_dt = datetime.fromtimestamp(end, tz=UTC)

    filtered: list[dict[str, str | bool]] = []
    for rec in recordings:
        if camera and rec.camera != camera:
            continue
        rec_end = rec.start_utc + timedelta(hours=1)
        if rec_end <= start_dt or rec.start_utc >= end_dt:
            continue
        filtered.append(serialize_recording(rec))

    return JSONResponse(content=filtered)


@app.get('/media/{path:path}')
async def media(path: str) -> FileResponse:
    root = get_archive_root()
    file_path = (root / path).resolve()
    try:
        file_path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail='File not found')
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail='File not found')
    return FileResponse(file_path)
