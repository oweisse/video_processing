// The editor's subtitles (docs/subtitles-plan.md §7.1): each source video's document,
// loaded for the project's videos, edited optimistically, reloaded while subtitle jobs
// write to it. Undo inside the Subtitles tab is its own stack (the project file is not
// involved: subtitles belong to the videos).
import { api, type SubtitleOp, type SubtitlesSetup } from "../lib/api";
import { loadPref, savePref } from "../lib/prefs";
import { reportError, toast } from "../lib/state.svelte";
import type { Cue, SubtitleDoc, Track } from "../lib/subtitles";

export const subs = $state<{
  docs: Record<string, SubtitleDoc>;
  track: Track;
  setup: SubtitlesSetup | null;
  undoCount: number; // how many edits can be undone (for the Undo button)
}>({ docs: {}, track: loadPref<Track>("edit.subtitleTrack", "mixed"), setup: null, undoCount: 0 });

export function setTrack(t: Track) {
  subs.track = t;
  savePref("edit.subtitleTrack", t);
}

const loading = new Map<string, Promise<void>>();
const asked = new Map<string, number>(); // the newest request per video
let seq = 0;
export function loadDoc(sourceId: string, force = false): Promise<void> {
  if (!force && (subs.docs[sourceId] || loading.has(sourceId))) return loading.get(sourceId) ?? Promise.resolve();
  const mine = ++seq;
  asked.set(sourceId, mine);
  const p = api.subtitles(sourceId)
    .then((d) => {
      // answers can arrive out of order while a job writes: an older request's answer, or
      // an older revision, must never replace newer lines
      const cur = subs.docs[sourceId];
      if (asked.get(sourceId) !== mine && cur && d.rev < cur.rev) return;
      if (cur && d.rev < cur.rev) return;
      subs.docs[sourceId] = d;
    })
    .catch(() => {})
    .finally(() => { if (loading.get(sourceId) === p) loading.delete(sourceId); });
  loading.set(sourceId, p);
  return p;
}

export async function loadSetup(fresh = false) {
  try {
    subs.setup = await api.subtitlesSetup(fresh);
  } catch (e) {
    reportError(e);
  }
  return subs.setup;
}

// ------------------------------------------------------------------ edits and undo
type Undo = { sourceId: string; ops: SubtitleOp[]; label: string };
const undoStack: Undo[] = [];

function snapshot(c: Cue): Cue {
  return JSON.parse(JSON.stringify(c));
}

/** Ops that put a cue back as it was: removed ones exactly (with translations). */
function restoreOps(before: Cue, now: Cue | undefined): SubtitleOp[] {
  if (!now) return [{ op: "put", cue: before }];
  const ops: SubtitleOp[] = [];
  if (now.text !== before.text) ops.push({ op: "text", id: now.id, text: before.text });
  for (const lang of ["en", "he"] as const) {
    const a = before.tr?.[lang]?.text ?? "";
    const b = now.tr?.[lang]?.text ?? "";
    if (a !== b) ops.push(a ? { op: "text", id: now.id, lang, text: a } : { op: "clear_tr", id: now.id, lang });
  }
  if (now.start !== before.start || now.end !== before.end) ops.push({ op: "time", id: now.id, start: before.start, end: before.end });
  return ops;
}

/** Apply edits to a source's subtitles: shown at once, saved, undoable. */
export async function edit(sourceId: string, ops: SubtitleOp[], label = "edit", record = true): Promise<boolean> {
  const doc = subs.docs[sourceId];
  if (!doc || !ops.length) return false;
  const touched = new Set(ops.map((o) => ("id" in o ? o.id : "")).filter(Boolean));
  // a stretch deleted at once: every cue in it counts as touched
  for (const o of ops) if (o.op === "delete_range")
    for (const c of doc.cues) if ((c.start + c.end) / 2 >= o.start && (c.start + c.end) / 2 < o.end) touched.add(c.id);
  const before = doc.cues.filter((c) => touched.has(c.id)).map(snapshot);
  const covered = ops.filter((o) => o.op === "delete_range")
    .map((o) => ({ op: "cover", start: (o as { start: number }).start, end: (o as { end: number }).end }) as SubtitleOp);
  // optimistic, for text and timing (structural ops wait for the server's answer)
  for (const o of ops) {
    const c = "id" in o ? doc.cues.find((x) => x.id === o.id) : undefined;
    if (!c) continue;
    if (o.op === "text" && (o.lang ?? "mixed") === "mixed") c.text = o.text;
    else if (o.op === "text" && o.lang && o.lang !== "mixed") c.tr = { ...(c.tr ?? {}), [o.lang]: { ...(c.tr?.[o.lang] ?? { src: "" }), text: o.text, edited: true, src: c.text.split(/\s+/).filter(Boolean).join(" ") } };
    else if (o.op === "time") { c.start = o.start; c.end = o.end; }
  }
  try {
    const r = await api.editSubtitles(sourceId, ops);
    const cur = subs.docs[sourceId];
    if (cur) {
      const removed = new Set(r.removed);
      const map = new Map(r.cues.map((c) => [c.id, c]));
      cur.cues = [
        ...cur.cues.filter((c) => !removed.has(c.id) && !map.has(c.id)),
        ...r.cues,
      ].sort((a, b) => a.start - b.start);
      cur.rev = r.rev;
      if (r.coverage) cur.coverage = r.coverage;
      if (record) {
        // undo in an order that cannot collide: remove what the edit created (split, add),
        // put the surviving cues back, then re-add what it removed (merge, delete)
        const created = r.cues.filter((c) => !before.some((b) => b.id === c.id) && !touched.has(c.id));
        const undo: SubtitleOp[] = created.map((c) => ({ op: "delete", id: c.id }) as SubtitleOp);
        const adds: SubtitleOp[] = [];
        for (const b of before) {
          const now = cur.cues.find((c) => c.id === b.id);
          (now ? undo : adds).push(...restoreOps(b, now));
        }
        undo.push(...adds, ...covered);
        if (undo.length) undoStack.push({ sourceId, ops: undo, label });
        if (undoStack.length > 200) undoStack.shift();
        subs.undoCount = undoStack.length;
      }
    }
    return true;
  } catch (e) {
    reportError(e);
    await loadDoc(sourceId, true);
    return false;
  }
}

export async function undo() {
  const u = undoStack.pop();
  subs.undoCount = undoStack.length;
  if (!u) return toast("Nothing to undo in the subtitles");
  // a merge or split cannot always be undone exactly: what can be put back is
  const ok = await edit(u.sourceId, u.ops, "undo", false);
  if (ok) toast(`Undid ${u.label}`);
}
export const canUndo = () => subs.undoCount > 0;
