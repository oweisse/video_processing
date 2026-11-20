<script lang="ts">
  // The whole video as a bar: frames along it, cleaned outputs on one lane, tuned windows on
  // another. Hover a piece to see it and where it falls in the picture strip; click to open
  // it. With a player (`onseek`), a click moves the playhead and a drag marks a span
  // (`onrange`), showing the frame under the mouse as it goes; without one, a click opens the whole video at that
  // moment and a drag starts a new clean of that range.
  import { media, type Source } from "../lib/api";
  import Filmstrip from "../components/Filmstrip.svelte";
  import { href, links } from "../lib/router.svelte";
  import { deleteVersion, renameVersion } from "../media/versions";
  import { formatClock } from "../lib/time";
  import { pieces, versionName, type Piece } from "../media/pieces";
  import { newCleanHref, pieceHref } from "./nav";

  let {
    source, current = null, strip = 34, time = null, selection = null, onseek, onrange,
    compact = false, openPieces = true, onpiece, tunedLane = !compact, pending = [], view = null, onversion,
  }: {
    source: Source;
    current?: string | null;
    strip?: number;
    time?: number | null;
    selection?: { start: number; end: number } | null;
    onseek?: (t: number) => void;
    onrange?: (start: number, end: number) => void;
    compact?: boolean; // no lane labels and no tuned lane (rows of several videos)
    openPieces?: boolean; // false: a click on a piece moves the playhead like anywhere else
    onpiece?: (p: Piece, t: number) => void; // a click (not a drag) on a piece; the parent decides whether to seek
    tunedLane?: boolean; // show tuned windows (their candidates) under the cleaned lane
    pending?: { start: number; end: number; label: string }[]; // cleans queued or running
    view?: { start: number; end: number } | null; // zoomed in: the stretch shown (else the whole video)
    /** A version was renamed (to {file, path}) or deleted (null) from its right-click menu. */
    onversion?: (oldFile: string, now: { file: string; path: string } | null) => void;
  } = $props();
  let hovered = $state<Piece | null>(null);
  // Right-click on a green bar (a rendered version): compare, rename, delete.
  let menu = $state<{ p: Piece; x: number; y: number } | null>(null);
  // Found from what is under the mouse, so it works whatever element gets the event.
  function context(ev: MouseEvent) {
    const block = document.elementsFromPoint(ev.clientX, ev.clientY)
      .find((el) => el instanceof HTMLElement && el.matches(".block.output") && bar?.contains(el)) as HTMLElement | undefined;
    const p = block ? list.find((x) => x.key === block.dataset.key) : null;
    if (!p || p.kind !== "output") return;
    ev.preventDefault();
    ev.stopPropagation();
    drag = null;
    menu = { p, x: ev.clientX, y: ev.clientY };
  }

  const list = $derived(pieces(source));
  const total = $derived(source.probe?.duration_sec ?? Math.max(1, ...list.map((p) => p.end)));
  // The stretch drawn across the bar.
  const v0 = $derived(view ? Math.max(0, view.start) : 0);
  const v1 = $derived(view ? Math.min(total, Math.max(view.end, v0 + 0.5)) : total);

  // Stack overlapping pieces of one lane into rows.
  function lane(items: Piece[]) {
    const ends: number[] = [];
    return items.map((p) => {
      let row = ends.findIndex((e) => e <= p.start + 0.01);
      if (row < 0) row = ends.push(p.end) - 1;
      else ends[row] = p.end;
      return { p, row };
    });
  }
  // In compact rows (Edit's video strips) only what is in view takes room, so an empty lane
  // takes none and the pictures get the space; the full bar (Enhance) keeps every lane.
  const seen = (p: { start: number; end: number }) => !compact || (p.end >= v0 && p.start <= v1);
  const cleaned = $derived(lane(list.filter((p) => p.kind === "output" && seen(p))));
  const tuned = $derived(lane(list.filter((p) => p.kind === "tuned" && seen(p))));
  const pendingShown = $derived(pending.filter(seen));
  const rows = (l: { row: number }[]) => Math.max(compact ? 0 : 1, ...l.map((x) => x.row + 1));
  const ROW = $derived(compact ? 15 : 11); // compact rows (Edit) carry readable version names

  let bar: HTMLDivElement;
  let drag = $state<{ a: number; b: number; x: number; moved: boolean; range: boolean; link: string | null; piece: string | null } | null>(null);
  const at = (ev: PointerEvent) => {
    const r = bar.getBoundingClientRect();
    return Math.max(0, Math.min(total, v0 + ((ev.clientX - r.left) / r.width) * (v1 - v0)));
  };
  function down(ev: PointerEvent) {
    // Only the main button seeks and drags; a right-click opens a version's menu (context()).
    if (ev.button !== 0) return;
    // With a player a drag may start on a piece; a click on one still opens it (in up()).
    const block = (ev.target as HTMLElement).closest("a");
    const link = openPieces ? (block?.getAttribute("href") ?? null) : null;
    const piece = block?.dataset.key ?? null;
    if (link && !onseek) return;
    bar.setPointerCapture(ev.pointerId);
    const t = at(ev);
    drag = { a: t, b: t, x: ev.clientX, moved: false, range: true, link, piece };
    if (!(piece && onpiece)) onseek?.(t);
  }
  function move(ev: PointerEvent) {
    if (!drag) return;
    drag.b = at(ev);
    if (Math.abs(ev.clientX - drag.x) > 3) drag.moved = true;
    if (drag.moved || !(drag.piece && onpiece)) onseek?.(drag.b);
  }
  function up() {
    if (!drag) return;
    const { range, moved, link, piece } = drag;
    const a = Math.min(drag.a, drag.b), b = Math.max(drag.a, drag.b);
    drag = null;
    if (!range) return;
    if (!moved && link) {
      location.hash = link;
      return;
    }
    if (!moved && piece && onpiece) {
      const p = list.find((x) => x.key === piece);
      if (p) onpiece(p, a);
      return;
    }
    if (moved && b - a >= 1) {
      if (onrange) {
        onrange(a, b);
        onseek?.(a);
      }
      else location.hash = newCleanHref(source, { start: Math.floor(a), duration: Math.ceil(b - a) });
    } else if (!onseek && !moved) {
      location.hash = href(["enhance", source.id], { t: Math.floor(a * 10) / 10 });
    }
  }
  const pct = (t: number) => ((t - v0) / (v1 - v0)) * 100;
  // A span as left/width inside the bar (cut at its ends when zoomed in).
  const box = (a: number, b: number) => {
    const l = Math.max(0, Math.min(100, pct(a))), r = Math.max(0, Math.min(100, pct(b)));
    return `left:${l}%;width:${r - l}%`;
  };
  const inView = (t: number) => t >= v0 && t <= v1;
  // A piece's block, cut at the bar's ends so its name stays readable; hidden when out of view.
  const blockStyle = (a: number, b: number) => (b < v0 || a > v1 ? "display:none" : `${box(a, b)};min-width:4px`);
  const currentPiece = $derived(list.find((p) => p.key === current) ?? null);
  const shown = $derived<{ start: number; end: number } | null>((drag ? null : hovered) ?? selection ?? currentPiece);
  const ticks = $derived.by(() => {
    const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 900, 1800, 3600];
    const span = v1 - v0;
    const step = steps.find((s) => span / s <= 8) ?? 3600;
    const first = Math.ceil(v0 / step) * step;
    return Array.from({ length: Math.floor((v1 - first) / step + 1e-6) + 1 }, (_, i) => Math.round((first + i * step) * 10) / 10);
  });
