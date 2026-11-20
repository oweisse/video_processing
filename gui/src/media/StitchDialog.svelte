<script lang="ts">
  // Several videos into one new timeline, in an order the user sets: empty, to pick clips
  // from the videos' strips in Edit, or filled with each video whole or its cleaned
  // pieces, with soft sound between them (docs/stitch-and-size-budget-plan.md §4).
  import { onMount } from "svelte";
  import { api, type Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { displayWidth, stitchClips, stretches, suggestCanvas, type StitchMode } from "../lib/edit";
  import { go } from "../lib/router.svelte";
  import { loadLibrary, reportError, store } from "../lib/state.svelte";
  import { formatDuration } from "../lib/time";
  import Icon from "../components/Icon.svelte";

  let { sources, onclose }: { sources: Source[]; onclose: () => void } = $props();

  // svelte-ignore state_referenced_locally
  let rows = $state(sources.map((source) => ({ source })));
  let startWith = $state<"nothing" | StitchMode>("nothing");
  let name = $state("");
  let soft = $state(1);
  let busy = $state(false);
  let probing = $state(true);

  onMount(async () => {
    // Lengths and formats are needed to cut the videos and choose a common frame.
    await Promise.all(
      rows.map(async (r) => {
        if (!r.source.probe && r.source.exists) r.source.probe = await api.probe(r.source.id).catch(() => null);
      }),
    );
    probing = false;
  });

  const cleaned = (s: Source) => {
    const parts = stretches(s);
    return { some: parts.some((p) => p.output), all: parts.length > 0 && parts.every((p) => p.output) };
  };
  const total = $derived(rows.reduce((t, r) => t + (r.source.probe?.duration_sec ?? 0), 0));
  const clips = $derived(
    probing || startWith === "nothing" ? [] : stitchClips(rows.map((r) => ({ source: r.source, mode: startWith as StitchMode })), soft),
  );
  const length = $derived(clips.reduce((t, c) => t + c.out_sec - c.in_sec, 0));
  const canvas = $derived(
    suggestCanvas(
      rows
        .filter((r) => r.source.probe?.video)
        .map((r) => {
          const v = r.source.probe!.video!;
          return { display_width: displayWidth(v), height: v.height, fps: v.fps, seconds: r.source.probe!.duration_sec ?? 0 };
        }),
    ),
  );
  const notes = $derived.by(() => {
    if (!canvas) return [];
    const out: string[] = [];
    const videos = rows.map((r) => r.source.probe?.video).filter((v) => !!v);
    const shape = canvas.width / canvas.height;
    const other = videos.filter((v) => Math.abs(displayWidth(v) / v.height - shape) > 0.01).length;
    if (other) out.push(`${other} of ${videos.length} videos have another shape and get bars at the sides or top.`);
    for (const r of rows) {
      const v = r.source.probe?.video;
      if (v?.fps && Math.abs(v.fps - canvas.fps) > 0.01) out.push(`${ltr(r.source.name)} is ${v.fps} fps and is converted to ${canvas.fps}.`);
    }
    const small = videos.filter((v) => v.height < canvas.height).length;
    if (small) out.push(`${small} smaller video${small > 1 ? "s are" : " is"} scaled up to ${canvas.width}×${canvas.height}.`);
    return out;
  });
  const unprepared = $derived(rows.filter((r) => !r.source.prep?.proxy));
  const taken = $derived(new Set((store.library?.projects ?? []).map((p) => p.name)));
  const nameError = $derived(
    !name.trim() ? "" : taken.has(name.trim()) ? "A project with this name exists." : /[/\\]/.test(name) ? "No / or \\ in names." : "",
  );

  function move(i: number, d: -1 | 1) {
    const j = i + d;
    if (j < 0 || j >= rows.length) return;
    [rows[i], rows[j]] = [rows[j], rows[i]];
  }
  let dragFrom = $state<number | null>(null);
  function drop(i: number) {
    if (dragFrom === null || dragFrom === i) return;
    const [r] = rows.splice(dragFrom, 1);
    rows.splice(i, 0, r);
    dragFrom = null;
  }

  async function create() {
    const n = name.trim();
    if (!n || nameError || (startWith !== "nothing" && !clips.length)) return;
    busy = true;
    try {
      await api.createProject(n);
      const p = await api.getProject(n);
      for (const r of rows) p.sources[r.source.id] = { path: r.source.path, probe: r.source.probe };
      p.tracks.video = clips;
      p.canvas = canvas;
      p.export = { ...p.export, level_clips: true };
      await api.saveProject(n, p);
      // The preview plays the editing copies.
      for (const r of unprepared) await api.submit({ type: "prep", source_id: r.source.id }).catch(() => {});
      await loadLibrary();
      onclose();
      go(["edit", n]);
    } catch (e) {
      reportError(e);
    } finally {
      busy = false;
    }
  }

  function key(ev: KeyboardEvent) {
    if (ev.key === "Escape") onclose();
  }
</script>

<svelte:window onkeydown={key} />

<div class="backdrop" role="presentation" onclick={onclose}></div>
<div class="modal card" role="dialog" aria-label="New project">
  <div class="row"><h2>New project with {rows.length} video{rows.length === 1 ? "" : "s"}</h2><span class="spacer"></span>
    <button class="icon ghost" aria-label="Close" onclick={onclose}><Icon name="x" /></button></div>

  <label class="col field">Name
    <input bind:value={name} placeholder="e.g. Wedding 1994" maxlength="80" />
    {#if nameError}<span class="danger small">{nameError}</span>{/if}
  </label>

  <div class="col field">
    <span>Order <span class="faint">(drag, or use the arrows)</span></span>
    <ol class="rows">
      {#each rows as r, i (r.source.id)}
        {@const c = cleaned(r.source)}
        <li draggable="true" class:dragging={dragFrom === i} ondragstart={() => (dragFrom = i)}
            ondragover={(e) => e.preventDefault()} ondrop={() => drop(i)} ondragend={() => (dragFrom = null)}>
          <span class="n faint">{i + 1}</span>
          <span class="name ellipsis" title={r.source.path}>{ltr(r.source.name)}</span>
          <span class="faint">{formatDuration(r.source.probe?.duration_sec)}</span>
          <span class="faint small">{c.all ? "cleaned" : c.some ? "partly cleaned" : "not cleaned"}</span>
          <button class="icon ghost" aria-label="Move up" disabled={i === 0} onclick={() => move(i, -1)}>↑</button>
          <button class="icon ghost" aria-label="Move down" disabled={i === rows.length - 1} onclick={() => move(i, 1)}>↓</button>
        </li>
      {/each}
    </ol>
  </div>

  <div class="col field">
    <span>Start with</span>
    <label class="row"><input type="radio" bind:group={startWith} value="nothing" /> Nothing yet: I'll pick the clips from the videos in Edit</label>
    <label class="row"><input type="radio" bind:group={startWith} value="whole" /> Each video whole (cleaned sound where it exists)</label>
    <label class="row"><input type="radio" bind:group={startWith} value="pieces" /> Each video's cleaned pieces</label>
  </div>

  <label class="row field">Sound between clips
    <select bind:value={soft}>
      <option value={1}>soft, 1 s</option>
      <option value={0.5}>soft, 0.5 s</option>
      <option value={2}>soft, 2 s</option>
      <option value={0}>plain cut</option>
    </select>
  </label>

  <div class="summary small">
    {#if probing}
      <span class="faint">Reading the videos…</span>
    {:else}
      <div>{startWith === "nothing" ? "A project with these videos, nothing on its timeline yet" : `${clips.length} clips · ${formatDuration(length)}${length < total - 1 ? ` of ${formatDuration(total)}` : ""}`}
        {#if canvas} · picture {canvas.width}×{canvas.height} at {canvas.fps} fps{/if}</div>
      {#each notes as n}<div class="faint">{n}</div>{/each}
      {#if unprepared.length}
        <div class="faint">{unprepared.length} video{unprepared.length > 1 ? "s are" : " is"} prepared for preview first (a light copy, made once).</div>
      {/if}
      {#if startWith !== "nothing" && !clips.length}<div class="warn">Nothing to stitch: these videos have no cleaned pieces.</div>{/if}
    {/if}
  </div>

  <div class="row">
    <span class="spacer"></span>
    <button onclick={onclose}>Cancel</button>
    <button class="primary" disabled={busy || probing || !name.trim() || !!nameError || (startWith !== "nothing" && !clips.length)} onclick={create}>
      {busy ? "Creating…" : "Create project"}</button>
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.45); z-index: 50; }
  .modal {
    position: fixed; z-index: 51; left: 50%; top: 8vh; transform: translateX(-50%);
    width: min(720px, 92vw); max-height: 84vh; overflow: auto; display: flex; flex-direction: column; gap: 12px;
  }
  .field { gap: 4px; }
  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .rows li {
    display: flex; align-items: center; gap: 8px; padding: 4px 6px; background: var(--bg-sunken);
    border-radius: var(--radius); cursor: grab;
  }
  .rows li.dragging { opacity: 0.5; }
  .n { width: 16px; text-align: right; }
  .name { flex: 1; min-width: 0; }
  .summary { display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; background: var(--bg-sunken); border-radius: var(--radius); }
  .small { font-size: 11.5px; }
</style>
