import json
import subprocess
from pathlib import Path

import pytest

from fricat import media


def _probe_result(
    packets: list[dict[str, str]],
    streams: list[dict[str, int]] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload = {'streams': [{'index': 1}] if streams is None else streams, 'packets': packets}
    return subprocess.CompletedProcess([], 0, json.dumps(payload), '')


def test_probe_audio_health_accepts_continuous_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    packets = [
        {'pts_time': f'{index * 0.128:.3f}', 'duration_time': '0.128'}
        for index in range(10)
    ]
    monkeypatch.setattr(media.subprocess, 'run', lambda *args, **kwargs: _probe_result(packets))

    health = media.probe_audio_health(Path('healthy.mp4'))

    assert health == media.AudioHealth(True, 10)


@pytest.mark.parametrize(
    ('packets', 'reason'),
    [
        (
            [
                {'pts_time': '0', 'duration_time': '95434'},
                {'pts_time': '0.128', 'duration_time': '0.128'},
            ],
            'audio packet 0 duration is 95434.000s',
        ),
        (
            [
                {'pts_time': '0', 'duration_time': '0.128'},
                {'pts_time': '10', 'duration_time': '0.128'},
            ],
            'audio PTS gap is 10.000s',
        ),
        (
            [
                {'pts_time': '1', 'duration_time': '0.128'},
                {'pts_time': '0', 'duration_time': '0.128'},
            ],
            'audio PTS gap is -1.000s',
        ),
    ],
)
def test_probe_audio_health_rejects_bad_packet_timing(
    monkeypatch: pytest.MonkeyPatch,
    packets: list[dict[str, str]],
    reason: str,
) -> None:
    monkeypatch.setattr(media.subprocess, 'run', lambda *args, **kwargs: _probe_result(packets))

    health = media.probe_audio_health(Path('bad.mp4'))

    assert health.healthy is False
    assert reason in (health.reason or '')


def test_probe_audio_health_requires_audio_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media.subprocess,
        'run',
        lambda *args, **kwargs: _probe_result([], streams=[]),
    )

    health = media.probe_audio_health(Path('silent.mp4'))

    assert health == media.AudioHealth(False, 0, 'audio stream is missing')


def test_probe_audio_health_rejects_invalid_probe_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        media.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '{', ''),
    )

    with pytest.raises(ValueError, match='Invalid ffprobe output'):
        media.probe_audio_health(Path('bad.mp4'))
