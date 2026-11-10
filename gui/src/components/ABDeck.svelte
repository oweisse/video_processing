<script lang="ts">
  // Transport, two-lane waveform and keyboard for an ABPlayer (docs/gui-design.md §6.2).
  import { onDestroy, onMount } from "svelte";
  import type { ABPlayer, Side } from "../lib/abplayer";
  import type { Decoded } from "../lib/audio";
  import { matchGains } from "../lib/loudness";
  import { peaks } from "../lib/peaks";
  import { formatClock } from "../lib/time";

  let {
    player,
    a,
    b,
    labelA = "Original",
    labelB = "Cleaned",
    fps = 30,
    levelMatch = $bindable(true),
    onframe,
  }: {
    player: ABPlayer;
    a: Decoded | null;
    b: Decoded | null;
    labelA?: string;
    labelB?: string;
    fps?: number;
    levelMatch?: boolean;
    onframe?: (time: number, playing: boolean) => void;
  } = $props();

  let time = $state(0);
  let playing = $state(false);
  // The deck is keyed to one player for its lifetime.
  // svelte-ignore state_referenced_locally
  let active = $state<Side>(player.active);
  let loopIn = $state<number | null>(null);
  let loopOut = $state<number | null>(null);
  let loopOn = $state(false);
  let flash = $state(0);
  let canvas: HTMLCanvasElement;
  let wrap: HTMLDivElement;
  let width = $state(800);

  const duration = $derived(a && b ? Math.min(a.buffer.duration, b.buffer.duration) : 0);
  const gains = $derived(
    a && b && levelMatch ? matchGains(a.level, b.level) : { gainA: 0, gainB: 0 },
  );

  $effect(() => {
    player.setLevels(gains.gainA, gains.gainB);
  });

  $effect(() => {
    player.setLoop(loopOn ? loopIn : null, loopOn ? loopOut : null);
  });

  // svelte-ignore state_referenced_locally
  const offState = player.on("state", () => {
    playing = player.playing;
    if (active !== player.active) flash++;
    active = player.active;
  });
  onDestroy(offState);

  let raf = 0;
  function frame() {
    time = player.time;
    onframe?.(time, player.playing);
    draw();
    raf = requestAnimationFrame(frame);
  }

  let ro: ResizeObserver;
  onMount(() => {
    raf = requestAnimationFrame(frame);
    ro = new ResizeObserver(() => (width = wrap.clientWidth));
    ro.observe(wrap);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  });

  function cssVar(name: string) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  const HEIGHT = 120;
  function draw() {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(100, Math.floor(width));
    if (canvas.width !== w * dpr) {
      canvas.width = w * dpr;
      canvas.height = HEIGHT * dpr;
    }
    const g = canvas.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, HEIGHT);
    if (!a || !b || !duration) return;
    const lane = HEIGHT / 2;
    const x = (t: number) => (t / duration) * w;
    if (loopOn && loopIn !== null && loopOut !== null) {
      g.fillStyle = cssVar("--bg-hover");
      g.fillRect(x(loopIn), 0, x(loopOut) - x(loopIn), HEIGHT);
    }
    const lanes: [Decoded, string, Side, number][] = [
      [a, cssVar("--a-color"), "A", 0],
      [b, cssVar("--b-color"), "B", lane],
    ];
    for (const [d, color, side, top] of lanes) {
      const cols = w;
      // Each side drawn at its level-matched gain, so the lanes compare honestly.
      const scale = 10 ** ((side === "A" ? gains.gainA : gains.gainB) / 20);
      const pk = peaks(d.buffer, Math.round((cols * d.buffer.duration) / duration));
      g.globalAlpha = side === active ? 1 : 0.35;
      g.fillStyle = color;
      const mid = top + lane / 2;
      const amp = (lane / 2 - 2) * scale;
      for (let i = 0; i < cols && i * 2 + 1 < pk.length; i++) {
        const lo = Math.max(-1, pk[i * 2]), hi = Math.min(1, pk[i * 2 + 1]);
        g.fillRect(i, mid - hi * amp, 1, Math.max(1, (hi - lo) * amp));
      }
    }
    g.globalAlpha = 1;
    g.strokeStyle = cssVar("--border");
    g.beginPath();
    g.moveTo(0, lane + 0.5);
    g.lineTo(w, lane + 0.5);
    g.stroke();
    g.fillStyle = cssVar("--text");
    g.fillRect(Math.round(x(time)), 0, 1.5, HEIGHT);
  }

  // ---------------------------------------------------------------- pointer
  let dragging = false;
  function timeFromEvent(ev: PointerEvent) {
    const r = canvas.getBoundingClientRect();
    return Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * duration;
  }
  let dragFrom = 0;
  function down(ev: PointerEvent) {
    if (!duration) return;
    dragging = true;
    canvas.setPointerCapture(ev.pointerId);
    dragFrom = timeFromEvent(ev);
    if (ev.shiftKey) {
      loopIn = dragFrom;
      loopOut = dragFrom;
    } else player.seek(dragFrom);
  }
  function move(ev: PointerEvent) {
    if (!dragging) return;
    const t = timeFromEvent(ev);
    if (ev.shiftKey) {
      loopIn = Math.min(dragFrom, t);
      loopOut = Math.max(dragFrom, t);
      loopOn = loopOut - loopIn > 0.1;
    } else player.seek(t);
  }
  function up() {
    dragging = false;
  }

  // ---------------------------------------------------------------- keyboard
  const HOLD_MS = 300;
  const held: Partial<Record<Side, { at: number; prev: Side }>> = {};
  function typing(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
  }
  function keydown(ev: KeyboardEvent) {
    if (typing(ev) || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const key = ev.key.toLowerCase();
    const step = ev.shiftKey ? 10 : 1;
    let handled = true;
    if (key === " ") player.togglePlay();
    else if (key === "a" || key === "b") {
      if (!ev.repeat) {
        const side = key.toUpperCase() as Side;
        held[side] = { at: performance.now(), prev: player.active };
        player.setActive(side);
      }
    } else if (key === "x" || key === "tab") player.toggle();
    else if (key === "arrowleft") player.seek(player.time - step);
    else if (key === "arrowright") player.seek(player.time + step);
    else if (key === "," && !player.playing) player.seek(player.time - 1 / fps);
    else if (key === "." && !player.playing) player.seek(player.time + 1 / fps);
    else if (key === "i") setIn();
    else if (key === "o") setOut();
    else if (key === "l") loopOn = !loopOn && loopIn !== null && loopOut !== null;
    else if (key === "m") levelMatch = !levelMatch;
    else if (key === "0" || key === "home") player.seek(loopOn && loopIn !== null ? loopIn : 0);
    else handled = false;
    if (handled) ev.preventDefault();
  }
  function keyup(ev: KeyboardEvent) {
    const side = ev.key.toUpperCase() as Side;
    const h = held[side];
    if (!h) return;
    delete held[side];
    // A tap switches; holding the key auditions that side until it is released.
    if (performance.now() - h.at > HOLD_MS) player.setActive(h.prev);
  }

  function setIn() {
    loopIn = player.time;
    if (loopOut !== null && loopOut <= loopIn) loopOut = null;
    loopOn = loopOut !== null;
  }
  function setOut() {
    loopOut = player.time;
    if (loopIn === null || loopIn >= loopOut) loopIn = Math.max(0, loopOut - 5);
    loopOn = true;
  }

  const fmtGain = (db: number) => (Math.abs(db) < 0.05 ? "0 dB" : `${db > 0 ? "+" : ""}${db.toFixed(1)} dB`);
</script>

<svelte:window onkeydown={keydown} onkeyup={keyup} />

<div class="deck">
  <div class="sides">
    <button class="side a" class:on={active === "A"} onclick={() => player.setActive("A")}>
      <span class="key">A</span> {labelA}
      {#if levelMatch && a && b}<span class="gain">{fmtGain(gains.gainA)}</span>{/if}
    </button>
    <button class="side b" class:on={active === "B"} onclick={() => player.setActive("B")}>
      <span class="key">B</span> {labelB}
      {#if levelMatch && a && b}<span class="gain">{fmtGain(gains.gainB)}</span>{/if}
    </button>
    {#key flash}
      <div class="now-playing" class:a={active === "A"} class:b={active === "B"}>
        {active === "A" ? labelA : labelB}
      </div>
    {/key}
  </div>

  <div class="wave" bind:this={wrap}>
    <canvas
      bind:this={canvas}
      style="width:100%;height:{HEIGHT}px"
      onpointerdown={down}
      onpointermove={move}
      onpointerup={up}
    ></canvas>
    {#if !a || !b}
      <div class="loading">Preparing audio…</div>
    {/if}
  </div>

  <div class="transport row">
    <button class="play" onclick={() => player.togglePlay()} disabled={!a || !b} title="Space">
      {playing ? "❚❚ Pause" : "▶ Play"}
    </button>
    <span class="mono clock">{formatClock(time)} / {formatClock(duration)}</span>
    <button class="small" onclick={setIn} title="I">In</button>
    <button class="small" onclick={setOut} title="O">Out</button>
    <label class="row small-label" title="L">
      <input type="checkbox" bind:checked={loopOn} disabled={loopIn === null || loopOut === null} /> Loop
      {#if loopIn !== null && loopOut !== null}
        <span class="mono faint">{formatClock(loopIn)}–{formatClock(loopOut)}</span>
      {/if}
    </label>
    <span class="spacer"></span>
    <label class="row small-label" title="M: play both sides at the same loudness, so louder never passes for better">
      <input type="checkbox" bind:checked={levelMatch} /> Level-match
    </label>
  </div>
  <div class="keys faint">
    <kbd>Space</kbd> play · <kbd>A</kbd>/<kbd>B</kbd> switch (hold to audition) · <kbd>X</kbd> toggle ·
    <kbd>←</kbd><kbd>→</kbd> ±1 s (<kbd>⇧</kbd> ±10 s) · <kbd>,</kbd><kbd>.</kbd> frame · <kbd>I</kbd>/<kbd>O</kbd>
    loop points · <kbd>L</kbd> loop · <kbd>M</kbd> level-match · ⇧-drag the waveform to loop a stretch
  </div>
</div>

<style>
  .deck { display: flex; flex-direction: column; gap: 10px; }
  .sides { display: flex; gap: 8px; align-items: stretch; }
  .side {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 12px; font-weight: 500; opacity: 0.6;
  }
  .side.on { opacity: 1; }
  .side.a.on { border-color: var(--a-color); box-shadow: inset 0 0 0 1px var(--a-color); }
  .side.b.on { border-color: var(--b-color); box-shadow: inset 0 0 0 1px var(--b-color); }
  .side .key {
    font-family: var(--mono); font-size: 11px; border: 1px solid var(--border);
    border-radius: 3px; padding: 0 4px;
  }
  .side.a .key { color: var(--a-color); }
  .side.b .key { color: var(--b-color); }
  .gain { font-size: 12px; color: var(--text-dim); font-variant-numeric: tabular-nums; }
  .now-playing {
    margin-left: auto; align-self: center;
    font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    animation: pop 0.35s ease-out;
  }
  .now-playing.a { color: var(--a-color); }
  .now-playing.b { color: var(--b-color); }
  @keyframes pop { from { transform: scale(1.25); } to { transform: scale(1); } }
  .wave {
    position: relative; background: var(--bg-sunken); border-radius: var(--radius);
    border: 1px solid var(--border); cursor: crosshair; touch-action: none;
  }
  canvas { display: block; }
  .loading {
    position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-dim);
  }
  .clock { min-width: 150px; }
  .play { min-width: 90px; }
  .small-label { font-size: 13px; gap: 5px; }
  .keys { font-size: 12px; line-height: 1.9; }
</style>
