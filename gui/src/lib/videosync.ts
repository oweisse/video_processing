// Keep a muted <video> on the A/B player's clock. Small drift is absorbed by nudging the
// playback rate, large drift (after a seek) by jumping. What is heard lags the audio clock
// by the output latency, so the picture follows what is heard. `offset` is where the
// player's time 0 falls in the video (a window cut from a longer source).
export function followClock(v: HTMLVideoElement | null | undefined, ctx: AudioContext, clock: number, playing: boolean, offset = 0) {
  if (!v || v.readyState < 2) return;
  const t = offset + (playing ? Math.max(0, clock - (ctx.outputLatency || ctx.baseLatency || 0)) : clock);
  const drift = v.currentTime - t;
  v.dataset.drift = drift.toFixed(3); // for checking sync from the console
  if (playing) {
    if (v.paused) void v.play().catch(() => {});
    if (Math.abs(drift) > 0.5) {
      if (!v.seeking) v.currentTime = t;
      v.playbackRate = 1;
    } else if (Math.abs(drift) > 0.04) {
      v.playbackRate = Math.max(0.9, Math.min(1.1, 1 - drift));
    } else if (Math.abs(drift) < 0.015) v.playbackRate = 1;
  } else {
    if (!v.paused) v.pause();
    if (Math.abs(drift) > 0.02 && !v.seeking) v.currentTime = t;
  }
}
