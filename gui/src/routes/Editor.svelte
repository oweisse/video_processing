<script lang="ts">
  // The editor (docs/gui-design.md §8-§10): pick ranges from prepared sources, arrange
  // them on the timeline, lay music under them, export one MP4.
  import { onDestroy } from "svelte";
  import { ltr } from "../lib/bidi";
  import { api, media, type ExportPlan, type Source } from "../lib/api";
  import { audioContext } from "../lib/audio";
  import {
    audioFor, coveringOutput, duration as editDuration, newId, place, split,
    type Clip, type MusicClip, type Project,
  } from "../lib/edit";
  import { Program } from "../lib/program";
  import { go, href, links } from "../lib/router.svelte";
  import { nativePick, reportError, store, toast, queued } from "../lib/state.svelte";
  import { formatClock, formatDuration } from "../lib/time";
  import FolderBrowser from "../components/FolderBrowser.svelte";
  import Timeline, { type Selection, type ThumbInfo } from "../components/Timeline.svelte";

  let { name }: { name: string } = $props();

  let project = $state<Project | null>(null);
  let error = $state<string | null>(null);
  let selected = $state<Selection>(null);
  let pps = $state(20);
  let time = $state(0);
  let playing = $state(false);
  let saving = $state<"saved" | "saving" | "error">("saved");
  const undo: string[] = [];
  const redo: string[] = [];
  let historyTick = $state(0);

  // ---------------------------------------------------------------- loading and saving
  // svelte-ignore state_referenced_locally
  api.getProject(name).then((p) => (project = p)).catch((e) => (error = e.message));

  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  function scheduleSave() {
    saving = "saving";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 600);
  }
  async function flush() {
    clearTimeout(saveTimer);
    if (!project) return;
    try {
      await api.saveProject(name, $state.snapshot(project));
      saving = "saved";
    } catch (e) {
      saving = "error";
      reportError(e);
    }
  }

  function commit(mutate: (p: Project) => void) {
    if (!project) return;
    const before = JSON.stringify($state.snapshot(project));
    const next = JSON.parse(before) as Project;
    mutate(next);
    if (JSON.stringify(next) === before) return;
    undo.push(before);
    if (undo.length > 200) undo.shift();
    redo.length = 0;
    historyTick++;
    project = next;
    scheduleSave();
  }
  function undoStep() {
    const prev = undo.pop();
    if (!prev || !project) return;
    redo.push(JSON.stringify($state.snapshot(project)));
    project = JSON.parse(prev);
    historyTick++;
    scheduleSave();
  }
  function redoStep() {
    const next = redo.pop();
    if (!next || !project) return;
    undo.push(JSON.stringify($state.snapshot(project)));
    project = JSON.parse(next);
    historyTick++;
    scheduleSave();
  }

  // ---------------------------------------------------------------- sources
  const sources = $derived((store.library?.sources ?? []).filter((s) => s.exists));
  const sourceById = (id: string) => store.library?.sources.find((s) => s.id === id) ?? null;
  const sourceLength = (id: string) =>
    sourceById(id)?.probe?.duration_sec ?? project?.sources[id]?.probe?.duration_sec ?? Infinity;
  const sourceName = (id: string) => sourceById(id)?.name ?? project?.sources[id]?.path.split("/").pop() ?? id;

  let thumbs = $state<Record<string, ThumbInfo | undefined>>({});
  $effect(() => {
    for (const s of sources) {
      if (s.prep?.thumbs && !thumbs[s.id]) {
        const v = s.probe?.video;
        const aspect = v ? v.width / v.height : 16 / 9;
        thumbs[s.id] = { interval: 2, count: 0, aspect };
        api.thumbs(s.id).then((t) => (thumbs[s.id] = { ...t, aspect })).catch(() => (thumbs[s.id] = undefined));
      }
    }
  });

  // ---------------------------------------------------------------- program player
  let videoA = $state<HTMLVideoElement>();
  let videoB = $state<HTMLVideoElement>();
  let program = $state<Program | null>(null);
  let raf = 0;
  // The videos only exist once the project has loaded.
  $effect(() => {
    if (!videoA || !videoB || program) return;
    const pr = new Program(audioContext(), [videoA, videoB], {
      proxy: (id) => `/media/proxy?source=${encodeURIComponent(id)}`,
      clipAudio: (c) =>
        c.audio?.output && c.audio.start_sec !== undefined
          ? { url: media.outputAudio(c.audio.output), offset: c.in_sec - c.audio.start_sec }
          : { url: media.original(c.source, c.in_sec, c.out_sec - c.in_sec), offset: 0 },
      music: (m) => media.file(m.file),
    });
    pr.onchange(() => (playing = pr.playing));
    const loop = () => {
      pr.frame();
      time = pr.time;
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    program = pr;
  });
  onDestroy(() => {
    cancelAnimationFrame(raf);
    program?.dispose();
    void flush();
  });
  $effect(() => {
    if (!project || !program) return;
    const snap = $state.snapshot(project.tracks) as Project["tracks"];
    void program.setEdit(snap.video, snap.music);
  });

  const total = $derived(project ? editDuration(project.tracks.video) : 0);
  const fps = $derived(project?.canvas?.fps ?? 30);

  // ---------------------------------------------------------------- source viewer
  let srcId = $state("");
  $effect(() => {
    if (!srcId && sources.length) srcId = (project && Object.keys(project.sources)[0]) || sources[0].id;
  });
  const src = $derived(sourceById(srcId));
  let srcVideo = $state<HTMLVideoElement | null>(null);
  let srcTime = $state(0);
  let srcPlaying = $state(false);
  let markIn = $state<number | null>(null);
  let markOut = $state<number | null>(null);
  $effect(() => {
    void srcId;
    markIn = null;
    markOut = null;
  });

  const prepJob = $derived(
    store.jobs.find((j) => j.type === "prep" && j.meta?.source_id === srcId && (j.state === "running" || j.state === "queued")),
  );
  async function prepare(s: Source) {
    try {
      await api.submit({ type: "prep", source_id: s.id });
      toast(`Preparing ${s.name}: proxy, thumbnails and waveform`);
    } catch (e) {
      reportError(e);
    }
  }

  function srcToggle() {
    if (!srcVideo) return;
    if (srcVideo.paused) {
      program?.pause();
      void srcVideo.play();
    } else srcVideo.pause();
  }

  function addClip(atPlayhead = false) {
    const s = src;
    if (!s || !project) return;
    const length = s.probe?.duration_sec ?? Infinity;
    const inSec = Math.max(0, markIn ?? srcTime);
    const outSec = Math.min(length, markOut !== null && markOut > inSec + 0.1 ? markOut : inSec + 10);
    const out = coveringOutput(s, inSec, outSec);
    const clip: Clip = { id: newId(), source: s.id, in_sec: inSec, out_sec: outSec, audio: audioFor(out) };
    commit((p) => {
      p.sources[s.id] = { path: s.path, probe: s.probe };
      let clips = p.tracks.video;
      let at = clips.length;
      if (atPlayhead && clips.length) {
        clips = split(clips, time);
        const placed = place(clips);
        at = placed.filter((q) => q.start + q.length / 2 <= time).length;
      }
      p.tracks.video = [...clips.slice(0, at), clip, ...clips.slice(at)];
    });
    selected = { kind: "clip", id: clip.id };
    if (!out) toast("No cleaned output covers this range: the clip plays its original sound. Use “Clean this range” in the inspector.");
    markIn = null;
    markOut = null;
  }

  // ---------------------------------------------------------------- selection and inspector
  const selClip = $derived(selected?.kind === "clip" ? project?.tracks.video.find((c) => c.id === selected!.id) ?? null : null);
  const selIndex = $derived(selClip && project ? project.tracks.video.findIndex((c) => c.id === selClip.id) : -1);
  const selMusic = $derived(selected?.kind === "music" ? project?.tracks.music.find((m) => m.id === selected!.id) ?? null : null);
  const covering = $derived.by(() => {
    if (!selClip) return [];
    const s = sourceById(selClip.source);
    return (s?.outputs ?? []).filter((o) => {
      const w = o.window;
      return o.exists && o.kind === "video" && w && w.actual_duration_sec != null &&
        w.actual_start_sec <= selClip.in_sec + 0.01 && w.actual_start_sec + w.actual_duration_sec >= selClip.out_sec - 0.01;
    });
  });

  function updateClip(id: string, change: (c: Clip) => void) {
    commit((p) => {
      const c = p.tracks.video.find((c) => c.id === id);
      if (!c) return;
      change(c);
      // A trim can leave the cleaned audio short: fall back to another covering output, or the original.
      if (c.audio?.output) {
        const s = sourceById(c.source);
        const o = coveringOutput(s, c.in_sec, c.out_sec, c.audio.output);
        if (!o) {
          c.audio = c.audio.gain_db ? { gain_db: c.audio.gain_db } : null;
          toast("The cleaned audio does not cover the new range: this clip now plays its original sound.");
        } else if (o.file !== c.audio.output) {
          c.audio = audioFor(o, c.audio.gain_db ?? 0);
        }
      }
    });
  }
  function setAudio(id: string, choice: string) {
    updateClip(id, (c) => {
      const gain = c.audio?.gain_db ?? 0;
      if (choice === "__original") c.audio = gain ? { gain_db: gain } : null;
      else if (choice === "__mute") c.audio = { mute: true };
      else c.audio = audioFor(covering.find((o) => o.file === choice) ?? null, gain);
    });
  }
  function updateMusic(id: string, change: (m: MusicClip) => void) {
    commit((p) => {
      const m = p.tracks.music.find((m) => m.id === id);
      if (m) change(m);
    });
  }

  function removeSelected() {
    if (!selected) return;
    const sel = selected;
    commit((p) => {
      if (sel.kind === "clip") {
        p.tracks.video = p.tracks.video.filter((c) => c.id !== sel.id);
        if (p.tracks.video[0]?.transition_in) p.tracks.video[0].transition_in = null;
      } else p.tracks.music = p.tracks.music.filter((m) => m.id !== sel.id);
    });
    selected = null;
  }

  // ---------------------------------------------------------------- music
  let browsingMusic = $state(false);
  async function chooseMusic() {
    const picked = await nativePick("audio");
    if (picked === undefined) browsingMusic = true;
    else if (picked) await addMusic(picked);
  }
  async function addMusic(path: string) {
    browsingMusic = false;
    try {
      const stored = await api.addMusic(name, path);
      const length = stored.duration_sec ?? Math.max(10, total);
      const m: MusicClip = {
        id: newId("m"), file: stored.path, name: path.split("/").pop(), at_sec: 0, in_sec: 0,
        out_sec: Math.min(length, Math.max(total, 10)), file_duration_sec: stored.duration_sec ?? undefined,
        gain_db: -18, fade_in_sec: 2, fade_out_sec: 3,
      };
      commit((p) => p.tracks.music.push(m));
      selected = { kind: "music", id: m.id };
    } catch (e) {
      reportError(e);
    }
  }

  // ---------------------------------------------------------------- export
  let exporting = $state(false);
  let plan = $state<ExportPlan | null>(null);
  async function openExport() {
    exporting = true;
    plan = null;
    await flush();
    try {
      plan = await api.exportPlan(name);
    } catch (e) {
      reportError(e);
      exporting = false;
    }
  }
  function setCanvas(value: string) {
    commit((p) => {
      if (value === "auto") p.canvas = null;
      else {
        const [w, h] = value.split("x").map(Number);
        p.canvas = { width: w, height: h, fps: p.canvas?.fps ?? plan?.canvas.fps ?? 30 };
      }
    });
    void refreshPlan();
  }
  function setFps(value: number) {
    commit((p) => {
      const c = p.canvas ?? plan?.canvas;
      if (c) p.canvas = { width: c.width, height: c.height, fps: value };
    });
    void refreshPlan();
  }
  async function refreshPlan() {
    await flush();
    plan = await api.exportPlan(name).catch(() => plan);
  }
  async function startExport() {
    await flush();
    try {
      const job = await api.submit({ type: "export", project: name });
      queued(job);
      exporting = false;
    } catch (e) {
      reportError(e);
    }
  }
  const canvasChoices = $derived.by(() => {
    const sizes = new Map<string, string>();
    for (const id of Object.keys(project?.sources ?? {})) {
      const v = project!.sources[id].probe?.video;
      if (v) sizes.set(`${v.width}x${v.height}`, `${v.width}×${v.height} (as ${sourceName(id)})`);
    }
    for (const [w, h] of [[1280, 720], [1920, 1080], [3840, 2160]]) {
      if (!sizes.has(`${w}x${h}`)) sizes.set(`${w}x${h}`, `${w}×${h}`);
    }
    return [...sizes.entries()];
  });

  // ---------------------------------------------------------------- keyboard
  function typing(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
  }
  function keydown(ev: KeyboardEvent) {
    if (typing(ev) || exporting || !program) return;
    const key = ev.key.toLowerCase();
    const mod = ev.metaKey || ev.ctrlKey;
    let handled = true;
    if (mod && key === "z") ev.shiftKey ? redoStep() : undoStep();
    else if (mod) handled = false;
    else if (key === " ") {
      srcVideo?.pause();
      program.toggle();
    } else if (key === "s") commit((p) => (p.tracks.video = split(p.tracks.video, time)));
    else if (key === "delete" || key === "backspace") removeSelected();
    else if (key === "arrowleft") program.seek(time - (ev.shiftKey ? 1 : 1 / fps));
    else if (key === "arrowright") program.seek(time + (ev.shiftKey ? 1 : 1 / fps));
    else if (key === "j") program.seek(time - 5);
    else if (key === "k") program.pause();
    else if (key === "l") void program.play();
    else if (key === "home") program.seek(0);
    else if (key === "end") program.seek(total);
    else if (key === "i") markIn = srcTime;
    else if (key === "o") markOut = srcTime;
    else if (key === ",") addClip(false);
    else if (key === "=" || key === "+") pps = Math.min(400, pps * 1.4);
    else if (key === "-") pps = Math.max(0.5, pps / 1.4);
    else handled = false;
    if (handled) ev.preventDefault();
  }

  function onTimelineChange(clips: Clip[], music: MusicClip[]) {
    commit((p) => {
      // re-check cleaned-audio coverage for trimmed clips
      p.tracks.video = clips.map((c) => {
        if (!c.audio?.output) return c;
        const o = coveringOutput(sourceById(c.source), c.in_sec, c.out_sec, c.audio.output);
        if (o) return o.file === c.audio.output ? c : { ...c, audio: audioFor(o, c.audio.gain_db ?? 0) };
        toast("The cleaned audio does not cover the new range: that clip now plays its original sound.");
        return { ...c, audio: c.audio.gain_db ? { gain_db: c.audio.gain_db } : null };
      });
      p.tracks.music = music;
    });
  }

  const num = (v: string) => (Number.isFinite(Number(v)) ? Number(v) : 0);
</script>

<svelte:window onkeydown={keydown} />

<div class="page col editor">
  {#if error}
    <div class="error-box">{error}</div>
  {:else if !project}
    <p class="dim">Loading…</p>
  {:else}
    <div class="row">
      <a href={href(["edit"])}>Edit</a><span class="faint">/</span>
      <h1>{ltr(project.name)}</h1>
      <span class="dim">{project.tracks.video.length} clips · {formatDuration(total)}</span>
      <span class="faint small">{saving === "saving" ? "saving…" : saving === "error" ? "not saved" : "saved"}</span>
      <span class="spacer"></span>
      {#key historyTick}
        <button class="small" disabled={!undo.length} onclick={undoStep} title="⌘Z">Undo</button>
        <button class="small" disabled={!redo.length} onclick={redoStep} title="⇧⌘Z">Redo</button>
      {/key}
      <button onclick={chooseMusic}>♪ Add music…</button>
      <button class="primary" disabled={!project.tracks.video.length} onclick={openExport}>Export…</button>
    </div>

    <div class="viewers">
      <!-- source viewer -->
      <div class="card col viewer">
        <div class="row">
          <h3>Source</h3>
          <select bind:value={srcId} class="grow">
            {#each sources as s (s.id)}<option value={s.id}>{ltr(s.name)}{s.prep?.proxy ? "" : " (not prepared)"}</option>{/each}
          </select>
        </div>
        {#if src && src.prep?.proxy}
          <div class="screen">
            <!-- svelte-ignore a11y_media_has_caption -->
            <video bind:this={srcVideo} src="/media/proxy?source={src.id}" preload="auto"
                   ontimeupdate={() => (srcTime = srcVideo?.currentTime ?? 0)}
                   onplay={() => (srcPlaying = true)} onpause={() => (srcPlaying = false)}></video>
          </div>
          <div class="scrub">
            <input type="range" min="0" max={src.probe?.duration_sec ?? 0} step="0.04" value={srcTime}
                   oninput={(e) => { if (srcVideo) srcVideo.currentTime = Number((e.target as HTMLInputElement).value); srcTime = srcVideo?.currentTime ?? 0; }} />
            <div class="cover">
              {#each src.outputs.filter((o) => o.exists && o.window?.actual_duration_sec) as o (o.file)}
                <div class="covered" title="cleaned: {o.file}"
                     style="left:{(o.window!.actual_start_sec / (src.probe?.duration_sec || 1)) * 100}%;width:{((o.window!.actual_duration_sec ?? 0) / (src.probe?.duration_sec || 1)) * 100}%"></div>
              {/each}
              {#if markIn !== null}
                <div class="mark" style="left:{(markIn / (src.probe?.duration_sec || 1)) * 100}%;width:{(((markOut ?? markIn) - markIn) / (src.probe?.duration_sec || 1)) * 100}%"></div>
              {/if}
            </div>
          </div>
          <div class="row wrap">
            <button class="small" onclick={srcToggle}>{srcPlaying ? "❚❚" : "▶"}</button>
            <span class="mono small">{formatClock(srcTime)}</span>
            <button class="small" onclick={() => (markIn = srcTime)} title="I">In {markIn !== null ? formatClock(markIn) : ""}</button>
            <button class="small" onclick={() => (markOut = srcTime)} title="O">Out {markOut !== null ? formatClock(markOut) : ""}</button>
            <span class="spacer"></span>
            <button class="small" onclick={() => addClip(true)} title="Insert at the playhead">Insert at playhead</button>
            <button class="small primary" onclick={() => addClip(false)} title=",">＋ Add to end</button>
          </div>
          <div class="faint tiny">Green under the slider = cleaned outputs. Clips there play their cleaned sound.</div>
        {:else if src}
          <div class="prep col">
            <p class="dim">{ltr(src.name)} needs a 540p editing copy, thumbnails and a waveform first, so
              scrubbing is instant. They are made once and kept in <code>_gui/cache</code>.</p>
            {#if prepJob}
              <div class="row">
                <span class="dim small">{prepJob.step?.title ?? "Waiting…"}
                  {#if prepJob.progress?.total_sec} · {Math.round((100 * prepJob.progress.processed_sec) / prepJob.progress.total_sec)}%{/if}</span>
                <button class="small danger" disabled={prepJob.cancel_requested}
                        onclick={() => api.cancel(prepJob.id).catch(reportError)}>{prepJob.cancel_requested ? "Cancelling…" : "Cancel"}</button>
              </div>
            {:else}
              <div><button class="primary" onclick={() => prepare(src)}>Prepare for editing</button></div>
            {/if}
          </div>
        {:else}
          <p class="dim">No sources in this project folder yet.</p>
        {/if}
      </div>

      <!-- program viewer -->
      <div class="card col viewer">
        <div class="row"><h3>Program</h3><span class="spacer"></span>
          <span class="mono small">{formatClock(time)} / {formatClock(total)}</span></div>
        <div class="screen program">
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={videoA} preload="auto"></video>
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={videoB} preload="auto"></video>
          {#if !project.tracks.video.length}<div class="overlay dim">The edit is empty.</div>{/if}
        </div>
        <div class="row">
          <button class="small" onclick={() => program?.seek(0)} title="Home">⏮</button>
          <button class="small" onclick={() => { srcVideo?.pause(); program?.toggle(); }} title="Space">{playing ? "❚❚ Pause" : "▶ Play"}</button>
          <button class="small" onclick={() => commit((p) => (p.tracks.video = split(p.tracks.video, time)))} title="S">✂ Split</button>
          <span class="spacer"></span>
          <span class="faint tiny">Music is heard without ducking here; the export ducks it under speech.</span>
        </div>
      </div>
    </div>

    <div class="row">
      <span class="dim small">Zoom</span>
      <input type="range" min="0.5" max="200" step="0.5" bind:value={pps} style="width:160px" />
      <span class="faint tiny">⌘-scroll to zoom · drag clips to reorder, edges to trim · <kbd>S</kbd> split ·
        <kbd>⌫</kbd> delete · <kbd>Space</kbd> play · <kbd>J</kbd><kbd>K</kbd><kbd>L</kbd> · <kbd>I</kbd>/<kbd>O</kbd>
        marks · <kbd>,</kbd> add · <kbd>⌘Z</kbd> undo</span>
    </div>
    <Timeline clips={project.tracks.video} music={project.tracks.music} {time} bind:pps {selected} {thumbs}
              {sourceLength} {sourceName} onseek={(t) => program?.seek(t)} onselect={(s) => (selected = s)}
              onchange={onTimelineChange} />

    <!-- inspector -->
    <div class="card inspector">
      {#if selClip}
        {@const c = selClip}
        <div class="row wrap">
          <h3>Clip {selIndex + 1}</h3><span class="dim small">{sourceName(c.source)}</span>
          <label class="field">In <input type="number" step="0.1" value={c.in_sec.toFixed(2)}
            onchange={(e) => updateClip(c.id, (x) => (x.in_sec = Math.max(0, Math.min(x.out_sec - 0.1, num((e.target as HTMLInputElement).value)))))} /></label>
          <label class="field">Out <input type="number" step="0.1" value={c.out_sec.toFixed(2)}
            onchange={(e) => updateClip(c.id, (x) => (x.out_sec = Math.min(sourceLength(x.source), Math.max(x.in_sec + 0.1, num((e.target as HTMLInputElement).value)))))} /></label>
          <span class="dim small">{formatClock(c.out_sec - c.in_sec)}</span>
          <label class="field">Sound
            <select value={c.audio?.mute ? "__mute" : c.audio?.output ?? "__original"} onchange={(e) => setAudio(c.id, (e.target as HTMLSelectElement).value)}>
              {#each covering as o (o.file)}<option value={o.file}>cleaned: {o.tag ?? o.label}</option>{/each}
              <option value="__original">original (not cleaned)</option>
              <option value="__mute">muted</option>
            </select></label>
          <label class="field">Gain <input type="number" step="0.5" value={c.audio?.gain_db ?? 0} disabled={c.audio?.mute}
            onchange={(e) => updateClip(c.id, (x) => (x.audio = { ...(x.audio ?? {}), gain_db: num((e.target as HTMLInputElement).value) }))} /> dB</label>
          <label class="field">Into this clip
            <select disabled={selIndex === 0} value={String(c.transition_in?.sec ?? 0)}
              onchange={(e) => updateClip(c.id, (x) => { const v = num((e.target as HTMLSelectElement).value); x.transition_in = v ? { type: "crossfade", sec: v } : null; })}>
              <option value="0">cut</option><option value="0.5">crossfade 0.5 s</option>
              <option value="1">crossfade 1 s</option><option value="2">crossfade 2 s</option>
            </select></label>
          <span class="spacer"></span>
          {#if !covering.length}
            <a href={links.newRun({ source: c.source, start: Math.floor(c.in_sec), duration: Math.ceil(c.out_sec - Math.floor(c.in_sec)), kind: sourceById(c.source)?.source_kind ?? "" })}>
              <button class="small">Clean this range…</button></a>
          {/if}
          <button class="small danger" onclick={removeSelected}>Delete</button>
        </div>
      {:else if selMusic}
        {@const m = selMusic}
        <div class="row wrap">
          <h3>♪ {m.name ?? "Music"}</h3>
          <label class="field">Starts at <input type="number" step="0.5" value={m.at_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.at_sec = Math.max(0, num((e.target as HTMLInputElement).value))))} /> s</label>
          <label class="field">From <input type="number" step="0.5" value={m.in_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.in_sec = Math.max(0, Math.min(x.out_sec - 0.5, num((e.target as HTMLInputElement).value)))))} /></label>
          <label class="field">To <input type="number" step="0.5" value={m.out_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.out_sec = Math.min(x.file_duration_sec ?? Infinity, Math.max(x.in_sec + 0.5, num((e.target as HTMLInputElement).value)))))} /></label>
          <label class="field">Level <input type="number" step="1" value={m.gain_db}
            onchange={(e) => updateMusic(m.id, (x) => (x.gain_db = num((e.target as HTMLInputElement).value)))} /> dB</label>
          <label class="field">Fade in <input type="number" step="0.5" min="0" value={m.fade_in_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.fade_in_sec = Math.max(0, num((e.target as HTMLInputElement).value))))} /></label>
          <label class="field">Fade out <input type="number" step="0.5" min="0" value={m.fade_out_sec}
            onchange={(e) => updateMusic(m.id, (x) => (x.fade_out_sec = Math.max(0, num((e.target as HTMLInputElement).value))))} /></label>
          <span class="spacer"></span>
          <button class="small danger" onclick={removeSelected}>Delete</button>
        </div>
      {:else}
        {@const duck = project.tracks.music_duck ?? { enabled: true, threshold_db: -35, ratio: 6, attack_ms: 20, release_ms: 600 }}
        <div class="row wrap">
          <h3>Project</h3>
          <span class="dim small">Select a clip or music to edit it.</span>
          <span class="spacer"></span>
          <label class="field"><input type="checkbox" checked={duck.enabled}
            onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, enabled: (e.target as HTMLInputElement).checked }))} />
            Lower the music under speech</label>
          <label class="field">by ratio <input type="number" step="1" min="1" value={duck.ratio} disabled={!duck.enabled}
            onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, ratio: Math.max(1, num((e.target as HTMLInputElement).value)) }))} /></label>
          <label class="field">when speech is above <input type="number" step="1" value={duck.threshold_db} disabled={!duck.enabled}
            onchange={(e) => commit((p) => (p.tracks.music_duck = { ...duck, threshold_db: num((e.target as HTMLInputElement).value) }))} /> dB</label>
        </div>
      {/if}
    </div>
  {/if}
