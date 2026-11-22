"""Local operator login and supported media import adapters."""
from getpass import getpass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
from time import monotonic

from fastapi import Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse


def password_digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1).hex()


def write_operator_password(root, password):
    if not 12 <= len(password) <= 128:
        raise ValueError('密码长度须为 12–128 个字符。')
    salt = secrets.token_bytes(16)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=root, delete=False) as out:
        pending = Path(out.name)
        json.dump({'salt': salt.hex(), 'digest': password_digest(password, salt)}, out)
    try:
        pending.replace(root / 'operator-login.json')
    finally:
        pending.unlink(missing_ok=True)


def configure_operator_password(root):
    password = getpass('新管理员密码：')
    if password != getpass('再次输入密码：'):
        raise ValueError('两次输入的密码不一致。')
    write_operator_password(root, password)


def install_login(app, root, internal_token):
    path = root / 'operator-login.json'
    if not path.is_file():
        raise ValueError('请先用 --set-operator-password 设置管理员密码。')
    record = json.loads(path.read_bytes())
    salt = bytes.fromhex(record['salt'])
    digest = record['digest']
    sessions = {}

    @app.post('/session/login')
    async def login(account: str = Form(...), password: str = Form(...)):
        matches = len(password) <= 128 and hmac.compare_digest(
            await run_in_threadpool(password_digest, password, salt), digest)
        if account != 'admin' or not matches:
            raise HTTPException(401, '账号或密码不正确。')
        now = monotonic()
        for key in [key for key, expires in sessions.items() if expires <= now]:
            sessions.pop(key)
        if len(sessions) >= 32:
            sessions.pop(next(iter(sessions)))
        session = secrets.token_urlsafe(32)
        sessions[session] = now + 8 * 3600
        return JSONResponse({'token': session}, headers={'Cache-Control': 'no-store'})

    @app.post('/session/logout')
    async def logout(request: Request):
        sessions.pop(request.headers.get('authorization', '').removeprefix('Bearer '), None)
        return {'ok': True}

    @app.middleware('http')
    async def authorize_session(request, call_next):
        if request.url.path.startswith('/api/'):
            auth = request.headers.get('authorization', '')
            session = auth.removeprefix('Bearer ')
            if not auth.startswith('Bearer ') or sessions.get(session, 0) <= monotonic():
                return JSONResponse({'detail': '请重新登录。'}, status_code=401)
            # The packaged API continues to enforce its private per-process token.
            headers = [(key, val) for key, val in request.scope['headers'] if key != b'authorization']
            request.scope['headers'] = headers + [(b'authorization', (f'Bearer {internal_token}').encode('ascii'))]
        return await call_next(request)


FORMATS = {
    '.mp3': 'mp3', '.mp4': 'mov', '.m4a': 'mov', '.mov': 'mov',
    '.aac': 'aac', '.flac': 'flac', '.ogg': 'ogg', '.opus': 'ogg',
    '.webm': 'matroska', '.wma': 'asf',
}


def install_media_import(web):
    read_wav = web.read_audio
    ffmpeg = shutil.which('ffmpeg')

    def read_media(upload):
        extension = Path(upload.filename or '').suffix.lower()
        if extension == '.wav':
            return read_wav(upload)
        demuxer = FORMATS.get(extension)
        if demuxer is None:
            raise ValueError('请上传 WAV、MP3、MP4、M4A 或其他受支持的音频文件。')
        if not ffmpeg:
            raise ValueError('此电脑未安装 FFmpeg，暂时只能上传 WAV。')
        with tempfile.TemporaryDirectory(prefix='zrs-import-') as tmp:
            source = Path(tmp) / ('source' + extension)
            output = Path(tmp) / 'converted.wav'
            with source.open('wb') as target:
                remaining = 50 * 1024 * 1024
                while block := upload.file.read(min(1024 * 1024, remaining + 1)):
                    remaining -= len(block)
                    if remaining < 0:
                        raise ValueError('上传文件不能超过 50 MB。')
                    target.write(block)
            try:
                subprocess.run([ffmpeg, '-nostdin', '-loglevel', 'error', '-protocol_whitelist', 'file',
                                '-f', demuxer, '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
                                '-t', '151', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(output)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=25,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                raise ValueError('无法读取音轨，请确认文件包含声音且可正常播放。') from None
            if output.stat().st_size > 150 * 16000 * 2 + 128:
                raise ValueError('上传文件的音轨不能超过 150 秒。')
            return output.read_bytes()

    web.read_audio = read_media


def allow_long_enrollment(web):
    extract = web.extract

    def extract_registration(raw, registration=False, denoise=False, decoded=None):
        # Enrollment has already decoded the clip and checked its center window.
        # A centered 5-second slice keeps the same center 48,000 samples.
        if registration and decoded is not None and len(decoded) > 80_000:
            start = (len(decoded) - 80_000) // 2
            decoded = decoded[start:start + 80_000]
        return extract(raw, registration=registration, denoise=denoise, decoded=decoded)

    web.extract = extract_registration
