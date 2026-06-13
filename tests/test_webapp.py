import json
from datetime import UTC
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from fricat import webapp


def archive_root() -> Path:
    return Path(__file__).resolve().parents[1] / 'test_archive'


def test_cameras_are_loaded_from_archive(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root()))
    client = TestClient(webapp.app)

    response = client.get('/api/cameras')

    assert response.status_code == 200
    assert response.json() == ['CAM1']


def test_recorded_dates_are_returned_for_camera(monkeypatch) -> None:
    monkeypatch.setenv('FRICAT_ARCHIVE_ROOT', str(archive_root()))
    client = TestClient(webapp.app)

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
