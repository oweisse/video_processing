<script lang="ts">
  // The whole original video, to look through and mark a stretch to clean. Plays the
  // editing proxy when the video is prepared (instant seeking), else the file itself.
  import { onMount } from "svelte";
  import { media, type Source } from "../lib/api";
  import { remember, resumeFor } from "../lib/playhead";
  import { formatClock } from "../lib/time";
  import { newCleanHref } from "./nav";

  let {
    source,
    start = null,
    time = $bindable(0),
    selection = $bindable(null),
  }: {
    source: Source;
    start?: number | null;
    time?: number;
    selection?: { start: number; end: number } | null;
  } = $props();

  let video = $state<HTMLVideoElement>();
  let playing = $state(false);
  let failed = $state(false);
  let markIn = $state<number | null>(null);
  const duration = $derived(source.probe?.duration_sec ?? 0);
  const fps = $derived(source.probe?.video?.fps ?? 25);
  const url = $derived(source.prep?.proxy ? `/media/proxy?source=${encodeURIComponent(source.id)}` : media.file(source.path));

  export function seek(t: number) {
    if (!video) return;
    const to = Math.max(0, Math.min(duration || video.duration || 0, t));
    video.currentTime = to;
    time = to;
  }

  function loaded() {
    // Where to begin: ?t= from the bar, else where this video was last heard.
    if (!video) return;
    const r = resumeFor(source.id, 0, duration || video.duration);
    const t = start ?? r?.t ?? 0;
    if (t) seek(t);
    if (r?.playing && start === null) void video.play().catch(() => {});
  }

  onMount(() => {
    let raf = 0;
    const tick = () => {
      if (video && !video.seeking) {
        time = video.currentTime;
        remember(source.id, time, "A", !video.paused);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  });

  function toggle() {
    if (!video) return;
    if (video.paused) void video.play().catch(() => {});
    else video.pause();
  }
  function setIn() {
    markIn = time;
    if (selection && selection.end > time + 1) selection = { start: time, end: selection.end };
    else selection = null;
  }
  function setOut() {
    const a = markIn ?? selection?.start ?? Math.max(0, time - 30);
    if (time - a >= 1) selection = { start: a, end: time };
  }
  function clear() {
    selection = null;
    markIn = null;
  }
  const cleanHref = $derived(
    selection
      ? newCleanHref(source, { start: Math.floor(selection.start), duration: Math.ceil(selection.end - Math.floor(selection.start)) })
      : null,
  );

  function keydown(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    if (ev.metaKey || ev.ctrlKey || ev.altKey || (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
    const step = ev.shiftKey ? 10 : 1;
    const k = ev.key.toLowerCase();
    if (k === " ") toggle();
    else if (k === "arrowleft") seek(time - step);
    else if (k === "arrowright") seek(time + step);
    else if (k === ",") seek(time - 1 / fps);
    else if (k === ".") seek(time + 1 / fps);
    else if (k === "home") seek(0);
    else if (k === "end") seek(duration);
    else if (k === "i") setIn();
    else if (k === "o") setOut();
    else if (k === "enter" && cleanHref) location.hash = cleanHref;
    else return;
    ev.preventDefault();
  }
</script>

<svelte:window onkeydown={keydown} />

<div class="player">
  <div class="screen">
    {#if failed}
      <div class="failed">
        <p>This file does not play in the app ({source.name.split(".").pop()?.toUpperCase()}).</p>
        <p class="faint">Prepare it for editing (in the details panel) to make a playable copy, then come back.</p>
      </div>
    {:else}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video bind:this={video} src={url} preload="auto" playsinline onloadedmetadata={loaded}
             onplay={() => (playing = true)} onpause={() => (playing = false)} onerror={() => (failed = true)}
             onclick={toggle}></video>
    {/if}
  </div>
  <div class="transport">
    <button class="play" onclick={toggle} disabled={failed} title="Space">{playing ? "❚❚ Pause" : "▶ Play"}</button>
    <span class="mono clock">{formatClock(time)} / {formatClock(duration)}</span>
    <button class="small" onclick={() => seek(time - 10)} title="⇧←">−10 s</button>
    <button class="small" onclick={() => seek(time + 10)} title="⇧→">+10 s</button>
    <span class="sep"></span>
    <button class="small" onclick={setIn} title="I: mark the start of the stretch to clean">In</button>
    <button class="small" onclick={setOut} title="O: mark its end">Out</button>
    {#if selection}
      <span class="mono sel">{formatClock(selection.start, false)}–{formatClock(selection.end, false)}</span>
      <span class="faint">({formatClock(selection.end - selection.start, false)})</span>
      <button class="small" onclick={clear}>Clear</button>
    {:else if markIn !== null}
      <span class="faint">from {formatClock(markIn, false)} — press O at the end</span>
    {:else}
      <span class="faint">Click and drag across the bar below to choose a span</span>
    {/if}
    <span class="spacer"></span>
    {#if cleanHref}
      <a href={cleanHref}><button class="primary" title="Enter">Clean this span…</button></a>
    {/if}
  </div>
  <div class="keys faint">
    <kbd>Space</kbd> play · <kbd>←</kbd><kbd>→</kbd> ±1 s (<kbd>⇧</kbd> ±10 s) · <kbd>,</kbd><kbd>.</kbd> frame ·
    <kbd>I</kbd>/<kbd>O</kbd> mark a span at the playhead · <kbd>Enter</kbd> clean it · on the bar below: click to jump, click and drag to choose a span
  </div>
</div>

<style>
  .player { display: flex; flex-direction: column; gap: 8px; padding: 10px; height: 100%; min-height: 0; }
  .screen { flex: 1; min-height: 160px; background: #000; border-radius: var(--radius); display: flex; align-items: center; justify-content: center; overflow: hidden; }
  video { width: 100%; height: 100%; object-fit: contain; display: block; cursor: pointer; }
  .failed { padding: 20px; text-align: center; }
  .transport { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .play { min-width: 84px; }
  .clock { min-width: 140px; }
  .sep { width: 10px; }
  .sel { color: var(--accent); }
  .keys { font-size: 11px; line-height: 1.8; }
</style>
