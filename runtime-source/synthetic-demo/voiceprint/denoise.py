"""Pinned CPU spectral gating on an already frozen three-second window."""
import hashlib,json,time
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path
import numpy as np

VERSION='P4_DENOISE_01_CENTER3_NONSTATIONARY_V1'
PARAMETERS=dict(stationary=False,prop_decrease=.7,time_constant_s=2.,freq_mask_smooth_hz=500,
                time_mask_smooth_ms=50,thresh_n_mult_nonstationary=2,sigmoid_slope_nonstationary=10,
                n_std_thresh_stationary=1.5,tmp_folder=None,chunk_size=600000,padding=30000,
                n_fft=1024,win_length=1024,hop_length=256,clip_noise_stationary=True,
                use_tqdm=False,n_jobs=1,use_torch=False,device='cpu',y_noise=None)


def settings(enable):
    if type(enable) is not bool:raise ValueError('降噪开关必须为布尔值')
    try:nr=version('noisereduce')
    except PackageNotFoundError:nr='NOT_INSTALLED'
    if enable and nr!='3.0.3':raise ValueError('降噪依赖须为已核验noisereduce 3.0.3')
    return dict(version=VERSION,enabled=enable,sample_rate=16000,samples=48000,
                noisereduce_version=nr,numpy_version=version('numpy'),scipy_version=version('scipy'),
                parameters=PARAMETERS.copy(),source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def denoise_3s(wave,enable=False,sample_rate=16000):
    started=time.perf_counter();x=np.asarray(wave)
    if (sample_rate!=16000 or x.dtype!=np.float32 or x.shape!=(48000,)
            or not np.isfinite(x).all() or float(abs(x).max())>1):
        raise ValueError('降噪输入须为16kHz单声道、48000点、有限且幅值≤1的float32片段')
    spec=settings(enable);out=x.copy();zero=not np.any(x)
    if enable and not zero:
        import noisereduce as nr
        try:out=np.asarray(nr.reduce_noise(y=x.astype(np.float64),sr=16000,**PARAMETERS),dtype=np.float32)
        except Exception as exc:raise ValueError('降噪处理失败，未使用未处理音频替代') from exc
    if out.shape!=(48000,) or not np.isfinite(out).all() or float(abs(out).max())>1:
        raise ValueError('降噪输出长度、数值或幅值异常')
    if zero and np.any(out):raise ValueError('降噪不得放大静音')
    if not zero and not np.any(out):raise ValueError('降噪输出全静音')
    return out,dict(settings=spec,input_sha256=hashlib.sha256(x.tobytes()).hexdigest(),
                    output_sha256=hashlib.sha256(out.tobytes()).hexdigest(),
                    input_peak=float(abs(x).max()),output_peak=float(abs(out).max()),
                    zero_passthrough=zero,processing_ms=(time.perf_counter()-started)*1000)
