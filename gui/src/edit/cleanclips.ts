// Cleaning a timeline's clips after the cut is decided: which stretches of which videos to
// clean, so every clip that still plays its original sound gets a cleaned version.
import type { Clip } from "../lib/edit";

/** Seconds a clean reaches past a clip, so soft sound into and out of it has cleaned sound to borrow. */
export const CLEAN_PAD = 2;
/** Clips of one video closer than this are cleaned as one stretch (one job, one consistent sound). */
export const MERGE_GAP = 20;

export interface Stretch {
  source: string;
  start: number;
  end: number;
  clips: string[]; // clip ids, in timeline order
}

/** The stretch to clean for one clip: the clip plus a little sound to spare on either side. */
export function clipStretch(c: Pick<Clip, "in_sec" | "out_sec">, length: number, pad = CLEAN_PAD): { start: number; end: number } {
  const start = Math.max(0, Math.floor(c.in_sec - pad));
  const end = Math.min(Number.isFinite(length) ? length : Infinity, Math.ceil(c.out_sec + pad));
  return { start, end };
}

/**
 * The stretches that cover the given clips: each clip padded, then stretches of the same
 * video that overlap or nearly touch merged into one. Ordered by where their first clip
 * sits on the timeline.
 */
export function cleanStretches(
  clips: Clip[],
  lengthOf: (source: string) => number,
  pad = CLEAN_PAD,
  gap = MERGE_GAP,
): Stretch[] {
  const order = new Map(clips.map((c, i) => [c.id, i]));
  const bySource = new Map<string, Clip[]>();
  for (const c of clips) bySource.set(c.source, [...(bySource.get(c.source) ?? []), c]);
  const out: Stretch[] = [];
  for (const [source, list] of bySource) {
    const sorted = [...list].sort((a, b) => a.in_sec - b.in_sec);
    let cur: Stretch | null = null;
    for (const c of sorted) {
      const { start, end } = clipStretch(c, lengthOf(source), pad);
      if (cur && start <= cur.end + gap) {
        cur.end = Math.max(cur.end, end);
        cur.clips.push(c.id);
      } else {
        cur = { source, start, end, clips: [c.id] };
        out.push(cur);
      }
    }
  }
  for (const s of out) s.clips.sort((a, b) => order.get(a)! - order.get(b)!);
  return out.sort((a, b) => order.get(a.clips[0])! - order.get(b.clips[0])!);
}

/** One stretch for the clean form's batch mode. */
export type BatchRow = { source: string; start: number; duration: number; note?: string; clips?: string[] };
