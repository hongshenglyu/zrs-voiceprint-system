"""CPU inference from independently copied, SHA-verified training artifacts.

Training checkpoints contain NumPy RNG pickle objects: accept only trusted local
copies with an independently verified SHA256; never load uploads or remote URLs.
"""
import hashlib
import json
from pathlib import Path
import re
from importlib.metadata import version

import torch
import yaml


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


class SelfTrainedEncoder:
    def __init__(self, config_path):
        from experiments.ecapa_training_r108_fix02.model import ECAPATraining
        config_path = Path(config_path).resolve()
        config = json.loads(config_path.read_text(encoding='utf-8'))
        paths = {}
        for name in ('checkpoint', 'training_config'):
            path = (config_path.parent / config[name]).resolve()
            expected = config[name + '_sha256']
            if path.parent != config_path.parent or not re.fullmatch('[0-9a-f]{64}', expected):
                raise ValueError('模型副本必须与推理配置同目录，且提供 SHA256')
            if sha256(path) != expected:
                raise ValueError(name + ' SHA256 不匹配')
            paths[name] = path
        training = yaml.safe_load(paths['training_config'].read_text(encoding='utf-8'))
        model_config = training['model']
        # Same constructor, parameters, feature defaults and padding as training.
        self.network = ECAPATraining(model_config).cpu()
        state = torch.load(paths['checkpoint'], map_location='cpu', weights_only=False)
        if (state.get('format') != 'P4_ECAPA_RANDOM_TRAINING_B04'
                or state.get('context', {}).get('config_sha256') != config['training_config_sha256']):
            raise ValueError('Checkpoint 格式或训练配置来源不匹配')
        self.network.load_state_dict(state['model'], strict=True)
        self.network.eval()
        self.network.requires_grad_(False)
        self.checkpoint_sha256 = config['checkpoint_sha256']
        identity = dict(checkpoint_sha256=config['checkpoint_sha256'],
                        training_config_sha256=config['training_config_sha256'],
                        model=model_config, sample_rate=16000, fbank_bins=80,
                        centering='features-features.mean(dim=1,keepdim=True)',
                        embedding_dim=192, normalization='L2', adapter='r108_fix02-v1',
                        speechbrain_version=version('speechbrain'))
        self.version = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def encode_batch(self, waves):
        if waves.device.type != 'cpu' or waves.ndim != 2 or not torch.isfinite(waves).all():
            raise ValueError('推理输入必须为 CPU 上的有限 16kHz 单声道波形批次')
        with torch.inference_mode():
            features = self.network.features(waves)
            features = features - features.mean(dim=1, keepdim=True)
            result = self.network.encoder(features).squeeze(1)
            norms = result.norm(dim=-1, keepdim=True)
            if result.shape != (waves.shape[0], 192) or not torch.isfinite(result).all() or (norms < 1e-8).any():
                raise ValueError('自训练模型输出无效')
            return result / norms
