"""Content-addressed, transactionally published local embedding cache."""
import hashlib,json,sqlite3
from pathlib import Path
import numpy as np


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def vector(value):
    x=np.asarray(value,dtype='<f4')
    if x.shape!=(192,) or not np.isfinite(x).all() or np.linalg.norm(x)==0:
        raise ValueError('Invalid 192-dimensional embedding')
    return x


class EmbeddingCache:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path,timeout=30)
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, binding TEXT NOT NULL, value BLOB NOT NULL, checksum TEXT NOT NULL)')

    def get(self,binding):
        row=self.db.execute('SELECT binding,value,checksum FROM embeddings WHERE key=?',(fingerprint(binding),)).fetchone()
        if row is None:return None
        if row[0]!=canonical(binding) or hashlib.sha256(row[1]).hexdigest()!=row[2]:
            raise ValueError('Corrupt or mismatched cached embedding')
        return vector(np.frombuffer(row[1],dtype='<f4')).copy()

    def put(self,binding,value):
        x=vector(value);data=x.tobytes()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO embeddings VALUES (?,?,?,?)',
                            (fingerprint(binding),canonical(binding),data,hashlib.sha256(data).hexdigest()))
            existing=self.get(binding)
            if not np.array_equal(existing,x):raise ValueError('Conflicting writers for identical inference binding')

    def close(self):self.db.close()
