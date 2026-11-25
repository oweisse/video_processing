// The edit model, mirroring video_audio_cleaner/export.py (docs/gui-design.md §8).
import type { Output, Probe, Source } from "./api";

export interface ClipAudio {
  file?: string; // a cleaned output covering the clip (absolute path, for export)
  output?: string; // its name in the library (for the player)
  start_sec?: number; // the source time where that output starts
  duration_sec?: number; // that output's length (how much sound a soft cut may borrow)
  gain_db?: number;
  mute?: boolean;
}

export interface Clip {
  id: string;
  source: string;
  in_sec: number;
  out_sec: number;
  audio: ClipAudio | null; // null: the source's own audio
  // crossfade: picture and sound overlap the previous clip; soft_cut: the picture cuts,
  // the sound blends (or dips) around the cut and nothing moves.
  transition_in?: { type: "crossfade" | "soft_cut"; sec: number } | null;
  video_filters?: string[];
  // Zoom: the part of the picture shown, as fractions of the source's (square-pixel) picture,
  // shaped like the output; absent for the whole picture.
  frame?: Frame | null;
  // Where this clip continues the previous one's shot with another framing, the picture
  // glides from that framing to this one over its first seconds. Absent: ZOOM_GLIDE_SEC;
  // false: a plain cut of the framing; {sec}: another length.
  zoom_glide?: { sec: number } | false;
}

