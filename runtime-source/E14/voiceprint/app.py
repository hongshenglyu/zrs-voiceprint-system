import hmac
import hashlib
import json
import logging
import re
import sqlite3
import uuid
import os
from functools import wraps
from threading import RLock
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from pathlib import Path
import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .model import load_model
from .audio import decode, quality
from .model import embedding, normalize, ROOT, MODEL_ID, MODEL_MODE, model_version
from .study_withdrawal import Withdrawal
from .denoise import denoise_3s
from .denoise_web import processing_version as denoise_processing_version, embedding as denoise_embedding

@asynccontextmanager
async def lifespan(app):
    init_db()
    if cleanup_withdrawal():
        raise RuntimeError('撤回文件清理待完成')
    load_model()
    logging.info('ECAPA-TDNN loaded')
    yield


app = FastAPI(title='复杂声学环境声纹认证', version='0.1.0', lifespan=lifespan)
DATA = Path(os.environ.get('VOICEPRINT_DATA_DIR', ROOT / ('.local/self-trained/data' if MODEL_MODE == 'SELF_TRAINED' else 'data'))).resolve()
if MODEL_MODE == 'SELF_TRAINED' and not DATA.is_relative_to((ROOT / '.local/self-trained').resolve()):
    raise ValueError('SELF_TRAINED 必须使用独立 .local/self-trained 数据目录')
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'voiceprint.sqlite3'
DENOISE_MODE = os.environ.get('VOICEPRINT_DENOISE_MODE', '0') == '1'
if DENOISE_MODE and (MODEL_MODE != 'SELF_TRAINED' or not DATA.is_relative_to((ROOT / '.local/self-trained/denoise01').resolve())):
    raise ValueError('降噪Web须使用自训练模型及独立 .local/self-trained/denoise01 数据目录')
if not DENOISE_MODE:
    raise ValueError('研究 Web 必须启用隔离双臂模式')
withdrawal = Withdrawal(DATA)
operator_token = os.environ['VOICEPRINT_STUDY_TOKEN']
if len(operator_token) < 32 or not operator_token.isascii():
    raise ValueError('研究操作员令牌须为至少 32 位 ASCII 字符')
E14_POLICIES = {}
_templates_lock = RLock()


def serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _templates_lock:
            return function(*args, **kwargs)
    return call


