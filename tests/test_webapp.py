import os
import json
import shutil
import logging
import subprocess
from pathlib import Path
from datetime import UTC
from datetime import datetime
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from fricat import media
from fricat import webapp


@pytest.fixture(autouse=True)
def isolate_web_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('FRICAT_SCAN_CACHE_TTL_SECONDS', raising=False)
    monkeypatch.delenv('FRICAT_WEB_INDEX_PATH', raising=False)
    monkeypatch.delenv('FRICAT_TIMEZONE', raising=False)


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    day_dir = tmp_path / '2026-03-24'
    day_dir.mkdir()
    for hour in ('22', '23'):
        (day_dir / f'{hour}_CAM1.mkv').write_bytes(b'test-media')

    (day_dir / '22_CAM1.json').write_text(
        json.dumps(
            {
                'camera': 'CAM1',
                'segments': [
                    {
                        'offset': 0.0,
                        'duration': 150.0,
                        'motion': 10.0,
                        'audio_dbfs': -40.0,
                    },
                    {
                        'offset': 150.0,
                        'duration': 150.0,
                        'motion': 20.0,
                        'audio_dbfs': -60.0,
                    },
                ],
            }
        ),
        encoding='utf-8',
    )
    (day_dir / '23_CAM1.json').write_text(
        json.dumps(
            {
                'camera': 'CAM1',
                'segments': [
                    {
                        'offset': 0.0,
                        'duration': 150.0,
                        'motion': 30.0,
                        'audio_dbfs': None,
                    },
                    {
                        'offset': 150.0,
                        'duration': 150.0,
                        'motion': 40.0,
                        'audio_dbfs': -20.0,
                    },
                ],
            }
        ),
        encoding='utf-8',
    )
    return tmp_path


def test_config_returns_default_timezone() -> None:
    client = TestClient(webapp.app)

    response = client.get('/api/config')

    assert response.status_code == 200
    assert response.json() == {'timezone': 'America/Vancouver'}


def test_config_returns_timezone_override(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_TIMEZONE', 'UTC')
    client = TestClient(webapp.app)

    response = client.get('/api/config')

    assert response.status_code == 200
    assert response.json() == {'timezone': 'UTC'}


def test_recorded_dates_are_returned_for_camera(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)

    response = client.get('/api/recorded_dates', params={'camera': 'CAM1'})

    assert response.status_code == 200
    assert response.json() == ['2026-03-24']


def test_recorded_date_strings_scan_filenames_without_recording_cache(monkeypatch, archive_root: Path) -> None:
    def fail_activity_profile(meta_path: Path | None) -> dict[str, list[float]] | None:
        raise AssertionError(f'should not load activity profile for {meta_path}')

    monkeypatch.setenv('FRICAT_TIMEZONE', 'UTC')
    monkeypatch.setattr(webapp, 'get_activity_profile', fail_activity_profile)

    dates = webapp._recorded_date_strings(archive_root, 'CAM1')

    assert dates == ['2026-03-24']


def test_recorded_dates_use_archive_timezone(monkeypatch, tmp_path) -> None:
    day_dir = tmp_path / '2026-03-24'
    day_dir.mkdir()
    (day_dir / '00_CAM1.mkv').write_bytes(b'')
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(tmp_path))
    client = TestClient(webapp.app)

    monkeypatch.setenv('FRICAT_TIMEZONE', 'America/Vancouver')
    response = client.get('/api/recorded_dates', params={'camera': 'CAM1'})
    assert response.status_code == 200
    assert response.json() == ['2026-03-23']

    monkeypatch.setenv('FRICAT_TIMEZONE', 'UTC')
    response = client.get('/api/recorded_dates', params={'camera': 'CAM1'})
    assert response.status_code == 200
    assert response.json() == ['2026-03-24']


def test_recordings_include_activity_profiles(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)
    start = datetime(2026, 3, 24, tzinfo=UTC).timestamp()
    end = datetime(2026, 3, 25, tzinfo=UTC).timestamp()

    response = client.get(
        '/api/recordings',
        params={'start': start, 'end': end, 'camera': 'CAM1'},
    )

    assert response.status_code == 200
    data = response.json()
    assert [rec['path'] for rec in data] == [
        '2026-03-24/22_CAM1.mkv',
        '2026-03-24/23_CAM1.mkv',
    ]
    assert all(rec['profile'] is not None for rec in data)
    assert all(len(rec['profile']['motion']) == 24 for rec in data)
    assert all(len(rec['profile']['sound']) == 24 for rec in data)


