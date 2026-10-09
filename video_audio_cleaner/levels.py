"""Streaming level analysis of the denoised audio.

Reads the file block by block (never whole), producing 100 ms RMS levels for
choosing the profile-hiss noise sample (hiss.find_quietest_region). A 90-minute
window is ~1 GB as float32; its 100 ms level track is ~200 KB.
"""
from dataclasses import dataclass

import numpy as np
import soundfile as sf

FRAME_SEC = 0.1


@dataclass
class Levels:
    rms: np.ndarray        # linear RMS per 100 ms frame (mono mix), never zero
    frame_sec: float = FRAME_SEC

    @property
    def db(self):
        return 20 * np.log10(self.rms)

    @property
    def floor_db(self):
        """The window's noise floor: its quietest frames (10th percentile)."""
        return float(np.percentile(self.db, 10)) if len(self.rms) else -120.0


def frame_levels(path, frame_sec=FRAME_SEC):
    info = sf.info(str(path))
    frame = max(1, int(round(frame_sec * info.samplerate)))
    out = []
    # Blocks of whole frames; a trailing partial frame is dropped.
    for block in sf.blocks(str(path), blocksize=frame * 600, dtype="float32",
                           always_2d=True):
        mono = block.mean(axis=1)
        count = len(mono) // frame
        if count:
            out.append(np.sqrt(np.mean(mono[:count * frame].reshape(count, frame) ** 2,
                                       axis=1)))
    rms = np.concatenate(out) if out else np.zeros(0)
    return Levels(rms + 1e-12, frame_sec)