@contextmanager
def connect():
    conn = sqlite3.connect(DB, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with connect() as db:
        db.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      user_id TEXT PRIMARY KEY, username TEXT NOT NULL, embedding TEXT NOT NULL,
      sample_count INTEGER NOT NULL, model TEXT NOT NULL, created_time TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS records (
      id INTEGER PRIMARY KEY, user_id TEXT, similarity REAL, threshold REAL,
      environment TEXT, accepted INTEGER, created_time TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS voiceprint_templates (
      voiceprint_id TEXT PRIMARY KEY, user_id TEXT NOT NULL UNIQUE,
      embedding_path TEXT NOT NULL, sample_count INTEGER NOT NULL,
      created_time TEXT DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY(user_id) REFERENCES users(user_id));
    ''')
        db.executescript('''
    CREATE TABLE IF NOT EXISTS audit_events (
      id INTEGER PRIMARY KEY, operation TEXT NOT NULL, error_type TEXT NOT NULL,
      status_code INTEGER NOT NULL, created_time TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS pending_template_deletions (path TEXT PRIMARY KEY);
    ''')
        for table in ('users', 'records'):
            columns = {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            for name in ('model_version', 'template_version'):
                if name not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} TEXT')
            for name, kind in (('preprocessing_version', 'TEXT'), ('denoise', 'INTEGER'), ('model_sha256', 'TEXT')):
                if name not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        if 'source_batch_sha256' not in {row['name'] for row in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN source_batch_sha256 TEXT')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(records)')}
        for name in ('model', 'model_mode'):
            if name not in columns:
                db.execute(f'ALTER TABLE records ADD COLUMN {name} TEXT')


def managed_path(value):
    path = Path(value).resolve()
    if path.parent != (DATA / 'templates').resolve() or not re.fullmatch(r'[0-9a-f]{32}\.npy', path.name):
        raise HTTPException(409, '模板不属于当前独立数据目录，拒绝修改')
    return path


@serialized
def cleanup_templates():
    pending = False
    with connect() as db:
        for row in db.execute('SELECT path FROM pending_template_deletions').fetchall():
            try:
                managed_path(row['path']).unlink(missing_ok=True)
                db.execute('DELETE FROM pending_template_deletions WHERE path=?', (row['path'],))
            except (OSError, HTTPException):
                pending = True
                logging.error('TEMPLATE_CLEANUP_PENDING')
    return pending


@app.middleware('http')
async def audit_errors(request, call_next):
    if request.url.path.startswith('/api/'):
        auth = request.headers.get('authorization', '').encode('utf-8')
        if not hmac.compare_digest(auth, ('Bearer ' + operator_token).encode('ascii')):
            return JSONResponse(status_code=401, content={'detail': '需要研究操作员授权'})
    try:
        response = await call_next(request)
    except Exception:
        logging.error('UNHANDLED_SYSTEM_ERROR')
        response = JSONResponse(status_code=503, content={'detail': '系统暂时不可用，请稍后重试'})
    if response.status_code >= 400 and request.url.path.startswith('/api/'):
        route = request.scope.get('route')
        operation = getattr(route, 'path', 'unmatched')
        def persist():
            with connect() as db:
                db.execute('INSERT INTO audit_events(operation,error_type,status_code) VALUES(?,?,?)',
                           (request.method + ' ' + operation, f'HTTP_{response.status_code}', response.status_code))
        try:
            await run_in_threadpool(persist)
        except sqlite3.Error:
            logging.error('AUDIT_WRITE_FAILED')
            return JSONResponse(status_code=503, content={'detail': '异常审计写入失败，请检查数据库'},
                                headers={'X-Audit-Status': 'failed'})
    return response


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse(status_code=422, content={'detail': str(exc)})


@app.exception_handler(sqlite3.Error)
async def database_error(request, exc):
    logging.error('STUDY_DATABASE_FAILURE')
    return JSONResponse(status_code=503, content={'detail': '数据库暂时不可用，请检查服务器日志'})


def processing_version(denoise=False):
    if not DENOISE_MODE:
        if denoise:raise HTTPException(409, '当前服务未启用自训练降噪模式')
        return None
    return denoise_processing_version(model_version(), denoise)


def normalize_current(vector):
    if DENOISE_MODE:
        from experiments.robust.unified_inference import normalized
        return normalized(vector)
    return normalize(vector)


def center_window(x):
    if len(x) < 48000:
        raise ValueError('当前处理模式需要至少3秒录音')
    start = (len(x)-48000)//2
    center = x[start:start+48000]
    if not np.isfinite(center).all() or np.sqrt(np.mean(center * center)) < .0001:
        raise ValueError('音频的中心3秒静音或无效，请重新录制')
    return center


def extract(raw, registration=False, denoise=False, decoded=None):
    x = decode(raw) if decoded is None else decoded
    if registration and not 3 <= len(x)/16000 <= 5:
        raise ValueError('注册语音每段须为 3～5 秒')
    info = dict(quality(x), denoise=denoise, denoise_ms=0.)
    if DENOISE_MODE:
        x, event = denoise_3s(center_window(x).copy(), denoise)
        info.update(denoise=denoise, denoise_ms=event['processing_ms'], input_samples=48000)
    elif denoise:
        raise HTTPException(409, '当前服务未启用自训练降噪模式')
    try:
        vector = denoise_embedding(x) if DENOISE_MODE else embedding(x)
        if vector.shape != (192,) or not np.isfinite(vector).all():
            raise ValueError('声纹模型输出无效')
        vector = normalize_current(vector)
        logging.getLogger('uvicorn.error').info('Audio decoded: mono 16000 Hz, %.3f s; embedding shape=%s', len(x)/16000, vector.shape)
        return vector, info
    except Exception as exc:
        logging.exception('ECAPA inference failed')
        raise HTTPException(503, '声纹模型加载或推理失败，请检查服务器日志') from exc


def read_audio(upload):
    raw = upload.file.read(10*1024*1024+1)
    if len(raw) > 10*1024*1024:
        raise ValueError('音频不能超过 10 MB')
    return raw


@app.get('/api/users')
def users():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT user_id,username,sample_count,created_time,model,model_version,template_version,denoise,preprocessing_version,model_sha256 FROM users')]


@app.post('/api/enroll')
def enroll(user_id: str = Form(...), username: str = Form(...), files: list[UploadFile] = File(...), denoise: bool = Form(False)):
    return save_identity(user_id, username, files, denoise=denoise)


@app.put('/api/users/{user_id}/template')
def update_template(user_id: str, username: str = Form(...), expected_version: str = Form(...),
                    files: list[UploadFile] = File(...), denoise: bool = Form(False)):
    return save_identity(user_id, username, files, expected_version, denoise=denoise)


@serialized
def save_identity(user_id, username, files, expected_version=None, denoise=False):
    preprocessing = processing_version(denoise)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', user_id) or not 1 <= len(username.strip()) <= 60:
        raise ValueError('ID 限 1～40 位字母数字下划线；姓名限 1～60 字')
    if not 5 <= len(files) <= 10:
        raise ValueError('注册需要 5～10 段独立录音')
    raw = [read_audio(f) for f in files]
    if len(set(raw)) != len(raw):
        raise ValueError('请使用不同的录音，不能重复上传同一文件')
    decoded = [decode(item) for item in raw]
    source_hashes = [hashlib.sha256(center_window(wave).tobytes()).hexdigest() for wave in decoded]
    if len(set(source_hashes)) != len(source_hashes):
        raise ValueError('注册音频包含重复的中心3秒，请更换录音')
    batch_sha = hashlib.sha256(''.join(sorted(source_hashes)).encode('ascii')).hexdigest()
    with connect() as db:
        withdrawal.check_enrollment(db, user_id, denoise)
        paired = db.execute('SELECT source_batch_sha256 FROM users WHERE user_id=?',
                            (user_id[:-3] + '-off' if denoise else user_id[:-4] + '-on',)).fetchone()
        if paired and paired['source_batch_sha256'] is None:
            raise HTTPException(409, '已有注册缺少来源记录，请撤回后新建身份')
        if paired and paired['source_batch_sha256'] != batch_sha:
            raise HTTPException(409, '与已注册模式的音频批次不同，请重选原批次或撤回后新建身份')
        existing = db.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone()
        old = db.execute('SELECT embedding_path FROM voiceprint_templates WHERE user_id=?', (user_id,)).fetchone()
        if expected_version is not None:
            if existing is None:
                raise HTTPException(404, '用户不存在')
            if expected_version != (existing['template_version'] or 'legacy'):
                raise HTTPException(409, '模板版本已变化，请刷新后重试')
            if old:
                managed_path(old['embedding_path'])
        elif existing:
            raise HTTPException(409, '用户 ID 已存在')
    extracted = [extract(item, registration=True, denoise=denoise, decoded=wave)
                 for item, wave in zip(raw, decoded)]
    template = normalize_current(np.mean([v for v, _ in extracted], axis=0))
    version = model_version()
    template_id = uuid.uuid4().hex
    template_dir = DATA / 'templates'
    template_dir.mkdir(parents=True, exist_ok=True)
    template_path = template_dir / (template_id + '.npy')
    created_file = False
    try:
        with template_path.open('xb') as stream:
            created_file = True
            np.save(stream, template, allow_pickle=False)
        with connect() as db:
            if expected_version is not None:
                db.execute('DELETE FROM voiceprint_templates WHERE user_id=?', (user_id,))
                db.execute('DELETE FROM users WHERE user_id=?', (user_id,))
                if old:
                    db.execute('INSERT OR IGNORE INTO pending_template_deletions(path) VALUES(?)', (old['embedding_path'],))
            db.execute('INSERT INTO users(user_id,username,embedding,sample_count,model,model_version,template_version,preprocessing_version,denoise,model_sha256,source_batch_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (user_id, username.strip(), json.dumps(template.tolist()), len(files), MODEL_ID, version, template_id,
                        preprocessing, int(denoise), getattr(load_model(), 'checkpoint_sha256', None) if DENOISE_MODE else None, batch_sha))
            db.execute('INSERT INTO voiceprint_templates(voiceprint_id,user_id,embedding_path,sample_count) VALUES(?,?,?,?)',
                       (template_id, user_id, str(template_path.resolve()), len(files)))
    except sqlite3.IntegrityError as exc:
        if created_file:
            template_path.unlink(missing_ok=True)
        if expected_version is None and 'users.user_id' in str(exc):
            raise HTTPException(409, '用户 ID 已存在') from exc
        raise
    except Exception:
        if created_file:
            template_path.unlink(missing_ok=True)
        raise
    logging.info('Enrollment completed: %s embeddings aggregated', len(files))
    with connect() as db:
        created = db.execute('SELECT created_time FROM users WHERE user_id=?', (user_id,)).fetchone()[0]
    pending = cleanup_templates()
    return {'user_id': user_id, 'username': username.strip(), 'sample_count': len(files),
            'embedding_dimension': len(template), 'created_time': created,
            'model': MODEL_ID, 'model_mode': MODEL_MODE, 'model_version': version,
            'template_version': template_id, 'cleanup_pending': pending, 'denoise': denoise,
            'denoise_ms': sum(info.get('denoise_ms', 0.) for _, info in extracted), 'preprocessing_version': preprocessing}


@app.delete('/api/users/{user_id}/template')
@serialized
def delete_template(user_id: str, expected_version: str):
    with connect() as db:
        withdrawal.withdraw(db, user_id, expected_version, managed_path)
    return {'deleted': True, 'cleanup_pending': cleanup_withdrawal()}


@serialized
def cleanup_withdrawal():
    with connect() as db:
        forgotten = withdrawal.reconcile(db, managed_path)
    cache_pending = withdrawal.cleanup_cache(forgotten)
    return cleanup_templates() or cache_pending


@app.post('/api/verify')
@serialized
def verify(user_id: str = Form(...), file: UploadFile = File(...), denoise: bool = Form(False)):
    started = perf_counter()
    preprocessing = processing_version(denoise)
    with connect() as db:
        user = db.execute('SELECT * FROM users WHERE user_id=?', (user_id,)).fetchone()
        stored = db.execute('SELECT voiceprint_id,embedding_path FROM voiceprint_templates WHERE user_id=?', (user_id,)).fetchone()
    if user is None:
        raise HTTPException(404, '用户不存在，请先注册')
    if user['model'] != MODEL_ID:
        raise HTTPException(409, '模板模型与当前模型不一致，请重新注册')
    if (user['preprocessing_version'] != preprocessing or bool(user['denoise']) != denoise
            or (DENOISE_MODE and user['model_sha256'] != load_model().checkpoint_sha256)):
        raise HTTPException(409, '注册与验证的降噪或输入策略不一致，请切换回注册模式或重新注册')
    version = model_version()
    if user['model_version'] != version or not user['template_version']:
        raise HTTPException(409, '模板模型版本未知或已变化，请重新录音更新模板')
    if (stored is None or stored['voiceprint_id'] != user['template_version']
            or Path(stored['embedding_path']).stem != user['template_version']):
        raise HTTPException(503, '模板版本索引不一致')
    vector, info = extract(read_audio(file), denoise=denoise)
    policy = E14_POLICIES.get(denoise)
    threshold = policy['threshold'] if policy else None
    calibrated = policy is not None
    try:
        # 历史 JSON 列保留，不再作为缺失文件或索引的认证回退。
        template = normalize_current(np.load(stored['embedding_path'], allow_pickle=False))
        if template.shape != (192,):
            raise ValueError('模板维度错误')
    except (OSError, ValueError) as exc:
        logging.exception('Voiceprint template unavailable')
        raise HTTPException(503, '声纹模板无法读取，请检查服务器日志') from exc
    similarity = float(np.clip(np.dot(vector, template), -1, 1))
    accepted = similarity >= threshold if calibrated else None
    with connect() as db:
        db.execute('INSERT INTO records(user_id,similarity,threshold,environment,accepted,model,model_mode,model_version,template_version,preprocessing_version,denoise,model_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (user_id, similarity, threshold, info['environment'], int(accepted) if calibrated else None, MODEL_ID, MODEL_MODE, version, user['template_version'],
                    preprocessing, int(denoise), user['model_sha256']))
    return dict(similarity=similarity, threshold=threshold, accepted=accepted,
                policy_version=policy['policy_version'] if policy else None,
                historical_policy_sha256=policy['source_sha256'] if policy else None,
                calibrated=calibrated, model=MODEL_ID, model_mode=MODEL_MODE,
                model_version=version, template_version=user['template_version'],
                user_id=user_id, preprocessing_version=preprocessing, processing_ms=round((perf_counter()-started)*1000),
                verified_at=datetime.now(timezone.utc).isoformat(), **info)


@app.get('/api/records')
def records():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM records ORDER BY id DESC LIMIT 100')]


@app.get('/api/audits')
def audits():
    with connect() as db:
        return [dict(r) for r in db.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT 100')]


def formal_policy(path, version):
    policy = json.loads(path.read_text(encoding='utf-8'))
    if (policy['model'] != MODEL_ID or policy['model_version'] != version
            or policy['calibration_split'] != 'dev'
            or not re.fullmatch('[0-9a-f]{64}', policy['dev_manifest_sha256'])
            or policy['genuine_count'] <= 0 or policy['impostor_count'] <= 0
            or not np.isfinite(float(policy['global'])) or not -1 <= float(policy['global']) <= 1):
        raise ValueError('正式阈值来源或数值无效')
    for name in ('clean', 'medium', 'noisy'):
        if name in policy and (not np.isfinite(float(policy[name])) or not -1 <= float(policy[name]) <= 1):
            raise ValueError('环境阈值无效')
    return policy


@app.get('/api/status')
def status():
    from . import model
    with connect() as db:
        count = db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    dimension = (192 if MODEL_MODE == 'SELF_TRAINED' else model._model.mods.embedding_model.fc.conv.out_channels) if model._model is not None else None
    ready = False in E14_POLICIES
    denoise_ready = True in E14_POLICIES
    return dict(backend='Running', runtime_mode='E14',
                diagnostic_ready=model._model is not None,
                model_sha256=load_model().checkpoint_sha256,
                preprocessing_versions={arm: processing_version(arm == 'on') for arm in ('off', 'on')},
                policy_versions={arm: E14_POLICIES.get(arm == 'on', {}).get('policy_version') for arm in ('off', 'on')},
                model_loaded=model._model is not None,
                embedding_dimension=dimension, registered_users=count,
                model=MODEL_ID, model_mode=MODEL_MODE, model_version=model_version(), authentication_ready=ready,
                denoise_available=DENOISE_MODE, denoise_ready=denoise_ready, denoise_default=False)


@app.get('/')
def home():
    return FileResponse(Path(__file__).resolve().parents[1] / 'static/index.html')


app.mount('/static', StaticFiles(directory=Path(__file__).resolve().parents[1] / 'static'), name='static')
