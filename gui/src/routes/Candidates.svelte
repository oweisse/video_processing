<script lang="ts">
  // Every candidate autotune measured for one window, audible against the source
  // (docs/gui-design.md §6.3).
  import { onDestroy } from "svelte";
  import { api, media, type CandidateMeasure, type PlanWindow } from "../lib/api";
  import { ABPlayer } from "../lib/abplayer";
  import { audioContext, decode, type Decoded } from "../lib/audio";
  import { href } from "../lib/router.svelte";
  import { findPlan, loadLibrary, reportError, store, toast } from "../lib/state.svelte";
  import { formatClock } from "../lib/time";
  import ABDeck from "../components/ABDeck.svelte";
  import Scatter from "../components/Scatter.svelte";

  let { planPath, label }: { planPath: string; label: string } = $props();

  const found = $derived(store.library ? findPlan(planPath) : null);
  const plan = $derived(found?.plan ?? null);
  const source = $derived(found?.source ?? null);
  const win = $derived(plan?.windows.find((w) => w.label === label) ?? null);
  const files = $derived(win?.candidate_files ?? null);

  const same = (x: CandidateMeasure, y: CandidateMeasure | null) =>
    !!y && x.atten === y.atten && x.hiss === y.hiss && Number(x.declick) === Number(y.declick) &&
    (x.music_level_db ?? null) === (y.music_level_db ?? null);
  const pickedIndex = $derived(
    win ? (win.user_pick ?? win.candidates.findIndex((c) => same(c, win.picked))) : -1,
  );
  const runnerIndex = $derived(win ? win.candidates.findIndex((c) => same(c, win.runner_up)) : -1);

  // Why a candidate does not qualify, from the same per-window yardsticks as tune.py.
  function verdicts(w: PlanWindow) {
    const crit = plan!.criteria;
    const fid = (c: CandidateMeasure) => c.fidelity ?? 0;
    const gentlest = Math.max(...w.candidates.map(fid));
    const faithful = w.candidates.filter((c) => fid(c) >= gentlest - crit.fidelity_tolerance);
    const calmest = Math.min(...faithful.map((c) => c.warble ?? Infinity));
    return w.candidates.map((c) => {
      const why = [];
      if (fid(c) < gentlest - crit.fidelity_tolerance) why.push("loses voice");
      if ((c.warble ?? 0) > calmest + crit.max_warble_increase) why.push("warbles");
      if (crit.min_fidelity !== null && fid(c) < crit.min_fidelity) why.push("below floor");
      return why.join(", ");
    });
  }
  const reasons = $derived(win && plan ? verdicts(win) : []);

  type SortKey = "index" | "fidelity" | "hiss_gain_db" | "warble" | "clicks" | "score";
  let sortKey = $state<SortKey>("score");
  let sortDesc = $state(true);
  const rows = $derived.by(() => {
    if (!win) return [];
    const r = win.candidates.map((c, i) => ({ c, i }));
    const val = (x: { c: CandidateMeasure; i: number }) =>
      sortKey === "index" ? x.i : ((x.c[sortKey] as number | null | undefined) ?? -Infinity);
    r.sort((x, y) => (val(x) - val(y)) * (sortDesc ? -1 : 1));
    return r;
  });
  function sortBy(key: SortKey) {
    if (sortKey === key) sortDesc = !sortDesc;
    else {
      sortKey = key;
      sortDesc = key !== "warble" && key !== "clicks" && key !== "index";
    }
  }

  // ---------------------------------------------------------------- audio
  const player = new ABPlayer(audioContext());
  onDestroy(() => player.dispose());
  let a = $state<Decoded | null>(null);
  let b = $state<Decoded | null>(null);
  let selected = $state<number | null>(null);
  let loadingB = $state(false);
  let error = $state<string | null>(null);
  let levelMatch = $state(true);

  const fileUrl = (name: string) => media.file(`${plan!.dir}/${files!.dir}/${name}`);

  let loadedA = "";
  $effect(() => {
    if (!plan || !files) return;
    const url = fileUrl(files.source.file);
    if (url === loadedA) return;
    loadedA = url;
    a = null;
    b = null;
    const first = selected ?? (pickedIndex >= 0 ? pickedIndex : 0);
    Promise.all([decode(url), decode(fileUrl(files.candidates[first].file))])
      .then(([da, db]) => {
        player.setBuffers(da.buffer, db.buffer);
        a = da;
        b = db;
        selected = first;
      })
      .catch((e) => (error = e instanceof Error ? e.message : String(e)));
  });

  async function select(i: number) {
    if (!files || !a || i === selected) {
      selected = i;
      return;
    }
    selected = i;
    loadingB = true;
    try {
      const d = await decode(fileUrl(files.candidates[i].file));
      if (selected !== i) return;
      player.replaceB(d.buffer);
      b = d;
    } catch (e) {
      reportError(e);
    } finally {
      loadingB = false;
    }
  }

  // ---------------------------------------------------------------- actions
  let tag = $state("");
  async function render() {
    if (selected === null || !plan) return;
    try {
      const job = await api.submit({ type: "clean_candidate", plan_path: plan.path, window: label,
        index: selected, tag: tag.trim() || `cand${selected}` });
      toast(`Queued: ${job.title}`);
    } catch (e) {
      reportError(e);
    }
  }
  async function setPick(index: number | null) {
    if (!plan) return;
    try {
      await api.setPick(plan.path, label, index);
      await loadLibrary();
      toast(index === null ? "Back to autotune's pick" : `Candidate ${index} is now the pick for ${label}`);
    } catch (e) {
      reportError(e);
    }
  }
  async function deleteAudio() {
    if (!plan || !confirm(`Delete the kept candidate audio for ${label}? The measurements stay.`)) return;
    try {
      await api.deleteCandidates(plan.path, label);
      await loadLibrary();
    } catch (e) {
      reportError(e);
    }
  }
  async function measureAgain() {
    if (!plan || !source || !win) return;
    try {
      const job = await api.submit({ type: "tune", source_id: source.id, source_kind: plan.source_kind,
        separate_music: plan.separate_music, windows: [{ start_sec: win.start_sec, end_sec: win.end_sec }] });
      toast(`Queued: ${job.title}`);
    } catch (e) {
      reportError(e);
    }
  }

  const fmt = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : v.toFixed(digits));
  const hissText = (h: string) => (h === "off" ? "off" : h.replace(":", " "));
  const declickText = (d: number | boolean) => (d === false ? "off" : d === true ? "12" : String(d));
