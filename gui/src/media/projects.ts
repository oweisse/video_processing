// Putting videos into a project: into its list of videos (to pick clips from in Edit), or
// one cleaned piece straight onto its timeline. Used by the Add to project menu and by
// dragging videos onto a project in Media.
import { api, type Output, type Source } from "../lib/api";
import { ltr } from "../lib/bidi";
import { audioFor, newId, type Clip } from "../lib/edit";
import { loadLibrary, reportError, toast } from "../lib/state.svelte";
import { versionName } from "./pieces";

export async function addToProject(name: string, sources: Source[], piece: Output | null = null) {
  try {
    const p = await api.getProject(name);
    if (!piece && sources.every((s) => p.sources[s.id])) {
      toast(`${ltr(name)} already has ${sources.length === 1 ? ltr(sources[0].name) : "these videos"}`);
      return;
    }
    const order: string[] = p.video_order ?? Object.keys(p.sources);
    for (const s of sources) {
      p.sources[s.id] = { path: s.path, probe: s.probe };
      if (!order.includes(s.id)) order.push(s.id);
    }
    p.video_order = order;
    const w = piece?.window;
    if (piece && w && w.actual_duration_sec != null) {
      const clip: Clip = { id: newId(), source: sources[0].id, in_sec: w.actual_start_sec,
                           out_sec: w.actual_start_sec + w.actual_duration_sec, audio: audioFor(piece) };
      if (p.tracks.video.length) clip.transition_in = { type: "soft_cut", sec: 1 };
      p.tracks.video.push(clip);
    }
    await api.saveProject(name, p);
    // The editor's preview plays the light copies.
    for (const s of sources) if (!s.prep?.proxy) await api.submit({ type: "prep", source_id: s.id }).catch(() => {});
    await loadLibrary();
    toast(piece
      ? `Added ${piece.label ?? "the piece"} (${versionName(piece)}) of ${ltr(sources[0].name)} to the end of ${ltr(name)}`
      : `Added ${sources.length === 1 ? ltr(sources[0].name) : `${sources.length} videos`} to ${ltr(name)}`);
  } catch (e) {
    reportError(e);
  }
}

/** The drag payload type for videos dragged inside the app (source ids, JSON). */
export const VIDEO_DRAG = "application/x-vac-videos";
