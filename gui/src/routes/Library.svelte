<script lang="ts">
  // Home: every source in the project with its outputs and tuning (docs/gui-design.md §6.1).
  import { api, type Output, type Plan, type Source } from "../lib/api";
  import { href } from "../lib/router.svelte";
  import { loadLibrary, nativePick, reportError, store, toast } from "../lib/state.svelte";
  import { formatBytes, formatDuration } from "../lib/time";
  import CoverageBar from "../components/CoverageBar.svelte";
  import FolderBrowser from "../components/FolderBrowser.svelte";
  import SourceStrip from "../components/SourceStrip.svelte";

  let browsing = $state(false);
  let typed = $state("");

  async function browse() {
    const picked = await nativePick("dir");
    if (picked === undefined) browsing = true;
    else if (picked) await openProject(picked);
  }

  async function openProject(path: string) {
    browsing = false;
    try {
      store.library = await api.openProject(path);
    } catch (e) {
      reportError(e);
    }
  }

  // Probe sources once (cached server-side); the cards fill in as results arrive.
  const probing = new Set<string>();
  $effect(() => {
    for (const s of store.library?.sources ?? []) {
      if (!s.probe && s.exists && !probing.has(s.id)) {
        probing.add(s.id);
        api.probe(s.id).then((p) => (s.probe = p)).catch(() => {});
      }
    }
  });

  function describe(s: Source) {
    const p = s.probe;
    if (!p) return s.exists ? "…" : "";
    const v = p.video;
    const bits = [];
    if (v) bits.push(`${v.width}×${v.height}`, v.fps ? `${Math.round(v.fps * 100) / 100} fps` : "", v.codec?.toUpperCase());
    else bits.push("audio only");
    bits.push(formatDuration(p.duration_sec), formatBytes(p.size));
    return bits.filter(Boolean).join(" · ");
  }

  function pick(o: Output) {
    const t = o.results.autotune;
    const p = t?.picked;
    if (!p) {
      const params = o.params;
      if (!params || params.atten_lim_db === undefined) return "—";
      return settingText(params.atten_lim_db, params.reduce_hiss ? `${params.hiss_method}` : "off",
        params.declick ? params.declick_nr : false) + " (manual)";
    }
    return settingText(p.atten ?? null, p.hiss ?? "off", p.declick ?? false) + (t?.chosen_by === "user" ? " (your pick)" : "");
  }

  function settingText(atten: number | null, hiss: string, declick: number | boolean) {
    return [
      `atten ${atten ?? "none"}`,
      hiss === "off" ? "hiss off" : `hiss ${hiss}`,
      declick ? `declick ${declick === true ? 12 : declick}` : "no declick",
    ].join(" · ");
  }

  function musicBed(o: Output) {
    if (!o.params?.separate_music) return "";
    const level = o.params.music_level_db;
    return level === null || level === undefined ? "music off" : `music ${level} dB`;
  }

  function planFor(s: Source, o: Output): { plan: Plan; label: string } | null {
    const t = o.results.autotune;
    if (!t?.cache || !t.window) return null;
    const plan = s.plans.find((p) => p.path === t.cache);
    return plan && plan.windows.some((w) => w.label === t.window) ? { plan, label: t.window } : null;
  }

  async function remove(o: Output) {
    if (!confirm(`Move ${o.file} and its .params.json to the project's _trash folder?`)) return;
    try {
      await api.deleteOutput(o.file);
      toast(`Moved ${o.file} to _trash`);
      await loadLibrary();
    } catch (e) {
      reportError(e);
    }
  }

  function rerunParams(s: Source, o: Output) {
    const w = o.window;
    return {
      source: s.id,
      start: w?.requested_start_sec ?? 0,
      duration: w?.requested_duration_sec ?? "",
      kind: o.params?.source ?? s.source_kind ?? "",
      music: o.params?.separate_music ? 1 : "",
    };
  }
</script>

