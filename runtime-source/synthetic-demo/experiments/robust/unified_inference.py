"""Three frozen self-trained ECAPA models; no training or legacy model loader."""
import hashlib
from importlib.metadata import version
from pathlib import Path
import numpy as np
from .eval_readiness import MODEL_SHA,SNR_DB
from .inference_cache import fingerprint,vector

VERSION='SELFTRAINED_CENTER3_CMN_FP32_V1'


def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def condition(category='clean',snr_db=None,source_sha256=None,offset=None,mask_sha256=None,recipe_sha256=None):
    if category=='clean':
        if any(x is not None for x in (snr_db,source_sha256,offset,mask_sha256,recipe_sha256)):
            raise ValueError('Clean condition cannot contain interference')
    elif (category not in ('noise','music','speech4') or type(snr_db) is not int or snr_db not in SNR_DB
          or type(offset) is not int or offset<0
          or any(not isinstance(s,str) or len(s)!=64 for s in (source_sha256,mask_sha256,recipe_sha256))):
        raise ValueError('Unbound interference condition')
    return dict(category=category,snr_db=snr_db,source_sha256=source_sha256,offset=offset,
                mask_sha256=mask_sha256,recipe_sha256=recipe_sha256,denoise=False)


def cache_binding(model_sha,audio_sha,start,wave,preprocessing,interference):
    if model_sha not in MODEL_SHA.values() or len(audio_sha)!=64 or type(start) is not int or start<0:
        raise ValueError('Invalid model/audio/crop identity')
    if interference!=condition(**{k:v for k,v in interference.items() if k!='denoise'}) or interference['denoise'] is not False:
        raise ValueError('Unexpected interference or denoise policy')
    x=np.asarray(wave,dtype='<f4')
    if x.shape!=(48000,) or not np.isfinite(x).all() or abs(x).max()>1:
        raise ValueError('Expected finite 16kHz mono three-second waveform')
    return dict(version=VERSION,model_sha256=model_sha,audio_sha256=audio_sha,crop_start=start,
                samples=48000,wave_sha256=hashlib.sha256(x.tobytes()).hexdigest(),
                preprocessing=preprocessing,condition=interference)


class SelfTrained:
    def __init__(self,name,checkpoint,configuration,cache,device='cpu'):
        if name not in MODEL_SHA or digest(checkpoint)!=MODEL_SHA[name]:raise ValueError('Frozen model SHA mismatch')
        import torch,yaml
        from experiments.ecapa_training_r108_fix02.model import ECAPATraining
        cfg=yaml.safe_load(Path(configuration).read_text(encoding='utf-8'))
        saved=torch.load(checkpoint,map_location='cpu',weights_only=False);context=saved['context']
        expected_epoch=13 if name=='baseline_best14' else 1
        expected_run={'baseline_best14':'P4-03B-BASELINE-20261105-01','robust_r2_best2':'P4-ROBUST-20261107-08-R2',
                      'baseline_initialized_finetune_best2':'P4-ROBUST-20261109-14'}[name]
        if (saved['epoch']!=expected_epoch or context['run_id']!=expected_run
                or context['config_sha256']!=digest(configuration)
                or len(context['labels'])!=800 or cfg['fbank_bins']!=80 or cfg['embedding_dim']!=192):
            raise ValueError('Checkpoint model/configuration/label identity mismatch')
        if any(v.is_floating_point() and not torch.isfinite(v).all() for v in saved['model'].values()):
            raise ValueError('Nonfinite checkpoint state')
        self.model=ECAPATraining(cfg['model']).float();self.model.load_state_dict(saved['model'],strict=True)
        if any(not torch.equal(v,saved['model'][k]) for k,v in self.model.state_dict().items()):raise ValueError('Incomplete state loading')
        self.model.to(device).eval();self.sha=MODEL_SHA[name];self.cache=cache;self.device=device;self.context=context
        self.preprocessing=dict(version=VERSION,model_configuration=cfg['model'],device=device,
                                torch_version=torch.__version__,speechbrain_version=version('speechbrain'),
                                crop='center_48000',sample_rate=16000)
        folders=('ecapa_training_r108_fix02','robust');base=Path(__file__).resolve().parents[1]
        self.preprocessing['source_sha256']={str(p.relative_to(base)):digest(p) for folder in folders
                    for p in (base/folder).glob('*.py') if p.name in ('model.py','deterministic_padding.py','unified_inference.py','inference_cache.py','eval_activity.py')}

    def embed(self,wave,audio_sha,start,interference=None):
        import torch
        bound=cache_binding(self.sha,audio_sha,start,wave,self.preprocessing,interference or condition())
        cached=self.cache.get(bound)
        if cached is not None:return cached
        self.model.eval()
        with torch.no_grad(),torch.autocast(device_type=torch.device(self.device).type,enabled=False):
            x=torch.from_numpy(np.asarray(wave,dtype=np.float32).copy())[None].to(self.device)
            f=self.model.features(x);e=self.model.encoder(f-f.mean(dim=1,keepdim=True))
            result=vector(e.float().reshape(-1).cpu().numpy())
        self.cache.put(bound,result);return result


