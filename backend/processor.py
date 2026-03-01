"""core audio processing — no http coupling, all I/O via BytesIO"""
import io
from dataclasses import dataclass

import librosa
import numpy as np
import soundfile as sf


@dataclass
class ProcessParams:
    mode: str               # 'single' | 'multi'
    n_fft: int              # fft window size
    hop_length: int         # hop in samples
    output_duration: float  # synth output length in seconds
    sample_rate: int        # target sr for all audio
    contrast_enable: bool
    contrast_threshold: float   # 0.0–1.0
    boost_power: float          # above-threshold exponent inversion
    suppress_power: float       # below-threshold exponent
    griffinlim_iters: int       # GL phase reconstruction iterations


def load_audio(file_bytes: bytes, target_sr: int) -> np.ndarray:
    """decode audio from raw bytes, resample to target_sr, mix to mono"""
    buf = io.BytesIO(file_bytes)
    y, _ = librosa.load(buf, sr=target_sr, mono=True)
    return y  # float32, shape: (n_samples,)


def compute_spectrum(y: np.ndarray, params: ProcessParams) -> np.ndarray:
    """STFT → magnitude → time-average → shape: (n_bins,)"""
    D = librosa.stft(y, n_fft=params.n_fft, hop_length=params.hop_length)
    S = np.abs(D)              # (bins, frames)
    return np.mean(S, axis=1)  # (bins,)


def apply_contrast(avg_mag: np.ndarray, params: ProcessParams) -> np.ndarray:
    """normalize to [0,1] then apply threshold-based power-law contrast"""
    mag = avg_mag / (avg_mag.max() + 1e-8)
    if not params.contrast_enable:
        return mag
    above = mag >= params.contrast_threshold
    # x^(1/p) boosts values closer to 1; x^p suppresses values toward 0
    enhanced = np.where(
        above,
        mag ** (1.0 / max(params.boost_power, 1e-6)),
        mag ** params.suppress_power,
    )
    return enhanced  # still in [0, 1]


def tile_to_frames(mag: np.ndarray, params: ProcessParams) -> np.ndarray:
    """tile 1D averaged spectrum to 2D for griffin-lim input"""
    target_samples = int(params.output_duration * params.sample_rate)
    target_frames = max(1, target_samples // params.hop_length)
    # (bins,) → (bins, frames)
    return np.tile(mag[:, np.newaxis], (1, target_frames))


def synthesize(mag2d: np.ndarray, params: ProcessParams) -> np.ndarray:
    """griffin-lim phase reconstruction → time-domain audio, peak-normalized"""
    audio = librosa.griffinlim(
        mag2d,
        n_iter=params.griffinlim_iters,
        hop_length=params.hop_length,
        n_fft=params.n_fft,
        random_state=0,  # deterministic output
    )
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.95
    return audio.astype(np.float32)


def encode_wav(audio: np.ndarray, sr: int) -> bytes:
    """write float32 audio to in-memory 16-bit WAV, return bytes"""
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


def get_audio_info(file_bytes: bytes) -> dict:
    """lightweight metadata read via soundfile.info — no full decode"""
    buf = io.BytesIO(file_bytes)
    info = sf.info(buf)
    return {
        "duration": round(info.duration, 2),
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "format": info.format,
    }


def process_single(file_bytes: bytes, params: ProcessParams) -> bytes:
    """single-file self-averaging pipeline"""
    y = load_audio(file_bytes, params.sample_rate)
    avg_mag = compute_spectrum(y, params)
    enhanced = apply_contrast(avg_mag, params)
    mag2d = tile_to_frames(enhanced, params)
    audio = synthesize(mag2d, params)
    return encode_wav(audio, params.sample_rate)


def process_multi(files_bytes: list[bytes], params: ProcessParams) -> bytes:
    """cross-file spectral averaging pipeline — mean across all file spectra"""
    spectra = []
    for fb in files_bytes:
        y = load_audio(fb, params.sample_rate)
        spectra.append(compute_spectrum(y, params))
    # stack (n_files, bins) → mean across files → (bins,)
    cross_avg = np.mean(np.stack(spectra, axis=0), axis=0)
    enhanced = apply_contrast(cross_avg, params)
    mag2d = tile_to_frames(enhanced, params)
    audio = synthesize(mag2d, params)
    return encode_wav(audio, params.sample_rate)
