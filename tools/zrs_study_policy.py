"""Study policy descriptors. Candidate policy inspection is offline."""
import hashlib
import json
import math
import re

E14_SHA = '3570c7725a27a392efec7e5824857d50701f0f3c0fded68a78d0e3b82439324d'
DEMO_SHA = '1' * 64
ARMS = {'off': False, 'on': True}


def synthetic_policy(web, model_version, enabled):
    return dict(kind='synthetic-demo', policy_version='p2-synthetic-v1', model=web.MODEL_ID,
                model_version=model_version, model_sha256=DEMO_SHA, calibration_split='dev',
                dev_manifest_sha256='2' * 64, genuine_count=1, impostor_count=1,
                denoise=enabled, preprocessing_version=web.processing_version(enabled),
                threshold_source='synthetic fixture only', **{'global': .5})


def install_synthetic_policy(web, model_version):
    for arm, enabled in ARMS.items():
        path = web.DATA / f'thresholds-denoise-{arm}.json'
        policy = synthetic_policy(web, model_version, enabled)
        if path.exists():
            if json.loads(path.read_bytes()) != policy:
                raise ValueError(f'{arm} 合成策略来源、阈值或版本绑定不一致')
        elif web.DB.exists():
            raise ValueError(f'{arm} 持久研究策略缺失')
        else:
            with path.open('x', encoding='utf-8') as stream:
                json.dump(policy, stream, sort_keys=True)


def inspect_candidate(path):
    raw = path.read_bytes()
    policy = json.loads(raw)
    if (policy['kind'], policy['protocol_version'], policy['approval_status'], policy['model_sha256']) != (
            'candidate-research', 'v2', 'PENDING', E14_SHA):
        raise ValueError('仅接受待审批 v2 研究候选配置及冻结 E14 模型绑定')
    if not isinstance(policy['policy_version'], str) or not policy['policy_version'].strip():
        raise ValueError('缺少策略版本')
    if set(policy['arms']) != ARMS.keys():
        raise ValueError('必须配置 off/on 两臂')
    versions = []
    for name, enabled in ARMS.items():
        arm = policy['arms'][name]
        if arm['denoise'] is not enabled:
            raise ValueError(f'{name} 降噪臂绑定错误')
        binding, threshold, source = (arm[key] for key in ('preprocessing_version', 'threshold', 'threshold_source'))
        if binding is not None and (not isinstance(binding, str) or not re.fullmatch(r'[0-9a-f]{64}', binding)):
            raise ValueError(f'{name} 预处理版本无效')
        if threshold is not None and (type(threshold) not in (float, int) or not math.isfinite(threshold)
                                      or not -1 <= threshold <= 1 or not binding
                                      or not isinstance(source, str) or not source.strip()):
            raise ValueError(f'{name} 阈值缺少有效数值、来源或预处理绑定')
        versions.append(binding)
    if versions[0] is not None and versions[0] == versions[1]:
        raise ValueError('两臂须使用独立预处理版本')
    return {'status': 'PENDING', 'policy_version': policy['policy_version'],
            'sha256': hashlib.sha256(raw).hexdigest(), 'runtime_enabled': False}
