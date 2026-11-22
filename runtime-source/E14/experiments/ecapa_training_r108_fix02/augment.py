"""Seeded augmentation interfaces. Array tests do not qualify real assets."""
import hashlib
import json
from fractions import Fraction

import numpy as np
import torch
from scipy.signal import fftconvolve, resample_poly


def sample_seed(seed, epoch, sample_id, operation):
    key = json.dumps([int(seed), int(epoch), str(sample_id), operation], separators=(',', ':'))
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], 'big')


def operation_seeds(name, seed):
    streams = {'musan': ['probability', 'category', 'asset', 'offset', 'snr'],
               'rir': ['probability', 'asset'], 'speed': ['probability', 'factor'],
               'volume': ['probability', 'gain'],
               'specaugment': ['probability', 'time', 'frequency']}
    return {stream: sample_seed(seed, 0, name, stream) for stream in streams[name]}


def wave_state(x, rate):
    x = np.asarray(x)
    finite = bool(np.isfinite(x).all())
    valid = finite and x.size > 0
    return dict(samples=int(x.size), shape=list(x.shape), sample_rate=rate,
                dtype=str(x.dtype), finite=finite,
                rms=float(np.sqrt(np.mean(x.astype(np.float64)**2))) if valid else None,
                peak=float(np.max(np.abs(x))) if valid else None,
                zero_fraction=float(np.mean(x == 0)) if valid else None,
                sha256=hashlib.sha256(x.tobytes()).hexdigest())


def validate_wave(x, rate, policy):
    x = np.asarray(x)
    state = wave_state(x, rate)
    if rate != 16000 or x.ndim != 1 or not x.size or not state['finite']:
        raise ValueError('Require finite nonempty mono 16kHz waveform')
    if state['rms'] <= policy['silence_rms']:
        raise ValueError('Silent/near-silent waveform')
    if state['peak'] > 1 or np.mean(np.abs(x) >= policy['clip_level']) > policy['max_clip_fraction']:
        raise ValueError('Out-of-range/clipped waveform')
    return state


def musan(x, seeds, params, asset):
    noise = np.asarray(asset, dtype=np.float64)
    if noise.ndim != 1 or not noise.size or not np.isfinite(noise).all():
        raise ValueError('Invalid noise')
    period=len(noise)
    mode=params['pairing_mode']
    if mode not in ('reject','loop','constrained') or params['allow_loop']!=(mode!='reject'):
        raise ValueError('Inconsistent explicit noise policy')
    minimum=params['minimum_period_samples'];maximum=params['maximum_repeats']
    if type(minimum) is not int or minimum<1 or type(maximum) is not int or maximum<1:
        raise ValueError('Invalid noise repetition limits')
    repeats=int(np.ceil(len(x)/period))
    if period<minimum or repeats>maximum:
        raise ValueError('Noise violates explicit period/repetition constraints')
    repeated = period < len(x)
    phase_mode=params['phase_mode']
    if phase_mode not in ('historical_remainder','repeat_zero','full_period'):
        raise ValueError('Explicit noise phase mode required')
    if repeated:
        if mode=='reject':
            raise ValueError('Noise shorter than speech; looping not approved')
        if phase_mode=='full_period':
            offset=int(np.random.default_rng(seeds['offset']).integers(period))
            noise=np.tile(noise,int(np.ceil((len(x)+offset)/period)))
        else:
            noise = np.tile(noise, int(np.ceil(len(x) / len(noise))))
            offset=0 if phase_mode=='repeat_zero' else int(np.random.default_rng(seeds['offset']).integers(len(noise)-len(x)+1))
    else:
        offset = int(np.random.default_rng(seeds['offset']).integers(len(noise) - len(x) + 1))
    buffer_period_copies=int(np.ceil(len(noise)/period))
    noise = noise[offset:offset + len(x)]
    ps, pn = np.mean(x.astype(np.float64)**2), np.mean(noise**2)
    if pn <= 0:
        raise ValueError('Zero-power noise')
    target = float(np.random.default_rng(seeds['snr']).uniform(*params['snr_db']))
    gain = float(np.sqrt(ps / (pn * 10**(target / 10))))
    y = x + gain * noise
    scale = 1 / max(1., float(np.max(np.abs(y))))
    return (y * scale).astype(np.float32), dict(target_snr_db=target,
        measured_snr_db=float(10*np.log10(ps/(gain*gain*pn))),
        energy_scope='full_segment_training', offset_samples=offset,
        repeated=repeated, gain=gain, common_scale=scale,loop_period_samples=period,repeat_count=repeats,
        pairing_mode=mode,phase_mode=phase_mode,phase_samples=offset%period,buffer_period_copies=buffer_period_copies,
        repeat_limit_semantics='ceil(target_samples/period); phase buffer copies separately recorded',
        join_jump=abs(float(asset[-1])-float(asset[0])) if repeated else 0.,
        join_positions=[i for i in range(period-offset,len(x),period) if i>0] if repeated else [],
        seeds={key: seeds[key] for key in ['offset', 'snr']})


