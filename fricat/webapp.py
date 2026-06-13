import os
import json
import sqlite3
import hashlib
import logging
from time import monotonic
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from fricat.utils import parse_recording_path

logger = logging.getLogger(__name__)

# Legacy files were using local time in filenames, while newer files are using
# UTC time in filenames.
LEGACY_FILENAME_CUTOFF = datetime(2025, 11, 18)
DEFAULT_ARCHIVE_TIMEZONE = 'America/Vancouver'
INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Recording:
    camera: str
    start_utc: datetime
    path: Path
    meta_path: Path | None


_SCAN_CACHE: dict[Path, tuple[float, list[Recording]]] = {}


def get_archive_root() -> Path:
    root = os.environ.get('FRICAT_ARCHIVE_ROOT')
    if not root:
        raise RuntimeError('FRICAT_ARCHIVE_ROOT is not set')
    return Path(root).resolve()


def get_archive_timezone_name() -> str:
    return os.environ.get('FRICAT_TIMEZONE', DEFAULT_ARCHIVE_TIMEZONE)


def get_archive_tz() -> ZoneInfo:
    return ZoneInfo(get_archive_timezone_name())


def get_scan_cache_ttl() -> float:
    raw_ttl = os.environ.get('FRICAT_SCAN_CACHE_TTL_SECONDS', '5')
    try:
        return max(0.0, float(raw_ttl))
    except ValueError:
        return 5.0


def get_recording_index_path(root: Path) -> Path:
    override = os.environ.get('FRICAT_WEB_INDEX_PATH')
    if override:
        return Path(override).expanduser()
    root_hash = hashlib.sha256(str(root).encode('utf-8')).hexdigest()[:16]
    return Path.home() / '.cache' / 'fricat' / f'{root_hash}.sqlite'


def _recording_start_utc(date_str: str, hour_str: str) -> datetime:
    filename_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00')
    if filename_dt < LEGACY_FILENAME_CUTOFF:
        return filename_dt.replace(tzinfo=get_archive_tz()).astimezone(UTC)
    return filename_dt.replace(tzinfo=UTC)


def _recording_from_index_row(root: Path, row: sqlite3.Row) -> Recording:
    meta_rel_path = row['meta_rel_path']
    return Recording(
        camera=row['camera'],
        start_utc=datetime.fromtimestamp(row['start_ts'], tz=UTC),
        path=root / row['rel_path'],
        meta_path=(root / meta_rel_path) if meta_rel_path else None,
    )


def scan_recordings(root: Path) -> list[Recording]:
    recordings: list[Recording] = []
    for path in root.rglob('*.mkv'):
        parsed = parse_recording_path(root, path)
        if not parsed:
            continue
        date_str, hour_str, camera = parsed
        try:
            start_utc = _recording_start_utc(date_str, hour_str)
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


def _create_recording_index_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f'''
        drop table if exists recordings;

        create table recordings (
            rel_path text primary key,
            camera text not null,
            start_ts real not null,
            date_str text not null,
            hour_str text not null,
            media_mtime_ns integer not null,
            media_size integer not null,
            meta_rel_path text,
            meta_mtime_ns integer,
            meta_size integer
        );

        create index recordings_start_idx on recordings(start_ts);
        create index recordings_camera_idx on recordings(camera);

        pragma user_version = {INDEX_SCHEMA_VERSION};
        '''
    )


def _ensure_recording_index_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute('pragma user_version').fetchone()[0]
    if version != INDEX_SCHEMA_VERSION:
        _create_recording_index_schema(conn)


