from pathlib import Path
from threading import RLock
from functools import lru_cache
import hashlib
import os
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL_MODE = os.environ.get('VOICEPRINT_MODEL_MODE', 'TEST_ONLY')
if MODEL_MODE not in ('TEST_ONLY', 'SELF_TRAINED'):
    raise ValueError('VOICEPRINT_MODEL_MODE 只允许 TEST_ONLY 或 SELF_TRAINED')
MODEL_ID = 'speechbrain/spkrec-ecapa-voxceleb' if MODEL_MODE == 'TEST_ONLY' else 'self-trained/ecapa-r108-fix02'
_model = None
_lock = RLock()


@lru_cache(maxsize=1)
def model_version():
    # 仅对本系统推理目录中的模型计算指纹，不访问训练 Checkpoint。
    load_model()
    if MODEL_MODE == 'SELF_TRAINED':
        return _model.version
    digest = hashlib.sha256()
    for name in ('hyperparams.yaml', 'embedding_model.ckpt', 'mean_var_norm_emb.ckpt'):
        digest.update(name.encode())
        with (ROOT / 'models/ecapa' / name).open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block)
    return digest.hexdigest()


def normalize(x):
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(x)
    if not np.isfinite(x).all() or norm < 1e-8:
        raise ValueError('无效声纹特征')
    return x / norm


def load_model():
    global _model
    if MODEL_MODE == 'SELF_TRAINED':
        from .self_trained import SelfTrainedEncoder
        with _lock:
            if _model is None:
                configured = os.environ.get('VOICEPRINT_SELF_TRAINED_CONFIG')
                if not configured:
                    raise ValueError('SELF_TRAINED 需要独立推理配置')
                config = Path(configured).resolve()
                if not config.is_relative_to((ROOT / '.local/self-trained').resolve()):
                    raise ValueError('正式模型只允许使用本 worktree 的 .local/self-trained 副本')
                _model = SelfTrainedEncoder(config)
            return _model
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import LocalStrategy
    with _lock:
        if _model is None:
            _model = EncoderClassifier.from_hparams(
                source=MODEL_ID, savedir=str(ROOT / 'models/ecapa'),
                run_opts={'device': 'cpu'}, local_strategy=LocalStrategy.COPY)
        return _model


def embedding(x):
    import torch
    model = load_model()
    with _lock:
        with torch.inference_mode():
            result = model.encode_batch(torch.from_numpy(x).unsqueeze(0))
        return normalize(result.cpu().numpy())
