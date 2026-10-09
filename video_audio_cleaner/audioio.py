"""Float-precision WAV helpers for the intermediate files."""
import numpy as np
import soundfile as sf
import torch


def write_wav(path, audio, sr):
    """Write a (channels, samples) tensor/array as 32-bit float WAV."""
    if isinstance(audio, torch.Tensor):
        data = audio.detach().cpu().numpy()
    else:
        data = np.asarray(audio)
    if data.ndim == 1:
        data = data[None, :]
    sf.write(str(path), data.T, int(sr), subtype="FLOAT")


def read_audio(path, target_sr=None):
    """Read audio as a (channels, samples) float32 tensor, resampled if asked.

    Used instead of DeepFilterNet's df.io.load_audio, which depends on
    torchaudio.info/load: both were removed from recent torchaudio.
    """
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = torch.from_numpy(np.ascontiguousarray(data.T))
    if target_sr is not None and sr != target_sr:
        import torchaudio.functional as F
        audio = F.resample(audio, sr, target_sr)
        sr = target_sr
    return audio, sr
