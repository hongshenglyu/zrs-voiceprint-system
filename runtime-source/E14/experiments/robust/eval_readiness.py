"""Readiness contract only; deliberately contains no audio/model execution entry."""
SNR_DB = (25, 20, 15, 10, 5, 0)
MODEL_SHA = {
    'baseline_best14': '3570c7725a27a392efec7e5824857d50701f0f3c0fded68a78d0e3b82439324d',
    'robust_r2_best2': '32ef9575ee3e59cd69d73b0a3f83555a4fc21d38afe9f61d6bbdb3fd96f258d3',
    'baseline_initialized_finetune_best2': 'a58bfc8c16f628d2e9e029dae6b780718a240186d40049049e988aae335dde12',
}


def plan():
    return dict(formal_eval_authorized=False, denoise=False, models=MODEL_SHA.copy(),
                conditions=[dict(category=c, snr_db=s) for c in ('clean','noise','music','speech4')
                            for s in ((None,) if c=='clean' else SNR_DB)],
                sample_rate=16000, crop_samples=48000, fbank_bins=80, embedding_dimension=192,
                crop='fixed_center', enrollment='5 clean normalized embeddings; mean then L2',
                score='cosine; genuine label=1', enrollments=735, probes=1470,
                trials=dict(genuine=1470, impostor=214620, total=216090),
                snr_power='frozen Clean activity mask; record activity and full-segment SNR',
                offset='same probe/category source and offset at all six SNRs',
                inference='same self-trained model.features/encoder; eval/no_grad, FP32, CMN then L2',
                threshold='model-specific frozen Dev policy; no Eval calibration')


def require_execution(*, authorized, materials_approved, bindings_verified, entry_verified):
    if not all(x is True for x in (authorized,materials_approved,bindings_verified,entry_verified)):
        raise PermissionError('Formal Eval requires separate authorization and complete frozen readiness')


def fixed_trials(roster):
    """Metadata-only Cartesian protocol, independent of model scores."""
    speakers=sorted({r['speaker_id'] for r in roster})
    if len(speakers)!=147 or len({r['sha256'] for r in roster})!=2205:
        raise ValueError('Expected frozen 147 x (5 enroll + 10 probe) roster')
    for speaker in speakers:
        rows=[r for r in roster if r['speaker_id']==speaker]
        if (len(rows)!=15 or any(r['split']!='eval' for r in rows)
                or sum(r['purpose']=='enroll' for r in rows)!=5
                or sum(r['purpose']=='probe' for r in rows)!=10):
            raise ValueError('Invalid fixed enrollment/probe membership')
    probes=sorted((r for r in roster if r['purpose']=='probe'),key=lambda r:(r['speaker_id'],r['sha256']))
    for probe in probes:
        for speaker in speakers:
            yield dict(enroll_speaker=speaker,test_speaker=probe['speaker_id'],
                       test_audio=probe['sha256'],label=int(speaker==probe['speaker_id']))