</div>

{#if browsingMusic}
  <FolderBrowser mode="file" title="Add music" onpick={addMusic} onclose={() => (browsingMusic = false)} />
{/if}

{#if exporting && project}
  <div class="backdrop" onclick={() => (exporting = false)} role="presentation"></div>
  <div class="modal card col" role="dialog" aria-label="Export">
    <div class="row"><h2>Export {ltr(project.name)}</h2><span class="spacer"></span><button class="small" onclick={() => (exporting = false)}>✕</button></div>
    {#if !plan}
      <p class="dim">Checking the cuts…</p>
    {:else}
      <div class="row wrap">
        <label class="field">Picture
          <select value={project.canvas ? `${project.canvas.width}x${project.canvas.height}` : "auto"} onchange={(e) => setCanvas((e.target as HTMLSelectElement).value)}>
            <option value="auto">as the first clip ({plan.canvas.width}×{plan.canvas.height})</option>
            {#each canvasChoices as [v, label]}<option value={v}>{label}</option>{/each}
          </select></label>
        <label class="field">Frame rate
          <select value={String(plan.canvas.fps)} onchange={(e) => setFps(Number((e.target as HTMLSelectElement).value))}>
            {#each [...new Set([plan.canvas.fps, 24, 25, 29.97, 30, 50, 59.94, 60])] as f}<option value={String(f)}>{f}</option>{/each}
          </select></label>
      </div>
      <div class="row wrap">
        <label class="field"><input type="radio" name="enc" checked={(project.export.encoder ?? "libx264") === "libx264"}
          onchange={() => commit((p) => (p.export.encoder = "libx264"))} /> Best quality (x264, slower)</label>
        <label class="field"><input type="radio" name="enc" checked={project.export.encoder === "h264_videotoolbox"}
          onchange={() => commit((p) => (p.export.encoder = "h264_videotoolbox"))} /> Fast (Apple hardware encoder)</label>
        <label class="field">Loudness <input type="number" step="1" value={project.export.target_lufs ?? -16}
          onchange={(e) => commit((p) => (p.export.target_lufs = num((e.target as HTMLInputElement).value)))} /> LUFS</label>
      </div>
      {#if plan.mode === "copy"}
        <div class="ok">Lossless: every cut is on a keyframe of one video, so the picture is copied untouched and only the sound is encoded.</div>
      {:else}
        <div class="dim">The picture is re-encoded: {plan.reasons.join("; ")}.</div>
      {/if}
      <div class="dim small">{formatDuration(plan.duration)} → <span class="mono">{ltr(plan.output)}</span>
        {#if plan.output_exists}<span class="warn"> (exists: will be replaced)</span>{/if}</div>
      <div class="dim small">MP4 · H.264 · AAC 48 kHz stereo · ready for Google Photos and YouTube.</div>
      <div class="row"><span class="spacer"></span>
        <button onclick={() => (exporting = false)}>Cancel</button>
        <button class="primary" onclick={startExport}>Export</button></div>
    {/if}
  </div>
{/if}

<style>
  .editor { max-width: none; gap: 10px; }
  .viewers { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .viewer { gap: 6px; padding: 10px 12px; }
  .screen { position: relative; background: #000; border-radius: var(--radius); aspect-ratio: 16 / 9; overflow: hidden; }
  .screen video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
  .overlay { position: absolute; inset: 0; display: grid; place-items: center; }
  .scrub { position: relative; }
  .scrub input { width: 100%; }
  .cover { position: relative; height: 5px; background: var(--bg-sunken); border-radius: 2px; }
  .covered { position: absolute; top: 0; height: 100%; background: var(--b-color); opacity: 0.8; }
  .mark { position: absolute; top: -2px; height: 9px; min-width: 2px; background: var(--accent); opacity: 0.7; }
  .prep { min-height: 200px; justify-content: center; }
  .grow { flex: 1; min-width: 0; }
  .wrap { flex-wrap: wrap; }
  .small { font-size: 12.5px; }
  .tiny { font-size: 11.5px; }
  .inspector { padding: 10px 14px; }
  .field { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--text-dim); }
  .field input[type="number"] { width: 78px; }
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.45); z-index: 50; }
  .modal { position: fixed; z-index: 51; left: 50%; top: 12vh; transform: translateX(-50%); width: min(680px, 92vw); }
</style>
