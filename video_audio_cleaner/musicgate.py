"""Keep the separated music only where music plays.

Demucs puts everything that is not voice into its music stem: the music, but also the
room, the crowd, wind and hiss. Mixed back whole, that stem returns all the background
noise the denoiser took out of the voices. Music is tonal (steady spectral peaks: notes,
chords) where ambience is not, so the stem is kept at full level where it is tonal and
turned down elsewhere, by about as much as the denoiser turns down noise, with a hold and
smooth fades so music never pumps.

Measured on a wedding ceremony (GoPro): ambience in the stem scored about 10 dB of peak
prominence throughout, the band at the end 21-26 dB; a burst of applause stayed under 9.
"""
import numpy as np

TONAL_DB = 16.0        # peak prominence above which a stretch counts as music
RANGE_DB = 20.0        # how far the stem is turned down where it is not music
FRAME = 4096           # analysis frame (85 ms at 48 kHz)
SMOOTH_SEC = 1.0       # median over this long, so single notes or bangs do not flip it
HOLD_SEC = 1.0         # kept open this long before and after music
FADE_SEC = 0.5         # gain ramps over this long


def _median_filter(x, width):
    if width <= 1 or len(x) == 0:
        return x
    pad = np.pad(x, width // 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, width)[: len(x)], axis=1)


def tonality(mono, sr, frame=FRAME):
    """Peak prominence per frame (dB): how far the strongest spectral peaks between
    100 Hz and 4 kHz stand above the local spectral level."""
    hop = frame // 2
    lo, hi = int(100 * frame / sr), int(4000 * frame / sr)
    win = np.hanning(frame).astype(np.float32)
    out = []
    for k in range(0, max(0, len(mono) - frame) + 1, hop):
        s = 20 * np.log10(np.abs(np.fft.rfft(mono[k:k + frame] * win))[lo:hi] + 1e-9)
        out.append(np.percentile(s - _median_filter(s, 31), 99))
    return np.asarray(out, dtype=np.float32), hop


def gate_gain(music, sr, tonal_db=TONAL_DB, range_db=RANGE_DB):
    """Per-sample gain for a (channels, samples) stem, and a summary of what it found."""
    mono = music.mean(axis=0) if music.ndim == 2 else music
    n = len(mono)
    if n < FRAME:
        return np.ones(n, dtype=np.float32), {"music_fraction": 1.0}
    tone, hop = tonality(mono, sr)
    per_sec = sr / hop
    tone = _median_filter(tone, max(1, int(SMOOTH_SEC * per_sec)))
    is_music = tone > tonal_db
    # hold: open a little before music starts and keep it open a little after it ends
    h = int(HOLD_SEC * per_sec)
    if h and is_music.any():
        is_music = np.convolve(is_music.astype(np.float32), np.ones(2 * h + 1), mode="same") > 0
    floor = 10 ** (-range_db / 20)
    g = np.where(is_music, 1.0, floor).astype(np.float32)
    # fades: a moving average of the gain
    f = max(1, int(FADE_SEC * per_sec))
    g = np.convolve(np.pad(g, f, mode="edge"), np.ones(f) / f, mode="same")[f:-f]
    centres = np.arange(len(g)) * hop + FRAME / 2
    gain = np.interp(np.arange(n), centres, g).astype(np.float32)
    return gain, {"music_fraction": float(is_music.mean()), "tonal_db": tonal_db,
                  "range_db": range_db}


def gate(music, sr, tonal_db=TONAL_DB, range_db=RANGE_DB):
    """The stem with everything but its music turned down. Works on numpy arrays and
    torch tensors shaped (channels, samples)."""
    try:
        import torch
        is_torch = isinstance(music, torch.Tensor)
    except ImportError:
        is_torch = False
    arr = music.detach().cpu().numpy() if is_torch else np.asarray(music)
    gain, info = gate_gain(arr, sr, tonal_db, range_db)
    out = arr * gain
    if is_torch:
        out = torch.from_numpy(np.ascontiguousarray(out)).to(music.dtype)
    return out, info
