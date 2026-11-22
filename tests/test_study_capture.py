"""P4 enrollment guards with generated input and the synthetic encoder."""
import io
from pathlib import Path
import sys
import tempfile

from fastapi.testclient import TestClient
import numpy as np
from scipy.io.wavfile import write

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from zrs_study import demo_app, prepare

TOKEN = 'fixture-only-token-with-sufficient-length'


def wav(pcm, rate):
    target = io.BytesIO()
    write(target, rate, pcm)
    return target.getvalue()


def run(archive, root):
    prepare(root, Path(archive) if archive else None)
    api, _ = demo_app(root, TOKEN)
    from voiceprint import app as web
    # The fixture upload adapter permits additional generated WAVs.
    web.read_audio = lambda upload: upload.file.read()

    rate = 48000
    t = np.arange(4 * rate) / rate
    pcm = [(9000 * np.sin(2 * np.pi * ((223 + 31 * i) * t + t * t))).astype('<i2') for i in range(5)]
    valid = [wav(x, rate) for x in pcm]
    duplicate = pcm[0].copy()
    duplicate[:1000] = 4000  # Distinct WAV bytes, identical center after resampling.
    silent = pcm[0].copy()
    silent[rate // 4:rate * 15 // 4] = 0
    nonfinite = np.ones(4 * rate, dtype=np.float32) / 2
    nonfinite[rate] = np.nan

    with TestClient(api, headers={'Authorization': 'Bearer ' + TOKEN}) as client:
        def enroll(arm, sources):
            return client.post('/api/enroll', data=dict(user_id='d' * 32 + '-' + arm, username='fixture',
                                                       denoise=str(arm == 'on')),
                               files=[('files', (f'{i}.wav', x, 'audio/wav')) for i, x in enumerate(sources)])

        for first, message in ((wav(nonfinite, rate), '无效'), (wav(silent, rate), '中心3秒静音')):
            response = enroll('off', [first, *valid[1:]])
            assert response.status_code == 422 and message in response.json()['detail'], response.text
        response = enroll('off', [valid[0], wav(duplicate, rate), *valid[2:]])
        assert response.status_code == 422 and '重复的中心3秒' in response.json()['detail'], response.text
        assert client.get('/api/users').json() == []
        assert enroll('off', valid).status_code == 200
        with web.connect() as db:
            original = db.execute('SELECT source_batch_sha256 FROM users').fetchone()[0]
            db.execute('UPDATE users SET source_batch_sha256=NULL')
        legacy = enroll('on', valid)
        assert legacy.status_code == 409 and '缺少来源记录' in legacy.json()['detail']
        with web.connect() as db:
            db.execute('UPDATE users SET source_batch_sha256=?', (original,))
        assert enroll('on', valid).status_code == 200
        assert len(client.get('/api/users').json()) == 2
        for mode in ('off', 'on'):
            for sample in (wav(silent, rate), wav(nonfinite, rate)):
                reply = client.post('/api/verify',
                                    data={'user_id': 'd' * 32 + '-' + mode, 'denoise': str(mode == 'on')},
                                    files={'file': ('fixture.wav', sample, 'audio/wav')})
                assert reply.status_code == 422, reply.text
        assert client.get('/api/records').json() == []
    print('PASS: 48 kHz registration, nonfinite and silent center rejection, duplicate decoded center rejection, legacy partial enrollment rejection, invalid probes rejected in both arms')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='zrs-p4-capture-') as temp:
        run(sys.argv[1] if len(sys.argv) > 1 else None, Path(temp))
