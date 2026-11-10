<script lang="ts">
  // Two panes and a draggable divider. The size of one pane (`fixed`) is kept in pixels and
  // remembered under `key`; the other takes the rest. Double-click the divider to reset.
  import type { Snippet } from "svelte";
  import { loadPref, savePref } from "../lib/prefs";

  let {
    direction = "row", key, initial, min = 120, max = Infinity, fixed = "first", first, second,
  }: {
    direction?: "row" | "column";
    key: string;
    initial: number;
    min?: number;
    max?: number;
    fixed?: "first" | "second";
    first: Snippet;
    second: Snippet;
  } = $props();

  // svelte-ignore state_referenced_locally
  let size = $state(loadPref<number>(`split.${key}`, initial));
  let box: HTMLDivElement;
  let dragging = $state(false);

  function clamp(v: number) {
    const total = box ? (direction === "row" ? box.clientWidth : box.clientHeight) : Infinity;
    return Math.max(min, Math.min(max, total - min, v));
  }

  function down(ev: PointerEvent) {
    ev.preventDefault();
    const target = ev.currentTarget as HTMLElement;
    target.setPointerCapture(ev.pointerId);
    dragging = true;
    const rect = box.getBoundingClientRect();
    const move = (e: PointerEvent) => {
      const pos = direction === "row" ? e.clientX - rect.left : e.clientY - rect.top;
      const total = direction === "row" ? rect.width : rect.height;
      size = clamp(fixed === "first" ? pos : total - pos);
    };
    const up = () => {
      dragging = false;
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      savePref(`split.${key}`, Math.round(size));
    };
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
  }

  function reset() {
    size = initial;
    savePref(`split.${key}`, initial);
  }

  const fixedStyle = $derived(`flex: 0 0 ${size}px; ${direction === "row" ? "width" : "height"}: ${size}px`);
</script>

<div class="split split-{direction}" class:dragging bind:this={box}>
  <div class="pane" style={fixed === "first" ? fixedStyle : "flex: 1 1 0"}>{@render first()}</div>
  <div class="divider" role="separator" aria-orientation={direction === "row" ? "vertical" : "horizontal"}
       onpointerdown={down} ondblclick={reset} title="Drag to resize · double-click to reset"></div>
  <div class="pane" style={fixed === "second" ? fixedStyle : "flex: 1 1 0"}>{@render second()}</div>
</div>

<style>
  .split { display: flex; width: 100%; height: 100%; min-width: 0; min-height: 0; }
  .split-column { flex-direction: column; }
  .pane { display: flex; flex-direction: column; min-width: 0; min-height: 0; overflow: hidden; }
  .divider { flex: 0 0 4px; background: var(--gap); }
  .split-row > .divider { cursor: col-resize; }
  .split-column > .divider { cursor: row-resize; }
  .divider:hover, .dragging > .divider { background: var(--accent-dim); }
  .dragging { user-select: none; }
</style>
