"""The ffmpeg filter graph for everything after DeepFilterNet.

Pure string building, no subprocesses, so it is unit-testable. Two graphs come
out of one set of options:

* analysis — hiss reduction, then a loudnorm measurement (two-pass loudnorm
  must measure exactly the signal it will later be applied to);
* render   — hiss, loudnorm, gain, music mix, declick and a final ebur128
  measurement, all in one ffmpeg run.

Look-ahead filters (afftdn, anlmdn, alimiter) delay their output. Each one is
wrapped so the delay is removed in place: the input tail is padded by the
filter's latency and the same amount trimmed from the head afterwards. The
caller measures those latencies (ffmpeg_utils.filter_latency) and passes them
in, which keeps this module free of I/O.

Profile hiss needs its noise sample *before* the audio it cleans (ffmpeg works
front to back), so the sample is prepended inside the graph, measured with
asendcmd, and trimmed off together with the latency.
"""
from dataclasses import dataclass, field


@dataclass
class GraphOptions:
    sample_rate: int = 48000

    hiss_method: str | None = None          # None, "profile" or "patch"
    hiss_nr: float = 12.0
    hiss_nf: float = -40.0
    hiss_gain_smooth: float = 0.0
    hiss_strength: float = 0.0003
    hiss_window: float = 0.0025
    noise_at: float | None = None           # seconds into the window (profile)
    noise_len: float = 1.0
    hiss_commands: tuple = ()               # ((seconds, "arg value"), ...) runtime changes

    normalize: bool = False
    target_lufs: float = -16.0
    target_tp: float = -1.5
    target_lra: float = 11.0
    measured: dict | None = None            # loudnorm analysis results

    audio_gain: str | None = None
    limiter_ceiling: float = 0.97

    music_level_db: float | None = None     # None = no music bed

    declick: bool = False
    declick_nr: float = 12.0
    declick_nf: float = -28.0
    declick_commands: tuple = ()            # ((seconds, "arg value"), ...)

    latency: dict = field(default_factory=dict)   # "hiss"/"limiter"/"declick" -> samples


# ---------------------------------------------------------------- filters
def hiss_filter(o):
    if o.hiss_method == "profile":
        return (f"afftdn@hiss=nr={o.hiss_nr:g}:nf={o.hiss_nf:g}"
                f":gs={o.hiss_gain_smooth:g}")
    if o.hiss_method == "patch":
        return f"anlmdn@hiss=s={o.hiss_strength:g}:p=0.002:r={o.hiss_window:g}"
    return None


def limiter_filter(o):
    return f"alimiter=limit={o.limiter_ceiling:g}:level=false"


def declick_filter(o):
    return f"afftdn@click=nr={o.declick_nr:g}:nf={o.declick_nf:g}"


def loudnorm_filter(o, measure=False):
    base = f"loudnorm=I={o.target_lufs:g}:TP={o.target_tp:g}:LRA={o.target_lra:g}"
    if measure:
        return base + ":print_format=json"
    m = o.measured
    if not m:
        return base
    return (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}"
            f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
            f":offset={m['target_offset']}:linear=true")


def _aligned(chain, latency, trim_extra=0):
    """`chain` with its look-ahead delay removed (plus `trim_extra` head samples)."""
    head = f"apad=pad_len={latency}," if latency else ""
    total = latency + trim_extra
    tail = f",atrim=start_sample={total},asetpts=PTS-STARTPTS" if total else ""
    return head + chain + tail


def _sendcmd(target, commands, offset=0.0):
    if not commands:
        return ""
    entries = "; ".join(f"{t + offset:.3f} {target} {arg}" for t, arg in commands)
    return f"asendcmd=c='{entries}',"


# ---------------------------------------------------------------- stages
def stage_names(o):
    """Human names of the stages the render pass performs, in order."""
    names = []
    if o.hiss_method:
        names.append("hiss")
    if o.normalize:
        names.append("loudnorm")
    if o.audio_gain:
        names.append("gain")
    if o.music_level_db is not None:
        names.append("mix")
    if o.declick:
        names.append("declick")
    return names


def needs_render(o):
    return bool(stage_names(o))


def inputs(o, denoised_wav, music_wav=None, mode="render"):
    """ffmpeg input arguments, in the order the graph's [N:a] labels expect.

    The analysis graph stops before the music mix, so it takes no music input.
    """
    args = ["-i", denoised_wav]
    if o.hiss_method == "profile":
        args += ["-ss", f"{o.noise_at:.6f}", "-i", denoised_wav]
    if mode == "render" and o.music_level_db is not None:
        if music_wav is None:
            raise ValueError("a music level was given without a music stem")
        args += ["-i", music_wav]
    return args


def _music_index(o):
    return 2 if o.hiss_method == "profile" else 1


def _hiss_part(o, parts):
    """Append the hiss stage; return the label carrying its output."""
    sr = o.sample_rate
    lat = o.latency.get("hiss", 0)
    if o.hiss_method == "profile":
        sample = int(round(o.noise_len * sr))
        stop = max(0.05, o.noise_len - 0.1)
        learn = ((0.0, "sn start"), (stop, "sn stop"))
        # The noise sample is prepended, so window times shift by its length.
        cmds = _sendcmd("afftdn@hiss", learn) + _sendcmd(
            "afftdn@hiss", o.hiss_commands, offset=o.noise_len)
        parts.append(f"[1:a]atrim=end_sample={sample},asetpts=PTS-STARTPTS[noise]")
        parts.append("[noise][0:a]concat=n=2:v=0:a=1[joined]")
        parts.append(f"[joined]{_aligned(cmds + hiss_filter(o), lat, sample)}[hiss]")
        return "[hiss]"
    if o.hiss_method == "patch":
        cmds = _sendcmd("anlmdn@hiss", o.hiss_commands)
        parts.append(f"[0:a]{_aligned(cmds + hiss_filter(o), lat)}[hiss]")
        return "[hiss]"
    return "[0:a]"


def build(o, mode):
    """filter_complex string for mode 'analysis' or 'render'; output label is [out]."""
    if mode not in ("analysis", "render"):
        raise ValueError(mode)
    sr = o.sample_rate
    parts = []
    cur = _hiss_part(o, parts)

    if mode == "analysis":
        parts.append(f"{cur}{loudnorm_filter(o, measure=True)}[out]")
        return ";".join(parts)

    chain = []
    if o.normalize:
        # loudnorm runs at 192 kHz internally; come back down before any
        # sample-counted filter.
        chain += [loudnorm_filter(o), f"aresample={sr}"]
    if o.audio_gain:
        chain += [f"volume={o.audio_gain}",
                  _aligned(limiter_filter(o), o.latency.get("limiter", 0))]
    if chain:
        parts.append(f"{cur}{','.join(chain)}[level]")
        cur = "[level]"

    if o.music_level_db is not None:
        parts.append(f"[{_music_index(o)}:a]volume={o.music_level_db:g}dB[bed]")
        parts.append(f"{cur}[bed]amix=inputs=2:duration=first:normalize=0,"
                     f"{_aligned(limiter_filter(o), o.latency.get('limiter', 0))}[mixed]")
        cur = "[mixed]"

    if o.declick:
        cmds = _sendcmd("afftdn@click", o.declick_commands)
        parts.append(f"{cur}{_aligned(cmds + declick_filter(o), o.latency.get('declick', 0))}"
                     f"[declicked]")
        cur = "[declicked]"

    # Measure the audio the user actually gets, in the same pass.
    parts.append(f"{cur}ebur128=peak=true,aresample={sr}[out]")
    return ";".join(parts)