def rir(x, seeds, params, asset):
    h = np.asarray(asset, dtype=np.float64)
    if h.ndim != 1 or not h.size or not np.isfinite(h).all() or np.sum(h*h) <= 0:
        raise ValueError('Invalid RIR')
    if params['normalization'] != 'l2' or params['alignment'] != 'causal' or params['length'] != 'input':
        raise ValueError('R3 requires explicit l2/causal/input RIR policy; peak alignment is rejected')
    h = h / np.linalg.norm(h)
    strongest = int(np.argmax(np.abs(h)))
    full = fftconvolve(x, h)
    y = full[:len(x)]
    scale = 1 / max(1., float(np.max(np.abs(y))))
    return (y * scale).astype(np.float32), dict(rir_samples=len(h), alignment='causal',
        alignment_offset_samples=0, strongest_rir_sample=strongest, discarded_prefix_samples=0,
        truncated_tail_samples=len(full)-len(y),
        truncated_tail_energy_fraction=float(np.sum(full[len(y):]**2)/np.sum(full**2)),
        common_scale=scale)


def speed(x, seeds, params, asset=None):
    factor = float(np.random.default_rng(seeds['factor']).choice(params['factors']))
    if factor <= 0 or not np.isfinite(factor):
        raise ValueError('Invalid speed factor')
    ratio = Fraction(1 / factor).limit_denominator(10000)
    y = resample_poly(x, ratio.numerator, ratio.denominator).astype(np.float32)
    # augment_wave validates the original input before resampling. Only the
    # resampled waveform gets deterministic, uniform headroom; never hard clip.
    before_peak = float(np.max(np.abs(y)))
    before_sha = hashlib.sha256(y.tobytes()).hexdigest()
    target = float(np.nextafter(np.float32(0.995), np.float32(0)))
    scale = target / before_peak if before_peak > 0.995 else 1.0
    if scale < 1.0:
        y = (y.astype(np.float64) * scale).astype(np.float32)
    return y, dict(factor=factor, resample_up=ratio.numerator, resample_down=ratio.denominator,
                   peak_guard_version='P4_ROBUST_10_UNIFORM_SCALE_V1',
                   peak_before_scale=before_peak, peak_after_scale=float(np.max(np.abs(y))),
                   peak_scale=scale, target_peak=0.995,
                   resampled_sha256_before_scale=before_sha,
                   resampled_sha256_after_scale=hashlib.sha256(y.tobytes()).hexdigest(),
                   seeds={'factor': seeds['factor']})


def volume(x, seeds, params, asset=None):
    db = float(np.random.default_rng(seeds['gain']).uniform(*params['gain_db']))
    return (x * 10**(db/20)).astype(np.float32), dict(gain_db=db,
        scope='whole_recording_gain', snr_invariant_for_same_window=True, seeds={'gain': seeds['gain']})


def augment_wave(name, x, rate, label, seed, probability, params, policy, asset=None):
    """Return waveform and audit event, preserving the supplied speaker label."""
    event = dict(operation=name, label_before=int(label), label_after=int(label),
                 seed=int(seed), probability=probability, before=wave_state(x, rate))
    try:
        if not 0 <= probability <= 1:
            raise ValueError('Invalid probability')
        validate_wave(x, rate, policy)
        seeds = operation_seeds(name, seed)
        event['seeds'] = {'probability': seeds['probability']}
        event['draw'] = float(np.random.default_rng(seeds['probability']).random())
        event['executed'] = event['draw'] < probability
        y = np.asarray(x, dtype=np.float32).copy()
        if event['executed']:
            y, details = {'musan': musan, 'rir': rir, 'speed': speed, 'volume': volume}[name](
                y, seeds, params, asset)
            event['seeds'].update(details.pop('seeds', {}))
            event.update(details)
        event['after'] = wave_state(y, rate)
        validate_wave(y, rate, policy)
        event['status'] = 'PASS'
        return y, event
    except (ValueError, KeyError, OverflowError) as exc:
        event.update(status='ERROR', error=str(exc))
        raise ValueError(json.dumps(event, allow_nan=False)) from exc


def specaugment(features, label, seed, probability, params):
    """Mask one [time, 80] feature matrix; never reinterpret it as PCM."""
    if features.ndim != 2 or features.shape[1] != 80 or not torch.isfinite(features).all():
        raise ValueError('Require finite [time,80] features')
    if not 0 <= probability <= 1:
        raise ValueError('Invalid probability')
    y = features.clone()
    seeds = operation_seeds('specaugment', seed)
    event = dict(operation='specaugment', seed=int(seed), probability=probability,
                 label_before=int(label), label_after=int(label), before_shape=list(y.shape),
                 before_nonzero=int(torch.count_nonzero(y)), masks=[])
    event['seeds'] = {'probability': seeds['probability']}
    event['draw'] = float(np.random.default_rng(seeds['probability']).random())
    event['executed'] = event['draw'] < probability
    if event['executed']:
        for axis, key in [(0, 'time'), (1, 'frequency')]:
            rng = np.random.default_rng(seeds[key])
            event['seeds'][key] = seeds[key]
            count, maximum = params[key + '_masks'], params[key + '_max_width']
            if count < 0 or not 0 <= maximum < y.shape[axis]:
                raise ValueError('Mask bounds exceed feature shape')
            for _ in range(count):
                width = int(rng.integers(maximum + 1))
                start = int(rng.integers(y.shape[axis] - width + 1))
                if axis == 0:
                    y[start:start + width, :] = 0
                else:
                    y[:, start:start + width] = 0
                event['masks'].append(dict(axis=axis, start=start, width=width))
    if not torch.isfinite(y).all() or not torch.count_nonzero(y):
        raise ValueError('Invalid/all-masked features')
    event.update(after_shape=list(y.shape), after_nonzero=int(torch.count_nonzero(y)), status='PASS')
    return y, event