</script>

{#if menu && menu.p.kind === "output"}
  {@const o = menu.p.output}
  <div class="vmenu-backdrop" role="presentation" onpointerdown={() => (menu = null)} oncontextmenu={(e) => { e.preventDefault(); menu = null; }}></div>
  <div class="vmenu card col" role="menu" style="left:{Math.min(menu.x, innerWidth - 230)}px;top:{Math.min(menu.y, innerHeight - 140)}px">
    <div class="faint tiny ellipsis" title={o.file}>{versionName(o)} · {o.file}</div>
    <a class="item" role="menuitem" href={links.output(o.file)} onclick={() => (menu = null)}>Compare with the original</a>
    <!-- read the version before closing the menu: closing it clears `o` -->
    <button class="item" role="menuitem" onclick={async () => { const v = o; menu = null; const r = await renameVersion(v); if (r) onversion?.(v.file, r); }}>Rename…</button>
    <button class="item danger" role="menuitem" onclick={async () => { const v = o; menu = null; if (await deleteVersion(v)) onversion?.(v.file, null); }}>Delete…</button>
  </div>
{/if}

<div class="piecebar" class:compact>
  {#if !compact}
    <div class="labels">
      <span style="height:{strip}px">Picture</span>
      <span style="height:{rows(cleaned) * ROW + 4}px">Cleaned</span>
      <span style="height:{rows(tuned) * ROW + 4}px">Tuned</span>
    </div>
  {/if}
  <div class="track" bind:this={bar} role="presentation" onpointerdown={down} onpointermove={move} onpointerup={up} oncontextmenu={context}
       title={onseek
         ? `Click to jump · click and drag to choose a span${openPieces ? " · click a piece to open it" : ""}`
         : "Click to watch the whole video from there · drag to clean that range · click a piece to open it"}>
    <div class="film">
      <Filmstrip {source} height={strip} start={v0} end={view ? v1 : null} />
      {#if shown}
        <div class="shade" style={box(v0, shown.start)}></div>
        <div class="shade" style={box(shown.end, v1)}></div>
        <div class="span" style={box(shown.start, shown.end)}></div>
      {/if}
    </div>
    {#if rows(cleaned) || pendingShown.length}
    <div class="lane" style="height:{(rows(cleaned) + (pendingShown.length ? 1 : 0)) * ROW + 4}px">
      {#each pendingShown as job, k (k)}
        <div class="block pending" title="{job.label}: cleaning…"
             style="{blockStyle(job.start, job.end)};top:{rows(cleaned) * ROW + 2}px;height:{ROW - 2}px;line-height:{ROW - 2}px"
             style:font-size={compact ? "10px" : null}>{job.label}</div>
      {/each}
      {#each cleaned as { p, row } (p.key)}
        <a class="block output" class:on={p.key === current} href={pieceHref(source, p)} data-key={p.key}
           style="{blockStyle(p.start, p.end)};top:{row * ROW + 2}px"
           onclick={(e) => onseek && e.preventDefault()} onpointerenter={() => (hovered = p)} onpointerleave={() => (hovered = null)}
           aria-label="{p.label} · {p.detail}" style:height="{ROW - 2}px" style:line-height="{ROW - 2}px"
           style:font-size={compact ? "10px" : null}>{p.kind === "output" ? versionName(p.output) : ""}</a>
      {/each}
    </div>
    {/if}
    {#if tunedLane && (!compact || tuned.length)}<div class="lane" style="height:{rows(tuned) * ROW + 4}px">
      {#each tuned as { p, row } (p.key)}
        <a class="block tuned" class:on={p.key === current} href={pieceHref(source, p)} data-key={p.key}
           style="{blockStyle(p.start, p.end)};top:{row * ROW + 2}px"
           onclick={(e) => onseek && e.preventDefault()} onpointerenter={() => (hovered = p)} onpointerleave={() => (hovered = null)}
           aria-label="{p.label} · {p.detail}" style:height="{ROW - 2}px" style:line-height="{ROW - 2}px"
           style:font-size={compact ? "10px" : null}>{p.detail}</a>
      {/each}
    </div>{/if}
    {#if selection && !drag}
      <div class="range sel" style={box(selection.start, selection.end)}></div>
    {/if}
    {#if time !== null && inView(time)}
      <div class="playhead" style="left:{pct(time)}%"></div>
    {/if}
    {#if drag?.range && drag.moved}
      {@const a = Math.min(drag.a, drag.b)}
      {@const b = Math.max(drag.a, drag.b)}
      <div class="range" style={box(a, b)}><span>{formatClock(a, false)}–{formatClock(b, false)}</span></div>
    {/if}
    {#if hovered && !drag && source.probe?.video}
      {@const mid = (hovered.start + hovered.end) / 2}
      <div class="preview" style="left:clamp(90px, {pct(mid)}%, calc(100% - 90px))">
        <img src={media.frame(source.id, mid, 320)} alt="" />
        <div class="row"><span class="mono">{hovered.label}</span><span class="faint ellipsis">{hovered.detail}</span></div>
      </div>
    {/if}
    <div class="ticks">
      {#each ticks as t}<span style="left:{pct(t)}%">{formatClock(t, v1 - v0 < 30)}</span>{/each}
    </div>
  </div>
</div>

<style>
  .piecebar { display: flex; gap: 6px; padding: 6px 10px 4px; background: var(--bg-raised); border-top: 1px solid var(--gap); }
  .piecebar.compact { padding: 0; background: none; border-top: none; }
  .labels { display: flex; flex-direction: column; gap: 2px; width: 52px; font-size: 10.5px; color: var(--text-faint); }
  .labels span { display: flex; align-items: center; }
  .track { position: relative; flex: 1; display: flex; flex-direction: column; gap: 2px; padding-bottom: 14px; cursor: crosshair; user-select: none; }
  .film { position: relative; overflow: hidden; }
  .shade { position: absolute; top: 0; bottom: 0; background: rgb(0 0 0 / 0.6); pointer-events: none; }
  .span { position: absolute; top: 0; bottom: 0; outline: 2px solid var(--select); outline-offset: -1px; pointer-events: none; }
  .preview {
    position: absolute; bottom: calc(100% + 6px); transform: translateX(-50%); width: 180px; z-index: 5;
    padding: 4px; background: var(--bg-header); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.5); pointer-events: none;
  }
  .preview img { width: 100%; display: block; background: #000; border-radius: 2px; }
  .preview .row { gap: 6px; padding-top: 3px; font-size: 10.5px; }
  .lane { position: relative; background: var(--bg-sunken); border-radius: 2px; overflow: hidden; }
  .block {
    position: absolute; height: 9px; border-radius: 2px; opacity: 0.75; cursor: pointer;
    font-size: 8px; line-height: 9px; color: #000; overflow: hidden; white-space: nowrap; padding: 0 2px;
  }
  .block:hover { opacity: 1; text-decoration: none; }
  .block.output { background: var(--b-color); }
  .block.tuned { background: var(--accent); color: #fff; }
  .block.pending {
    background: repeating-linear-gradient(135deg, var(--b-color) 0 6px, transparent 6px 12px);
    border: 1px solid var(--b-color); color: var(--text); opacity: 0.9; pointer-events: none;
  }
  .block.on { opacity: 1; outline: 2px solid var(--select); outline-offset: 0; z-index: 1; }
  .range {
    position: absolute; top: 0; bottom: 14px; z-index: 2; background: color-mix(in srgb, var(--accent) 25%, transparent);
    border: 1px solid var(--accent); pointer-events: none;
  }
  .range.sel { background: color-mix(in srgb, var(--accent) 15%, transparent); }
  .playhead { position: absolute; top: 0; bottom: 14px; width: 1.5px; margin-left: -0.75px; background: var(--playhead); z-index: 3; pointer-events: none; }
  .range span { position: absolute; top: -1px; left: 2px; font-size: 10px; white-space: nowrap; }
  .ticks { position: absolute; left: 0; right: 0; bottom: 0; height: 12px; overflow: hidden; }
  .ticks span { position: absolute; transform: translateX(-50%); font-size: 9.5px; color: var(--text-faint); }
  .ticks span:first-child { transform: none; }
  .vmenu-backdrop { position: fixed; inset: 0; z-index: 70; }
  .vmenu { position: fixed; z-index: 71; width: 220px; gap: 1px; padding: 6px; box-shadow: 0 8px 24px rgb(0 0 0 / 0.5); }
  .vmenu .item { display: block; text-align: left; background: none; border: none; padding: 5px 6px; border-radius: 4px; cursor: pointer; color: var(--text); font-size: 12.5px; }
  .vmenu .item:hover { background: var(--bg-hover); text-decoration: none; }
  .vmenu .item.danger { color: var(--danger, #e5534b); }
  .vmenu .tiny { font-size: 11px; padding: 0 6px 4px; }
</style>