export interface Frame {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface MusicClip {
  id: string;
  file: string;
  name?: string;
  at_sec: number;
  in_sec: number;
  out_sec: number;
  file_duration_sec?: number;
  gain_db: number;
  fade_in_sec: number;
  fade_out_sec: number;
}

export interface Duck {
  enabled: boolean;
  threshold_db: number;
  ratio: number;
  attack_ms: number;
  release_ms: number;
}

export interface Project {
  schema: 1;
  name: string;
  canvas: { width: number; height: number; fps: number } | null;
  sources: Record<string, { path: string; probe?: Probe | null }>;
  tracks: { video: Clip[]; music: MusicClip[]; music_duck?: Duck };
  video_order?: string[]; // the order of the Videos panel (source ids)
  export: {
    encoder?: string; crf?: number; preset?: string; audio_bitrate?: string; target_lufs?: number; target_tp?: number;
    output?: string | null;
    level_clips?: boolean;
    budget?: { per_hour_gb?: number; total_gb?: number } | null;
    codec?: "h264" | "hevc";
    height?: number | null;
    fast?: boolean;
  };
  modified?: string;
  rev?: number; // the revision on disk (every save adds one)
}

/** Width once the pixels are square (tape transfers often store 16:15 pixels). */
export function displayWidth(v: { width: number; sar?: number }): number {
  return Math.round((v.width * (v.sar || 1)) / 2) * 2;
}

export interface CanvasItem {
  display_width: number | null;
  height: number | null;
  fps: number | null;
  seconds: number;
}

/**
 * A common frame for clips from different videos: the largest picture (ties: more
 * seconds), at the frame rate carrying the most seconds (rates within 0.01 are one).
 * Mirrors export.suggest_canvas; both are tested against tests/data/canvas.json.
 */
export function suggestCanvas(items: CanvasItem[]): { width: number; height: number; fps: number } | null {
  const usable = items.filter((i) => i.display_width && i.height);
  if (!usable.length) return null;
  const big = usable.reduce((a, b) => {
    const da = a.display_width! * a.height!, db = b.display_width! * b.height!;
    return db > da || (db === da && (b.seconds || 0) > (a.seconds || 0)) ? b : a;
  });
  const rates: [number, number][] = [];
  for (const i of usable) {
    const fps = i.fps || 30;
    const r = rates.find((r) => Math.abs(r[0] - fps) <= 0.01);
    if (r) r[1] += i.seconds || 0;
    else rates.push([fps, i.seconds || 0]);
  }
  const fps = rates.reduce((a, b) => (b[1] > a[1] ? b : a))[0];
  return { width: big.display_width!, height: big.height!, fps };
}

export interface Placed {
  clip: Clip;
  index: number;
  start: number; // on the timeline
  length: number;
  fadeIn: number; // overlap with the previous clip
}

export const transitionOf = (c: Clip) => (c.transition_in?.type === "crossfade" ? c.transition_in.sec || 0 : 0);

/** Clips with their timeline positions; crossfades overlap the previous clip. */
export function place(clips: Clip[]): Placed[] {
  const out: Placed[] = [];
  let t = 0;
  clips.forEach((clip, index) => {
    const length = clip.out_sec - clip.in_sec;
    let fadeIn = index ? transitionOf(clip) : 0;
    if (index) fadeIn = Math.min(fadeIn, out[index - 1].length / 2, length / 2);
    t -= fadeIn;
    out.push({ clip, index, start: t, length, fadeIn });
    t += length;
  });
  return out;
}

export function duration(clips: Clip[]): number {
  const p = place(clips);
  return p.length ? p[p.length - 1].start + p[p.length - 1].length : 0;
}

/** The clip showing at time t (the incoming one from the middle of a crossfade). */
export function clipAt(placed: Placed[], t: number): Placed | null {
  for (let i = placed.length - 1; i >= 0; i--) {
    const p = placed[i];
    if (t >= p.start + p.fadeIn / 2 - 1e-6) return t < p.start + p.length + 1e-6 ? p : null;
  }
  return placed[0] ?? null;
}

export function newId(prefix = "c"): string {
  return prefix + Math.random().toString(36).slice(2, 9);
}

/** The cleaned output that covers [inSec, outSec] of the source, newest first. */
export function coveringOutput(source: Source | null, inSec: number, outSec: number, prefer?: string): Output | null {
  if (!source) return null;
  const covers = source.outputs.filter((o) => {
    const w = o.window;
    if (!o.exists || o.kind !== "video" || !w || w.actual_duration_sec == null) return false;
    return w.actual_start_sec <= inSec + 0.01 && w.actual_start_sec + w.actual_duration_sec >= outSec - 0.01;
  });
  if (!covers.length) return null;
  return covers.find((o) => o.file === prefer) ?? [...covers].sort((a, b) => (b.created ?? "").localeCompare(a.created ?? ""))[0];
}

/** Every existing cleaned video output that covers [inSec, outSec] of the source, newest first. */
export function coveringOutputs(source: Pick<Source, "outputs"> | null, inSec: number, outSec: number): Output[] {
  if (!source) return [];
  return source.outputs
    .filter((o) => {
      const w = o.window;
      if (!o.exists || o.kind !== "video" || !w || w.actual_duration_sec == null) return false;
      return w.actual_start_sec <= inSec + 0.01 && w.actual_start_sec + w.actual_duration_sec >= outSec - 0.01;
    })
    .sort((a, b) => (b.created ?? "").localeCompare(a.created ?? ""));
}

export function audioFor(o: Output | null, gain = 0): ClipAudio | null {
  if (!o || !o.window) return gain ? { gain_db: gain } : null;
  const a: ClipAudio = { file: o.path, output: o.file, start_sec: o.window.actual_start_sec, gain_db: gain };
  if (o.window.actual_duration_sec != null) a.duration_sec = o.window.actual_duration_sec;
  return a;
}

// ------------------------------------------------------------------ how clips' sounds meet

export type JoinKind = "cut" | "crossfade" | "blend" | "dip";
export interface Join {
  kind: JoinKind;
  sec: number;
  lead?: number; // blends: seconds of the overlap before the cut
}

/** Lengths the join rules need: a source's, and a clip's cleaned audio when it does not record it. */
export interface JoinInfo {
  sourceSeconds(id: string): number | null | undefined;
  audioSeconds(clip: Clip): number | null | undefined;
}

type Sound = "mute" | `file:${string}` | `source:${string}`;
function soundOf(c: Clip): Sound {
  if (c.audio?.mute) return "mute";
  return c.audio?.file ? `file:${c.audio.file}` : `source:${c.source}`;
}

/** Sound that exists beyond the clip's edges: [before the in point, after the out point]. */
export function spareSound(c: Clip, info: JoinInfo): [number, number] {
  const sound = soundOf(c);
  if (sound === "mute") return [0, 0];
  let before: number, after: number;
  if (sound.startsWith("file:")) {
    const start = c.audio!.start_sec ?? 0;
    const length = c.audio!.duration_sec ?? info.audioSeconds(c);
    before = c.in_sec - start;
    after = length != null ? start + length - c.out_sec : 0;
  } else {
    const length = info.sourceSeconds(c.source);
    before = c.in_sec;
    after = length != null ? length - c.out_sec : 0;
  }
  return [Math.max(0, before), Math.max(0, after)];
}

/**
 * How each clip's sound meets the previous one (index 0 is always a cut). Mirrors
 * export.joins; both are tested against tests/data/joins.json.
 */
export function joinsOf(clips: Clip[], info: JoinInfo): Join[] {
  const out: Join[] = clips.length ? [{ kind: "cut", sec: 0 }] : [];
  for (let i = 1; i < clips.length; i++) {
    const prev = clips[i - 1], c = clips[i];
    const plen = prev.out_sec - prev.in_sec, len = c.out_sec - c.in_sec;
    const tr = c.transition_in;
    const sec = tr?.sec ?? 0;
    if (tr?.type === "crossfade" && sec > 0) {
      out.push({ kind: "crossfade", sec: Math.min(sec, plen / 2, len / 2) });
      continue;
    }
    if (tr?.type !== "soft_cut" || sec <= 0) {
      out.push({ kind: "cut", sec: 0 });
      continue;
    }
    const d = Math.min(sec, plen, len);
    if (prev.source === c.source && soundOf(prev) === soundOf(c) && Math.abs(prev.out_sec - c.in_sec) < 0.02) {
      out.push({ kind: "cut", sec: 0 });
      continue;
    }
    if (soundOf(prev) === "mute" || soundOf(c) === "mute") {
      out.push({ kind: "dip", sec: d });
      continue;
    }
    const lead = blendLead(d, spareSound(c, info)[0], spareSound(prev, info)[1]);
    out.push(lead === null ? { kind: "dip", sec: d } : { kind: "blend", sec: d, lead });
  }
  return out;
}

/**
 * Seconds of a d-second overlap that lie before the cut: centred when both sides have
 * sound to spare, else as near the centre as they allow; null when together they have
 * less than d. Mirrors export.blend_lead.
 */
export function blendLead(d: number, before: number, after: number): number | null {
  const lo = Math.max(0, d - after), hi = Math.min(d, before);
  if (lo > hi + 1e-6) return null;
  return Math.round(Math.min(Math.max(d / 2, lo), hi) * 1e6) / 1e6;
}

export interface Fade {
  from: number; // timeline seconds
  to: number;
  dir: "in" | "out";
  shape: "linear" | "qsin";
}

/**
 * What one clip's sound does on the timeline: it starts `pre` seconds before the clip and
 * ends `post` after it (sound borrowed for blends), and these fades shape it.
 */
export function voiceShape(placed: Placed[], joins: Join[], i: number): { pre: number; post: number; fades: Fade[] } {
  const p = placed[i];
  const start = p.start, end = p.start + p.length;
  const into = joins[i] ?? { kind: "cut", sec: 0 };
  const next = joins[i + 1] ?? { kind: "cut", sec: 0 };
  // A blend's overlap: `lead` seconds before the cut, the rest after it.
  const pre = into.kind === "blend" ? (into.lead ?? into.sec / 2) : 0;
  const post = next.kind === "blend" ? next.sec - (next.lead ?? next.sec / 2) : 0;
  const fades: Fade[] = [];
  if (into.kind === "crossfade" && p.fadeIn > 0) fades.push({ from: start, to: start + p.fadeIn, dir: "in", shape: "linear" });
  if (into.kind === "blend") fades.push({ from: start - pre, to: start - pre + into.sec, dir: "in", shape: "qsin" });
  if (into.kind === "dip") fades.push({ from: start, to: start + into.sec / 2, dir: "in", shape: "qsin" });
  const nextPlaced = placed[i + 1];
  if (next.kind === "crossfade" && nextPlaced?.fadeIn) fades.push({ from: end - nextPlaced.fadeIn, to: end, dir: "out", shape: "linear" });
  if (next.kind === "blend") fades.push({ from: end + post - next.sec, to: end + post, dir: "out", shape: "qsin" });
  if (next.kind === "dip") fades.push({ from: end - next.sec / 2, to: end, dir: "out", shape: "qsin" });
  return { pre, post, fades };
}

/** The fade factor (0–1) at timeline time t. qsin is ffmpeg's quarter sine (equal power). */
export function gainAt(fades: Fade[], t: number): number {
  let g = 1;
  for (const f of fades) {
    if (t <= f.from) {
      if (f.dir === "in") g *= 0;
      continue;
    }
    if (t >= f.to) {
      if (f.dir === "out") g *= 0;
      continue;
    }
    const x = (t - f.from) / (f.to - f.from);
    const v = f.dir === "in" ? x : 1 - x;
    g *= f.shape === "qsin" ? Math.sin((v * Math.PI) / 2) : v;
  }
  return g;
}

/** Split clip i at timeline time t; returns the new clip list (or the same if t is not inside). */
export function split(clips: Clip[], t: number): Clip[] {
  const placed = place(clips);
  const p = placed.find((p) => t > p.start + 0.05 && t < p.start + p.length - 0.05);
  if (!p) return clips;
  const cut = p.clip.in_sec + (t - p.start);
  const left: Clip = { ...p.clip, out_sec: cut };
  const right: Clip = { ...structuredClone(p.clip), id: newId(), in_sec: cut, transition_in: null };
  return [...clips.slice(0, p.index), left, right, ...clips.slice(p.index + 1)];
}

// ------------------------------------------------------------------ stitching videos

export type StitchMode = "whole" | "pieces";
export interface StitchItem {
  source: Pick<Source, "id" | "path" | "probe" | "outputs">;
  mode: StitchMode;
}

const TINY = 0.5; // shorter stretches join a neighbour rather than become a clip

/** The newest existing cleaned video output that covers [a, b] of the source. */
function newestCovering(outputs: Output[], a: number, b: number): Output | null {
  let best: Output | null = null;
  for (const o of outputs) {
    const w = o.window;
    if (!w || w.actual_duration_sec == null) continue;
    if (w.actual_start_sec <= a + 0.01 && w.actual_start_sec + w.actual_duration_sec >= b - 0.01) {
      if (!best || (o.created ?? "") > (best.created ?? "")) best = o;
    }
  }
  return best;
}

/**
 * One video as stretches, each heard through the newest cleaned output covering it, or
 * through the original sound (output null) where nothing covers it.
 */
export function stretches(source: StitchItem["source"]): { a: number; b: number; output: Output | null }[] {
  const length = source.probe?.duration_sec;
  if (!length) return [];
  const outs = source.outputs.filter((o) => o.exists && o.kind === "video" && o.window?.actual_duration_sec != null);
  const cuts = new Set([0, length]);
  for (const o of outs) {
    const a = o.window!.actual_start_sec, b = a + o.window!.actual_duration_sec!;
    if (a > 0 && a < length) cuts.add(a);
    if (b > 0 && b < length) cuts.add(b);
  }
  const bounds = [...cuts].sort((x, y) => x - y);
  const out: { a: number; b: number; output: Output | null }[] = [];
  for (let k = 0; k + 1 < bounds.length; k++) {
    const a = bounds[k], b = bounds[k + 1];
    if (b - a < 1e-6) continue;
    const output = newestCovering(outs, a, b);
    const last = out[out.length - 1];
    if (last && last.output === output) last.b = b;
    else out.push({ a, b, output });
  }
  // Fold slivers (rounding between windows) into a neighbour whose sound covers them.
  for (let k = 0; k < out.length; k++) {
    const s = out[k];
    if (s.b - s.a >= TINY || out.length === 1) continue;
    const covers = (n: (typeof out)[number] | undefined) =>
      !!n && (n.output === null || newestCovering([n.output], s.a, s.b) !== null);
    if (covers(out[k - 1])) out[k - 1].b = s.b;
    else if (covers(out[k + 1])) out[k + 1].a = s.a;
    else continue;
    out.splice(k, 1);
    k--;
  }
  // Neighbours that now share a sound become one.
  for (let k = 1; k < out.length; k++) {
    if (out[k].output === out[k - 1].output) {
      out[k - 1].b = out[k].b;
      out.splice(k, 1);
      k--;
    }
  }
  return out;
}

/**
 * Clips for several videos in order: the whole of each ("whole") or only its cleaned
 * stretches ("pieces"), each with the newest cleaned sound that covers it. Between
 * different videos, or where the sound changes, the clips meet with a soft cut of
 * `soft` seconds (0: a plain cut).
 */
export function stitchClips(items: StitchItem[], soft: number): Clip[] {
  const clips: Clip[] = [];
  for (const { source, mode } of items) {
    for (const s of stretches(source)) {
      if (mode === "pieces" && !s.output) continue;
      const prev = clips[clips.length - 1];
      const clip: Clip = { id: newId(), source: source.id, in_sec: s.a, out_sec: s.b, audio: audioFor(s.output) };
      const contiguous = prev && prev.source === source.id && Math.abs(prev.out_sec - s.a) < 0.02;
      const sameSound = contiguous && (prev.audio?.file ?? null) === (clip.audio?.file ?? null);
      clip.transition_in = prev && soft > 0 && !sameSound ? { type: "soft_cut", sec: soft } : null;
      clips.push(clip);
    }
  }
  return clips;
}

// ------------------------------------------------------------------ zoom

/**
 * A frame of the given zoom (1 = whole picture) shaped like the output, centred on (cx, cy)
 * and kept inside the picture. `shape` is the output's width / height, `picture` the
 * source's. Mirrors how export.framing reads it.
 */
export function frameAt(zoom: number, cx: number, cy: number, shape: number, picture: number): Frame | null {
  return zoom <= 1.001 ? null : placeFrame(zoom, cx, cy, shape, picture);
}

/** frameAt without the "whole picture" case: the frame drawn, at any zoom from 1. */
export function placeFrame(zoom: number, cx: number, cy: number, shape: number, picture: number): Frame {
  // The largest frame of the output's shape that fits the picture, then zoomed.
  let w = 1, h = 1;
  if (shape > picture) h = picture / shape;
  else w = shape / picture;
  w /= zoom;
  h /= zoom;
  const x = Math.min(1 - w, Math.max(0, cx - w / 2));
  const y = Math.min(1 - h, Math.max(0, cy - h / 2));
  return { x, y, w, h };
}

/** How far a frame zooms in, against the largest frame of its shape. */
export function zoomOf(f: Frame | null | undefined, shape: number, picture: number): number {
  if (!f) return 1;
  const full = shape > picture ? 1 : shape / picture;
  return full / f.w;
}

/** CSS that makes a video or image of the whole picture show only the frame, filling its box. */
export function frameStyle(f: Frame | null | undefined): string {
  if (!f) return "";
  return `object-fit: fill; width: ${100 / f.w}%; height: ${100 / f.h}%; left: ${(-f.x / f.w) * 100}%; top: ${(-f.y / f.h) * 100}%; right: auto; bottom: auto;`;
}


// ------------------------------------------------------------------ zoom glides

export const ZOOM_GLIDE_SEC = 1;
export const ZOOM_GLIDE_CHOICES = [0.5, 1, 1.5, 2, 3];

export function sameFrame(a: Frame | null | undefined, b: Frame | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  return (["x", "y", "w", "h"] as const).every((k) => Math.abs(a[k] - b[k]) < 1e-4);
}

/** Whether c continues prev's shot (same video, no gap) with another framing. */
export function zoomJoin(prev: Clip | null | undefined, c: Clip): boolean {
  return !!prev && prev.source === c.source && Math.abs(prev.out_sec - c.in_sec) < 0.05 &&
    !sameFrame(prev.frame, c.frame) && c.transition_in?.type !== "crossfade";
}

/** The glide into c (from prev's framing to c's), or null for a plain cut. */
export function zoomGlide(prev: Clip | null | undefined, c: Clip): { from: Frame | null; to: Frame | null; sec: number } | null {
  if (!zoomJoin(prev, c) || c.zoom_glide === false) return null;
  const sec = Math.min(c.zoom_glide?.sec ?? ZOOM_GLIDE_SEC, c.out_sec - c.in_sec);
  return sec > 0.04 ? { from: prev!.frame ?? null, to: c.frame ?? null, sec } : null;
}

/** The whole picture as seen in an output-shaped canvas (letterboxed or pillarboxed), as a
 *  frame in picture fractions: it reaches past 0..1 on the side with bars. */
export function canvasFrame(shape: number, picture: number): Frame {
  if (picture >= shape) {
    const h = picture / shape;
    return { x: 0, y: -(h - 1) / 2, w: 1, h };
  }
  const w = shape / picture;
  return { x: -(w - 1) / 2, y: 0, w, h: 1 };
}

/** The framing `t` seconds into a glide: eased (slow, faster, slow) between its ends. */
export function glideFrame(g: { from: Frame | null; to: Frame | null; sec: number }, t: number, shape: number, picture: number): Frame {
  const a = g.from ?? canvasFrame(shape, picture);
  const b = g.to ?? canvasFrame(shape, picture);
  const k = Math.max(0, Math.min(1, t / g.sec));
  const e = k * k * (3 - 2 * k);
  const mix = (p: number, q: number) => p + (q - p) * e;
  return { x: mix(a.x, b.x), y: mix(a.y, b.y), w: mix(a.w, b.w), h: mix(a.h, b.h) };
}

/** Another clip's framing carried onto a clip of a picture of shape `to`: the same zoom and
 *  the same point of the picture at its centre (identical when the shapes are the same). */
export function matchFrame(f: Frame | null | undefined, shape: number, from: number, to: number): Frame | null {
  if (!f) return null;
  return frameAt(zoomOf(f, shape, from), f.x + f.w / 2, f.y + f.h / 2, shape, to);
}

/** Clips with each one's automatic level (export.level_clips) added to its own gain, as
 * the export hears them (with_levels() in video_audio_cleaner/export.py). */
export function withLevels(clips: Clip[], levels: Record<string, { gain_db: number }> | null): Clip[] {
  if (!levels) return clips;
  return clips.map((c) => {
    const g = levels[c.id]?.gain_db ?? 0;
    if (!g || c.audio?.mute) return c;
    return { ...c, audio: { ...(c.audio ?? {}), gain_db: (c.audio?.gain_db ?? 0) + g } };
  });
}
