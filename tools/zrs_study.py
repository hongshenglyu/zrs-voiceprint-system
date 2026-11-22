"""Launch the isolated synthetic or frozen E14 study Web on Windows or another local host."""
import argparse
from contextlib import closing, nullcontext
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from zipfile import ZipFile

from zrs_study_local import allow_long_enrollment, configure_operator_password, install_login, install_media_import
from zrs_study_e14 import approved_policies, check_assets
from zrs_study_policy import DEMO_SHA, inspect_candidate, install_synthetic_policy
from zrs_study_storage import DATA, LEDGER, init_ledger, initialize, restore, snapshot, workspace

ROOT = Path(__file__).resolve().parents[1]
P0 = ROOT / 'docs/zrs-auth-08-11-02-p0/source-manifest.json'
UI = ROOT / 'tools/zrs_study_ui'
BUNDLE = ROOT / 'tools/zrs-runtime.zip'


def check_dependencies():
    from importlib.metadata import PackageNotFoundError, version

    if sys.version_info[:3] != (3, 11, 6):
        raise ValueError('需要 Python 3.11.6 (Windows x64)')
    for line in (ROOT / 'requirements-zrs.txt').read_text(encoding='ascii').splitlines():
        name, expected = line.split('==')
        try:
            actual = version(name)
        except PackageNotFoundError:
            raise ValueError(f'缺少依赖 {name}，请运行 Install-ZRS.ps1') from None
        if actual != expected:
            raise ValueError(f'依赖版本不匹配 {name}：需要 {expected}，当前 {actual}')


def read_bundle():
    raw = BUNDLE.read_bytes()
    expected = BUNDLE.with_suffix('.sha256').read_text(encoding='ascii').strip()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('本地运行包 SHA256 不匹配，请重新获取完整发布文件')
    return raw


def unpack(root, e14):
    import io

    prefix = ('E14' if e14 else 'synthetic-demo') + '/'
    with ZipFile(io.BytesIO(read_bundle())) as bundle:
        for entry in bundle.namelist():
            if entry.startswith(prefix):
                path = root / entry.removeprefix(prefix)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bundle.read(entry))


def prepare(root, repo=None, e14=False):
    if repo is None:
        unpack(root, e14)
        # Replace only UI static files in the P6 package; keep E14 and its policy untouched.
        shutil.copytree(UI, root / 'static', dirs_exist_ok=True)
        return
    manifest = json.loads(P0.read_bytes())
    archive = manifest['archive_web_base']
    sources = {name: entry['sha256'] for name, entry in manifest['archive_sources'].items()}
    if e14:
        sources.update(json.loads((ROOT / 'tools/zrs_study_e14_sources.json').read_bytes()))
    for name, expected in sources.items():
        raw = subprocess.check_output(['git', '-C', str(repo), 'show', f'{archive}:{name}'])
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError(f'Archive source digest mismatch: {name}')
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    subprocess.run(['git', 'apply', str(ROOT / 'tools/zrs_study_web.patch')], cwd=root, check=True)
    subprocess.run(['git', 'apply', str(ROOT / 'tools/zrs_study_static.patch')], cwd=root, check=True)
    if e14:
        subprocess.run(['git', 'apply', str(ROOT / 'tools/zrs_study_e14.patch')], cwd=root, check=True)
    voiceprint = root / 'voiceprint'
    scoring_sources = [hashlib.sha256((voiceprint / name).read_bytes()).hexdigest()
                       for name in ('denoise_web.py', 'app.py')]
    (voiceprint / 'p3-scoring-sources.txt').write_text('\n'.join(scoring_sources), encoding='ascii', newline='\n')
    subprocess.run(['git', 'apply', str(ROOT / 'tools/zrs_study_flow.patch')], cwd=root, check=True)
    shutil.copyfile(ROOT / 'tools/zrs_study_withdrawal.py', root / 'voiceprint/study_withdrawal.py')
    shutil.copytree(UI, root / 'static')
    style = subprocess.check_output(['git', '-C', str(repo), 'show', f'{archive}:static/style.css'])
    (root / 'static/style.css').write_bytes(style)


def configure_web(root, token, study_root):
    storage = study_root or root
    data = storage / DATA
    ledger = storage / LEDGER
    if study_root is None:
        init_ledger(ledger)
    elif not data.is_dir() or data.resolve() != data:
        raise ValueError('研究数据目录缺失或路径无效，请恢复有效快照')
    os.environ.update(VOICEPRINT_MODEL_MODE='SELF_TRAINED', VOICEPRINT_DENOISE_MODE='1',
                      VOICEPRINT_DATA_DIR=str(data), VOICEPRINT_WITHDRAWAL_LEDGER=str(ledger),
                      VOICEPRINT_STUDY_TOKEN=token)
    sys.path.insert(0, str(root))
    from voiceprint import model
    model.ROOT = storage
    return model


