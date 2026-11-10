<script lang="ts">
  // The whole video as a bar: frames along it, cleaned outputs on one lane, tuned windows on
  // another. Hover a piece to see it and where it falls in the picture strip; click to open
  // it. With a player (`onseek`), a click moves the playhead and a drag marks a span
  // (`onrange`), showing the frame under the mouse as it goes; without one, a click opens the whole video at that
  // moment and a drag starts a new clean of that range.
  import { media, type Source } from "../lib/api";
  import Filmstrip from "../components/Filmstrip.svelte";
  import { href } from "../lib/router.svelte";
  import { formatClock } from "../lib/time";
  import { pieces, type Piece } from "../media/pieces";
  import { newCleanHref, pieceHref } from "./nav";

  let { source, current = null, strip = 34, time = null, selection = null, onseek, onrange }: {
    source: Source;
    current?: string | null;
    strip?: number;
    time?: number | null;
    selection?: { start: number; end: number } | null;
    onseek?: (t: number) => void;
    onrange?: (start: number, end: number) => void;
  } = $props();
  let hovered = $state<Piece | null>(null);

  const list = $derived(pieces(source));
  const total = $derived(source.probe?.duration_sec ?? Math.max(1, ...list.map((p) => p.end)));

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
  const cleaned = $derived(lane(list.filter((p) => p.kind === "output")));
  const tuned = $derived(lane(list.filter((p) => p.kind === "tuned")));
  const rows = (l: { row: number }[]) => Math.max(1, ...l.map((x) => x.row + 1));
  const ROW = 11;

  let bar: HTMLDivElement;
  let drag = $state<{ a: number; b: number; x: number; moved: boolean; range: boolean; link: string | null } | null>(null);
  const at = (ev: PointerEvent) => {
    const r = bar.getBoundingClientRect();
    return Math.max(0, Math.min(total, ((ev.clientX - r.left) / r.width) * total));
  };
  function down(ev: PointerEvent) {
    // With a player a drag may start on a piece; a click on one still opens it (in up()).
    const link = (ev.target as HTMLElement).closest("a")?.getAttribute("href") ?? null;
    if (link && !onseek) return;
    bar.setPointerCapture(ev.pointerId);
    const t = at(ev);
    drag = { a: t, b: t, x: ev.clientX, moved: false, range: true, link };
    onseek?.(t);
  }
  function move(ev: PointerEvent) {
    if (!drag) return;
    drag.b = at(ev);
    if (Math.abs(ev.clientX - drag.x) > 3) drag.moved = true;
    onseek?.(drag.b);
  }
  function up() {
    if (!drag) return;
    const { range, moved, link } = drag;
    const a = Math.min(drag.a, drag.b), b = Math.max(drag.a, drag.b);
    drag = null;
    if (!range) return;
    if (!moved && link) {
      location.hash = link;
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
  const pct = (t: number) => (t / total) * 100;
  const currentPiece = $derived(list.find((p) => p.key === current) ?? null);
  const shown = $derived<{ start: number; end: number } | null>((drag ? null : hovered) ?? currentPiece ?? selection);
  const ticks = $derived.by(() => {
    const steps = [10, 30, 60, 120, 300, 600, 900, 1800, 3600];
    const step = steps.find((s) => total / s <= 8) ?? 3600;
    return Array.from({ length: Math.floor(total / step) + 1 }, (_, i) => i * step);
  });
</script>

<div class="piecebar">
  <div class="labels">
    <span style="height:{strip}px">Picture</span>
    <span style="height:{rows(cleaned) * ROW + 4}px">Cleaned</span>
    <span style="height:{rows(tuned) * ROW + 4}px">Tuned</span>
  </div>
  <div class="track" bind:this={bar} role="presentation" onpointerdown={down} onpointermove={move} onpointerup={up}
       title={onseek
         ? "Click to jump · click and drag to choose a span · click a piece to open it"
         : "Click to watch the whole video from there · drag to clean that range · click a piece to open it"}>
    <div class="film">
      <Filmstrip {source} height={strip} />
      {#if shown}
        <div class="shade" style="left:0;width:{pct(shown.start)}%"></div>
        <div class="shade" style="left:{pct(shown.end)}%;right:0"></div>
        <div class="span" style="left:{pct(shown.start)}%;width:{pct(shown.end - shown.start)}%"></div>
      {/if}
    </div>
    <div class="lane" style="height:{rows(cleaned) * ROW + 4}px">
      {#each cleaned as { p, row } (p.key)}
        <a class="block output" class:on={p.key === current} href={pieceHref(source, p)}
           style="left:{pct(p.start)}%;width:max(4px, {pct(p.end - p.start)}%);top:{row * ROW + 2}px"
           onclick={(e) => onseek && e.preventDefault()} onpointerenter={() => (hovered = p)} onpointerleave={() => (hovered = null)}
           aria-label="{p.label} · {p.detail}">{p.kind === "output" ? (p.output.tag ?? "") : ""}</a>
      {/each}
    </div>
    <div class="lane" style="height:{rows(tuned) * ROW + 4}px">
      {#each tuned as { p, row } (p.key)}
        <a class="block tuned" class:on={p.key === current} href={pieceHref(source, p)}
           style="left:{pct(p.start)}%;width:max(4px, {pct(p.end - p.start)}%);top:{row * ROW + 2}px"
           onclick={(e) => onseek && e.preventDefault()} onpointerenter={() => (hovered = p)} onpointerleave={() => (hovered = null)}
           aria-label="{p.label} · {p.detail}"></a>
      {/each}
    </div>
    {#if selection && !drag}
      <div class="range sel" style="left:{pct(selection.start)}%;width:{pct(selection.end - selection.start)}%"></div>
    {/if}
    {#if time !== null}
      <div class="playhead" style="left:{pct(time)}%"></div>
    {/if}
    {#if drag?.range && drag.moved}
      {@const a = Math.min(drag.a, drag.b)}
      {@const b = Math.max(drag.a, drag.b)}
      <div class="range" style="left:{pct(a)}%;width:{pct(b - a)}%"><span>{formatClock(a, false)}–{formatClock(b, false)}</span></div>
    {/if}
    {#if hovered && !drag && source.probe?.video}
      {@const mid = (hovered.start + hovered.end) / 2}
      <div class="preview" style="left:clamp(90px, {pct(mid)}%, calc(100% - 90px))">
        <img src={media.frame(source.id, mid, 320)} alt="" />
        <div class="row"><span class="mono">{hovered.label}</span><span class="faint ellipsis">{hovered.detail}</span></div>
      </div>
    {/if}
    <div class="ticks">
      {#each ticks as t}<span style="left:{pct(t)}%">{formatClock(t, false)}</span>{/each}
    </div>
  </div>
</div>

<style>
  .piecebar { display: flex; gap: 6px; padding: 6px 10px 4px; background: var(--bg-raised); border-top: 1px solid var(--gap); }
  .labels { display: flex; flex-direction: column; gap: 2px; width: 52px; font-size: 10.5px; color: var(--text-faint); }
  .labels span { display: flex; align-items: center; }
  .track { position: relative; flex: 1; display: flex; flex-direction: column; gap: 2px; padding-bottom: 14px; cursor: crosshair; user-select: none; }
  .film { position: relative; }
  .shade { position: absolute; top: 0; bottom: 0; background: rgb(0 0 0 / 0.6); pointer-events: none; }
  .span { position: absolute; top: 0; bottom: 0; outline: 2px solid var(--select); outline-offset: -1px; pointer-events: none; }
  .preview {
    position: absolute; bottom: calc(100% + 6px); transform: translateX(-50%); width: 180px; z-index: 5;
    padding: 4px; background: var(--bg-header); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: 0 6px 18px rgb(0 0 0 / 0.5); pointer-events: none;
  }
  .preview img { width: 100%; display: block; background: #000; border-radius: 2px; }
  .preview .row { gap: 6px; padding-top: 3px; font-size: 10.5px; }
  .lane { position: relative; background: var(--bg-sunken); border-radius: 2px; }
  .block {
    position: absolute; height: 9px; border-radius: 2px; opacity: 0.75; cursor: pointer;
    font-size: 8px; line-height: 9px; color: #000; overflow: hidden; white-space: nowrap; padding: 0 2px;
  }
  .block:hover { opacity: 1; text-decoration: none; }
  .block.output { background: var(--b-color); }
  .block.tuned { background: var(--accent); }
  .block.on { opacity: 1; outline: 2px solid var(--select); outline-offset: 0; z-index: 1; }
  .range {
    position: absolute; top: 0; bottom: 14px; z-index: 2; background: color-mix(in srgb, var(--accent) 25%, transparent);
    border: 1px solid var(--accent); pointer-events: none;
  }
  .range.sel { background: color-mix(in srgb, var(--accent) 15%, transparent); }
  .playhead { position: absolute; top: 0; bottom: 14px; width: 1.5px; margin-left: -0.75px; background: var(--playhead); z-index: 3; pointer-events: none; }
  .range span { position: absolute; top: -1px; left: 2px; font-size: 10px; white-space: nowrap; }
  .ticks { position: absolute; left: 0; right: 0; bottom: 0; height: 12px; }
  .ticks span { position: absolute; transform: translateX(-50%); font-size: 9.5px; color: var(--text-faint); }
  .ticks span:first-child { transform: none; }
</style>
