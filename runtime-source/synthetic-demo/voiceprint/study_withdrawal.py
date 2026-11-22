"""Withdrawal for the isolated, single-worker off/on study Web."""
import hashlib
import os
import re
import shutil
import sqlite3
from pathlib import Path

from fastapi import HTTPException

ALIAS = re.compile(r'([0-9a-f]{32})-(?:off|on)')


def digest(participant):
    return hashlib.sha256(participant.encode('ascii')).hexdigest()


class Withdrawal:
    def __init__(self, data):
        self.data = data
        self.ledger = Path(os.environ['VOICEPRINT_WITHDRAWAL_LEDGER']).resolve(strict=True)
        if self.ledger.is_relative_to(data):
            raise ValueError('撤回台账须独立于可恢复的数据目录')

    def attach(self, db):
        db.execute('ATTACH DATABASE ? AS revocations', (self.ledger.as_uri() + '?mode=rw',))
        if any(db.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0] != 'delete'
               for schema in ('main', 'revocations')):
            raise sqlite3.OperationalError('withdrawal requires DELETE journal mode')
        db.execute('PRAGMA main.synchronous=FULL')
        db.execute('PRAGMA revocations.synchronous=FULL')
        db.execute('PRAGMA main.secure_delete=ON')
        db.execute('SELECT digest FROM revocations.withdrawn LIMIT 0')

    def check_enrollment(self, db, alias, denoise):
        match = ALIAS.fullmatch(alias)
        if not match or alias.endswith('-on') != denoise:
            raise HTTPException(422, '请使用同一随机参与者编号及对应的 -off/-on 后缀')
        self.attach(db)
        if self.revoked(db, match[1]):
            raise HTTPException(410, '此参与者已撤回')

    @staticmethod
    def revoked(db, participant):
        return db.execute('SELECT 1 FROM revocations.withdrawn WHERE digest=?',
                          (digest(participant),)).fetchone() is not None

    @staticmethod
    def purge(db, alias, managed_path):
        old = db.execute('SELECT embedding_path FROM voiceprint_templates WHERE user_id=?', (alias,)).fetchone()
        if old:
            path = str(managed_path(old['embedding_path']))
            db.execute('INSERT OR IGNORE INTO pending_template_deletions VALUES(?)', (path,))
        for table in ('voiceprint_templates', 'records', 'users'):
            db.execute(f'DELETE FROM {table} WHERE user_id=?', (alias,))

    def withdraw(self, db, alias, version, managed_path):
        match = ALIAS.fullmatch(alias)
        if not match:
            raise HTTPException(404, '找不到已关联的研究参与者')
        participant = match[1]
        self.attach(db)
        db.execute('BEGIN IMMEDIATE')
        if self.revoked(db, participant):
            return
        row = db.execute('SELECT template_version FROM users WHERE user_id=?', (alias,)).fetchone()
        if not row:
            raise HTTPException(404, '用户不存在')
        if row['template_version'] != version:
            raise HTTPException(409, '模板版本已变化，请刷新后重试')
        db.execute('INSERT INTO revocations.withdrawn(digest) VALUES(?)', (digest(participant),))
        for arm in ('off', 'on'):
            self.purge(db, f'{participant}-{arm}', managed_path)
        db.execute("INSERT INTO audit_events(operation,error_type,status_code) VALUES('participant_withdrawal','NONE',200)")

    def reconcile(self, db, managed_path):
        self.attach(db)
        db.execute('BEGIN IMMEDIATE')
        forgotten = {r[0] for r in db.execute('SELECT digest FROM revocations.withdrawn')}
        for row in db.execute('SELECT user_id FROM users UNION SELECT user_id FROM records '
                              'UNION SELECT user_id FROM voiceprint_templates').fetchall():
            match = ALIAS.fullmatch(row[0] or '')
            if not match:
                raise RuntimeError('恢复副本含未关联的历史身份，禁止开放研究服务')
            if digest(match[1]) in forgotten:
                self.purge(db, row[0], managed_path)
        return forgotten

    def cleanup_cache(self, forgotten):
        pending = False
        for token in forgotten:
            if not re.fullmatch(r'[a-f0-9]{64}', token):
                raise RuntimeError('撤回台账摘要无效')
            root = self.data / 'participant-cache'
            target = root / token
            try:
                if root.resolve() != root or target.resolve() != target:
                    raise OSError('unsafe cache location')
                if target.exists():
                    shutil.rmtree(target)
            except OSError:
                pending = True
        return pending