def test_recordings_range_scans_only_date_window(monkeypatch, tmp_path: Path) -> None:
    scanned_dates: list[str] = []

    def fake_scan_day_recordings(root: Path, day_dir: Path) -> list[webapp.Recording]:
        assert root == tmp_path
        scanned_dates.append(day_dir.name)
        if day_dir.name != '2026-03-24':
            return []
        return [
            webapp.Recording(
                camera='CAM2',
                start_utc=datetime(2026, 3, 24, 10, tzinfo=UTC),
                path=tmp_path / '2026-03-24' / '10_CAM2.mkv',
                meta_path=None,
            ),
            webapp.Recording(
                camera='CAM1',
                start_utc=datetime(2026, 3, 24, 10, tzinfo=UTC),
                path=tmp_path / '2026-03-24' / '10_CAM1.mkv',
                meta_path=None,
            ),
        ]

    monkeypatch.setattr(webapp, 'scan_day_recordings', fake_scan_day_recordings)

    recordings = webapp.load_recordings_for_range(
        tmp_path,
        datetime(2026, 3, 24, 9, 30, tzinfo=UTC),
        datetime(2026, 3, 24, 10, 30, tzinfo=UTC),
        'CAM1',
    )

    assert scanned_dates == ['2026-03-23', '2026-03-24', '2026-03-25']
    assert [rec.path.name for rec in recordings] == ['10_CAM1.mkv']


def test_recordings_range_returns_empty_for_missing_days(tmp_path: Path) -> None:
    recordings = webapp.load_recordings_for_range(
        tmp_path,
        datetime(2026, 3, 24, tzinfo=UTC),
        datetime(2026, 3, 25, tzinfo=UTC),
        'CAM1',
    )

    assert recordings == []


def test_activity_profile_updates_when_sidecar_changes(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)
    start = datetime(2026, 3, 24, tzinfo=UTC).timestamp()
    end = datetime(2026, 3, 25, tzinfo=UTC).timestamp()

    response = client.get('/api/recordings', params={'start': start, 'end': end, 'camera': 'CAM1'})
    recording = next(rec for rec in response.json() if rec['path'] == '2026-03-24/22_CAM1.mkv')
    assert recording['profile']['motion'][0] == 10.0

    day_dir = archive_root / '2026-03-24'
    sidecar = day_dir / '22_CAM1.json'
    old_day_mtime_ns = day_dir.stat().st_mtime_ns
    old_sidecar_mtime_ns = sidecar.stat().st_mtime_ns
    sidecar.write_text(
        json.dumps(
            {
                'camera': 'CAM1',
                'segments': [
                    {
                        'offset': 0.0,
                        'duration': 150.0,
                        'motion': 90.0,
                        'audio_dbfs': -40.0,
                    }
                ],
            }
        ),
        encoding='utf-8',
    )
    os.utime(sidecar, ns=(old_sidecar_mtime_ns + 1_000_000_000, old_sidecar_mtime_ns + 1_000_000_000))
    os.utime(day_dir, ns=(old_day_mtime_ns, old_day_mtime_ns))

    response = client.get('/api/recordings', params={'start': start, 'end': end, 'camera': 'CAM1'})
    recording = next(rec for rec in response.json() if rec['path'] == '2026-03-24/22_CAM1.mkv')

    assert recording['profile']['motion'][0] == 90.0


def test_recordings_refresh_when_hour_is_added(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)
    start = datetime(2026, 3, 24, tzinfo=UTC).timestamp()
    end = datetime(2026, 3, 25, tzinfo=UTC).timestamp()

    response = client.get('/api/recordings', params={'start': start, 'end': end, 'camera': 'CAM1'})
    assert [rec['path'] for rec in response.json()] == [
        '2026-03-24/22_CAM1.mkv',
        '2026-03-24/23_CAM1.mkv',
    ]

    day_dir = archive_root / '2026-03-24'
    old_mtime_ns = day_dir.stat().st_mtime_ns
    (day_dir / '21_CAM1.mkv').write_bytes(b'test-media')
    os.utime(day_dir, ns=(old_mtime_ns + 1_000_000_000, old_mtime_ns + 1_000_000_000))

    response = client.get('/api/recordings', params={'start': start, 'end': end, 'camera': 'CAM1'})

    assert [rec['path'] for rec in response.json()] == [
        '2026-03-24/21_CAM1.mkv',
        '2026-03-24/22_CAM1.mkv',
        '2026-03-24/23_CAM1.mkv',
    ]


