<script lang="ts">
  // Zoom into a clip: a rectangle on the original picture shows what the resulting video
  // shows. Drag it to move, drag a corner (or scroll, or the slider) to zoom; it keeps the
  // resulting video's shape. Export crops that part and scales it up.
  import { media, type Source } from "../lib/api";
  import { displayWidth, frameAt, frameStyle, placeFrame, zoomOf, type Clip, type Frame } from "../lib/edit";
  import { formatClock } from "../lib/time";

  let { source, clip, shape, height, onapply, onclose }: {
    source: Source;
    clip: Clip;
    shape: number; // the resulting video's width / height
    height: number; // the resulting video's height in pixels
    onapply: (f: Frame | null) => void;
    onclose: () => void;
  } = $props();

  const v = $derived(source.probe?.video ?? null);
  const picture = $derived(v ? displayWidth(v) / v.height : 16 / 9);
  const MAX_ZOOM = 6;

  // svelte-ignore state_referenced_locally
  const start = clip.frame ?? null;
  // svelte-ignore state_referenced_locally
  let zoom = $state(zoomOf(start, shape, picture));
  let cx = $state(start ? start.x + start.w / 2 : 0.5);
  let cy = $state(start ? start.y + start.h / 2 : 0.5);
  const frame = $derived(frameAt(zoom, cx, cy, shape, picture));
  // The frame actually drawn (the whole picture's largest frame of the output's shape at zoom 1).
  const shown = $derived(placeFrame(Math.max(zoom, 1), cx, cy, shape, picture));
  // svelte-ignore state_referenced_locally
  let t = $state((clip.in_sec + clip.out_sec) / 2);
  const image = $derived(media.frame(source.id, t, 960));

  // Lines of the original picture the zoomed frame holds, against the output's height.
  const lines = $derived(v ? Math.round(shown.h * v.height) : null);
  const soft = $derived(!!frame && lines !== null && lines < height * 0.8);

  let stage = $state<HTMLDivElement | null>(null);
  function at(ev: PointerEvent) {
    const r = stage!.getBoundingClientRect();
    return { x: (ev.clientX - r.left) / r.width, y: (ev.clientY - r.top) / r.height };
  }
  const clampZoom = (z: number) => Math.max(1, Math.min(MAX_ZOOM, z));

  // Moving the frame, or resizing it from a corner with the opposite corner held.
  let drag: { kind: "move"; dx: number; dy: number } | { kind: "size"; ox: number; oy: number; sx: number; sy: number } | null = null;
  function down(ev: PointerEvent, kind: "move" | "nw" | "ne" | "sw" | "se" | "stage") {
    ev.preventDefault();
    ev.stopPropagation();
    const p = at(ev);
    (ev.currentTarget as Element).setPointerCapture(ev.pointerId);
    if (kind === "stage") {
      // a click outside the frame centres it there
      cx = p.x;
      cy = p.y;
      settle();
      drag = { kind: "move", dx: 0, dy: 0 };
    } else if (kind === "move") {
      drag = { kind: "move", dx: p.x - cx, dy: p.y - cy };
    } else {
      const f = shown;
      drag = {
        kind: "size",
        ox: kind.includes("w") ? f.x + f.w : f.x,
        oy: kind.includes("n") ? f.y + f.h : f.y,
        sx: kind.includes("w") ? -1 : 1,
        sy: kind.includes("n") ? -1 : 1,
      };
    }
  }
  function move(ev: PointerEvent) {
    if (!drag) return;
    const p = at(ev);
    if (drag.kind === "move") {
      cx = p.x - drag.dx;
      cy = p.y - drag.dy;
    } else {
      const full = placeFrame(1, 0.5, 0.5, shape, picture);
      // the size the pointer asks for, from whichever side gives the bigger frame
      const w = Math.max(Math.abs(p.x - drag.ox), Math.abs(p.y - drag.oy) * (full.w / full.h), full.w / MAX_ZOOM);
      zoom = clampZoom(full.w / w);
      const nw = full.w / zoom, nh = full.h / zoom;
      cx = drag.ox + (drag.sx * nw) / 2;
      cy = drag.oy + (drag.sy * nh) / 2;
    }
    settle();
  }
  function up() {
    drag = null;
  }
  // Keep the centre where the frame really is (it stops at the picture's edges).
  function settle() {
    const f = placeFrame(Math.max(zoom, 1), cx, cy, shape, picture);
    cx = f.x + f.w / 2;
    cy = f.y + f.h / 2;
  }
  function wheel(ev: WheelEvent) {
    ev.preventDefault();
    zoom = clampZoom(zoom * Math.exp(-ev.deltaY / 400));
    settle();
  }
  function key(ev: KeyboardEvent) {
    if (ev.key === "Enter") onapply(frame);
  }
