"""Password rotation, timeout and expiry checks without protected assets."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import zrs_study_local as local


def verify(root):
    old, new = local.secrets.token_urlsafe(20), local.secrets.token_urlsafe(20)
    local.write_operator_password(root, old)
    local.write_operator_password(root, new)
    persisted = (root / 'operator-login.json').read_text()
    assert old not in persisted and new not in persisted
    app = FastAPI()
    internal = local.secrets.token_urlsafe(32)
    local.install_login(app, root, internal)

    @app.get('/api/status')
    def status():
        return {'ok': True}

    with TestClient(app) as client:
        assert client.post('/session/login', data={'account':'admin', 'password':old}).status_code == 401
        assert client.get('/api/status', headers={'Authorization':'Bearer '+internal}).status_code == 401
        with patch.object(local, 'monotonic', return_value=100):
            response = client.post('/session/login', data={'account':'admin', 'password':new})
            assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
            headers = {'Authorization':'Bearer '+response.json()['token']}
            assert client.get('/api/status', headers=headers).status_code == 200
        with patch.object(local, 'monotonic', return_value=100+8*3600):
            assert client.get('/api/status',headers=headers).status_code == 401
    print('PASS: password rotation, hashed storage, private token rejected, session expiry.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='zrs-ui04-auth-') as temp:
        verify(Path(temp))
