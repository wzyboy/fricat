import json
from collections.abc import Generator
from datetime import UTC
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from fricat import webapp


@pytest.fixture(autouse=True)
def clear_scan_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.delenv('FRICAT_SCAN_CACHE_TTL_SECONDS', raising=False)
    monkeypatch.delenv('FRICAT_TIMEZONE', raising=False)
    webapp.clear_scan_cache()
    yield
    webapp.clear_scan_cache()


def archive_root() -> Path:
    return Path(__file__).resolve().parents[1] / 'test_archive'


def test_cameras_are_loaded_from_archive(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root()))
    client = TestClient(webapp.app)

    response = client.get('/api/cameras')

    assert response.status_code == 200
    assert response.json() == ['CAM1']


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


def test_recorded_dates_are_returned_for_camera(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root()))
    client = TestClient(webapp.app)

    response = client.get('/api/recorded_dates', params={'camera': 'CAM1'})

    assert response.status_code == 200
    assert response.json() == ['2026-03-24']


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


def test_recordings_include_activity_profiles(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root()))
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


def test_meta_accepts_encoded_path(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root()))
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


def test_activity_profile_handles_null_audio() -> None:
    profile = webapp.get_activity_profile(archive_root() / '2026-03-24' / '23_CAM1.json')

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


def test_archive_scan_is_cached_within_ttl(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(tmp_path))
    monkeypatch.setenv('FRICAT_SCAN_CACHE_TTL_SECONDS', '60')
    calls = 0

    def fake_scan_recordings(root: Path) -> list[webapp.Recording]:
        nonlocal calls
        calls += 1
        return [
            webapp.Recording(
                camera='CAMX',
                start_utc=datetime(2026, 3, 24, tzinfo=UTC),
                path=root / '2026-03-24' / '00_CAMX.mkv',
                meta_path=None,
            )
        ]

    monkeypatch.setattr(webapp, 'scan_recordings', fake_scan_recordings)
    client = TestClient(webapp.app)

    assert client.get('/api/cameras').json() == ['CAMX']
    assert client.get('/api/cameras').json() == ['CAMX']
    assert calls == 1


def test_archive_scan_cache_can_be_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(tmp_path))
    monkeypatch.setenv('FRICAT_SCAN_CACHE_TTL_SECONDS', '0')
    calls = 0

    def fake_scan_recordings(root: Path) -> list[webapp.Recording]:
        nonlocal calls
        calls += 1
        return [
            webapp.Recording(
                camera=f'CAM{calls}',
                start_utc=datetime(2026, 3, 24, tzinfo=UTC),
                path=root / '2026-03-24' / f'00_CAM{calls}.mkv',
                meta_path=None,
            )
        ]

    monkeypatch.setattr(webapp, 'scan_recordings', fake_scan_recordings)
    client = TestClient(webapp.app)

    assert client.get('/api/cameras').json() == ['CAM1']
    assert client.get('/api/cameras').json() == ['CAM2']
    assert calls == 2