def test_recorded_dates_refresh_recent_new_day(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    monkeypatch.setenv('FRICAT_TIMEZONE', 'UTC')
    client = TestClient(webapp.app)

    response = client.get('/api/recorded_dates', params={'camera': 'CAM1'})
    assert response.json() == ['2026-03-24']

    day_dir = archive_root / '2026-03-25'
    day_dir.mkdir()
    (day_dir / '12_CAM1.mkv').write_bytes(b'test-media')

    response = client.get('/api/recorded_dates', params={'camera': 'CAM1'})

    assert response.json() == ['2026-03-24', '2026-03-25']


def test_meta_accepts_encoded_path(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)

    response = client.get('/api/meta?path=2026-03-24%2F23_CAM1.mkv')

    assert response.status_code == 200
    assert response.json()['camera'] == 'CAM1'


def test_media_accepts_encoded_reserved_path_segments(monkeypatch, tmp_path) -> None:
    day_dir = tmp_path / '2026-03-24'
    day_dir.mkdir()
    filename = '00_CAM & #%?.mkv'
    media_body = b'test-media'
    (day_dir / filename).write_bytes(media_body)
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(tmp_path))
    client = TestClient(webapp.app)

    response = client.get(f'/media/2026-03-24/{quote(filename)}')

    assert response.status_code == 200
    assert response.content == media_body


def test_clip_export_stream_copies_mp4_and_cleans_up(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    monkeypatch.setenv('FRICAT_TIMEZONE', 'America/Vancouver')
    commands: list[list[str]] = []
    output_dirs: list[Path] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        output_path = Path(command[-1])
        output_dirs.append(output_path.parent)
        output_path.write_bytes(b'clip-data')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(webapp.subprocess, 'run', fake_run)
    client = TestClient(webapp.app)

    response = client.post(
        '/api/clip',
        json={'path': '2026-03-24/22_CAM1.mkv', 'start': 61.9, 'end': 125.1},
    )

    assert response.status_code == 200
    assert response.content == b'clip-data'
    assert response.headers['content-type'] == 'video/mp4'
    assert response.headers['content-disposition'] == (
        'attachment; filename="2026-03-24_15-01-01_to_15-02-05_CAM1.mp4"'
    )
    command = commands[0]
    assert command[command.index('-ss') + 1] == '61.9'
    assert command[command.index('-t') + 1] == str(125.1 - 61.9)
    assert command[command.index('-map') + 1] == '0:v:0'
    assert command[command.index('-map', command.index('-map') + 1) + 1] == '0:a:0?'
    assert command[command.index('-c:v') + 1] == 'copy'
    assert command[command.index('-af') + 1] == 'aresample=async=1:first_pts=0,apad'
    assert command[command.index('-c:a') + 1] == 'aac'
    assert '-shortest' in command
    assert command[command.index('-movflags') + 1] == '+faststart'
    assert all(not output_dir.exists() for output_dir in output_dirs)


@pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg and ffprobe are required',
)
def test_clip_export_normalizes_sparse_audio(tmp_path: Path) -> None:
    source = tmp_path / 'sparse.mkv'
    subprocess.run(
        [
            'ffmpeg',
            '-hide_banner',
            '-loglevel',
            'error',
            '-f',
            'lavfi',
            '-i',
            'color=size=160x90:rate=10:duration=4',
            '-f',
            'lavfi',
            '-i',
            'sine=frequency=440:sample_rate=8000:duration=4',
            '-filter:a',
            "aselect='lt(t,0.256)+between(t,2,2.256)'",
            '-c:v',
            'mpeg4',
            '-c:a',
            'aac',
            str(source),
        ],
        check=True,
    )

    output_path, temp_dir = webapp._export_clip(source, 0, 4)
    try:
        health = media.probe_audio_health(output_path, max_gap=0.5)
        assert health.healthy is True
        assert health.packet_count > 20
    finally:
        shutil.rmtree(temp_dir)


@pytest.mark.parametrize(
    ('start', 'end'),
    [
        (-1, 1),
        (1, 1),
        (2, 1),
        ('nan', 1),
        (0, 'inf'),
    ],
)
def test_clip_export_rejects_invalid_ranges(
    monkeypatch,
    archive_root: Path,
    start: float | str,
    end: float | str,
) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)

    response = client.post(
        '/api/clip',
        json={'path': '2026-03-24/22_CAM1.mkv', 'start': start, 'end': end},
    )

    assert response.status_code == 400


