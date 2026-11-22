import io
import math
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def decode(raw):
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('音频不能超过 10 MB')
    try:
        with sf.SoundFile(io.BytesIO(raw)) as f:
            if not 8000 <= f.samplerate <= 192000 or not 1 <= f.channels <= 2:
                raise ValueError('不支持的采样率或声道数')
            if not 2 <= len(f) / f.samplerate <= 15:
                raise ValueError('请提供 2～15 秒音频')
            rate = f.samplerate
            x = f.read(dtype='float32', always_2d=True).mean(axis=1)
    except (RuntimeError, sf.LibsndfileError) as exc:
        raise ValueError('无法读取音频，请使用 WAV 或 FLAC') from exc
    if not np.isfinite(x).all() or np.sqrt(np.mean(x*x)) < .0001:
        raise ValueError('录音静音或无效，请重新录制')
    d = math.gcd(rate, 16000)
    return resample_poly(x, 16000 // d, rate // d).astype('float32')


def quality(x):
    frames = x[:len(x)//320*320].reshape(-1, 320)
    power = np.mean(frames ** 2, axis=1)
    proxy = float(10*np.log10((np.percentile(power, 90)+1e-10)/(np.percentile(power, 10)+1e-10)))
    return dict(duration=len(x)/16000, quality_proxy_db=proxy,
                environment='clean' if proxy >= 20 else 'medium' if proxy >= 10 else 'noisy')
