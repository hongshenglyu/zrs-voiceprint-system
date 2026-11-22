"""Opt-in self-trained Web path; legacy Web behavior remains the default."""
import hashlib
from pathlib import Path
import numpy as np
from .denoise import settings,identity


# P4 enrollment guards do not change accepted-wave scoring. Keep the P3
# scoring provenance for existing templates and their exact policy bindings.
def scoring_sources():
    return dict(zip(('web_source_sha256', 'app_source_sha256'),
                    Path(__file__).with_name('p3-scoring-sources.txt').read_text().splitlines()))


def processing_version(model_version,enable):
    return identity(dict(model_version=model_version,crop='center_48000',normalization='float64_l2_mean_l2',
                         denoise=settings(enable),adapter_source_sha256=hashlib.sha256(Path(__file__).with_name('self_trained.py').read_bytes()).hexdigest(),
                         **scoring_sources()))


def embedding(wave):
    import torch
    from .model import load_model,MODEL_MODE,_lock
    if MODEL_MODE!='SELF_TRAINED':raise ValueError('降噪Web模式只支持已绑定的自训练模型')
    adapter=load_model()
    with _lock,torch.inference_mode(),torch.autocast(device_type='cpu',enabled=False):
        x=torch.from_numpy(np.asarray(wave,dtype=np.float32).copy())[None]
        f=adapter.network.features(x)
        result=adapter.network.encoder(f-f.mean(dim=1,keepdim=True)).float().reshape(-1).cpu().numpy()
    if result.shape!=(192,) or not np.isfinite(result).all():raise ValueError('自训练模型输出无效')
    return result
