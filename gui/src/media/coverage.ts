// How much of a source its cleaned video outputs cover, for the Media badges.
import type { Source } from "../lib/api";

/** Seconds covered by the union of the source's existing video outputs. */
export function coveredSeconds(source: Pick<Source, "outputs">): number {
  const spans = source.outputs
    .filter((o) => o.exists && o.kind === "video" && o.window?.actual_duration_sec)
    .map((o) => [o.window!.actual_start_sec, o.window!.actual_start_sec + o.window!.actual_duration_sec!] as const)
    .sort((a, b) => a[0] - b[0]);
  let total = 0;
  let end = -Infinity;
  for (const [a, b] of spans) {
    if (b <= end) continue;
    total += b - Math.max(a, end);
    end = b;
  }
  return total;
}

/** Share of the source covered (0–1), or null when its length is unknown. */
export function coverage(source: Pick<Source, "outputs" | "probe">): number | null {
  const d = source.probe?.duration_sec;
  return d ? Math.min(1, coveredSeconds(source) / d) : null;
}