<div class="page col">
  {#if !store.library}
    <p class="dim">Loading…</p>
  {:else if !store.library.output_dir}
    <div class="card col chooser">
      <h1>Open a project folder</h1>
      <p class="dim">A project is an output folder: every cleaned video there, with the sources it came from
        and its tuning, is shown here. Pick the folder you pass to <code>--output_dir</code>{store.native ? ", or drop it onto this window" : ""}.</p>
      {#each store.library.recent_output_dirs as dir}
        <button class="recent" onclick={() => openProject(dir)}>{dir}</button>
      {/each}
      <form class="row" onsubmit={(e) => { e.preventDefault(); openProject(typed); }}>
        <input class="mono grow" placeholder="/Users/…/video_processing_output" bind:value={typed} />
        <button type="submit" disabled={!typed}>Open</button>
        <button type="button" onclick={browse}>Browse…</button>
      </form>
    </div>
  {:else}
    <div class="row">
      <h1>Library</h1>
      <span class="faint">{store.library.sources.length} sources ·
        {store.library.sources.reduce((n, s) => n + s.outputs.length, 0)} outputs</span>
      <span class="spacer"></span>
      <button onclick={() => loadLibrary(true)}>Rescan</button>
      <a href={href(["new"])}><button class="primary">New run</button></a>
    </div>

    {#each store.library.sources as s (s.id)}
      <section class="card col">
        <div class="row">
          <h2 class="ellipsis">{s.name}</h2>
          {#if s.source_kind}<span class="chip">{s.source_kind}</span>{/if}
          {#if !s.exists}<span class="chip failed">missing</span>{/if}
          <span class="dim small">{describe(s)}</span>
          <span class="spacer"></span>
          <a href={href(["new"], { source: s.id, kind: s.source_kind ?? "" })}><button disabled={!s.exists}>New run…</button></a>
        </div>
        <div class="faint small ellipsis mono" title={s.path}>{s.path}</div>
        <SourceStrip source={s} />
        <CoverageBar duration={s.probe?.duration_sec ?? null} outputs={s.outputs} />

        {#if s.outputs.length}
          <table>
            <thead>
              <tr><th>Window</th><th>Tag</th><th>Settings</th><th>Music</th><th class="num">LUFS</th>
                <th class="num">Size</th><th>Made</th><th></th></tr>
            </thead>
            <tbody>
              {#each s.outputs as o (o.file)}
                {@const tuned = planFor(s, o)}
                <tr class:missing={!o.exists}>
                  <td class="mono nowrap"><a href={href(["compare", o.file])} title={o.file}>{o.label ?? o.file}</a></td>
                  <td>{#if o.tag}<span class="chip">{o.tag}</span>{/if}</td>
                  <td class="small">{pick(o)}</td>
                  <td class="small">{musicBed(o)}</td>
                  <td class="num">{o.results.final_lufs?.toFixed(1) ?? "—"}</td>
                  <td class="num">{formatBytes(o.size)}</td>
                  <td class="small faint">{o.created?.slice(0, 10) ?? ""}</td>
                  <td class="actions">
                    <a href={href(["compare", o.file])}><button class="small" disabled={!o.exists || o.kind === "compare"}>Compare</button></a>
                    {#if tuned}
                      <a href={href(["candidates", tuned.plan.path, tuned.label])}><button class="small">Candidates</button></a>
                    {/if}
                    <a href={href(["new"], rerunParams(s, o))}><button class="small" disabled={!s.exists}>Re-run…</button></a>
                    <button class="small" disabled={!o.exists} onclick={() => api.reveal(o.path).catch(reportError)}>Reveal</button>
                    <button class="small danger" onclick={() => remove(o)}>Delete</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}

        {#each s.plans as plan (plan.path)}
          <div class="plans small">
            <span class="dim">Tuned windows ({plan.source_kind}{plan.separate_music ? ", with music" : ""}):</span>
            {#each plan.windows as w (w.label)}
              <a class="chip" href={href(["candidates", plan.path, w.label])}
                 title={w.candidate_files ? "Candidate audio kept: listen to them" : "Measured without audio"}>
                {w.label}{w.candidate_files ? " ♪" : ""}{w.user_pick != null ? " ★" : ""}
              </a>
            {/each}
          </div>
        {/each}
      </section>
    {:else}
      <div class="card dim">No outputs in this folder yet. <a href={href(["new"])}>Start a run</a>.</div>
    {/each}

    {#if store.library.unknown.length}
      <section class="card col">
        <h2>Other files</h2>
        <p class="dim small">No <code>.params.json</code> beside them, so their source and settings are unknown.</p>
        <table>
          <tbody>
            {#each store.library.unknown as o (o.file)}
              <tr><td class="mono">{o.file}</td><td class="num">{formatBytes(o.size)}</td>
                <td class="actions"><button class="small" onclick={() => api.reveal(o.path).catch(reportError)}>Reveal</button></td></tr>
            {/each}
          </tbody>
        </table>
      </section>
    {/if}
  {/if}
</div>

{#if browsing}
  <FolderBrowser mode="dir" title="Open a project folder" onpick={openProject} onclose={() => (browsing = false)} />
{/if}

<style>
  .chooser { max-width: 760px; margin: 40px auto; }
  .recent { text-align: left; font-family: var(--mono); font-size: 12.5px; }
  .grow { flex: 1; }
  .small { font-size: 12.5px; }
  .nowrap { white-space: nowrap; }
  .actions { white-space: nowrap; text-align: right; }
  .actions button { margin-left: 3px; }
  tr.missing td { opacity: 0.5; }
  .plans { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  section h2 { max-width: 50%; }
</style>
