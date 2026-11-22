"""Exercise registration through the packaged routes with deterministic generated clips."""
import shutil
import subprocess
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
import numpy as np

from test_study_capture import TOKEN, wav
from zrs_study import demo_app, prepare
from zrs_study_local import allow_long_enrollment, install_media_import


def run(root):
    prepare(root, e14=True)
    app, _ = demo_app(root, TOKEN)
    from voiceprint import app as web

    # As in E14 mode, accept uploaded files. Keep the synthetic feature encoder.
    web.read_audio = lambda upload: upload.file.read()
    install_media_import(web)
    allow_long_enrollment(web)
    durations = (3, 5, 7.125, 10, 15)
    inputs = []
    reference_centers = []
    for i, seconds in enumerate(durations):
        rate = 48000
        t = np.arange(int(seconds * rate)) / rate
        pcm = (10000 * np.sin(2 * np.pi * ((200 + i * 37) * t + t * t))).astype('int16')
        data = wav(pcm, rate)
        inputs.append((f'{i}.wav', data, 'audio/wav'))
        reference_centers.append(web.center_window(web.decode(data)).copy())

    ffmpeg = shutil.which('ffmpeg')
    assert ffmpeg, 'FFmpeg needed to verify compressed audio'
    converted = []
    for i, extension in enumerate(('mp3', 'mp4', 'm4a', 'flac'), 1):
        source = root / f'source-{i}.wav'
        source.write_bytes(inputs[i][1])
        target = source.with_suffix('.' + extension)
        subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-i', str(source), str(target)], check=True)
        converted.append((target.name, target.read_bytes(), 'application/octet-stream'))
    video_only = root / 'silent.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'color=s=24x24:d=1', '-an', str(video_only)], check=True)

    with TestClient(app, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        def enroll(key, arm, files):
            return client.post('/api/enroll', data={
                'user_id': key * 32 + '-' + arm, 'username': 'duration-fixture', 'denoise': str(arm == 'on'),
            }, files=[('files', item) for item in files])

        short = ('short.wav', wav(np.ones(46_400, dtype='int16') * 4000, 16000), 'audio/wav')
        too_long = ('long.wav', wav(np.ones(16 * 16_000, dtype='int16') * 4000, 16000), 'audio/wav')
        for rejected in (short, too_long):
            reply = enroll('a', 'off', [rejected, *inputs[1:]])
            assert reply.status_code == 422, reply.text
        assert client.get('/api/users').json() == []

        actual_centers = []
        before_denoise = web.denoise_3s

        def record_input(center, enabled):
            actual_centers.append(center.copy())
            return before_denoise(center, enabled)

        web.denoise_3s = record_input
        try:
            for arm in ('off', 'on'):
                response = enroll('a', arm, inputs)
                assert response.status_code == 200, response.text
            assert len(actual_centers) == 10
            for actual, expected in zip(actual_centers, reference_centers * 2, strict=True):
                np.testing.assert_array_equal(actual, expected)
            response = enroll('b', 'off', [inputs[0], *converted])
            assert response.status_code == 200, response.text
            assert len(client.get('/api/users').json()) == 3
            for invalid in (video_only.read_bytes(), b'not media'):
                response = enroll('c', 'off', [*inputs[:4], ('invalid.mp4', invalid, 'video/mp4')])
                assert response.status_code == 422 and '无法读取音轨' in response.json()['detail']
            assert len(client.get('/api/users').json()) == 3
        finally:
            web.denoise_3s = before_denoise

    print('PASS: 3/5/7.125/10/15-second WAVs in both arms, WAV/MP3/MP4/M4A/FLAC mixed enrollment, corrupt and video-only rejection, duration limits, unchanged center samples.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='zrs-duration-') as folder:
        run(Path(folder))