def normalized(x):
    x=np.asarray(x,dtype=np.float64);n=np.linalg.norm(x)
    if x.shape!=(192,) or not np.isfinite(x).all() or not np.isfinite(n) or n<=0:raise ValueError('Invalid speaker embedding/template')
    return x/n


def templates(model_sha,embeddings,roster):
    if model_sha not in MODEL_SHA.values():raise ValueError('Unknown template model')
    if len({r['audio_ref'] for r in roster})!=len(roster):raise ValueError('Duplicate recording')
    result={}
    for speaker in sorted({r['speaker'] for r in roster}):
        rows=[r for r in roster if r['speaker']==speaker]
        if len(rows)!=15 or sum(r['purpose']=='enroll' for r in rows)!=5 or sum(r['purpose']=='probe' for r in rows)!=10:
            raise ValueError('Expected five Clean enrollments and ten probes')
        result[speaker]=normalized(np.mean([normalized(embeddings[r['audio_ref']]) for r in rows if r['purpose']=='enroll'],axis=0))
    return dict(model_sha256=model_sha,vectors=result)


def score(model_sha,bank,embeddings,roster,trials):
    if bank['model_sha256']!=model_sha:raise ValueError('Cross-model template use forbidden')
    expected={(s,r['speaker'],r['audio_ref']) for r in roster if r['purpose']=='probe' for s in bank['vectors']}
    seen=set();result=[]
    for t in trials:
        key=(t['enroll_speaker'],t['test_speaker'],t['test_audio'])
        if key not in expected or key in seen or int(t['label'])!=int(key[0]==key[1]):raise ValueError('Invalid trial identity/label')
        seen.add(key);result.append(dict(label=int(t['label']),score=float(np.clip(np.dot(bank['vectors'][key[0]],normalized(embeddings[key[2]])),-1,1))))
    if seen!=expected:raise ValueError('Incomplete trial coverage')
    return result


def require_scope(scope,configuration=None,authorization=None,materials=None,policies=None):
    if scope=='dev':return
    if scope!='eval' or not all(isinstance(v,dict) for v in (configuration,authorization,materials,policies)):
        raise PermissionError('Formal Eval requires independent frozen approval')
    a=authorization;c=configuration;m=materials
    ids={'noise-free-sound-0260','music-fma-0112','speech-librivox-0024','speech-librivox-0172','speech-librivox-0063','speech-librivox-0060'}
    from .eval_readiness import plan
    if (a.get('execute_eval') is not True or not a.get('owner_approval_id')
            or a.get('configuration_sha256')!=fingerprint(c) or a.get('materials_sha256')!=fingerprint(m)
            or c.get('models')!=MODEL_SHA or c.get('conditions')!=plan()['conditions'] or c.get('denoise') is not False
            or {r.get('id') for r in m.get('items',[])}!=ids or len(m['items'])!=6
            or c.get('material_sources')!={r['id']:r.get('sha256') for r in m['items']}
            or not all(r.get('eval_approved') is True and r.get('listened') is True
                       and r.get('known_source_isolation') is True and r.get('license_evidence_sha256')
                       and r.get('sha256') and r.get('approved_at') for r in m['items'])
            or set(policies)!=set(MODEL_SHA)
            or any(policies[k].get('model_sha256')!=v or policies[k].get('calibration_split')!='dev'
                   or c.get('threshold_policies',{}).get(k)!=fingerprint(policies[k]) for k,v in MODEL_SHA.items())):
        raise PermissionError('Missing or mismatched Eval/model/material/threshold approval')


def read_center(row,root,*,scope,grant=None):
    require_scope(scope,**(grant or {}))
    import soundfile as sf
    root=Path(root).resolve();path=(root/row['path']).resolve()
    if not path.is_relative_to(root) or digest(path)!=row['audio_ref']:raise ValueError('Audio path/content mismatch')
    x,rate=sf.read(path,dtype='float32')
    if rate!=16000 or x.ndim!=1 or len(x)<48000 or not np.isfinite(x).all():raise ValueError('Invalid input audio')
    start=(len(x)-48000)//2;return x[start:start+48000].copy(),start
