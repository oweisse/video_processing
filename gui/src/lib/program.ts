// Plays an edit: cleaned (or original) audio per clip scheduled on the Web Audio
// clock, the music bed under it, and the picture from 540p proxies on two
// alternating <video> elements so cuts do not stall. Crossfades and soft cuts are heard
// as the export renders them (export.joins / edit.joinsOf); the picture cuts at a
// crossfade's midpoint. Ducking is applied on export only. Sound longer than a few
// minutes is streamed (lib/voices.ts) rather than decoded.
import { decode } from "./audio";
import { PRELOAD, STREAM_AFTER, StreamVoice } from "./voices";
import {
  clipAt, gainAt, joinsOf, place, voiceShape, type Clip, type Fade, type Frame, type JoinInfo, type MusicClip, type Placed,
} from "./edit";

export interface Resolver {
  proxy(sourceId: string): string;
  /**
   * The clip's sound with `pre`/`post` seconds to spare around it. offset: media time at
   * clip.in_sec; seconds: how long the media behind the URL is (long media is streamed).
   */
  clipAudio(clip: Clip, pre: number, post: number): { url: string; offset: number; seconds?: number | null };
  music(m: MusicClip): string;
  joinInfo: JoinInfo;
  /** The framing of the clip `t` seconds into it (zoom glides move it); default its frame. */
  frameAt?(index: number, t: number): Frame | null | undefined;
}

const CURVE_POINTS = 33;

const LEAD = 0.05;

export class Program {
  private placed: Placed[] = [];
  private shapes: { pre: number; post: number; fades: Fade[] }[] = [];
  private music: MusicClip[] = [];
  private nodes: AudioScheduledSourceNode[] = [];
  private streams = new Map<number, StreamVoice>(); // clip index → its live streamed voice
  private startedAt = 0;
  private startOffset = 0;
  private pausedAt = 0;
  private active = 0; // which of the two videos shows
  private shown: (string | null)[] = [null, null]; // clip id each video holds
  private listeners = new Set<() => void>();
  private loadToken = 0;
  playing = false;
  duration = 0;
  loading = false;

  constructor(
    private ctx: AudioContext,
    private videos: [HTMLVideoElement, HTMLVideoElement],
    private resolve: Resolver,
  ) {
    videos.forEach((v, i) => {
      v.muted = true;
      v.playsInline = true;
      v.style.visibility = i === 0 ? "visible" : "hidden";
    });
  }

  onchange(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    this.listeners.forEach((f) => f());
  }

  /** New edit (after any change): keeps the position, restarts playback if playing. */
  async setEdit(clips: Clip[], music: MusicClip[]) {
    const t = this.time;
    const was = this.playing;
    this.stopNodes();
    this.placed = place(clips);
    const joins = joinsOf(clips, this.resolve.joinInfo);
    this.shapes = this.placed.map((_, i) => voiceShape(this.placed, joins, i));
    this.music = music;
    this.duration = this.placed.length ? this.placed[this.placed.length - 1].start + this.placed[this.placed.length - 1].length : 0;
    this.pausedAt = Math.min(t, this.duration);
    this.shown = [null, null];
    const token = ++this.loadToken;
    this.loading = true;
    this.emit();
    // Decode what the edit needs (cached by URL, so unchanged clips are instant); long
    // sound is streamed when it plays.
    await Promise.allSettled([
      ...this.placed.map((p) => (p.clip.audio?.mute || this.streamed(p.index) ? null : decode(this.audioOf(p.index).url))),
      ...music.map((m) => decode(this.resolve.music(m))),
    ]);
    if (token !== this.loadToken) return;
    this.loading = false;
    if (was && this.playing) this.startNodes(this.pausedAt);
    this.showFrame(this.pausedAt, false);
    this.emit();
  }

  private audioOf(i: number) {
    const { pre, post } = this.shapes[i];
    return this.resolve.clipAudio(this.placed[i].clip, pre, post);
  }

  /** Whether a clip's sound is streamed rather than decoded. */
  private streamed(i: number): boolean {
    return (this.audioOf(i).seconds ?? Infinity) > STREAM_AFTER;
  }

  get time(): number {
    if (!this.playing) return this.pausedAt;
    return Math.min(this.duration, this.startOffset + Math.max(0, this.ctx.currentTime - this.startedAt));
  }

  async play() {
    if (this.playing || !this.placed.length) return;
    if (this.ctx.state === "suspended") await this.ctx.resume();
    if (this.pausedAt >= this.duration - 0.02) this.pausedAt = 0;
    this.playing = true;
    if (!this.loading) this.startNodes(this.pausedAt);
    this.emit();
  }