</script>

<svelte:window onkeydown={key} />

<div class="frame-dialog col">
  <p class="dim small">The bright rectangle is what the resulting video shows of this clip. Drag it to move it, drag a corner
    or scroll to zoom, click elsewhere to centre it there.</p>
  <div class="panes">
    <div class="col grow">
      <div class="stage" bind:this={stage} style:aspect-ratio={picture} style:width="min(100%, calc(62vh * {picture}))" role="presentation"
           onpointerdown={(e) => down(e, "stage")} onpointermove={move} onpointerup={up} onwheel={wheel}>
        <img src={image} alt="" draggable="false" />
        <div class="rect" class:whole={!frame}
             style:left="{shown.x * 100}%" style:top="{shown.y * 100}%" style:width="{shown.w * 100}%" style:height="{shown.h * 100}%"
             role="presentation" onpointerdown={(e) => down(e, "move")} onpointermove={move} onpointerup={up}>
          {#each ["nw", "ne", "sw", "se"] as c (c)}
            <span class="handle {c}" role="presentation"
                  onpointerdown={(e) => down(e, c as "nw")} onpointermove={move} onpointerup={up}></span>
          {/each}
        </div>
      </div>
      <label class="row small">Look at
        <input type="range" class="grow" min={clip.in_sec} max={clip.out_sec} step="0.1" bind:value={t} />
        <span class="mono">{formatClock(t - clip.in_sec)} of {formatClock(clip.out_sec - clip.in_sec)}</span>
      </label>
      <span class="faint small">Check the frame over the whole clip: the picture moves, the frame stays put.</span>
    </div>
    <div class="col side">
      <span class="small">Resulting video</span>
      <div class="result" style:aspect-ratio={shape}>
        <img src={image} alt="" draggable="false" style={frameStyle(frame) || "object-fit: contain"} />
      </div>
      <label class="col small">Zoom {Math.round(zoom * 100)}%
        <input type="range" min="1" max={MAX_ZOOM} step="0.01" value={zoom}
               oninput={(e) => { zoom = Number((e.target as HTMLInputElement).value); settle(); }} />
      </label>
      {#if frame && lines !== null}
        <span class="small {soft ? 'warn' : 'faint'}">
          {soft ? `Only ${lines} lines of the original are shown, stretched to ${height}: it will look softer.`
                : `${lines} lines of the original, shown at ${height}.`}</span>
      {/if}
      <span class="spacer"></span>
      <button onclick={() => { zoom = 1; cx = 0.5; cy = 0.5; }} disabled={!frame}>Whole picture</button>
      <div class="row">
        <button onclick={onclose}>Cancel</button>
        <button class="primary" onclick={() => onapply(frame)}>Apply</button>
      </div>
    </div>
  </div>
</div>

<style>
  .frame-dialog { gap: 10px; }
  .panes { display: flex; gap: 16px; align-items: stretch; }
  .grow { flex: 1; min-width: 0; }
  .side { width: 240px; gap: 8px; }
  .stage {
    position: relative; align-self: center; background: #000; overflow: hidden;
    border-radius: var(--radius); cursor: crosshair; touch-action: none; user-select: none;
  }
  .stage img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; pointer-events: none; }
  .rect {
    position: absolute; box-sizing: border-box; border: 2px solid var(--accent); cursor: move;
    box-shadow: 0 0 0 9999px rgb(0 0 0 / 0.55);
  }
  .rect.whole { border-style: dashed; }
  .handle {
    position: absolute; width: 12px; height: 12px; background: var(--accent); border-radius: 2px;
  }
  .handle.nw { left: -7px; top: -7px; cursor: nwse-resize; }
  .handle.se { right: -7px; bottom: -7px; cursor: nwse-resize; }
  .handle.ne { right: -7px; top: -7px; cursor: nesw-resize; }
  .handle.sw { left: -7px; bottom: -7px; cursor: nesw-resize; }
  .result { position: relative; width: 100%; background: #000; overflow: hidden; border-radius: var(--radius); }
  .result img { position: absolute; inset: 0; width: 100%; height: 100%; }
  .small { font-size: 12px; }
</style>
