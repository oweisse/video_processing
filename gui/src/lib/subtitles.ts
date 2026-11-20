// Subtitles on the timeline (docs/subtitles-plan.md §4.6): each source video's cues, in
// its own time, shown where its clips play. Mirrors timeline_cues() in
// video_audio_cleaner/subs.py; both are checked against tests/data/subtitle_mapping.json.
import { place, type Clip } from "./edit";

export type SubLang = "mixed" | "en" | "he";
export type Track = SubLang | "off";

export interface CueTranslation {
  text: string;
  src: string; // the mixed text it was made from (normalised)
  run?: string | null;
  edited?: boolean;
  flags?: string[];
}

export interface Cue {
  id: string;
  start: number;
  end: number;
  text: string;
  lang: "he" | "en" | "mixed" | "other";
  run?: string | null;
  edited?: boolean;
  flags?: string[];
  tr?: Partial<Record<"en" | "he", CueTranslation>>;
}

export interface SubtitleDoc {
  schema: number;
  rev: number;
  source?: { id: string | null; path: string | null; duration_sec: number | null };
  coverage: [number, number][];
  runs?: unknown[];
  cues: Cue[];
}

export interface TimelineCue {
  start: number;
  end: number;
  text: string;
  cue: string;
  source: string;
  clip: string;
  missing: boolean; // English/Hebrew track: no translation yet, the spoken text is shown
  stale: boolean; // the translation was made from an older version of the line
}

const GAP = 0.04;
const MIN_CUE = 0.3;

export const normalise = (t: string | null | undefined) => (t ?? "").split(/\s+/).filter(Boolean).join(" ");

export function isStale(c: Cue, lang: "en" | "he"): boolean {
  const tr = c.tr?.[lang];
  return !!(tr && tr.text && tr.src !== normalise(c.text));
}

const HEB = /[֐-׿]/g;
const LAT = /[A-Za-zÀ-ɏ]/g;
export function scriptLang(text: string): Cue["lang"] {
  const heb = (text.match(HEB) ?? []).length;
  const lat = (text.match(LAT) ?? []).length;
  const total = heb + lat;
  if (!total) return "other";
  if (lat < 0.1 * total) return "he";
  if (heb < 0.1 * total) return "en";
  return "mixed";
}

/** Each clip's share of the timeline, crossfades split in the middle. */
function owned(clips: Clip[]) {
  const p = place(clips);
  const froms = p.map((x) => x.start + x.fadeIn / 2);
  const end = p.length ? p[p.length - 1].start + p[p.length - 1].length : 0;
  return p.map((x, i) => ({ clip: x.clip, start: x.start, a: froms[i], b: i + 1 < p.length ? froms[i + 1] : end }));
}

function itemText(c: Cue, lang: SubLang): [string, boolean] {
  if (lang === "mixed" || c.lang === lang) return [c.text, false];
  const tr = c.tr?.[lang];
  if (tr?.text) return [tr.text, false];
  return [c.text, true];
}

const r3 = (t: number) => Math.round(t * 1000) / 1000;

/** The cues the timeline shows, on the timeline's clock. */
export function timelineCues(clips: Clip[], docs: Record<string, SubtitleDoc | undefined>, lang: SubLang): TimelineCue[] {
  const items: TimelineCue[] = [];
  for (const { clip: c, start, a: ownA, b: ownB } of owned(clips)) {
    if (c.audio?.mute) continue;
    const doc = docs[c.source];
    if (!doc) continue;
    const s0 = c.in_sec + (ownA - start);
    const s1 = c.in_sec + (ownB - start);
    for (const cue of doc.cues) {
      const a = Math.max(cue.start, s0);
      const b = Math.min(cue.end, s1);
      const need = Math.min(0.4, (cue.end - cue.start) / 2);
      if (b - a < need - 1e-6) continue;
      const [text, missing] = itemText(cue, lang);
      items.push({
        start: start + (a - c.in_sec), end: start + (b - c.in_sec), text, cue: cue.id, source: c.source, clip: c.id,
        missing, stale: lang !== "mixed" && !missing && isStale(cue, lang),
      });
    }
  }
  items.sort((x, y) => x.start - y.start);
  const out: TimelineCue[] = [];
  for (const it of items) {
    const last = out[out.length - 1];
    // one line running on across a join (a split shot): one subtitle, not two
    if (last && last.cue === it.cue && last.source === it.source && it.start - last.end < 0.1) {
      last.end = Math.max(last.end, it.end);
      continue;
    }
    if (last && it.start < last.end + GAP) {
      last.end = it.start - GAP;
      if (last.end - last.start < MIN_CUE) out.pop();
    }
    if (it.end - it.start >= MIN_CUE) out.push(it);
  }
  for (const it of out) {
    it.start = r3(it.start);
    it.end = r3(it.end);
  }
  return out;
}

/** The item showing at time t (binary search over timelineCues' output). */
export function cueAt(items: TimelineCue[], t: number): TimelineCue | null {
  let lo = 0, hi = items.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const it = items[mid];
    if (t < it.start) hi = mid - 1;
    else if (t >= it.end) lo = mid + 1;
    else return it;
  }
  return null;
}

/** Parts of [start, end] outside coverage. */
export function uncovered(coverage: [number, number][], start: number, end: number): [number, number][] {
  const out: [number, number][] = [];
  let t = start;
  for (const [a, b] of [...coverage].sort((x, y) => x[0] - y[0])) {
    if (b <= t) continue;
    if (a >= end) break;
    if (a > t) out.push([t, Math.min(a, end)]);
    t = Math.max(t, b);
  }
  if (t < end) out.push([t, end]);
  return out.filter(([a, b]) => b - a > 0.05);
}

/** What a clip has: its cues' count and how far each translation got. */
export function clipStatus(clip: Clip, doc: SubtitleDoc | undefined) {
  const cues = (doc?.cues ?? []).filter((c) => c.end > clip.in_sec + 0.2 && c.start < clip.out_sec - 0.2);
  const gap = uncovered(doc?.coverage ?? [], clip.in_sec, clip.out_sec).reduce((s, [a, b]) => s + b - a, 0);
  const lang = (l: "en" | "he") => {
    const need = cues.filter((c) => c.lang !== l);
    const done = need.filter((c) => c.tr?.[l]?.text);
    return { done: done.length, of: need.length, stale: done.filter((c) => isStale(c, l)).length };
  };
  return {
    cues: cues.length, covered: gap < 0.5, uncoveredSec: gap,
    flagged: cues.filter((c) => c.flags?.length).length,
    en: lang("en"), he: lang("he"),
  };
}

/** Source time ↔ timeline time for one placed clip. */
export const toSource = (clip: Clip, clipStart: number, t: number) => clip.in_sec + (t - clipStart);
export const toTimeline = (clip: Clip, clipStart: number, s: number) => clipStart + (s - clip.in_sec);

export const FLAG_TEXT: Record<string, string> = {
  low_confidence: "The recognizer was unsure here",
  repeated: "The same words came out several times in a row",
  language_uncertain: "Hard to tell which language this is",
  chunk_edge: "Cut in the middle of speech; check the words at the edge",
  too_fast: "Reads fast: more than 20 characters a second",
  too_long: "Long: more than two lines",
  failed: "The translation failed; write it or try again",
  number_changed: "A number differs from the original",
  wrong_language: "Not in the expected language",
};
