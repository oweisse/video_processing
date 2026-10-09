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

