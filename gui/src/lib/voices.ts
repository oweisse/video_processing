// Long sound in the timeline player (docs/stitch-and-size-budget-plan.md §5). Decoding a
// whole-video clean into an AudioBuffer costs over a gigabyte per hour, so sound longer
// than STREAM_AFTER seconds plays from an <audio> element routed into the same Web Audio
// graph instead: its gain node takes the same fades, and it is kept on the context clock
// by nudging its rate or reseating it, as the picture is.

export const STREAM_AFTER = 180; // seconds of sound behind a URL before it is streamed
export const PRELOAD = 3; // seconds ahead a streamed voice is created

/** What a drifting element should do: nothing, nudge its rate, or jump. */
export function driftAction(drift: number, rate: number): { rate: number; seek: boolean } {
  if (Math.abs(drift) > 0.25) return { rate: 1, seek: true };
  if (Math.abs(drift) > 0.04) return { rate: Math.max(0.95, Math.min(1.05, 1 - drift)), seek: false };
  if (Math.abs(drift) < 0.015) return { rate: 1, seek: false };
  return { rate, seek: false };
}

/**
 * One streamed stretch of sound: from `position` in the element's media, starting at
 * context time `startAt`, for `length` seconds.
 */
export class StreamVoice {
  readonly el: HTMLAudioElement;
  readonly gain: GainNode;
  private node: MediaElementAudioSourceNode | null = null;
  private started = false;
  private done = false;

  constructor(
    private ctx: AudioContext,
    url: string,
    private position: number,
    private startAt: number,
    private length: number,
    private volumeAt?: (ctxTime: number) => number, // used only when the element cannot be routed
  ) {
    this.el = new Audio();
    this.el.preload = "auto";
    this.el.crossOrigin = "anonymous";
    this.el.src = url;
    this.gain = ctx.createGain();
    try {
      this.node = ctx.createMediaElementSource(this.el);
      this.node.connect(this.gain);
    } catch {
      this.node = null; // plays straight out; fades fall back to element volume
    }
    this.gain.connect(ctx.destination);
    this.el.currentTime = position;
    // For checking sync from the console: `__vacVoices = new Set()` before playing.
    (globalThis as { __vacVoices?: Set<StreamVoice> }).__vacVoices?.add(this);
  }

  /** Where the element should be at context time t. */
  expected(t: number): number {
    return this.position + Math.max(0, t - this.startAt);
  }

  /** Call every animation frame while the program plays. */
  tick() {
    if (this.done) return;
    const t = this.ctx.currentTime;
    if (t >= this.startAt + this.length) {
      this.stop();
      return;
    }
    if (!this.started) {
      if (t < this.startAt - 0.02) return;
      this.started = true;
      this.el.currentTime = this.expected(t);
      void this.el.play().catch(() => {});
      return;
    }
    if (!this.node && this.volumeAt) this.el.volume = Math.max(0, Math.min(1, this.volumeAt(t)));
    if (this.el.seeking || this.el.readyState < 2) return;
    const drift = this.el.currentTime - this.expected(t);
    const act = driftAction(drift, this.el.playbackRate);
    if (act.seek) this.el.currentTime = this.expected(t);
    if (this.el.playbackRate !== act.rate) this.el.playbackRate = act.rate;
  }

  stop() {
    this.done = true;
    this.el.pause();
  }

  /** Stop and let go of the element and nodes. */
  release() {
    this.stop();
    this.el.removeAttribute("src");
    this.el.load();
    this.node?.disconnect();
    this.gain.disconnect();
  }
}
