// A video's pieces: every cleaned output and every tuned window, in time order, so the
// Enhance workspace can list them, draw them on one bar and step between them.
import type { Output, Plan, PlanWindow, Source } from "../lib/api";
import { formatClock } from "../lib/time";

export type Piece =
  | { kind: "output"; key: string; start: number; end: number; label: string; detail: string; output: Output }
  | { kind: "tuned"; key: string; start: number; end: number; label: string; detail: string; plan: Plan; window: PlanWindow };

export const outputKey = (file: string) => `out:${file}`;
export const tunedKey = (plan: string, label: string) => `tuned:${plan}#${label}`;

const span = (a: number, b: number) => `${formatClock(a, false)}–${formatClock(b, false)}`;

export function pieces(source: Pick<Source, "outputs" | "plans" | "probe">): Piece[] {
  const length = source.probe?.duration_sec ?? null;
  const list: Piece[] = [];
  for (const o of source.outputs) {
    if (o.kind === "compare") continue;
    const start = o.window?.actual_start_sec ?? 0;
    const dur = o.window?.actual_duration_sec ?? (length !== null ? length - start : 0);
    list.push({
      kind: "output", key: outputKey(o.file), start, end: start + dur, output: o,
      label: o.window ? span(start, start + dur) : "Whole video",
      detail: o.tag ?? "cleaned",
    });
  }
  for (const plan of source.plans) {
    for (const w of plan.windows) {
      list.push({
        kind: "tuned", key: tunedKey(plan.path, w.label), start: w.start_sec, end: w.end_sec, plan, window: w,
        label: span(w.start_sec, w.end_sec),
        detail: `${w.candidates.length} candidates${w.candidate_files ? "" : " (not kept)"}`,
      });
    }
  }
  // Time order; at the same start, cleaned outputs before the tuning they came from.
  return list.sort((a, b) => a.start - b.start || (a.kind === b.kind ? a.label.localeCompare(b.label) : a.kind === "output" ? -1 : 1));
}

/** The piece `step` places before (−1) or after (+1) `key`, or null at either end. */
export function neighbour(list: Piece[], key: string | null, step: -1 | 1): Piece | null {
  const i = list.findIndex((p) => p.key === key);
  if (i < 0) return list.length ? list[step > 0 ? 0 : list.length - 1] : null;
  return list[i + step] ?? null;
}
