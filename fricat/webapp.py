import os
import json
import hashlib
import logging
import sqlite3
from time import time
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
from threading import Lock
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from fricat.utils import parse_recording_path

logger = logging.getLogger(__name__)

# Legacy files were using local time in filenames, while newer files are using
# UTC time in filenames.
LEGACY_FILENAME_CUTOFF = datetime(2025, 11, 18)
DEFAULT_ARCHIVE_TIMEZONE = 'America/Vancouver'
INDEX_SCHEMA_VERSION = 4
RECENT_ARCHIVE_DAYS = 2
CAMERA_NAMES: list[str] = ['CAM1', 'CAM2', 'CAM3', 'CAM4']


@dataclass(frozen=True)
class Recording:
    camera: str
    start_utc: datetime
    path: Path
    meta_path: Path | None
    profile: dict[str, list[float]] | None = None
    profile_loaded: bool = False


_REFRESH_LOCKS: dict[Path, Lock] = {}
_REFRESH_LOCKS_LOCK = Lock()


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


def _refresh_lock(root: Path) -> Lock:
    with _REFRESH_LOCKS_LOCK:
        lock = _REFRESH_LOCKS.get(root)
        if lock is None:
            lock = Lock()
            _REFRESH_LOCKS[root] = lock
        return lock


def _recording_start_utc(date_str: str, hour_str: str) -> datetime:
    filename_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00')
    if filename_dt < LEGACY_FILENAME_CUTOFF:
        return filename_dt.replace(tzinfo=get_archive_tz()).astimezone(UTC)
    return filename_dt.replace(tzinfo=UTC)


def _profile_from_json(raw_profile: str | None) -> dict[str, list[float]] | None:
    if not raw_profile:
        return None
    try:
        profile = json.loads(raw_profile)
    except json.JSONDecodeError:
        return None
    if not isinstance(profile, dict):
        return None
    motion = profile.get('motion')
    sound = profile.get('sound')
    if not isinstance(motion, list) or not isinstance(sound, list):
        return None
    try:
        return {'motion': [float(v) for v in motion], 'sound': [float(v) for v in sound]}
    except (TypeError, ValueError):
        return None


def _recording_from_index_row(root: Path, row: sqlite3.Row, *, include_profile: bool = False) -> Recording:
    meta_rel_path = row['meta_rel_path']
    profile = _profile_from_json(row['profile_json']) if include_profile else None
    return Recording(
        camera=row['camera'],
        start_utc=datetime.fromtimestamp(row['start_ts'], tz=UTC),
        path=root / row['rel_path'],
        meta_path=(root / meta_rel_path) if meta_rel_path else None,
        profile=profile,
        profile_loaded=include_profile and bool(row['profile_loaded']),
    )


def _archive_date_str(path: Path) -> str | None:
    try:
        datetime.fromisoformat(f'{path.name} 00:00:00')
    except ValueError:
        return None
    return path.name


def _iter_archive_day_dirs(root: Path) -> list[Path]:
    if _archive_date_str(root):
        return [root]

    try:
        children = list(root.iterdir())
    except OSError as err:
        logger.warning('Failed to list archive root %s: %s', root, err)
        return []

    return sorted(child for child in children if child.is_dir() and _archive_date_str(child))


def _day_dir_for_date(root: Path, date_str: str) -> Path:
    if _archive_date_str(root) == date_str:
        return root
    return root / date_str


def scan_day_recordings(root: Path, day_dir: Path) -> list[Recording]:
    recordings: list[Recording] = []
    try:
        paths = sorted(day_dir.glob('*.mkv'))
    except OSError as err:
        logger.warning('Failed to list archive day %s: %s', day_dir, err)
        return recordings

    for path in paths:
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
    return recordings


def scan_recordings(root: Path) -> list[Recording]:
    recordings: list[Recording] = []
    for day_dir in _iter_archive_day_dirs(root):
        recordings.extend(scan_day_recordings(root, day_dir))
    recordings.sort(key=lambda rec: (rec.start_utc, rec.camera))
    return recordings