  pause() {
    if (!this.playing) return;
    this.pausedAt = this.time;
    this.playing = false;
    this.stopNodes();
    this.videos.forEach((v) => v.pause());
    this.emit();
  }

  toggle() {
    if (this.playing) this.pause();
    else void this.play();
  }

  seek(t: number) {
    const target = Math.max(0, Math.min(t, this.duration));
    if (this.playing) {
      this.stopNodes();
      this.startNodes(target);
    } else {
      this.pausedAt = target;
      this.showFrame(target, false);
    }
    this.emit();
  }

  private async startNodes(from: number) {
    const when = this.ctx.currentTime + LEAD;
    this.startedAt = when;
    this.startOffset = from;
    for (const p of this.placed) {
      const { pre, post, fades } = this.shapes[p.index];
      // A blend's sound starts before its clip and runs on after it.
      const vStart = p.start - pre, vEnd = p.start + p.length + post;
      if (vEnd <= from || p.clip.audio?.mute || this.streamed(p.index)) continue;
      const { url, offset } = this.audioOf(p.index);
      const buffer = (await decode(url).catch(() => null))?.buffer;
      if (!buffer || !this.playing || this.startedAt !== when) continue;
      const into = Math.max(0, from - vStart);
      const gain = this.ctx.createGain();
      const g = 10 ** ((p.clip.audio?.gain_db ?? 0) / 20);
      const at = when + Math.max(0, vStart - from);
      this.envelope(gain.gain, fades, g, Math.max(from, vStart), from, when);
      gain.connect(this.ctx.destination);
      const src = this.ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(gain);
      src.start(at, offset - pre + into, vEnd - vStart - into);
      this.nodes.push(src);
    }
    this.syncStreams();
    for (const m of this.music) {
      const length = m.out_sec - m.in_sec;
      const end = Math.min(this.duration, m.at_sec + length);
      if (end <= from) continue;
      const buffer = (await decode(this.resolve.music(m)).catch(() => null))?.buffer;
      if (!buffer || !this.playing || this.startedAt !== when) continue;
      const into = Math.max(0, from - m.at_sec);
      const at = when + Math.max(0, m.at_sec - from);
      const gain = this.ctx.createGain();
      const g = 10 ** (m.gain_db / 20);
      const fi = Math.min(m.fade_in_sec, length), fo = Math.min(m.fade_out_sec, length);
      const level = (x: number) => g * Math.min(1, fi ? x / fi : 1, fo ? (end - m.at_sec - x) / fo : 1);
      gain.gain.setValueAtTime(level(into), at);
      if (fi && into < fi) gain.gain.linearRampToValueAtTime(g, at + (fi - into));
      if (fo) {
        const foStart = when + (end - fo - from);
        if (foStart > at) gain.gain.setValueAtTime(g, foStart);
        gain.gain.linearRampToValueAtTime(0, when + (end - from));
      }
      gain.connect(this.ctx.destination);
      const src = this.ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(gain);
      src.start(at, m.in_sec + into, end - m.at_sec - into);
      this.nodes.push(src);
    }
  }

  /**
   * Keep the streamed voices to the clip playing now and the next one: create a voice a
   * few seconds before its sound starts, let it go once it has played, and keep the
   * live ones on the clock.
   */
  private syncStreams() {
    if (!this.playing) return;
    const now = this.time;
    for (const p of this.placed) {
      if (p.clip.audio?.mute || !this.streamed(p.index)) continue;
      const { pre, post, fades } = this.shapes[p.index];
      const vStart = p.start - pre, vEnd = p.start + p.length + post;
      const live = this.streams.get(p.index);
      if (vEnd <= now) {
        if (live) {
          live.release();
          this.streams.delete(p.index);
        }
        continue;
      }
      if (live || vStart - now > PRELOAD) continue;
      const t0 = Math.max(now, vStart);
      const { url, offset } = this.audioOf(p.index);
      const g = 10 ** ((p.clip.audio?.gain_db ?? 0) / 20);
      const ctxAt = (t: number) => this.startedAt + (t - this.startOffset);
      const voice = new StreamVoice(this.ctx, url, offset - pre + (t0 - vStart), ctxAt(t0), vEnd - t0,
        (ct) => g * gainAt(fades, this.startOffset + (ct - this.startedAt)));
      this.envelope(voice.gain.gain, fades, g, t0, this.startOffset, this.startedAt);
      this.streams.set(p.index, voice);
    }
    for (const v of this.streams.values()) v.tick();
  }

