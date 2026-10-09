"""Run the real post-DeepFilterNet render graph on a file, for tests."""
import contextlib
import io

from video_audio_cleaner import graph, hiss, postprocess


def render(src, out, duration, music=None, **options):
    o = graph.GraphOptions(**options)
    if o.hiss_method == "profile" and o.noise_at is None:
        o.noise_at = hiss.find_quietest_region(str(src), o.noise_len).start
    o.latency = postprocess.measure_latencies(o)
    with contextlib.redirect_stdout(io.StringIO()):
        if o.normalize:
            o.measured = postprocess.analyse(o, str(src), duration, "analyse")
        result = postprocess.render(o, str(src), str(music) if music else None, str(out),
                                    duration, "render")
    return o, result
