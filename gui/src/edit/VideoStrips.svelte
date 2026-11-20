<script lang="ts">
  // Every video of this timeline as a strip of frames with its cleaned stretches: click to
  // look at a moment in the Source viewer, drag to choose a clip
  // (docs/stitch-and-size-budget-plan.md, "pick clips").
  import type { Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { formatDuration } from "../lib/time";
  import Icon from "../components/Icon.svelte";
  import PieceBar from "../enhance/PieceBar.svelte";
  import { outputKey, type Piece } from "../media/pieces";
  import { prepareVideos, store } from "../lib/state.svelte";
  import { formatClock } from "../lib/time";
  import { loadPref, savePref } from "../lib/prefs";

  // Cleans of a video that are queued or running, drawn as placeholders on its strip.
  function pendingFor(id: string) {
    return store.jobs
      .filter((j) => j.type === "clean" && (j.state === "queued" || j.state === "running") && j.meta?.source_id === id)
      .map((j) => {
        const start = Number(j.meta?.start_sec ?? 0);
        const length = Number(j.meta?.duration_sec ?? 0) || (videos.find((v) => v.id === id)?.probe?.duration_sec ?? 0) - start;
        return { start, end: start + length,
                 label: `${j.state === "running" ? "cleaning" : "waiting to clean"} ${formatClock(start, false)}–${formatClock(start + length, false)}` };
      });
  }

  let { videos, current, time, marks, hearing, used, onseek, onrange, onpiece, onadd, onremove, onreorder, onversion }: {
    videos: Source[];
    current: string | null;
    time: number;
    marks: { start: number; end: number } | null;
    hearing: string | null; // the cleaned output heard in the Source viewer
    onpiece: (id: string, p: Piece, t: number) => void;
    used: Set<string>; // videos with clips on the timeline (cannot be removed)
    onseek: (id: string, t: number) => void;
    onrange: (id: string, start: number, end: number) => void;
    onadd: () => void;
    onremove: (id: string) => void;
    onreorder: (ids: string[]) => void;
    onversion?: (oldFile: string, now: { file: string; path: string } | null) => void;
  } = $props();

  // Videos still to prepare (not prepared, and not already queued for it).
  const preparing = $derived(new Set(store.jobs.filter((j) => j.type === "prep" && (j.state === "queued" || j.state === "running")).map((j) => j.meta?.source_id)));
  const unprepared = $derived(videos.filter((v) => v.exists && !(v.prep?.proxy && v.prep?.thumbs) && !preparing.has(v.id)));

  // ---------------------------------------------------------------- zoom
  // Each strip has its own zoom (1 = the whole video fits) and shows its own stretch, panned
  // with the bar above it or a sideways scroll. The header's Zoom sets every strip at once;
  // a strip's −/+ changes just that one. Scrolling only pans, never zooms.
  const MAX_ZOOM = 200;
  let zoom = $state<number>(loadPref("edit.stripZoom", 1));
  $effect(() => savePref("edit.stripZoom", zoom));
  let zooms = $state<Record<string, number>>(loadPref("edit.stripZooms", {}));
  $effect(() => savePref("edit.stripZooms", $state.snapshot(zooms)));
  const zoomFor = (v: Source) => zooms[v.id] ?? zoom;
  const clampZoom = (z: number) => Math.max(1, Math.min(MAX_ZOOM, z));
  const zoomLabel = (z: number) => (z < 1.05 ? "whole" : `${z < 10 ? z.toFixed(1) : Math.round(z)}×`);
  let centres = $state<Record<string, number>>({});
  const lengthOf = (v: Source) => v.probe?.duration_sec ?? 0;
  function viewOf(v: Source) {
    const d = lengthOf(v);
    const zoom = zoomFor(v);
    if (zoom <= 1.001 || !d) return null;
    const half = d / zoom / 2;
    const c = Math.max(half, Math.min(d - half, centres[v.id] ?? (v.id === current && time !== null ? time : half)));
    return { start: c - half, end: c + half };
  }
  /** Every strip at once (forgets the strips' own zooms). */
  /** Keep a strip's view where it is: a click on it moves the playhead, never the view. */
  function pin(v: Source) {
    const w = viewOf(v);
    if (w && centres[v.id] === undefined) centres[v.id] = (w.start + w.end) / 2;
    followed = time;   // this jump of the playhead is the user's, not playback's
  }
  function setZoom(z: number) {
    zoom = clampZoom(z);
    for (const k of Object.keys(zooms)) delete zooms[k];
  }
  /** One strip, keeping the moment at `frac` of its width (default the middle) in place. */
  function zoomStrip(v: Source, z: number, frac = 0.5) {
    const d = lengthOf(v);
    if (!d) return;
    const w = viewOf(v) ?? { start: 0, end: d };
    const under = w.start + frac * (w.end - w.start);
    zooms[v.id] = clampZoom(z);
    const span = d / zooms[v.id];
    centres[v.id] = under - frac * span + span / 2;
  }
  // The strip of the video being looked at follows its playhead when it moves out of the
  // view (not when the view is panned away from a still playhead).
  let followed: number | null = null;
  $effect(() => {
    const v = videos.find((x) => x.id === current);
    if (!v || time === null || time === followed) return;
    followed = time;
    const w = viewOf(v);
    if (w && (time < w.start || time > w.end)) {
      const span = w.end - w.start;
      centres[v.id] = time > w.end ? time + span * 0.4 : time - span * 0.4;
    }
  });
  function wheel(ev: WheelEvent, v: Source, el: HTMLElement) {
    const d = lengthOf(v);
    if (!d) return;
    const w = viewOf(v) ?? { start: 0, end: d };
    const r = el.getBoundingClientRect();
    // Scrolling never zooms a strip (only the Zoom slider and a strip's −/+ do). A pinch or
    // ⌘/ctrl-scroll would otherwise zoom the whole page, so it does nothing here.
    if (ev.metaKey || ev.ctrlKey) {
      ev.preventDefault();
      return;
    }
    const dx = ev.deltaX || (ev.shiftKey ? ev.deltaY : 0);
    if (!dx || zoomFor(v) <= 1.001) return;
    ev.preventDefault();
    const span = w.end - w.start;
    centres[v.id] = (w.start + w.end) / 2 + (dx / r.width) * span;
  }
  // Any scroll over the bar above a zoomed strip moves along it (a plain mouse wheel too).
  function panWheel(ev: WheelEvent, v: Source) {
    const w = viewOf(v);
    if (ev.metaKey || ev.ctrlKey) {
      ev.preventDefault();
      return;
    }
    if (!w) return;
    ev.preventDefault();
    ev.stopPropagation();
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    const dx = Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY;
    centres[v.id] = (w.start + w.end) / 2 + (dx / r.width) * lengthOf(v) * 0.5;
  }
  // Dragging the little scrollbar under a strip.
  let panning: { id: string; x: number; c: number; width: number; d: number } | null = null;
  function panDown(ev: PointerEvent, v: Source) {
    const w = viewOf(v);
    if (!w) return;
    const el = ev.currentTarget as HTMLElement;
    el.setPointerCapture(ev.pointerId);
    const r = el.getBoundingClientRect();
    const d = lengthOf(v);
    const thumb = (ev.target as HTMLElement).classList.contains("thumb");
    // a click beside the thumb moves the view there
    const c = thumb ? (w.start + w.end) / 2 : ((ev.clientX - r.left) / r.width) * d;
    centres[v.id] = c;
    panning = { id: v.id, x: ev.clientX, c, width: r.width, d };
  }
  function panMove(ev: PointerEvent) {
    if (!panning) return;
    centres[panning.id] = panning.c + ((ev.clientX - panning.x) / panning.width) * panning.d;
  }

  // Reordering: drag a row by its name, or send it to the top.
  let dragging = $state<string | null>(null);
  let over = $state<string | null>(null);
  function moveTo(id: string, before: string | null) {
    const ids = videos.map((v) => v.id).filter((x) => x !== id);
    const at = before === null ? 0 : ids.indexOf(before);
    ids.splice(at < 0 ? ids.length : at, 0, id);
    onreorder(ids);
  }
</script>

<div class="strips card">
  <div class="row head">
    <h3>Videos</h3>
    <span class="faint small">click a strip to look · drag along it to choose a clip · click a green bar to hear that version · drag a name to reorder</span>
    <span class="spacer"></span>
    <label class="row small zoom" title="Zoom every strip; a strip's own − / + zooms just that one; a sideways scroll (or ⇧-scroll) on a strip pans it">
      Zoom
      <input type="range" min="0" max={Math.log(MAX_ZOOM)} step="0.01" value={Math.log(zoom)}
             oninput={(e) => setZoom(Math.exp(Number((e.target as HTMLInputElement).value)))} />
      <span class="mono faint">{zoomLabel(zoom)}</span>
      {#if zoom > 1.001 || Object.keys(zooms).length}<button class="link small" onclick={() => setZoom(1)} title="Show every whole video">fit all</button>{/if}
    </label>
    {#if unprepared.length}
      <button class="small" onclick={() => prepareVideos(unprepared)}
              title="Make the light editing copies (needed to preview and add clips), one video after another">
        Prepare all ({unprepared.length})</button>
    {/if}
    <button class="small" onclick={onadd}><Icon name="plus" size={12} /> Add videos…</button>
  </div>
  {#each videos as v (v.id)}
    {@const view = viewOf(v)}
    <div class="video" class:on={v.id === current} class:over={over === v.id && dragging !== v.id}
         ondragover={(e) => { if (dragging) { e.preventDefault(); over = v.id; } }}
         ondragleave={() => (over = over === v.id ? null : over)}
         ondrop={() => { if (dragging && dragging !== v.id) moveTo(dragging, v.id); dragging = null; over = null; }}
         role="listitem">
      <div class="name col" role="button" tabindex="-1" draggable="true" ondragstart={() => (dragging = v.id)} ondragend={() => { dragging = null; over = null; }}
           title="Drag to reorder">
        <span class="ellipsis fullname" title="{v.name}
{v.path}">{ltr(v.name)}</span>
        <span class="faint small">{formatDuration(v.probe?.duration_sec)}{v.prep?.proxy ? "" : preparing.has(v.id) ? " · preparing…" : " · not prepared"}</span>
        <span class="row actions">
          {#if videos[0]?.id !== v.id}
            <button class="link small" onclick={() => moveTo(v.id, null)} title="Show this video first">to top</button>
          {/if}
          {#if !used.has(v.id)}
            <button class="link small" onclick={() => onremove(v.id)} title="Take this video off the list (it has no clips)">remove</button>
          {/if}
        </span>
        {#if lengthOf(v)}
          {@const z = zoomFor(v)}
          <span class="row strip-zoom">
            <button class="icon-btn" onclick={() => zoomStrip(v, z / 2)} disabled={z <= 1.001} title="Zoom this strip out">−</button>
            <span class="mono faint">{zoomLabel(z)}</span>
            <button class="icon-btn" onclick={() => zoomStrip(v, z * 2)} disabled={z >= MAX_ZOOM} title="Zoom this strip in">+</button>
            {#if z > 1.001}<button class="link small" onclick={() => zoomStrip(v, 1)} title="Show the whole video">fit</button>{/if}
          </span>
        {/if}
      </div>
      <div class="bar" role="presentation" onwheel={(e) => wheel(e, v, e.currentTarget as HTMLElement)}>
        {#if view}
          {@const d = lengthOf(v)}
          <div class="pan" role="presentation" onpointerdown={(e) => panDown(e, v)} onpointermove={panMove} onpointerup={() => (panning = null)}
               onwheel={(e) => panWheel(e, v)}
               title="Drag (or scroll over this bar) to move along the video">
            <div class="thumb" style="left:{(view.start / d) * 100}%;width:max(6px, {((view.end - view.start) / d) * 100}%)"></div>
            {#if v.id === current && time !== null}<div class="pan-head" style="left:{(time / d) * 100}%"></div>{/if}
          </div>
        {/if}
        <PieceBar source={v} compact openPieces={false} strip={52} {view}
                  current={v.id === current && hearing ? outputKey(hearing) : null}
                  tunedLane pending={pendingFor(v.id)}
                  onpiece={(p, t) => onpiece(v.id, p, t)}
                  time={v.id === current ? time : null} selection={v.id === current ? marks : null}
                  onseek={(t) => { pin(v); onseek(v.id, t); }} onrange={(a, b) => { pin(v); onrange(v.id, a, b); }} {onversion} />
      </div>
    </div>
  {:else}
    <p class="faint">No videos yet. <button class="link" onclick={onadd}>Add videos…</button> to pick clips from them.</p>
  {/each}
</div>

<style>
  /* no scroller of its own: the page scrolls under the pinned viewers, and this header
     sticks just below them (--pinned-h, set by the editor) */
  .strips { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; }
  .head { gap: 10px; position: sticky; top: var(--pinned-h, 0px); z-index: 3; margin: -8px -10px 0; padding: 8px 10px 4px; background: var(--bg-raised, var(--bg)); }
  .strip-zoom { gap: 4px; align-items: center; margin-top: 2px; }
  .strip-zoom .mono { min-width: 38px; text-align: center; font-size: 11px; }
  .icon-btn { width: 20px; height: 18px; padding: 0; line-height: 1; font-size: 13px; }
  .video { display: flex; gap: 8px; align-items: flex-start; padding: 3px 4px; border-radius: var(--radius); }
  .video.on { background: var(--bg-hover); }
  .video.over { box-shadow: inset 0 2px 0 var(--accent); }
  .name[draggable="true"] { cursor: grab; }
  .actions { gap: 8px; }
  .name:hover .fullname { white-space: normal; overflow-wrap: anywhere; }
  .name { width: 150px; flex: 0 0 150px; gap: 0; padding-top: 2px; }
  .bar { flex: 1; min-width: 0; }
  .zoom { gap: 6px; }
  .zoom input { width: 120px; }
  .pan { position: relative; height: 10px; margin-bottom: 2px; background: var(--bg-sunken); border-radius: 5px; cursor: pointer; touch-action: none; }
  .thumb { position: absolute; top: 0; bottom: 0; background: var(--text-dim); border-radius: 5px; cursor: grab; }
  .pan:hover .thumb { background: var(--accent); }
  .pan-head { position: absolute; top: -1px; bottom: -1px; width: 2px; margin-left: -1px; background: var(--playhead); pointer-events: none; }
  .small { font-size: 11px; }
  .link.small { align-self: flex-start; padding: 0; }
</style>
