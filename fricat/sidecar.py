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
    segment_path: str
    motion_heatmap: dict[str, int] | None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    cursor = conn.execute(f'pragma table_info({table})')
    return {row[1] for row in cursor.fetchall()}


def _parse_heatmap(raw: object) -> dict[str, int] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _relative_segment_path(path: str, recordings_root: Path | None) -> str:
    if recordings_root is None:
        return path
    try:
        return str(Path(path).resolve().relative_to(recordings_root))
    except ValueError:
        return path


def fetch_segments(
    db_path: Path,
    recordings_root: Path | None,
    camera: str,
    start_ts: float,
    end_ts: float,
) -> list[SegmentInfo]:
    conn = sqlite3.connect(db_path)
    columns = _table_columns(conn, 'recordings')
    has_heatmap = 'motion_heatmap' in columns
    select_cols = ['path', 'start_time', 'end_time', 'motion', 'objects']
    if has_heatmap:
        select_cols.append('motion_heatmap')
    query = (
        f'select {", ".join(select_cols)} '
        'from recordings '
        'where camera = ? and start_time >= ? and start_time < ? '
        'order by start_time'
    )
    cursor = conn.execute(query, (camera, start_ts, end_ts))
    segments: list[SegmentInfo] = []
    for row in cursor.fetchall():
        path = row[0]
        seg_start = float(row[1])
        seg_end = float(row[2])
        motion = row[3]
        objects = row[4]
        heatmap_raw = row[5] if has_heatmap else None
        segments.append(
            SegmentInfo(
                offset=seg_start - start_ts,
                duration=seg_end - seg_start,
                motion=motion,
                objects=objects,
                segment_path=_relative_segment_path(path, recordings_root),
                motion_heatmap=_parse_heatmap(heatmap_raw),
            )
        )
    conn.close()
    return segments


def build_sidecar(
    camera: str,
    start_utc: datetime,
    segments: list[SegmentInfo],
    db_path: Path,
    recordings_root: Path | None,
) -> dict[str, object]:
    payload = {
        'camera': camera,
        'start_utc': start_utc.replace(tzinfo=UTC).isoformat(),
        'duration_seconds': 3600,
        'source': {
            'db_path': str(db_path),
            'recordings_root': str(recordings_root) if recordings_root else None,
        },
        'segments': [
            {
                'offset': segment.offset,
                'duration': segment.duration,
                'motion': segment.motion,
                'objects': segment.objects,
                'segment_path': segment.segment_path,
                'motion_heatmap': segment.motion_heatmap,
            }
            for segment in segments
        ],
    }
    return payload


def write_sidecar(sidecar_path: Path, data: dict[str, object]) -> None:
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    with sidecar_path.open('w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=True, indent=2)
