// Sample-synced A/B playback with Web Audio (docs/gui-design.md §6.2).
//
// Both sides play all the time from the same start instant through their own
// gain; "switching" is an 8 ms gain crossfade, so it is gapless, click-free and
// never loses position. The audio clock is the master; the view slaves the
// picture to `time`.

export type Side = "A" | "B";
export type PlayerEvent = "state" | "ended";

const START_LEAD = 0.03; // s: schedule slightly ahead so both sources start together
const SWITCH_RAMP = 0.008; // s

const dbToGain = (db: number) => 10 ** (db / 20);

export class ABPlayer {
  private a: AudioBuffer | null = null;
  private b: AudioBuffer | null = null;
  private gainA: GainNode;
  private gainB: GainNode;
  private srcA: AudioBufferSourceNode | null = null;
  private srcB: AudioBufferSourceNode | null = null;
  private startedAt = 0; // ctx time the current sources started
  private startOffset = 0; // buffer position at startedAt
  private pausedAt = 0;
  private levelA = 1;
  private levelB = 1;
  private listeners = new Map<PlayerEvent, Set<() => void>>();

  playing = false;
  active: Side = "B";
  loop: { start: number; end: number } | null = null;

  constructor(public ctx: AudioContext) {
    this.gainA = ctx.createGain();
    this.gainB = ctx.createGain();
    this.gainA.connect(ctx.destination);
    this.gainB.connect(ctx.destination);
    this.applyGains(0);
  }

  get duration(): number {
    if (!this.a && !this.b) return 0;
    return Math.min(this.a?.duration ?? Infinity, this.b?.duration ?? Infinity);
  }

  get ready(): boolean {
    return !!(this.a && this.b);
  }

  on(event: PlayerEvent, fn: () => void): () => void {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(fn);
    return () => this.listeners.get(event)!.delete(fn);
  }

  private emit(event: PlayerEvent) {
    this.listeners.get(event)?.forEach((fn) => fn());
  }

  /** Load both sides; playback stops and the position is kept if it still fits. */
  setBuffers(a: AudioBuffer, b: AudioBuffer) {
    const t = this.time;
    this.stopSources();
    this.a = a;
    this.b = b;
    this.pausedAt = Math.min(t, this.duration);
    if (this.playing) this.startSources(this.pausedAt);
  }

  /** Swap side B (e.g. another candidate) without interrupting A. */
  replaceB(b: AudioBuffer) {
    this.b = b;
    if (!this.playing || !this.a) {
      this.pausedAt = Math.min(this.pausedAt, this.duration);
      return;
    }
    const when = this.ctx.currentTime + START_LEAD;
    const offset = this.timeAt(when);
    this.srcB?.stop();
    this.srcB?.disconnect();
    this.srcB = this.makeSource(b, this.gainB);
    this.srcB.start(when, offset);
  }

  setLevels(gainADb: number, gainBDb: number) {
    this.levelA = dbToGain(gainADb);
    this.levelB = dbToGain(gainBDb);
    this.applyGains(SWITCH_RAMP);
  }

  setActive(side: Side) {
    if (side === this.active) return;
    this.active = side;
    this.applyGains(SWITCH_RAMP);
    this.emit("state");
  }

  toggle() {
    this.setActive(this.active === "A" ? "B" : "A");
  }

  private applyGains(ramp: number) {
    const now = this.ctx.currentTime;
    for (const [node, target] of [
      [this.gainA, this.active === "A" ? this.levelA : 0],
      [this.gainB, this.active === "B" ? this.levelB : 0],
    ] as [GainNode, number][]) {
      node.gain.cancelScheduledValues(now);
      node.gain.setValueAtTime(node.gain.value, now);
      if (ramp > 0) node.gain.linearRampToValueAtTime(target, now + ramp);
      else node.gain.setValueAtTime(target, now);
    }
  }

  async play() {
    if (this.playing || !this.ready) return;
    if (this.ctx.state === "suspended") await this.ctx.resume();
    let from = this.pausedAt;
    if (this.loop) {
      if (from < this.loop.start || from >= this.loop.end - 0.01) from = this.loop.start;
    } else if (from >= this.duration - 0.01) {
      from = 0;
    }
    this.playing = true;
    this.startSources(from);
    this.emit("state");
  }

  pause() {
    if (!this.playing) return;
    this.pausedAt = this.time;
    this.playing = false;
    this.stopSources();
    this.emit("state");
  }

  togglePlay() {
    if (this.playing) this.pause();
    else void this.play();
  }

  seek(t: number) {
    const target = Math.max(0, Math.min(t, this.duration));
    if (this.playing) {
      this.stopSources();
      this.startSources(target);
    } else {
      this.pausedAt = target;
    }
    this.emit("state");
  }

  setLoop(start: number | null, end: number | null) {
    if (start === null || end === null || end - start < 0.05) {
      this.loop = null;
    } else {
      this.loop = { start: Math.max(0, start), end: Math.min(end, this.duration) };
    }
    if (this.playing) {
      // Restart so the sources pick up the loop; jump inside it if we are outside.
      let t = this.time;
      if (this.loop && (t < this.loop.start || t >= this.loop.end)) t = this.loop.start;
      this.stopSources();
      this.startSources(t);
    }
    this.emit("state");
  }

  /** Current position in seconds (buffer time), authoritative for the picture. */
  get time(): number {
    return this.playing ? this.timeAt(this.ctx.currentTime) : this.pausedAt;
  }

  private timeAt(ctxTime: number): number {
    const t = this.startOffset + Math.max(0, ctxTime - this.startedAt);
    if (this.loop && t >= this.loop.end) {
      const len = this.loop.end - this.loop.start;
      return this.loop.start + ((t - this.loop.end) % len);
    }
    return Math.min(t, this.duration);
  }

  private makeSource(buffer: AudioBuffer, gain: GainNode): AudioBufferSourceNode {
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    if (this.loop) {
      src.loop = true;
      src.loopStart = this.loop.start;
      src.loopEnd = this.loop.end;
    }
    src.connect(gain);
    return src;
  }

  private startSources(offset: number) {
    if (!this.a || !this.b) return;
    const when = this.ctx.currentTime + START_LEAD;
    this.srcA = this.makeSource(this.a, this.gainA);
    this.srcB = this.makeSource(this.b, this.gainB);
    const srcA = this.srcA;
    // Stop both at the shorter side's end so they never run unpaired.
    const length = this.loop ? undefined : Math.max(0, this.duration - offset);
    this.srcA.start(when, offset, length);
    this.srcB.start(when, offset, length);
    this.startedAt = when;
    this.startOffset = offset;
    srcA.onended = () => {
      if (this.srcA === srcA && this.playing) {
        this.playing = false;
        this.pausedAt = this.duration;
        this.srcA = this.srcB = null;
        this.emit("ended");
        this.emit("state");
      }
    };
  }

  private stopSources() {
    for (const src of [this.srcA, this.srcB]) {
      if (!src) continue;
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* not started yet */
      }
      src.disconnect();
    }
    this.srcA = this.srcB = null;
  }

  dispose() {
    this.stopSources();
    this.playing = false;
    this.gainA.disconnect();
    this.gainB.disconnect();
    this.listeners.clear();
  }
}
