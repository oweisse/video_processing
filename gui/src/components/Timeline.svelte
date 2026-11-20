<script lang="ts">
  // The edit's timeline: one picture+speech track, one music track (docs/gui-design.md §10).
  // Drag a clip to reorder, its edges to trim; drag music to move it. Changes are drafted
  // while dragging and committed on release.
  import { place, zoomGlide, type Clip, type MusicClip } from "../lib/edit";
  import { loadPref, savePref } from "../lib/prefs";
  import { findOutput } from "../lib/state.svelte";
  import { versionName, versionSettings } from "../media/pieces";
  import { formatClock } from "../lib/time";

  export type Selection = { kind: "clip" | "music"; id: string } | null;
  export interface ThumbInfo { interval: number; count: number; aspect: number }

  let {
    clips,
    music,
    time,
    pps = $bindable(20),
    viewWidth = $bindable(0),
    memory = null,
    selected,
    thumbs,
    sourceLength,
    sourceName,
    onseek,
    onselect,
    onchange,
    onsound,
  }: {
    clips: Clip[];
    music: MusicClip[];
    time: number;
    pps?: number;
    viewWidth?: number; // the visible width, for "fit"
    memory?: string | null; // a key to remember how far it is scrolled (per project), across reloads
    selected: Selection;
    thumbs: Record<string, ThumbInfo | undefined>;
    sourceLength: (sourceId: string) => number;
    sourceName: (sourceId: string) => string;
    onseek: (t: number) => void;
    onselect: (s: Selection) => void;
    onchange: (clips: Clip[], music: MusicClip[]) => void;
    /** The sound badge of a clip was clicked: choose its version there. */
    onsound?: (clipId: string, anchor: DOMRect) => void;
  } = $props();

  const outOf = (c: Clip) => (c.audio?.output ? findOutput(c.audio.output)?.output ?? null : null);
  let draftClips = $state<Clip[] | null>(null);
  let draftMusic = $state<MusicClip[] | null>(null);
  const shownClips = $derived(draftClips ?? clips);
  const shownMusic = $derived(draftMusic ?? music);
  const placed = $derived(place(shownClips));
  const editEnd = $derived(placed.length ? placed[placed.length - 1].start + placed[placed.length - 1].length : 0);
  const musicEnd = $derived(Math.max(0, ...shownMusic.map((m) => m.at_sec + m.out_sec - m.in_sec)));
  // A little room after the end (to drop a clip there), and never narrower than the view,
  // so "fit" shows the whole edit without a scrollbar.
  const width = $derived(Math.max(Math.max(editEnd, musicEnd, 1) * pps + 60, viewWidth));

  let scroller: HTMLDivElement;
  // How far the timeline is scrolled, so a clip's badge and name stay at the left of the
  // part of the clip in view instead of scrolling off with its left edge.
  let scrollX = $state(0);
  const stick = (start: number, length: number) =>
    Math.max(0, Math.min(scrollX - start * pps, length * pps - 140));
  let insertAt = $state<number | null>(null);

  // Remembered scroll: restored once the content is laid out, saved as it changes.
  let restored = false;
  $effect(() => {
    if (restored || !scroller || !memory || !viewWidth || width <= viewWidth) return;
    restored = true;
    const saved = loadPref<number>(memory, 0);
    if (saved > 0) scroller.scrollLeft = Math.min(saved, width - viewWidth);
    scrollX = scroller.scrollLeft;
  });
  function scrolled() {
    scrollX = scroller.scrollLeft;
    if (memory && restored) savePref(memory, Math.round(scroller.scrollLeft));
  }

  // Keep the playhead in view when it moves; zooming, opening the page (which keeps the
  // remembered view) or scrolling by hand never pulls the view back to it.
  let lastTime: number | null = null;
  $effect(() => {
    if (!scroller) return;
    const x = time * pps;
    if (lastTime === null || lastTime === time) {
      lastTime = time;
      return;
    }
    lastTime = time;
    const { scrollLeft, clientWidth } = scroller;
    if (x > scrollLeft + clientWidth - 40) scroller.scrollLeft = x - 80;
    else if (x < scrollLeft) scroller.scrollLeft = Math.max(0, x - 80);
  });

  const tick = $derived([1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800].find((s) => s * pps >= 70) ?? 3600);
  const ticks = $derived(Array.from({ length: Math.ceil(width / pps / tick) + 1 }, (_, i) => i * tick));

  // Scrolling never zooms (only the Zoom −/+ do, like the video strips); a pinch or
  // ⌘-scroll would otherwise zoom the whole page, so it does nothing here.
  function wheel(ev: WheelEvent) {
    if (ev.metaKey || ev.ctrlKey) ev.preventDefault();
  }

  function timeAt(ev: PointerEvent) {
    const rect = scroller.getBoundingClientRect();
    return Math.max(0, (scroller.scrollLeft + ev.clientX - rect.left) / pps);
  }

  // ---------------------------------------------------------------- dragging
  type Drag =
    | { kind: "seek" }
    | { kind: "clip-move" | "clip-in" | "clip-out"; id: string; x0: number; orig: Clip; moved: boolean }
    | { kind: "music-move" | "music-in" | "music-out"; id: string; x0: number; orig: MusicClip; moved: boolean };
  let drag: Drag | null = null;

  function startSeek(ev: PointerEvent) {
    drag = { kind: "seek" };
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    onseek(timeAt(ev));
  }

  function startClip(ev: PointerEvent, c: Clip, part: "move" | "in" | "out") {
    ev.stopPropagation();
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    onselect({ kind: "clip", id: c.id });
    drag = { kind: `clip-${part}`, id: c.id, x0: ev.clientX, orig: { ...c }, moved: false };
  }

  function startMusic(ev: PointerEvent, m: MusicClip, part: "move" | "in" | "out") {
    ev.stopPropagation();
    (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
    onselect({ kind: "music", id: m.id });
    drag = { kind: `music-${part}`, id: m.id, x0: ev.clientX, orig: { ...m }, moved: false };
  }

  const snap = (t: number) => (Math.abs(t - time) * pps < 6 ? time : t);

  function move(ev: PointerEvent) {
    if (!drag) return;
    if (drag.kind === "seek") {
      onseek(timeAt(ev));
      return;
    }
    const dt = (ev.clientX - drag.x0) / pps;
    if (!drag.moved && Math.abs(ev.clientX - drag.x0) < 4) return;
    drag.moved = true;
    if (drag.kind.startsWith("clip")) {
      const d = drag as Extract<Drag, { orig: Clip }>;
      const o = d.orig;
      const list = clips.map((c) => ({ ...c }));
      const i = list.findIndex((c) => c.id === d.id);
      if (d.kind === "clip-in") {
        list[i].in_sec = Math.max(0, Math.min(o.out_sec - 0.1, o.in_sec + dt));
      } else if (d.kind === "clip-out") {
        const start = place(clips)[i].start;
        const end = snap(start + (o.out_sec - o.in_sec) + dt);   // the clip's end on the timeline
        list[i].out_sec = Math.min(sourceLength(o.source), Math.max(o.in_sec + 0.1, o.in_sec + end - start));
      } else {
        const t = timeAt(ev);
        const others = place(clips.filter((c) => c.id !== d.id));
        insertAt = others.filter((p) => t > p.start + p.length / 2).length;
      }
      draftClips = list;
    } else {
      const d = drag as Extract<Drag, { orig: MusicClip }>;
      const o = d.orig;
      const list = music.map((m) => ({ ...m }));
      const m = list.find((m) => m.id === d.id)!;
      const maxOut = o.file_duration_sec ?? Infinity;
      if (d.kind === "music-move") m.at_sec = Math.max(0, snap(o.at_sec + dt));
      else if (d.kind === "music-in") {
        const shift = Math.max(-o.in_sec, Math.min(o.out_sec - o.in_sec - 0.5, dt));
        m.in_sec = o.in_sec + shift;
        m.at_sec = Math.max(0, o.at_sec + shift);
      } else m.out_sec = Math.min(maxOut, Math.max(o.in_sec + 0.5, o.out_sec + dt));
      draftMusic = list;
    }
  }

  function up() {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.kind === "clip-move" && d.moved && insertAt !== null) {
      const moving = clips.find((c) => c.id === d.id)!;
      const rest = clips.filter((c) => c.id !== d.id);
      const reordered = [...rest.slice(0, insertAt), moving, ...rest.slice(insertAt)]
        .map((c, i) => (i === 0 && c.transition_in ? { ...c, transition_in: null } : c));
      onchange(reordered, music);
    } else if (d.kind !== "seek" && d.moved && (draftClips || draftMusic)) {
      onchange(draftClips ?? clips, draftMusic ?? music);
    } else if (d.kind === "clip-move" && !d.moved) {
      const p = placed.find((p) => p.clip.id === d.id);
      if (p) onseek(Math.max(p.start, Math.min(time, p.start + p.length)));
    }
    draftClips = null;
    draftMusic = null;
    insertAt = null;
  }

  function thumbsFor(c: Clip, length: number) {
    const info = thumbs[c.source];
    if (!info || !info.count) return [];
    const tw = Math.round(52 * info.aspect);
    const n = Math.min(400, Math.ceil((length * pps) / tw));
    return Array.from({ length: n }, (_, k) => {
      const t = c.in_sec + (k * tw) / pps;
      return { x: k * tw, w: tw, i: Math.min(info.count - 1, Math.floor(t / info.interval)) };
    });
  }

  const insertX = $derived.by(() => {
    if (insertAt === null) return null;
    const others = place(shownClips.filter((c) => !(drag && "id" in drag && c.id === drag.id)));
    if (insertAt >= others.length) return others.length ? (others[others.length - 1].start + others[others.length - 1].length) * pps : 0;
    return others[insertAt].start * pps;
  });