def _connect_recording_index(root: Path) -> sqlite3.Connection | None:
    index_path = get_recording_index_path(root)
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        logger.warning('Failed to create recording index directory %s: %s', index_path.parent, err)
        return None

    conn = None
    try:
        conn = sqlite3.connect(index_path)
        conn.row_factory = sqlite3.Row
        _ensure_recording_index_schema(conn)
        return conn
    except sqlite3.DatabaseError as err:
        logger.warning('Failed to open recording index %s: %s', index_path, err)
        if conn is not None:
            conn.close()

    try:
        index_path.unlink(missing_ok=True)
        conn = sqlite3.connect(index_path)
        conn.row_factory = sqlite3.Row
        _ensure_recording_index_schema(conn)
        return conn
    except (OSError, sqlite3.DatabaseError) as err:
        logger.warning('Failed to recreate recording index %s: %s', index_path, err)
        return None


def _recording_index_rows(root: Path, recordings: list[Recording]) -> list[tuple[object, ...]]:
    rows = []
    for rec in recordings:
        try:
            parsed = parse_recording_path(root, rec.path)
            if not parsed:
                continue
            date_str, hour_str, _ = parsed
            media_stat = rec.path.stat()
            rel_path = rec.path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        meta_rel_path = None
        meta_mtime_ns = None
        meta_size = None
        if rec.meta_path:
            try:
                meta_stat = rec.meta_path.stat()
                meta_rel_path = rec.meta_path.relative_to(root).as_posix()
            except (OSError, ValueError):
                pass
            else:
                meta_mtime_ns = meta_stat.st_mtime_ns
                meta_size = meta_stat.st_size

        rows.append(
            (
                rel_path,
                rec.camera,
                rec.start_utc.timestamp(),
                date_str,
                hour_str,
                media_stat.st_mtime_ns,
                media_stat.st_size,
                meta_rel_path,
                meta_mtime_ns,
                meta_size,
            )
        )
    return rows


