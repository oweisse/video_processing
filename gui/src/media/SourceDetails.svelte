<script lang="ts">
  // Everything about one source: file facts, cleaned sound, tuned windows, editing state.
  // The Media workspace's right panel; Enhance shows it until a window is picked.
  import { api, type Output, type Plan, type Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { audioFor, coveringOutput, newId } from "../lib/edit";
  import { links } from "../lib/router.svelte";
  import { loadLibrary, reportError, store, toast } from "../lib/state.svelte";
  import { formatBytes, formatDuration } from "../lib/time";
  import PieceBar from "../enhance/PieceBar.svelte";
  import Icon from "../components/Icon.svelte";
  import SourceStrip from "../components/SourceStrip.svelte";

  let { source: s }: { source: Source } = $props();

  const facts = $derived.by(() => {
    const p = s.probe;
    if (!p) return [];
    const v = p.video;
    const rows: [string, string][] = [["Length", formatDuration(p.duration_sec)], ["Size", formatBytes(p.size)]];
    if (v) {
      rows.push(["Picture", `${v.width}×${v.height}${v.fps ? ` · ${Math.round(v.fps * 100) / 100} fps` : ""}`]);
      rows.push(["Codec", `${v.codec?.toUpperCase() ?? "?"}${v.field_order && v.field_order !== "progressive" && v.field_order !== "unknown" ? " · interlaced" : ""}`]);
    } else rows.push(["Picture", "none (audio only)"]);
    if (p.audio) rows.push(["Sound", `${p.audio.codec?.toUpperCase() ?? "?"} · ${p.audio.channels} ch · ${p.audio.sample_rate / 1000} kHz`]);
    if (s.source_kind) rows.push(["Recorded on", s.source_kind === "tape" ? "tape" : "digital camera"]);
    return rows;
  });

  function settings(o: Output) {
    const t = o.results.autotune;
    const p = t?.picked;
    if (!p) {
      const params = o.params;
      if (!params || params.atten_lim_db === undefined) return "";
      return text(params.atten_lim_db, params.reduce_hiss ? `${params.hiss_method}` : "off",
        params.declick ? params.declick_nr : false) + " (manual)";
    }
    return text(p.atten ?? null, p.hiss ?? "off", p.declick ?? false) + (t?.chosen_by === "user" ? " (your pick)" : "");
  }
  function text(atten: number | null, hiss: string, declick: number | boolean) {
    return [
      `atten ${atten ?? "none"}`,
      hiss === "off" ? "hiss off" : `hiss ${hiss}`,
      declick ? `declick ${declick === true ? 12 : declick}` : "no declick",
    ].join(" · ");
  }

  function planFor(o: Output): { plan: Plan; label: string } | null {
    const t = o.results.autotune;
    if (!t?.cache || !t.window) return null;
    const plan = s.plans.find((p) => p.path === t.cache);
    return plan && plan.windows.some((w) => w.label === t.window) ? { plan, label: t.window } : null;
  }

  function rerun(o: Output) {
    const w = o.window;
    return links.newRun({
      source: s.id,
      start: w?.requested_start_sec ?? 0,
      duration: w?.requested_duration_sec ?? "",
      kind: o.params?.source ?? s.source_kind ?? "",
      music: o.params?.separate_music ? 1 : "",
    });
  }

  async function remove(o: Output) {
    if (!confirm(`Move ${o.file} and its .params.json to the project's _trash folder?`)) return;
    try {
      await api.deleteOutput(o.file);
      toast(`Moved ${ltr(o.file)} to _trash`);
      await loadLibrary();
    } catch (e) {
      reportError(e);
    }
  }

  const prepJob = $derived(
    store.jobs.find((j) => j.type === "prep" && j.meta?.source_id === s.id && (j.state === "running" || j.state === "queued")),
  );
  async function prepare() {
    try {
      await api.submit({ type: "prep", source_id: s.id });
    } catch (e) {
      reportError(e);
    }
  }

  // Append the whole video to a timeline (the editor trims it later).
  let target = $state("");
  const timelines = $derived(store.library?.projects ?? []);
  $effect(() => {
    if (!timelines.some((p) => p.name === target)) target = timelines[0]?.name ?? "";
  });
  async function addToTimeline() {
    const length = s.probe?.duration_sec;
    if (!target || !length) return;
    try {
      const p = await api.getProject(target);
      const out = coveringOutput(s, 0, length);
      p.sources[s.id] = { path: s.path, probe: s.probe };
      p.tracks.video.push({ id: newId(), source: s.id, in_sec: 0, out_sec: length, audio: audioFor(out) });
      await api.saveProject(target, p);
      await loadLibrary();
      toast(`Added ${ltr(s.name)} to ${ltr(target)}`);
    } catch (e) {
      reportError(e);
    }
  }
</script>

<div class="details col">
  <div class="title row">
    <h2 class="ellipsis" title={s.path}>{ltr(s.name)}</h2>
    {#if !s.exists}<span class="chip failed">missing</span>{/if}
  </div>
  <SourceStrip source={s} fill />
  {#if facts.length}
    <dl class="facts">
      {#each facts as [k, v]}<dt>{k}</dt><dd>{v}</dd>{/each}
    </dl>
  {:else if s.exists}
    <p class="faint">Reading the file…</p>
  {/if}

  <section class="col">
    <div class="row head">
      <h3>Cleaned sound</h3><span class="spacer"></span>
      <a href={links.newRun({ source: s.id, kind: s.source_kind ?? "" })}><button class="small" disabled={!s.exists}>Clean…</button></a>
    </div>
    <div class="bar"><PieceBar source={s} strip={28} /></div>
    {#each s.outputs as o (o.file)}
      {@const tuned = planFor(o)}
      <div class="output col" class:missing={!o.exists}>
        <div class="row">
          <a class="mono ellipsis" href={links.output(o.file)} title={o.file}>{ltr(o.label ?? o.file)}</a>
          {#if o.tag}<span class="chip">{o.tag}</span>{/if}
          <span class="spacer"></span>
          <span class="faint">{o.results.final_lufs != null ? `${o.results.final_lufs.toFixed(1)} LUFS` : ""}</span>
        </div>
        <div class="faint ellipsis">{settings(o)}{o.created ? ` · ${o.created.slice(0, 10)}` : ""}</div>
        <div class="row actions">
          <a href={links.output(o.file)}><button class="small" disabled={!o.exists || o.kind === "compare"}>Compare</button></a>
          {#if tuned}<a href={links.candidates(tuned.plan.path, tuned.label)}><button class="small">Candidates</button></a>{/if}
          <a href={rerun(o)}><button class="small" disabled={!s.exists}>Re-run…</button></a>
          <span class="spacer"></span>
          <button class="icon ghost" title="Reveal in Finder" aria-label="Reveal in Finder" disabled={!o.exists}
                  onclick={() => api.reveal(o.path).catch(reportError)}><Icon name="external-link" size={14} /></button>
          <button class="icon ghost danger" title="Move to _trash" aria-label="Delete" onclick={() => remove(o)}><Icon name="trash" size={14} /></button>
        </div>
      </div>
    {:else}
      <p class="faint">Not cleaned yet.</p>
    {/each}
    {#each s.plans as plan (plan.path)}
      <div class="plans">
        <span class="dim">Tuned windows{plan.separate_music ? " (music)" : ""}:</span>
        {#each plan.windows as w (w.label)}
          <a class="chip" href={links.candidates(plan.path, w.label)}
             title={w.candidate_files ? "Candidate audio kept: listen to them" : "Measured without audio"}>
            {w.label}{w.candidate_files ? " ♪" : ""}{w.user_pick != null ? " ★" : ""}
          </a>
        {/each}
      </div>
    {/each}
  </section>

  {#if s.probe?.video}
    <section class="col">
      <div class="row head"><h3>Editing</h3></div>
      {#if s.prep?.proxy && s.prep.thumbs}
        <div class="row"><span class="ok">Ready to edit</span></div>
        {#if timelines.length}
          <div class="row">
            <select bind:value={target} class="grow">
              {#each timelines as p (p.name)}<option value={p.name}>{ltr(p.name)}</option>{/each}
            </select>
            <button class="small" onclick={addToTimeline}>Add to timeline</button>
          </div>
        {:else}
          <p class="faint">No timelines yet: create one in <a href="#/edit">Edit</a>.</p>
        {/if}
      {:else if prepJob}
        <div class="row">
          <span class="dim">{prepJob.step?.title ?? "Waiting…"}{prepJob.progress?.total_sec ? ` · ${Math.round((100 * prepJob.progress.processed_sec) / prepJob.progress.total_sec)} %` : ""}</span>
          <span class="spacer"></span>
          <button class="small danger" disabled={prepJob.cancel_requested} onclick={() => api.cancel(prepJob.id).catch(reportError)}>
            {prepJob.cancel_requested ? "Cancelling…" : "Cancel"}</button>
        </div>
      {:else}
        <div class="row"><button class="small" disabled={!s.exists} onclick={prepare}>Prepare for editing</button>
          <span class="faint">a light copy for smooth scrubbing</span></div>
      {/if}
    </section>
  {/if}

  <div class="row">
    <button class="small" disabled={!s.exists} onclick={() => api.reveal(s.path).catch(reportError)}>
      <Icon name="external-link" size={12} /> Reveal original</button>
  </div>
</div>

<style>
  .details { gap: 12px; padding: 10px; }
  .title h2 { font-size: 13px; }
  .facts { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0; }
  dt { color: var(--text-faint); }
  dd { margin: 0; }
  section { gap: 6px; border-top: 1px solid var(--border); padding-top: 10px; }
  .head h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); }
  .output { gap: 2px; padding: 6px 8px; background: var(--bg-sunken); border-radius: var(--radius); }
  .output.missing { opacity: 0.5; }
  .actions { gap: 4px; margin-top: 2px; }
  .plans { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .grow { flex: 1; min-width: 0; }
  .bar { margin: 0 -10px; }
  .bar :global(.piecebar) { background: none; border-top: none; }
  button.danger:hover { color: var(--danger); }
</style>
