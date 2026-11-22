"""Frozen E14 assets and explicitly approved Dev policy bindings."""
import hashlib
import json
import math
from pathlib import Path

from zrs_study_policy import ARMS, E14_SHA

TRAINING_SHA = '81aa46aa7b4d9b14715370c60207f8a38c6a07d6a0fc0448597aa40662a2dab9'
SOURCE_SHA = {
    'off': '74a54f05e877bd7ce6178fefc815c7fd87e901fd698a5d062a96cb641ef4ed59',
    'on': '3ba41ef4b0918f413bbee9a31ac32e647e0f461bc3882d3e09b102c597621181',
}


def read_pinned_json(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f'JSON SHA256 不匹配: {path.name}')
    return json.loads(raw)


def check_assets(config_path, asset_root):
    config_path = config_path.resolve(strict=True)
    if not config_path.is_relative_to(asset_root):
        raise ValueError('模型配置须位于研究目录下的 .local/self-trained/e14-assets')
    config = json.loads(config_path.read_bytes())
    for key, expected in (('checkpoint', E14_SHA), ('training_config', TRAINING_SHA)):
        if config[key + '_sha256'] != expected:
            raise ValueError(f'冻结 E14 资产声明 SHA256 不匹配: {key}')
        path = (config_path.parent / config[key]).resolve(strict=True)
        if path.parent != config_path.parent or not path.is_file():
            raise ValueError(f'E14 资产必须是配置同目录的本地文件: {key}')
        # The archived encoder checks the actual file hashes before deserialization.
    return config_path


def approved_policies(approval_path, model_version, processing_version):
    """Bind immutable history to the running chain using an owner-approved receipt."""
    if approval_path is None:
        return {}
    approval = json.loads(approval_path.read_bytes())
    if (approval.get('kind') != 'owner-approved-e14-dev-binding'
            or not str(approval.get('approval_id') or '').strip()
            or not str(approval.get('policy_version') or '').strip()):
        raise ValueError('缺少负责人批准或策略版本')
    base = approval_path.parent
    receipt = read_pinned_json(base / approval['equivalence_receipt'], approval['equivalence_sha256'])
    if (receipt.get('status'), receipt.get('model_sha256'), receipt.get('training_config_sha256'),
        receipt.get('model_version')) != ('PASS', E14_SHA, TRAINING_SHA, model_version):
        raise ValueError('等价性证据与当前冻结模型或配置不一致')
    result = {}
    for arm, enabled in ARMS.items():
        source = read_pinned_json(base / approval['historical_policies'][arm], SOURCE_SHA[arm])
        evidence = receipt['arms'][arm]
        limits = [evidence[name] for name in ('max_feature_diff', 'max_template_diff', 'max_score_diff')]
        if (evidence.get('preprocessing_version') != processing_version(enabled)
                or evidence.get('historical_policy_sha256') != SOURCE_SHA[arm]
                or evidence.get('decision_matches') is not True
                or not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1e-8 for v in limits)
                or source['enrollment_denoise'] is not enabled or source['probe_denoise'] is not enabled):
            raise ValueError(f'{arm} 等价性证据、阈值来源或预处理版本不匹配')
        result[enabled] = {'threshold': source['operating_threshold'],
                           'policy_version': approval['policy_version'],
                           'source_sha256': SOURCE_SHA[arm]}
    return result