</script>

<div class="page col">
  {#if !store.library}
    <p class="dim">Loading…</p>
  {:else if !plan || !win}
    <div class="error-box">No tuned window {label} in {planPath}.</div>
  {:else}
    <div class="row">
      <a href={href([])}>Library</a><span class="faint">/</span>
      <h1 class="ellipsis">{source?.name} · {label}</h1>
      <span class="chip">{plan.source_kind}</span>
      {#if plan.separate_music}<span class="chip">music separated{win.music ? `, bed ${win.music.level_db ?? "off"} dB` : ""}</span>{/if}
    </div>

    {#if !files}
      <div class="card col">
        <h2>No candidate audio kept for this window</h2>
        <p class="dim">It was tuned before the GUI kept candidates. Measuring it again keeps every candidate's audio
          (about {Math.round(((win.end_sec - win.start_sec) * 0.8) / 60) || 1} min of work, and roughly
          {Math.round((win.end_sec - win.start_sec) * 0.024 * win.candidates.length)} MB on disk).</p>
        <div><button class="primary" disabled={!source?.exists} onclick={measureAgain}>Measure again, keeping audio</button></div>
      </div>
    {:else}
      {#if error}<div class="error-box">{error}</div>{/if}
      <ABDeck {player} {a} {b} bind:levelMatch labelA="Source"
              labelB={selected === null ? "Candidate" : `Candidate ${selected}${loadingB ? " …" : ""}`} />
    {/if}

    <div class="layout">
      <div class="card">
        <table>
          <thead>
            <tr>
              <th><button class="link" onclick={() => sortBy("index")}>#</button></th>
              <th>Atten</th><th>Hiss</th><th>Declick</th>
              {#if plan.separate_music}<th>Music</th>{/if}
              <th class="num"><button class="link" onclick={() => sortBy("fidelity")}>Fidelity</button></th>
              <th class="num"><button class="link" onclick={() => sortBy("hiss_gain_db")}>Hiss −dB</button></th>
              <th class="num"><button class="link" onclick={() => sortBy("warble")}>Warble</button></th>
              <th class="num"><button class="link" onclick={() => sortBy("clicks")}>Ticks/s</button></th>
              <th class="num"><button class="link" onclick={() => sortBy("score")}>Score</button></th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            <tr class="source-row">
              <td></td><td colspan={plan.separate_music ? 4 : 3} class="dim">source (untouched)</td>
              <td class="num">1.000</td><td class="num">—</td>
              <td class="num">{fmt(win.source.warble)}</td><td class="num">{fmt(win.source.clicks)}</td><td></td><td></td>
            </tr>
            {#each rows as { c, i } (i)}
              <tr class:sel={i === selected} onclick={() => select(i)} class:clickable={!!files}>
                <td class="num faint">{i}</td>
                <td>{c.atten ?? "none"}</td>
                <td>{hissText(c.hiss)}</td>
                <td>{declickText(c.declick)}</td>
                {#if plan.separate_music}<td>{c.music_level_db ?? "off"}</td>{/if}
                <td class="num">{fmt(c.fidelity, 3)}</td>
                <td class="num">{fmt(c.hiss_gain_db)}</td>
                <td class="num">{fmt(c.warble)}</td>
                <td class="num">{fmt(c.clicks)}</td>
                <td class="num">{fmt(c.score)}</td>
                <td class="verdict">
                  {#if i === pickedIndex}<span class="chip done">★ {win.user_pick != null ? "your pick" : "pick"}</span>{/if}
                  {#if i === runnerIndex && i !== pickedIndex}<span class="chip">☆ runner-up</span>{/if}
                  {#if reasons[i]}<span class="faint">{reasons[i]}</span>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>

      <aside class="col">
        <div class="card col">
          <Scatter candidates={win.candidates} {selected} picked={pickedIndex} onselect={select} />
        </div>
        {#if selected !== null}
          {@const c = win.candidates[selected]}
          <div class="card col">
            <h3>Candidate {selected}</h3>
            <div class="dim small">atten {c.atten ?? "none"} · hiss {hissText(c.hiss)} · declick {declickText(c.declick)}
              {#if plan.separate_music} · music {c.music_level_db ?? "off"}{/if}</div>
            <div class="row">
              <input placeholder={`cand${selected}`} bind:value={tag} class="grow" title="Tag for the output name" />
              <button class="primary" disabled={!source?.exists} onclick={render}>Render this one</button>
            </div>
            <div class="row wrap">
              {#if selected !== pickedIndex}
                <button onclick={() => setPick(selected)} title="Autotune and clean-audio-batch will use it for this window">Make it the pick</button>
              {/if}
              {#if win.user_pick != null}
                <button onclick={() => setPick(null)}>Back to autotune's pick</button>
              {/if}
            </div>
          </div>
        {/if}
        <div class="card col small dim">
          <div>Window {formatClock(win.start_sec, false)}–{formatClock(win.end_sec, false)} · {win.candidates.length} candidates</div>
          <div>Qualify: fidelity within {plan.criteria.fidelity_tolerance} of the gentlest, warble at most
            {plan.criteria.max_warble_increase} above the calmest of those. Score = hiss removed + {plan.criteria.click_weight} × ticks removed.</div>
          <div>Candidates are heard as measured (before loudness normalisation); level-match evens them out.</div>
          {#if files}<div><button class="small danger" onclick={deleteAudio}>Delete candidate audio</button></div>{/if}
        </div>
      </aside>
    </div>
  {/if}
</div>

<style>
  .layout { display: grid; grid-template-columns: minmax(0, 1fr) 390px; gap: 18px; align-items: start; }
  table { font-size: 13px; }
  td, th { padding: 4px 7px; }
  tr.clickable { cursor: pointer; }
  tr.clickable:hover td { background: var(--bg-hover); }
  tr.sel td { background: var(--bg-hover); box-shadow: inset 0 1px 0 var(--b-color), inset 0 -1px 0 var(--b-color); }
  .source-row td { color: var(--a-color); }
  .verdict { white-space: nowrap; font-size: 12px; }
  .verdict .chip { margin-right: 4px; }
  th .link { color: var(--text-dim); font-weight: 500; font-size: 12.5px; }
  .grow { flex: 1; }
  .small { font-size: 12.5px; }
  .wrap { flex-wrap: wrap; }
</style>
