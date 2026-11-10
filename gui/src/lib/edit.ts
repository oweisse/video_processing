// The edit model, mirroring video_audio_cleaner/export.py (docs/gui-design.md §8).
import type { Output, Probe, Source } from "./api";

export interface ClipAudio {
  file?: string; // a cleaned output covering the clip (absolute path, for export)
  output?: string; // its name in the library (for the player)
  start_sec?: number; // the source time where that output starts
  gain_db?: number;
  mute?: boolean;
}

export interface Clip {
  id: string;
  source: string;
  in_sec: number;
  out_sec: number;
  audio: ClipAudio | null; // null: the source's own audio
  transition_in?: { type: "crossfade"; sec: number } | null;
  video_filters?: string[];
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
  export: { encoder?: string; crf?: number; preset?: string; audio_bitrate?: string; target_lufs?: number; target_tp?: number; output?: string | null };
  modified?: string;
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

export function audioFor(o: Output | null, gain = 0): ClipAudio | null {
  if (!o || !o.window) return gain ? { gain_db: gain } : null;
  return { file: o.path, output: o.file, start_sec: o.window.actual_start_sec, gain_db: gain };
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
