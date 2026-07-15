import os
import json
import math
import shutil
import logging
import tempfile
import subprocess
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from fricat.utils import parse_recording_path

logger = logging.getLogger(__name__)

# Legacy files were using local time in filenames, while newer files are using
# UTC time in filenames.
LEGACY_FILENAME_CUTOFF = datetime(2025, 11, 18)
DEFAULT_ARCHIVE_TIMEZONE = 'America/Vancouver'
CAMERA_NAMES: list[str] = ['CAM1', 'CAM2', 'CAM3', 'CAM4', 'CAM5', 'CAM6', 'CAM7', 'CAM8']
MAX_CLIP_OFFSET_SECONDS = 3600.0


@dataclass(frozen=True)
class Recording:
    camera: str
    start_utc: datetime
    path: Path
    meta_path: Path | None


class ClipRequest(BaseModel):
    path: str
    start: float
    end: float


def get_archive_root() -> Path:
    root = os.environ.get('FRICAT_ARCHIVE_ROOT')
    if not root:
        raise RuntimeError('FRICAT_ARCHIVE_ROOT is not set')
    return Path(root).resolve()


def get_archive_timezone_name() -> str:
    return os.environ.get('FRICAT_TIMEZONE', DEFAULT_ARCHIVE_TIMEZONE)


def get_archive_tz() -> ZoneInfo:
    return ZoneInfo(get_archive_timezone_name())


def _recording_start_utc(date_str: str, hour_str: str) -> datetime:
    filename_dt = datetime.fromisoformat(f'{date_str} {hour_str}:00:00')
    if filename_dt < LEGACY_FILENAME_CUTOFF:
        return filename_dt.replace(tzinfo=get_archive_tz()).astimezone(UTC)
    return filename_dt.replace(tzinfo=UTC)


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


def _range_archive_date_strs(start_dt: datetime, end_dt: datetime) -> list[str]:
    first_date = (start_dt - timedelta(days=1)).date()
    last_date = (end_dt + timedelta(days=1)).date()
    dates = []
    current = first_date
    while current <= last_date:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def load_recordings(root: Path) -> list[Recording]:
    return scan_recordings(root)


def load_recordings_for_range(
    root: Path,
    start_dt: datetime,
    end_dt: datetime,
    camera: str | None,
) -> list[Recording]:
    recordings: list[Recording] = []
    for date_str in _range_archive_date_strs(start_dt, end_dt):
        day_recordings = scan_day_recordings(root, _day_dir_for_date(root, date_str))
        recordings.extend(
            rec
            for rec in day_recordings
            if (not camera or rec.camera == camera)
            and rec.start_utc + timedelta(hours=1) > start_dt
            and rec.start_utc < end_dt
        )
    recordings.sort(key=lambda rec: (rec.start_utc, rec.camera))
    return recordings


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
    return {
        'camera': rec.camera,
        'start_utc': rec.start_utc.replace(tzinfo=UTC).isoformat(),
        'path': rel,
        'has_meta': rec.meta_path is not None,
        'profile': get_activity_profile(rec.meta_path),
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


def _resolve_clip_source(root: Path, path: str) -> tuple[Path, datetime, str]:
    file_path = (root / path).resolve()
    try:
        file_path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid recording path')

    parsed = parse_recording_path(root, file_path)
    if not parsed:
        raise HTTPException(status_code=400, detail='Invalid recording path')
    date_str, hour_str, camera = parsed
    try:
        start_utc = _recording_start_utc(date_str, hour_str)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid recording path')
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail='Recording not found')
    return file_path, start_utc, camera


def _clip_filename(recording_start_utc: datetime, start: float, end: float, camera: str) -> str:
    archive_tz = get_archive_tz()
    start_dt = (recording_start_utc + timedelta(seconds=start)).astimezone(archive_tz)
    end_dt = (recording_start_utc + timedelta(seconds=end)).astimezone(archive_tz)
    return f'{start_dt:%Y-%m-%d_%H-%M-%S}_to_{end_dt:%H-%M-%S}_{camera}.mp4'


def _export_clip(source: Path, start: float, end: float) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix='fricat-clip-'))
    output_path = temp_dir / 'clip.mp4'
    command = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel',
        'error',
        '-ss',
        str(start),
        '-i',
        str(source),
        '-t',
        str(end - start),
        '-map',
        '0:v:0',
        '-map',
        '0:a:0',
        '-c',
        'copy',
        '-avoid_negative_ts',
        'make_zero',
        '-movflags',
        '+faststart',
        '-y',
        str(output_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as err:
        shutil.rmtree(temp_dir, ignore_errors=True)
        stderr = err.stderr if isinstance(err, subprocess.CalledProcessError) else str(err)
        logger.error('Failed to export clip from %s: %s', source, stderr)
        raise HTTPException(status_code=500, detail='Failed to export clip')
    if not output_path.is_file():
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error('FFmpeg did not create clip output for %s', source)
        raise HTTPException(status_code=500, detail='Failed to export clip')
    return output_path, temp_dir


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


@app.post('/api/clip')
async def export_clip(request: ClipRequest) -> FileResponse:
    if not math.isfinite(request.start) or not math.isfinite(request.end):
        raise HTTPException(status_code=400, detail='Clip offsets must be finite')
    if request.start < 0 or request.end > MAX_CLIP_OFFSET_SECONDS or request.start >= request.end:
        raise HTTPException(status_code=400, detail='Clip range must satisfy 0 <= start < end <= 3600')

    source, recording_start_utc, camera = _resolve_clip_source(get_archive_root(), request.path)
    output_path, temp_dir = await run_in_threadpool(_export_clip, source, request.start, request.end)
    filename = _clip_filename(recording_start_utc, request.start, request.end, camera)
    return FileResponse(
        output_path,
        media_type='video/mp4',
        filename=filename,
        background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True),
    )


@app.get('/api/meta')
async def meta(path: str) -> JSONResponse:
    data = await run_in_threadpool(_read_sidecar, path)
    return JSONResponse(content=data)
