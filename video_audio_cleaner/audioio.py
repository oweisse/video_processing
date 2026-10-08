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


def read_wav(path):
    """Read a WAV as a (channels, samples) float32 tensor."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return torch.from_numpy(np.ascontiguousarray(data.T)), sr