def _create_recording_index_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
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
            meta_size integer,
            profile_json text,
            profile_loaded integer not null
        );

        create index recordings_start_idx on recordings(start_ts);
        create index recordings_camera_idx on recordings(camera);

        create table scanned_dirs (
            date_str text primary key,
            rel_path text not null,
            dir_mtime_ns integer not null,
            scanned_at real not null
        );

        create table index_state (
            key text primary key,
            value text not null
        );

        pragma user_version = {INDEX_SCHEMA_VERSION};
        """
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


def _recording_index_rows(
    root: Path,
    recordings: list[Recording],
    *,
    cache_profiles: bool,
) -> list[tuple[object, ...]]:
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
        profile = rec.profile
        profile_loaded = rec.profile_loaded
        if rec.meta_path:
            try:
                meta_stat = rec.meta_path.stat()
                meta_rel_path = rec.meta_path.relative_to(root).as_posix()
            except (OSError, ValueError):
                pass
            else:
                meta_mtime_ns = meta_stat.st_mtime_ns
                meta_size = meta_stat.st_size
                if cache_profiles and not profile_loaded:
                    profile = get_activity_profile(rec.meta_path)
                    profile_loaded = True
        else:
            profile_loaded = True
        profile_json = json.dumps(profile, separators=(',', ':')) if profile is not None else None

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
                profile_json,
                int(profile_loaded),
            )
        )
    return rows


def _replace_recording_day(
    conn: sqlite3.Connection,
    root: Path,
    date_str: str,
    day_dir: Path,
    recordings: list[Recording],
    dir_mtime_ns: int,
    scanned_at: float,
    cache_profiles: bool,
) -> None:
    rows = _recording_index_rows(root, recordings, cache_profiles=cache_profiles)
    rel_path = '.' if day_dir == root else day_dir.relative_to(root).as_posix()
    with conn:
        conn.execute('delete from recordings where date_str = ?', (date_str,))
        conn.executemany(
            """
            insert into recordings (
                rel_path, camera, start_ts, date_str, hour_str, media_mtime_ns,
                media_size, meta_rel_path, meta_mtime_ns, meta_size,
                profile_json, profile_loaded
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.execute(
            """
            insert into scanned_dirs (date_str, rel_path, dir_mtime_ns, scanned_at)
            values (?, ?, ?, ?)
            on conflict(date_str) do update set
                rel_path = excluded.rel_path,
                dir_mtime_ns = excluded.dir_mtime_ns,
                scanned_at = excluded.scanned_at
            """,
            (date_str, rel_path, dir_mtime_ns, scanned_at),
        )


def _remove_recording_day(conn: sqlite3.Connection, date_str: str) -> None:
    with conn:
        conn.execute('delete from recordings where date_str = ?', (date_str,))
        conn.execute('delete from scanned_dirs where date_str = ?', (date_str,))


def _index_full_scan_complete(conn: sqlite3.Connection) -> bool:
    row = conn.execute("select value from index_state where key = 'full_scan_complete'").fetchone()
    return row is not None and row['value'] == '1'


def _set_index_full_scan_complete(conn: sqlite3.Connection, complete: bool) -> None:
    with conn:
        conn.execute(
            """
            insert into index_state (key, value)
            values ('full_scan_complete', ?)
            on conflict(key) do update set value = excluded.value
            """,
            ('1' if complete else '0',),
        )


def _acquire_refresh_lock(root: Path, stale_available: bool) -> Lock | None:
    lock = _refresh_lock(root)
    if lock.acquire(blocking=False):
        return lock
    if stale_available:
        return None
    lock.acquire()
    return lock


def _all_archive_date_strs(root: Path) -> list[str]:
    return [day_dir.name for day_dir in _iter_archive_day_dirs(root)]


def _recent_archive_date_strs(root: Path) -> list[str]:
    return _all_archive_date_strs(root)[-RECENT_ARCHIVE_DAYS:]


def _range_archive_date_strs(start_dt: datetime, end_dt: datetime) -> list[str]:
    first_date = (start_dt - timedelta(days=1)).date()
    last_date = (end_dt + timedelta(days=1)).date()
    dates = []
    current = first_date
    while current <= last_date:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def _should_refresh_day(
    conn: sqlite3.Connection,
    date_str: str,
    dir_mtime_ns: int,
    now: float,
    ttl: float,
    force: bool,
) -> bool:
    if force:
        return True
    row = conn.execute(
        'select dir_mtime_ns, scanned_at from scanned_dirs where date_str = ?',
        (date_str,),
    ).fetchone()
    if row is None:
        return True
    if row['dir_mtime_ns'] != dir_mtime_ns:
        return True
    return now - row['scanned_at'] >= ttl


def _refresh_index_days(
    conn: sqlite3.Connection,
    root: Path,
    date_strs: list[str],
    *,
    force: bool = False,
    cache_profiles: bool = False,
) -> None:
    ttl = get_scan_cache_ttl()
    now = time()
    for date_str in sorted(set(date_strs)):
        day_dir = _day_dir_for_date(root, date_str)
        try:
            dir_stat = day_dir.stat()
        except OSError:
            _remove_recording_day(conn, date_str)
            continue
        if not day_dir.is_dir():
            _remove_recording_day(conn, date_str)
            continue
        if not _should_refresh_day(conn, date_str, dir_stat.st_mtime_ns, now, ttl, force):
            continue
        recordings = scan_day_recordings(root, day_dir)
        _replace_recording_day(
            conn,
            root,
            date_str,
            day_dir,
            recordings,
            dir_stat.st_mtime_ns,
            now,
            cache_profiles,
        )


def _load_indexed_recordings(conn: sqlite3.Connection, root: Path) -> list[Recording]:
    rows = conn.execute(
        """
        select rel_path, camera, start_ts, meta_rel_path
        from recordings
        order by start_ts, camera
        """
    ).fetchall()
    return [_recording_from_index_row(root, row) for row in rows]


def _profile_cache_update(root: Path, row: sqlite3.Row) -> tuple[object, ...] | None:
    media_path = root / row['rel_path']
    sidecar_path = media_path.with_suffix('.json')
    try:
        meta_stat = sidecar_path.stat()
        meta_rel_path = sidecar_path.relative_to(root).as_posix()
    except (OSError, ValueError):
        if row['meta_rel_path'] is None and row['profile_loaded']:
            return None
        return (None, None, None, None, 1, row['rel_path'])

    if (
        row['meta_rel_path'] == meta_rel_path
        and row['meta_mtime_ns'] == meta_stat.st_mtime_ns
        and row['meta_size'] == meta_stat.st_size
        and row['profile_loaded']
    ):
        return None

    profile = get_activity_profile(sidecar_path)
    profile_json = json.dumps(profile, separators=(',', ':')) if profile is not None else None
    return (
        meta_rel_path,
        meta_stat.st_mtime_ns,
        meta_stat.st_size,
        profile_json,
        1,
        row['rel_path'],
    )


def _refresh_profile_cache_for_rows(conn: sqlite3.Connection, root: Path, rows: list[sqlite3.Row]) -> bool:
    updates = []
    for row in rows:
        update = _profile_cache_update(root, row)
        if update is not None:
            updates.append(update)
    if not updates:
        return False
    with conn:
        conn.executemany(
            """
            update recordings set
                meta_rel_path = ?,
                meta_mtime_ns = ?,
                meta_size = ?,
                profile_json = ?,
                profile_loaded = ?
            where rel_path = ?
            """,
            updates,
        )
    return True


def _load_indexed_recordings_for_range(
    conn: sqlite3.Connection,
    root: Path,
    start_dt: datetime,
    end_dt: datetime,
    camera: str | None,
    *,
    refresh_profiles: bool = True,
) -> list[Recording]:
    params: list[object] = [(start_dt - timedelta(hours=1)).timestamp(), end_dt.timestamp()]
    camera_filter = ''
    if camera:
        camera_filter = 'and camera = ?'
        params.append(camera)
    query = f"""
    select
        rel_path, camera, start_ts, meta_rel_path, meta_mtime_ns, meta_size,
        profile_json, profile_loaded
    from recordings
    where start_ts > ? and start_ts < ?
    {camera_filter}
    order by start_ts, camera
    """
    rows = conn.execute(query, params).fetchall()
    if refresh_profiles and _refresh_profile_cache_for_rows(conn, root, rows):
        rows = conn.execute(query, params).fetchall()
    recordings = [_recording_from_index_row(root, row, include_profile=True) for row in rows]
    return [rec for rec in recordings if rec.start_utc + timedelta(hours=1) > start_dt and rec.start_utc < end_dt]


def load_recordings(root: Path) -> list[Recording]:
    conn = _connect_recording_index(root)
    if conn is None:
        return scan_recordings(root)

    try:
        lock = _acquire_refresh_lock(root, _index_full_scan_complete(conn))
        if lock is None:
            return _load_indexed_recordings(conn, root)
        try:
            if _index_full_scan_complete(conn):
                _refresh_index_days(conn, root, _recent_archive_date_strs(root), cache_profiles=False)
            else:
                date_strs = _all_archive_date_strs(root)
                if not date_strs:
                    _set_index_full_scan_complete(conn, True)
                    return scan_recordings(root)
                _set_index_full_scan_complete(conn, False)
                _refresh_index_days(conn, root, date_strs, force=True, cache_profiles=False)
                _set_index_full_scan_complete(conn, True)
        finally:
            lock.release()
        recordings = _load_indexed_recordings(conn, root)
        return recordings
    finally:
        conn.close()


def load_recordings_for_range(
    root: Path,
    start_dt: datetime,
    end_dt: datetime,
    camera: str | None,
) -> list[Recording]:
    conn = _connect_recording_index(root)
    if conn is None:
        return [
            rec
            for rec in scan_recordings(root)
            if (not camera or rec.camera == camera)
            and rec.start_utc + timedelta(hours=1) > start_dt
            and rec.start_utc < end_dt
        ]

    try:
        lock = _acquire_refresh_lock(root, _index_full_scan_complete(conn))
        if lock is None:
            return _load_indexed_recordings_for_range(
                conn,
                root,
                start_dt,
                end_dt,
                camera,
                refresh_profiles=False,
            )
        try:
            _refresh_index_days(conn, root, _range_archive_date_strs(start_dt, end_dt), cache_profiles=False)
        finally:
            lock.release()
        return _load_indexed_recordings_for_range(conn, root, start_dt, end_dt, camera)
    finally:
        conn.close()


def _coerce_float(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be numeric')
    if not isinstance(value, int | float | str):
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
    profile = rec.profile if rec.profile_loaded else get_activity_profile(rec.meta_path)
    return {
        'camera': rec.camera,
        'start_utc': rec.start_utc.replace(tzinfo=UTC).isoformat(),
        'path': rel,
        'has_meta': rec.meta_path is not None,
        'profile': profile,
    }


def _recorded_date_strings(root: Path, camera: str | None) -> list[str]:
    archive_tz = get_archive_tz()
    dates = set()
    for day_dir in _iter_archive_day_dirs(root):
        try:
            entries = list(os.scandir(day_dir))
        except OSError as err:
            logger.warning('Failed to list archive day %s: %s', day_dir, err)
            continue

        for entry in entries:
            if not entry.name.endswith('.mkv'):
                continue
            parsed = parse_recording_path(root, day_dir / entry.name)
            if not parsed:
                continue
            date_str, hour_str, rec_camera = parsed
            if camera and rec_camera != camera:
                continue
            try:
                start_utc = _recording_start_utc(date_str, hour_str)
            except ValueError:
                continue
            local_dt = start_utc.replace(tzinfo=UTC).astimezone(archive_tz)
            dates.add(local_dt.strftime('%Y-%m-%d'))
    return sorted(list(dates))


def _serialized_recordings_for_range(
    root: Path,
    start_dt: datetime,
    end_dt: datetime,
    camera: str | None,
) -> list[dict[str, str | bool | dict[str, list[float]] | None]]:
    recordings = load_recordings_for_range(root, start_dt, end_dt, camera)
    return [serialize_recording(rec) for rec in recordings]


def _read_sidecar(path: str) -> object:
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
        return json.loads(sidecar_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail='Sidecar invalid JSON')


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
    return JSONResponse(content=list(CAMERA_NAMES))


@app.get('/api/recorded_dates')
async def recorded_dates(camera: str | None = None) -> JSONResponse:
    root = get_archive_root()
    dates = await run_in_threadpool(_recorded_date_strings, root, camera)
    return JSONResponse(content=dates)


@app.get('/api/recordings')
async def recordings(start: float, end: float, camera: str | None = None) -> JSONResponse:
    if start >= end:
        raise HTTPException(status_code=400, detail='start must be less than end')
    root = get_archive_root()
    start_dt = datetime.fromtimestamp(start, tz=UTC)
    end_dt = datetime.fromtimestamp(end, tz=UTC)
    filtered = await run_in_threadpool(
        _serialized_recordings_for_range,
        root,
        start_dt,
        end_dt,
        camera,
    )

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
    data = await run_in_threadpool(_read_sidecar, path)
    return JSONResponse(content=data)