  /** Schedule fades (timeline times) on a gain, from timeline time t0 on; `when` is when `from` plays. */
  private envelope(param: AudioParam, fades: Fade[], g: number, t0: number, from: number, when: number) {
    param.setValueAtTime(g * gainAt(fades, t0), when + (t0 - from));
    let last = -Infinity;
    for (const f of [...fades].sort((a, b) => a.from - b.from)) {
      const a = Math.max(f.from, t0, last + 0.001), b = f.to;
      if (b - a < 0.002) continue;
      const curve = new Float32Array(CURVE_POINTS);
      for (let k = 0; k < CURVE_POINTS; k++) curve[k] = g * gainAt(fades, a + ((b - a) * k) / (CURVE_POINTS - 1));
      param.setValueCurveAtTime(curve, when + (a - from), b - a);
      last = b;
    }
  }

  private stopNodes() {
    for (const v of this.streams.values()) v.release();
    this.streams.clear();
    for (const n of this.nodes) {
      try {
        n.stop();
      } catch {
        /* not started */
      }
      n.disconnect();
    }
    this.nodes = [];
  }

  private frameOf(p: Placed, t: number) {
    return this.resolve.frameAt ? this.resolve.frameAt(p.index, t - p.start) : p.clip.frame;
  }

  /** Point a video at the clip showing at t; returns it. */
  private load(slot: number, p: Placed, t: number) {
    const v = this.videos[slot];
    const url = this.resolve.proxy(p.clip.source);
    if (!v.src.endsWith(url)) v.src = url;
    const target = p.clip.in_sec + (t - p.start);
    if (this.shown[slot] !== p.clip.id || Math.abs(v.currentTime - target) > 0.3) v.currentTime = target;
    this.shown[slot] = p.clip.id;
    showFrameOf(v, this.frameOf(p, t));
    return v;
  }

  private showFrame(t: number, playing: boolean) {
    const p = clipAt(this.placed, t);
    if (!p) return;
    let slot = this.active;
    if (this.shown[slot] !== p.clip.id) {
      // the standby video may already hold this clip
      if (this.shown[1 - slot] === p.clip.id) slot = 1 - slot;
      this.load(slot, p, t);
    }
    if (slot !== this.active) {
      this.videos[slot].style.visibility = "visible";
      this.videos[this.active].style.visibility = "hidden";
      this.videos[this.active].pause();
      this.active = slot;
    }
    const v = this.videos[slot];
    showFrameOf(v, this.frameOf(p, t));
    const target = p.clip.in_sec + (t - p.start);
    const drift = v.currentTime - target;
    if (playing) {
      if (v.paused) void v.play().catch(() => {});
      if (Math.abs(drift) > 0.5 && !v.seeking) v.currentTime = target;
      else if (Math.abs(drift) > 0.04) v.playbackRate = Math.max(0.9, Math.min(1.1, 1 - drift));
      else if (Math.abs(drift) < 0.015) v.playbackRate = 1;
      // Get the next clip ready on the other video a second ahead.
      const next = this.placed[p.index + 1];
      if (next && t > next.start + next.fadeIn / 2 - 1.0 && this.shown[1 - slot] !== next.clip.id) {
        this.load(1 - slot, next, next.start + next.fadeIn / 2);
        this.videos[1 - slot].pause();
      }
    } else {
      if (!v.paused) v.pause();
      if (Math.abs(drift) > 0.02 && !v.seeking) v.currentTime = target;
    }
  }

  /** Call every animation frame. */
  frame() {
    const t = this.time;
    if (this.playing && t >= this.duration - 0.001) {
      this.pausedAt = this.duration;
      this.playing = false;
      this.stopNodes();
      this.videos.forEach((v) => v.pause());
      this.emit();
      return;
    }
    this.syncStreams();
    const heard = this.playing ? Math.max(0, t - (this.ctx.outputLatency || this.ctx.baseLatency || 0)) : t;
    this.showFrame(heard, this.playing);
  }

  dispose() {
    this.stopNodes();
    this.videos.forEach((v) => {
      v.pause();
      v.removeAttribute("src");
      v.load();
    });
    this.listeners.clear();
  }
}

/** Show only a clip's zoom frame of the video, or the whole picture. */
function showFrameOf(v: HTMLVideoElement, f: Frame | null | undefined) {
  const st = v.style;
  if (!st) return; // test doubles
  st.objectFit = f ? "fill" : "";
  st.width = f ? `${100 / f.w}%` : "";
  st.height = f ? `${100 / f.h}%` : "";
  st.left = f ? `${(-f.x / f.w) * 100}%` : "";
  st.top = f ? `${(-f.y / f.h) * 100}%` : "";
  st.right = f ? "auto" : "";
  st.bottom = f ? "auto" : "";
}
