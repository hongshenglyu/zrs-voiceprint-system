"""Storage and offline snapshot operations for the prepared study."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

LEDGER = 'withdrawal.sqlite3'
DATA = Path('.local/self-trained/denoise01')
MARKER = 'study.json'


def init_ledger(path, study_id=None):
    with path.open('xb'):
        pass
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE withdrawn(digest TEXT PRIMARY KEY, withdrawn_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        if study_id:
            db.execute('CREATE TABLE study(id TEXT NOT NULL)')
            db.execute('INSERT INTO study VALUES(?)', (study_id,))


def initialize(root, mode='synthetic-demo'):
    root.mkdir()
    study_id = uuid.uuid4().hex
    init_ledger(root / LEDGER, study_id)
    (root / DATA).mkdir(parents=True)
    (root / MARKER).write_text(json.dumps({'id': study_id, 'path': str(root), 'format': 1, 'mode': mode}), encoding='utf-8')


@contextmanager
def workspace(root, mode=None):
    marker = json.loads((root / MARKER).read_bytes())
    if marker['path'] != str(root) or marker['format'] != 1:
        raise ValueError('研究目录路径或版本不匹配')
    if mode and marker.get('mode', 'synthetic-demo') != mode:
        raise ValueError('研究目录模式不匹配，请使用独立目录')
    with (root / 'workspace.lock').open('a+b') as handle:
        # Keep the file in place so every process locks the same inode.
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = root / LEDGER
        if ledger.resolve(strict=True) != ledger:
            raise ValueError('撤回台账路径不可链接到其他位置')
        with closing(sqlite3.connect(ledger.as_uri() + '?mode=rw', uri=True)) as db:
            if db.execute('SELECT id FROM study').fetchall() != [(marker['id'],)]:
                raise ValueError('撤回台账与研究目录不匹配')
            db.execute('SELECT digest FROM withdrawn LIMIT 0')
        yield marker


def check_tree(data):
    """Reject links and active SQLite sidecars before copying a snapshot."""
    if data.resolve(strict=True) != data or not data.is_dir():
        raise ValueError('数据目录无效')
    for path in data.rglob('*'):
        if path.resolve() != path or path.is_symlink():
            raise ValueError('研究数据目录不允许链接')
    db_path = data / 'voiceprint.sqlite3'
    for suffix in ('-wal', '-shm', '-journal'):
        if db_path.with_name(db_path.name + suffix).exists():
            raise ValueError('研究库仍有 SQLite 日志文件，不能创建离线快照')
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite 快照完整性检查失败')


def snapshot(root, destination, marker):
    data = root / DATA
    check_tree(data)
    destination.mkdir()
    shutil.copytree(data, destination / 'data')
    (destination / MARKER).write_text(json.dumps(marker), encoding='utf-8')


def restore(root, source, marker):
    if json.loads((source / MARKER).read_bytes()) != marker:
        raise ValueError('快照并非来自当前研究目录')
    data = root / DATA
    check_tree(source / 'data')
    if data.parent.resolve(strict=True) != data.parent or data.resolve() != data:
        raise ValueError('研究数据目录不可链接到其他位置')
    staging = Path(tempfile.mkdtemp(prefix='restore-', dir=data.parent)).resolve()
    if staging.parent != data.parent:
        raise ValueError('恢复暂存目录位置无效')
    previous = staging / 'previous'
    try:
        shutil.copytree(source / 'data', staging / 'incoming')
        if data.exists():
            data.rename(previous)
        try:
            (staging / 'incoming').rename(data)
        except OSError:
            if previous.exists():
                previous.rename(data)
            raise
    finally:
        if data.exists() or not previous.exists():
            shutil.rmtree(staging)
