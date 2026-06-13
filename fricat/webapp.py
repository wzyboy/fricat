import os
import json
import logging
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

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


def _recording_start_utc(date_str: str, hour_str: str) -> datetime:
    filename_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00')
    if filename_dt < LEGACY_FILENAME_CUTOFF:
        return filename_dt.replace(tzinfo=get_archive_tz()).astimezone(UTC)
    return filename_dt.replace(tzinfo=UTC)


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

    recordings = scan_recordings(root)
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
        sound_normalized = [
            max(0.0, (val + 80.0) / 80.0) * 100.0 for val in sound_bins
        ]
        
        return {
            'motion': [round(v, 1) for v in motion_bins],
            'sound': [round(v, 1) for v in sound_normalized]
        }
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
        'profile': profile
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