def demo_app(root, token, study_root=None):
    import numpy as np
    from scipy.io.wavfile import write

    model = configure_web(root, token, study_root)
    model.load_model = lambda: SimpleNamespace(checkpoint_sha256=DEMO_SHA)
    model.model_version = lambda: 'synthetic-token-v1'
    from voiceprint import app as web

    directory = root / 'demo-audio'
    directory.mkdir()
    allowed = set()
    time = np.arange(64000) / 16000
    for i in range(6):
        path = directory / f'{i + 1:02}.wav'
        wave = 9000 * np.sin(2 * np.pi * ((223 + i * 31) * time + time * time))
        write(path, 16000, wave.astype('<i2'))
        allowed.add(hashlib.sha256(path.read_bytes()).digest())

    read = web.read_audio

    def read_fixture(upload):
        raw = read(upload)
        if hashlib.sha256(raw).digest() not in allowed:
            raise ValueError('本演示只接受启动时生成的合成 WAV')
        return raw

    def feature(wave):
        result = np.zeros(192)
        result[:32] = np.frombuffer(hashlib.sha256(wave.tobytes()).digest(), np.uint8) + 1.
        return result

    web.read_audio = read_fixture
    web.denoise_embedding = feature
    install_synthetic_policy(web, model.model_version())
    return web.app, directory


def e14_app(root, token, study_root, config, approval):
    from importlib.metadata import version

    config = check_assets(config, study_root / '.local/self-trained/e14-assets')
    os.environ['VOICEPRINT_SELF_TRAINED_CONFIG'] = str(config)
    model = configure_web(root, token, study_root)
    from voiceprint import app as web
    from voiceprint.denoise import identity

    preprocessing = web.processing_version
    dependencies = {name: version(name) for name in ('torch', 'torchaudio', 'soundfile', 'PyYAML')}
    audio_sha = hashlib.sha256((root / 'voiceprint/audio.py').read_bytes()).hexdigest()
    web.processing_version = lambda enabled=False: identity(dict(
        base=preprocessing(enabled), dependencies=dependencies, audio_source_sha256=audio_sha))
    install_media_import(web)
    allow_long_enrollment(web)
    web.E14_POLICIES = approved_policies(approval, model.model_version(), web.processing_version)
    return web.app


def serve(repo, study_root, mode, config, approval, port=0):
    import uvicorn

    lock = workspace(study_root, mode) if study_root else nullcontext()
    with lock, tempfile.TemporaryDirectory(prefix='zrs-study-') as temp, closing(socket.socket()) as listener:
        try:
            listener.bind(('127.0.0.1', port))
        except OSError as exc:
            raise ValueError(f'不能监听 127.0.0.1:{port}，请检查端口占用或选择另一端口 ({exc})') from exc
        port = listener.getsockname()[1]
        if port == 8076:
            raise ValueError('端口 8076 保留')
        root = Path(temp)
        prepare(root, repo, e14=(mode == 'E14'))
        token = secrets.token_urlsafe(32)
        if mode == 'E14':
            app, fixtures = e14_app(root, token, study_root, config, approval), None
        else:
            app, fixtures = demo_app(root, token, study_root)
        install_login(app, study_root or root, token)
        print(f'URL=http://127.0.0.1:{port}/\nMODE={mode}', flush=True)
        if fixtures:
            print(f'WAV_DIR={fixtures}', flush=True)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False))
        server.run(sockets=[listener])
        if not server.started:
            raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-repo', type=Path)
    parser.add_argument('--study-dir', type=Path)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--mode', choices=['synthetic-demo', 'E14'], default='synthetic-demo')
    parser.add_argument('--e14-config', type=Path)
    parser.add_argument('--policy-approval', type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--check-install', action='store_true')
    actions.add_argument('--init', action='store_true')
    actions.add_argument('--set-operator-password', action='store_true')
    actions.add_argument('--backup', type=Path)
    actions.add_argument('--restore', type=Path)
    actions.add_argument('--check-research-policy', type=Path)
    args = parser.parse_args()
    if args.check_install:
        check_dependencies()
        read_bundle()
        print('OK: Python, dependencies, runtime SHA256')
        return
    offline = args.init or args.backup or args.restore or args.check_research_policy or args.set_operator_password
    if (args.e14_config or args.policy_approval) and (offline or args.mode != 'E14'):
        parser.error('E14 配置与批准文件只用于显式 E14 启动')
    if args.check_research_policy:
        if args.study_dir or args.archive_repo:
            parser.error('候选策略校验是独立离线操作')
        print(json.dumps(inspect_candidate(args.check_research_policy)))
        return
    root = args.study_dir.resolve() if args.study_dir else None
    if args.set_operator_password:
        if not root:
            parser.error('设置密码需要 --study-dir')
        with workspace(root, args.mode):
            configure_operator_password(root)
        print('OK')
        return
    if args.init or args.backup or args.restore:
        if not root or args.archive_repo:
            parser.error('离线操作需要 --study-dir，且无需 --archive-repo')
        if args.init:
            initialize(root, args.mode)
        else:
            path = (args.backup or args.restore).resolve()
            if path.is_relative_to(root) or root.is_relative_to(path):
                parser.error('快照目录与研究目录必须分开')
            with workspace(root) as marker:
                if args.backup:
                    snapshot(root, path, marker)
                else:
                    restore(root, path, marker)
        print('OK')
        return
    if not root:
        parser.error('启动需要 --study-dir，首次使用请先初始化并设置管理员密码')
    if not 0 <= args.port <= 65535 or args.port == 8076:
        parser.error('端口无效或保留')
    if not args.archive_repo:
        check_dependencies()
    if args.mode == 'E14' and not (root and args.e14_config):
        parser.error('E14 启动需要独立研究目录和 --e14-config')
    repo = args.archive_repo.resolve() if args.archive_repo else None
    serve(repo, root, args.mode, args.e14_config, args.policy_approval, args.port)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, ImportError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        print(f'ZRS 启动或操作失败：{exc}', file=sys.stderr)
        raise SystemExit(1) from None