</script>

<div class="timeline" bind:this={scroller} bind:clientWidth={viewWidth} onwheel={wheel} onscroll={scrolled}>
  <div class="content" style="width:{width}px" onpointermove={move} onpointerup={up} role="presentation">
    <div class="ruler" onpointerdown={startSeek} role="presentation">
      {#each ticks as t}
        <div class="tick" style="left:{t * pps}px"><span>{formatClock(t, false)}</span></div>
      {/each}
    </div>

    <div class="track video" role="presentation" onpointerdown={startSeek}>
      {#each placed as p (p.clip.id)}
        {@const c = p.clip}
        <div
          class="clip"
          class:sel={selected?.kind === "clip" && selected.id === c.id}
          class:original={!c.audio?.file && !c.audio?.mute}
          class:muted={c.audio?.mute}
          style="left:{p.start * pps}px;width:{Math.max(2, p.length * pps)}px;z-index:{p.index + 1}"
          onpointerdown={(e) => startClip(e, c, "move")}
          role="presentation"
          title="{sourceName(c.source)} {formatClock(c.in_sec)}–{formatClock(c.out_sec)}"
        >
          <div class="strip">
            {#each thumbsFor(c, p.length) as th (th.x)}
              <img src="/media/thumb?source={c.source}&i={th.i}" alt="" style="left:{th.x}px;width:{th.w}px" draggable="false" loading="lazy" />
            {/each}
          </div>
          {#if p.fadeIn}<div class="fade" style="width:{p.fadeIn * pps}px"></div>{/if}
          <button class="sound" style:left="{4 + stick(p.start, p.length)}px" style:max-width="calc(100% - {8 + stick(p.start, p.length)}px)" class:orig={!c.audio?.file && !c.audio?.mute} class:mute={c.audio?.mute}
                  title={c.audio?.mute ? "Muted · click to choose its sound"
                    : !c.audio?.file ? "Plays the original, uncleaned sound · click to choose a cleaned version"
                    : `Plays the cleaned version ${outOf(c) ? versionName(outOf(c)!) : c.audio.output}${outOf(c) ? ` (${versionSettings(outOf(c)!)})` : ""} · click to switch`}
                  onpointerdown={(e) => e.stopPropagation()}
                  onclick={(e) => { e.stopPropagation(); onselect({ kind: "clip", id: c.id }); onsound?.(c.id, (e.currentTarget as HTMLElement).getBoundingClientRect()); }}>
            {#if c.audio?.mute}🔇 muted{:else if !c.audio?.file}⚠ original sound{:else}🔊 {outOf(c) ? versionName(outOf(c)!) : (c.audio.output ?? "cleaned")}{#if outOf(c) && versionSettings(outOf(c)!)}<span class="dim"> · {versionSettings(outOf(c)!)}</span>{/if}{/if} ▾
          </button>
          <div class="label" style:left="{6 + stick(p.start, p.length)}px">{sourceName(c.source)} · {formatClock(c.in_sec, false)}</div>
          <div class="handle left" onpointerdown={(e) => startClip(e, c, "in")} role="presentation"></div>
          <div class="handle right" onpointerdown={(e) => startClip(e, c, "out")} role="presentation"></div>
        </div>
        {#if p.index > 0 && zoomGlide(placed[p.index - 1].clip, c)}
          {@const g = zoomGlide(placed[p.index - 1].clip, c)!}
          <div class="glide" style="left:{p.start * pps}px;width:{Math.max(3, g.sec * pps)}px"
               title="Zoom glide: the framing moves smoothly into this clip's over {g.sec} s (select the clip to change or turn off)"></div>
        {/if}
        {#if c.transition_in?.type === "soft_cut" && p.index > 0}
          <div class="soft" style="left:{p.start * pps - 7}px" title="Soft sound into this clip ({c.transition_in.sec} s)">
            <svg viewBox="-1 -1 12 12" width="14" height="14" aria-hidden="true"><path d="M5 0 L10 5 L5 10 L0 5 Z" /></svg>
          </div>
        {/if}
      {/each}
      {#if insertX !== null}<div class="insert" style="left:{insertX}px"></div>{/if}
      {#if !placed.length}<div class="empty">Add clips from the source viewer above (mark In and Out, then ＋ Add).</div>{/if}
    </div>

    <div class="track music" role="presentation" onpointerdown={startSeek}>
      {#each shownMusic as m (m.id)}
        <div
          class="mclip"
          class:sel={selected?.kind === "music" && selected.id === m.id}
          style="left:{m.at_sec * pps}px;width:{Math.max(2, (m.out_sec - m.in_sec) * pps)}px"
          onpointerdown={(e) => startMusic(e, m, "move")}
          role="presentation"
        >
          <span class="label">♪ {m.name ?? m.file.split("/").pop()} · {m.gain_db} dB</span>
          <div class="handle left" onpointerdown={(e) => startMusic(e, m, "in")} role="presentation"></div>
          <div class="handle right" onpointerdown={(e) => startMusic(e, m, "out")} role="presentation"></div>
        </div>
      {/each}
    </div>

    <div class="playhead" style="left:{time * pps}px"></div>
  </div>
</div>

<style>
  .timeline { isolation: isolate;
    overflow-x: auto; overflow-y: hidden; background: var(--bg-sunken);
    border: 1px solid var(--border); border-radius: var(--radius); user-select: none;
  }
  .content { position: relative; height: 162px; }
  .ruler { position: relative; height: 22px; border-bottom: 1px solid var(--border); cursor: text; overflow: hidden; }
  .tick { position: absolute; top: 0; height: 100%; border-left: 1px solid var(--border); }
  .tick span { position: absolute; left: 3px; top: 3px; font-size: 10.5px; color: var(--text-faint); white-space: nowrap; }
  .track { position: relative; border-bottom: 1px solid var(--border); }
  .track.video { height: 86px; }
  .track.music { height: 52px; }
  .clip, .mclip {
    position: absolute; top: 5px; bottom: 5px; border-radius: 5px; overflow: hidden; cursor: grab;
    border: 1px solid var(--b-color); background: color-mix(in srgb, var(--b-color) 18%, var(--bg-raised));
  }
  .clip.original { border-color: var(--a-color); background: color-mix(in srgb, var(--a-color) 18%, var(--bg-raised)); }
  .clip.muted { border-color: var(--text-faint); }
  .clip.sel, .mclip.sel { outline: 2px solid var(--accent); outline-offset: 1px; }
  .strip { position: absolute; inset: 0 0 18px 0; overflow: hidden; }
  .strip img { position: absolute; top: 0; height: 100%; object-fit: cover; pointer-events: none; opacity: 0.9; }
  /* A soft cut: the picture cuts here, the sound passes softly. */
  .soft {
    position: absolute; top: 50%; margin-top: -7px; width: 14px; height: 14px;
    pointer-events: none; z-index: 1000; fill: var(--select); stroke: #000; stroke-width: 1.2;
  }
  .fade {
    position: absolute; left: 0; top: 0; bottom: 0; pointer-events: none;
    background: linear-gradient(to right, rgb(0 0 0 / 0.55), transparent);
  }
  .sound {
    position: absolute; left: 4px; top: 4px; max-width: calc(100% - 8px); z-index: 2;
    padding: 1px 6px; font-size: 10.5px; line-height: 15px; border-radius: 4px; cursor: pointer;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    background: color-mix(in srgb, var(--b-color) 85%, black); color: #000; border: none;
  }
  .sound .dim { color: rgb(0 0 0 / 0.65); }
  .sound.orig { background: var(--a-color); color: #000; }
  .sound.mute { background: var(--bg-header); color: var(--text-dim); }
  .sound:hover { filter: brightness(1.15); }
  .label {
    position: absolute; left: 6px; right: 6px; bottom: 2px; font-size: 11px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; color: var(--text); pointer-events: none;
  }
  .mclip { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 20%, var(--bg-raised)); }
  .mclip .label { bottom: auto; top: 50%; transform: translateY(-50%); }
  .handle { position: absolute; top: 0; bottom: 0; width: 7px; cursor: ew-resize; }
  .handle.left { left: 0; }
  .handle.right { right: 0; }
  .handle:hover { background: rgb(255 255 255 / 0.25); }
  .glide {
    position: absolute; top: 2px; height: 4px; z-index: 900; pointer-events: auto; border-radius: 2px;
    background: linear-gradient(to right, var(--accent), transparent); opacity: 0.9;
  }
  .insert { position: absolute; top: 0; bottom: 0; width: 3px; margin-left: -1px; background: var(--accent); }
  .playhead { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--danger); pointer-events: none; z-index: 100; }
  .empty { position: absolute; left: 14px; top: 32px; color: var(--text-faint); font-size: 13px; }
</style>
