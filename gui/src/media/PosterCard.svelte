<script lang="ts">
  // A source as a card: poster that scrubs through the video under the mouse, name, facts, badges.
  import type { Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { formatDuration } from "../lib/time";
  import { coverage } from "./coverage";

  let { source: s, selected = false, onselect, onopen }: {
    source: Source;
    selected?: boolean;
    onselect: () => void;
    onopen: () => void;
  } = $props();

  const SCRUB = 12; // media.SCRUB_FRAMES on the server
  const poster = $derived(`/media/poster?source=${encodeURIComponent(s.id)}&i=1`); // same spot as scrub frame 4
  const scrubUrl = (i: number) => `/media/poster?source=${encodeURIComponent(s.id)}&i=${i}&n=${SCRUB}`;

  // The twelve frames load on the first hover; until one has arrived the poster stays.
  let loaded = $state<boolean[]>([]);
  let hover = $state<number | null>(null);
  let failed = $state(false);
  let requested = false;
  function load() {
    if (requested) return;
    requested = true;
    for (let i = 0; i < SCRUB; i++) {
      const img = new Image();
      img.onload = () => (loaded[i] = true);
      img.src = scrubUrl(i);
    }
  }
  function move(ev: PointerEvent) {
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    hover = Math.max(0, Math.min(SCRUB - 1, Math.floor(((ev.clientX - r.left) / r.width) * SCRUB)));
    load();
  }
  const shown = $derived(hover !== null && loaded[hover] ? scrubUrl(hover) : poster);

  const share = $derived(coverage(s));
  const hasPicture = $derived(s.exists && s.probe?.video !== null);
  const v = $derived(s.probe?.video);
</script>

<button class="card-btn" class:selected onclick={onselect} ondblclick={onopen} title={s.path}>
  <div class="poster" onpointermove={move} onpointerleave={() => (hover = null)} role="presentation">
    {#if hasPicture && !failed}
      <img src={shown} alt="" loading="lazy" onerror={() => (failed = true)} />
    {:else}
      <span class="faint">{s.exists ? (s.probe ? "no picture" : "…") : "file missing"}</span>
    {/if}
    {#if hover !== null && s.probe?.duration_sec}
      <div class="scrub" style="left:{((hover + 0.5) / SCRUB) * 100}%"></div>
    {/if}
    {#if s.probe?.duration_sec}<span class="length">{formatDuration(s.probe.duration_sec)}</span>{/if}
  </div>
  <div class="name ellipsis">{ltr(s.name)}</div>
  <div class="meta">
    {#if v}<span class="faint">{v.height}p</span>{/if}
    {#if !s.exists}<span class="badge bad">missing</span>{/if}
    {#if share !== null && share > 0}
      <span class="badge clean" title="Share of the video with cleaned sound">cleaned {share >= 0.995 ? "" : `${Math.max(1, Math.round(share * 100))} %`}</span>
    {:else if s.plans.length}
      <span class="badge">tuned</span>
    {/if}
    {#if s.prep?.proxy}<span class="badge" title="Prepared for editing">ready to edit</span>{/if}
  </div>
</button>

<style>
  .card-btn {
    display: flex; flex-direction: column; gap: 4px; padding: 6px; text-align: left; white-space: normal;
    background: var(--bg-raised); border: 1px solid transparent; border-radius: var(--radius); min-width: 0;
  }
  .card-btn:hover:not(:disabled) { border-color: var(--border); }
  .card-btn.selected { border-color: var(--accent); background: var(--bg-hover); }
  .card-btn.selected .badge:not(.clean) { background: var(--bg-sunken); }
  .poster {
    position: relative; aspect-ratio: 16 / 9; background: #000; border-radius: 2px; overflow: hidden;
    display: flex; align-items: center; justify-content: center;
  }
  img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .scrub { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--playhead); pointer-events: none; }
  .length {
    position: absolute; right: 4px; bottom: 4px; padding: 0 4px; font-size: 10.5px;
    background: rgb(0 0 0 / 0.7); border-radius: 2px; font-variant-numeric: tabular-nums;
  }
  .name { font-weight: 500; }
  .meta { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; min-height: 16px; font-size: 10.5px; }
  .badge { padding: 0 5px; border-radius: 2px; background: var(--bg-hover); color: var(--text-dim); }
  .badge.clean { background: color-mix(in srgb, var(--b-color) 22%, transparent); color: var(--b-color); }
  .badge.bad { color: var(--danger); }
</style>
