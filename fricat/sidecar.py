import json
import sqlite3
import subprocess
from pathlib import Path
from datetime import UTC
from datetime import datetime
from dataclasses import dataclass

ANALYSIS_WINDOW_SECONDS = 1
ANALYSIS_SAMPLE_RATE = 8000


@dataclass
class SegmentInfo:
    offset: float
    duration: float
    motion: int | None
    objects: int | None
    audio_dbfs: float | None


def fetch_segments(
    db_path: Path,
    camera: str,
    start_ts: float,
    end_ts: float,
) -> list[SegmentInfo]:
    conn = sqlite3.connect(db_path)
    select_cols = ['start_time', 'end_time', 'motion', 'objects']
    query = (
        f'select {", ".join(select_cols)} '
        'from recordings '
        'where camera = ? and start_time >= ? and start_time < ? '
        'order by start_time'
    )
    cursor = conn.execute(query, (camera, start_ts, end_ts))
    segments: list[SegmentInfo] = []
    for row in cursor.fetchall():
        seg_start = float(row[0])
        seg_end = float(row[1])
        motion = row[2]
        objects = row[3]
        segments.append(
            SegmentInfo(
                offset=seg_start - start_ts,
                duration=seg_end - seg_start,
                motion=motion,
                objects=objects,
                audio_dbfs=None,
            )
        )
    conn.close()
    return segments


def _escape_filter_path(path: Path) -> str:
    escaped = path.resolve().as_posix()
    for char in ['\\', ':', ',', "'", '[', ']']:
        escaped = escaped.replace(char, f'\\{char}')
    return escaped


def _extract_audio_samples(recording_path: Path) -> list[tuple[float, float]]:
    """Analyze the audio level in 1-second segments. Example:

    $ ffprobe -v error -f lavfi -i "amovie=test_archive/2026-03-24/23_CAM1.mkv,aresample=8000,asetnsamples=n=8000,astats=metadata=1:reset=1" -show_entries frame=best_effort_timestamp_time:frame_tags=lavfi.astats.Overall.RMS_level -of compact=p=0:nk=1 | head
    0.017000|-72.633335
    1.017000|-72.217010
    2.017000|-72.216889
    3.017000|-72.217687
    4.017000|-58.835769
    5.017000|-72.217138
    6.017000|-72.217321
    7.017000|-72.216633
    8.017000|-72.217564
    9.017000|-72.217664
    """
    escaped_path = _escape_filter_path(recording_path)
    filter_spec = (
        f'amovie={escaped_path},'
        f'aresample={ANALYSIS_SAMPLE_RATE},'
        f'asetnsamples=n={ANALYSIS_WINDOW_SECONDS * ANALYSIS_SAMPLE_RATE},'
        'astats=metadata=1:reset=1'
    )
    cmd = [
        'ffprobe',
        '-v',
        'error',
        '-f',
        'lavfi',
        '-i',
        filter_spec,
        '-show_entries',
        'frame=best_effort_timestamp_time:frame_tags=lavfi.astats.Overall.RMS_level',
        '-of',
        'compact=p=0:nk=1',
    ]
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    samples: list[tuple[float, float]] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            timestamp_text, dbfs_text = stripped.split('|', 1)
            timestamp = float(timestamp_text)
            dbfs = float(dbfs_text)
        except ValueError:
            continue
        samples.append((timestamp, dbfs))
    return samples


def enrich_segments_with_audio(
    segments: list[SegmentInfo],
    recording_path: Path,
) -> list[SegmentInfo]:
    """Populate each segment's `audio_dbfs` from the recording file.

    Extract 1-second dBFS samples from the `.mkv` audio stream, then for each
    existing DB-derived segment assign the loudest overlapping sample. Segment
    timing comes from Frigate's `recordings` table, so audio stays aligned with
    the same offsets/durations as `motion` and `objects`.
    """
    samples = _extract_audio_samples(recording_path)
    if not samples:
        return segments

    sample_index = 0
    sample_count = len(samples)
    for segment in segments:
        segment_start = segment.offset
        segment_end = segment.offset + segment.duration

        while sample_index < sample_count and samples[sample_index][0] < segment_start:
            sample_index += 1

        scan_index = sample_index
        max_audio_dbfs: float | None = None
        while scan_index < sample_count and samples[scan_index][0] < segment_end:
            _, value = samples[scan_index]
            max_audio_dbfs = value if max_audio_dbfs is None else max(max_audio_dbfs, value)
            scan_index += 1

        segment.audio_dbfs = max_audio_dbfs

    return segments


def build_sidecar(
    camera: str,
    start_utc: datetime,
    segments: list[SegmentInfo],
) -> dict:
    payload = {
        'camera': camera,
        'start_utc': start_utc.replace(tzinfo=UTC).isoformat(),
        'duration_seconds': 3600,
        'segments': [
            {
                'offset': segment.offset,
                'duration': segment.duration,
                'motion': segment.motion,
                'objects': segment.objects,
                'audio_dbfs': segment.audio_dbfs,
            }
            for segment in segments
        ],
    }
    return payload


def generate_sidecar(
    db_path: Path,
    recording_path: Path,
    camera: str,
    start_utc: datetime,
) -> dict[str, object] | None:
    start_ts = start_utc.timestamp()
    end_ts = start_ts + 3600
    segments = fetch_segments(
        db_path=db_path,
        camera=camera,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if not segments:
        return None
    segments = enrich_segments_with_audio(segments, recording_path)
    return build_sidecar(
        camera=camera,
        start_utc=start_utc,
        segments=segments,
    )


def write_sidecar(sidecar_path: Path, data: dict[str, object]) -> None:
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=True, indent=2)