def test_clip_export_accepts_full_hour_boundary(monkeypatch, archive_root: Path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[-1]).write_bytes(b'clip-data')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(webapp.subprocess, 'run', fake_run)
    client = TestClient(webapp.app)

    response = client.post(
        '/api/clip',
        json={'path': '2026-03-24/22_CAM1.mkv', 'start': 0, 'end': 3600},
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    ('path', 'status_code'),
    [
        ('../outside.mkv', 400),
        ('2026-03-24/not-an-hour.mkv', 400),
        ('2026-03-24/21_CAM1.mkv', 404),
    ],
)
def test_clip_export_rejects_invalid_or_missing_sources(
    monkeypatch,
    archive_root: Path,
    path: str,
    status_code: int,
) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))
    client = TestClient(webapp.app)

    response = client.post('/api/clip', json={'path': path, 'start': 0, 'end': 1})

    assert response.status_code == status_code


def test_clip_export_handles_ffmpeg_failure(monkeypatch, archive_root: Path, caplog) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root))

    def fail_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, stderr='muxing failed')

    monkeypatch.setattr(webapp.subprocess, 'run', fail_run)
    client = TestClient(webapp.app)

    with caplog.at_level(logging.ERROR, logger='fricat.webapp'):
        response = client.post(
            '/api/clip',
            json={'path': '2026-03-24/22_CAM1.mkv', 'start': 10, 'end': 20},
        )

    assert response.status_code == 500
    assert response.json() == {'detail': 'Failed to export clip'}
    assert 'muxing failed' in caplog.text


def test_index_includes_clip_controls() -> None:
    client = TestClient(webapp.app)

    response = client.get('/')

    assert response.status_code == 200
    assert 'id="clip-start-btn"' in response.text
    assert 'id="clip-end-btn"' in response.text
    assert 'id="clip-export-btn"' in response.text
    assert 'id="clip-start-marker"' in response.text
    assert 'id="clip-end-marker"' in response.text
    assert 'id="clip-range"' in response.text


def test_index_includes_accessible_playback_controls() -> None:
    client = TestClient(webapp.app)

    response = client.get('/')

    assert response.status_code == 200
    assert 'aria-label="Back 5 minutes">−5m</button>' in response.text
    assert 'aria-label="Forward 10 seconds">+10s</button>' in response.text
    assert 'id="activity-seeker"' in response.text
    assert 'type="range"' in response.text
    assert 'aria-label="Video position"' in response.text
    assert 'id="playback-speed"' in response.text
    for rate in ('1', '1.5', '2', '4', '8', '16'):
        assert f'<option value="{rate}"' in response.text


def test_activity_profile_handles_null_audio(archive_root: Path) -> None:
    profile = webapp.get_activity_profile(archive_root / '2026-03-24' / '23_CAM1.json')

    assert profile is not None
    assert len(profile['motion']) == 24
    assert len(profile['sound']) == 24


def test_sound_profile_averaging_starts_at_zero(tmp_path) -> None:
    sidecar = tmp_path / 'sample.json'
    sidecar.write_text(
        json.dumps(
            {
                'segments': [
                    {
                        'offset': 0.0,
                        'duration': 10.0,
                        'motion': 0.0,
                        'audio_dbfs': -40.0,
                    },
                ],
            }
        ),
        encoding='utf-8',
    )

    profile = webapp.get_activity_profile(sidecar)

    assert profile is not None
    assert profile['sound'][0] == 50.0


def test_activity_profile_logs_invalid_json(tmp_path, caplog) -> None:
    sidecar = tmp_path / 'invalid.json'
    sidecar.write_text('{', encoding='utf-8')

    with caplog.at_level(logging.WARNING, logger='fricat.webapp'):
        profile = webapp.get_activity_profile(sidecar)

    assert profile is None
    assert 'Failed to read sidecar profile' in caplog.text


def test_recordings_keep_malformed_profiles_from_crashing(monkeypatch, tmp_path, caplog) -> None:
    day_dir = tmp_path / '2026-03-24'
    day_dir.mkdir()
    (day_dir / '00_CAM1.mkv').write_bytes(b'')
    (day_dir / '00_CAM1.json').write_text(
        json.dumps({'segments': [{'offset': 'not-a-number', 'motion': 1.0}]}),
        encoding='utf-8',
    )
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(tmp_path))
    client = TestClient(webapp.app)
    start = datetime(2026, 3, 24, tzinfo=UTC).timestamp()
    end = datetime(2026, 3, 25, tzinfo=UTC).timestamp()

    with caplog.at_level(logging.WARNING, logger='fricat.webapp'):
        response = client.get('/api/recordings', params={'start': start, 'end': end})

    assert response.status_code == 200
    assert response.json()[0]['has_meta'] is True
    assert response.json()[0]['profile'] is None
    assert 'Invalid sidecar profile' in caplog.text
