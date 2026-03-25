import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SegmentInfo:
    offset: float
    duration: float
    motion: int | None
    objects: int | None
    dBFS: int | None


def fetch_segments(
    db_path: Path,
    camera: str,
    start_ts: float,
    end_ts: float,
) -> list[SegmentInfo]:
    conn = sqlite3.connect(db_path)
    select_cols = ['start_time', 'end_time', 'motion', 'objects', 'dBFS']
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
        dBFS = row[4]
        segments.append(
            SegmentInfo(
                offset=seg_start - start_ts,
                duration=seg_end - seg_start,
                motion=motion,
                objects=objects,
                dBFS=dBFS,
            )
        )
    conn.close()
    return segments


def build_sidecar(
    camera: str,
    start_utc: datetime,
    segments: list[SegmentInfo],
) -> dict[str, object]:
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
                'dBFS': segment.dBFS,
            }
            for segment in segments
        ],
    }
    return payload


def write_sidecar(sidecar_path: Path, data: dict[str, object]) -> None:
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=True, indent=2)