def _replace_recording_index(conn: sqlite3.Connection, root: Path, recordings: list[Recording]) -> None:
    rows = _recording_index_rows(root, recordings)
    with conn:
        conn.execute('delete from recordings')
        conn.executemany(
            '''
            insert into recordings (
                rel_path, camera, start_ts, date_str, hour_str, media_mtime_ns,
                media_size, meta_rel_path, meta_mtime_ns, meta_size
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            rows,
        )


def _load_indexed_recordings(conn: sqlite3.Connection, root: Path) -> list[Recording]:
    rows = conn.execute(
        '''
        select rel_path, camera, start_ts, meta_rel_path
        from recordings
        order by start_ts, camera
        '''
    ).fetchall()
    return [_recording_from_index_row(root, row) for row in rows]


def load_recordings(root: Path) -> list[Recording]:
    conn = _connect_recording_index(root)
    if conn is None:
        return scan_recordings(root)

    try:
        with conn:
            recordings = _load_indexed_recordings(conn, root)
            if recordings:
                return recordings

            recordings = scan_recordings(root)
            _replace_recording_index(conn, root, recordings)
            return recordings
    finally:
        conn.close()


def clear_scan_cache() -> None:
    _SCAN_CACHE.clear()


def get_cached_recordings(root: Path) -> list[Recording]:
    ttl = get_scan_cache_ttl()
    if ttl == 0:
        return scan_recordings(root)

    now = monotonic()
    cached = _SCAN_CACHE.get(root)
    if cached:
        cached_at, recordings = cached
        if now - cached_at < ttl:
            return recordings

    recordings = load_recordings(root)
    _SCAN_CACHE[root] = (now, recordings)
    return recordings


def _coerce_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be numeric')
    try:
        return float(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f'{field_name} must be numeric') from err


def get_activity_profile(meta_path: Path | None) -> dict[str, list[float]] | None:
    if not meta_path or not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as err:
        logger.warning('Failed to read sidecar profile from %s: %s', meta_path, err)
        return None

    try:
        if not isinstance(data, dict):
            raise ValueError('sidecar root must be an object')
        segments = data.get('segments', [])
        if not isinstance(segments, list):
            raise ValueError('segments must be a list')
        if not segments:
            return None

        # Downsample 3600 seconds into 24 bins (150 seconds per bin)
        bins_count = 24
        bin_size = 150.0
        motion_bins = [0.0] * bins_count
        sound_bins = [0.0] * bins_count
        motion_counts = [0] * bins_count
        sound_counts = [0] * bins_count

        for seg in segments:
            if not isinstance(seg, dict):
                raise ValueError('segment must be an object')
            offset = _coerce_float(seg.get('offset', 0.0), 'offset')
            bin_idx = min(int(offset / bin_size), bins_count - 1)

            motion = _coerce_float(seg.get('motion', 0.0), 'motion')
            audio = seg.get('audio_dbfs')
            if audio is None:
                audio = -80.0
            else:
                audio = _coerce_float(audio, 'audio_dbfs')

            motion_bins[bin_idx] += motion
            motion_counts[bin_idx] += 1

            if audio > -80.0:
                sound_bins[bin_idx] += audio
                sound_counts[bin_idx] += 1

        # Calculate averages
        for i in range(bins_count):
            if motion_counts[i] > 0:
                motion_bins[i] = motion_bins[i] / motion_counts[i]
            if sound_counts[i] > 0:
                sound_bins[i] = sound_bins[i] / sound_counts[i]
            else:
                sound_bins[i] = -80.0

        # Normalize sound to 0-100 range for easy drawing
        sound_normalized = [max(0.0, (val + 80.0) / 80.0) * 100.0 for val in sound_bins]

        return {'motion': [round(v, 1) for v in motion_bins], 'sound': [round(v, 1) for v in sound_normalized]}
    except ValueError as err:
        logger.warning('Invalid sidecar profile in %s: %s', meta_path, err)
        return None


def serialize_recording(rec: Recording) -> dict[str, str | bool | dict[str, list[float]] | None]:
    rel = rec.path.relative_to(get_archive_root()).as_posix()
    profile = get_activity_profile(rec.meta_path)
    return {
        'camera': rec.camera,
        'start_utc': rec.start_utc.replace(tzinfo=UTC).isoformat(),
        'path': rel,
        'has_meta': rec.meta_path is not None,
        'profile': profile,
    }


app = FastAPI(title='fricat archive')

static_dir = Path(__file__).resolve().parent / 'static'
app.mount('/static', StaticFiles(directory=static_dir), name='static')


@app.get('/')
async def index() -> FileResponse:
    index_path = static_dir / 'index.html'
    return FileResponse(index_path)


@app.get('/api/config')
async def config() -> JSONResponse:
    get_archive_tz()
    return JSONResponse(content={'timezone': get_archive_timezone_name()})


@app.get('/api/cameras')
async def cameras() -> JSONResponse:
    root = get_archive_root()
    recordings = get_cached_recordings(root)
    cameras = sorted({rec.camera for rec in recordings})
    return JSONResponse(content=cameras)


@app.get('/api/recorded_dates')
async def recorded_dates(camera: str | None = None) -> JSONResponse:
    root = get_archive_root()
    recordings = get_cached_recordings(root)
    archive_tz = get_archive_tz()
    dates = set()
    for rec in recordings:
        if camera and rec.camera != camera:
            continue
        local_dt = rec.start_utc.replace(tzinfo=UTC).astimezone(archive_tz)
        dates.add(local_dt.strftime('%Y-%m-%d'))
    return JSONResponse(content=sorted(list(dates)))


@app.get('/api/recordings')
async def recordings(start: float, end: float, camera: str | None = None) -> JSONResponse:
    if start >= end:
        raise HTTPException(status_code=400, detail='start must be less than end')
    root = get_archive_root()
    recordings = get_cached_recordings(root)
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


@app.get('/api/meta')
async def meta(path: str) -> JSONResponse:
    root = get_archive_root()
    file_path = (root / path).resolve()
    try:
        file_path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404, detail='File not found')
    sidecar_path = file_path.with_suffix('.json')
    if not sidecar_path.is_file():
        raise HTTPException(status_code=404, detail='Sidecar not found')
    try:
        data = json.loads(sidecar_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail='Sidecar invalid JSON')
    return JSONResponse(content=data)
