<script lang="ts">
  // Every candidate autotune measured for one window, audible against the source
  // (docs/gui-design.md §6.3).
  import { onDestroy } from "svelte";
  import { api, media, type CandidateMeasure, type Job, type Output, type PlanWindow } from "../lib/api";
  import { links, shell } from "../lib/router.svelte";
  import { ltr } from "../lib/bidi";
  import { ABPlayer } from "../lib/abplayer";
  import { audioContext, decode, type Decoded } from "../lib/audio";
  import { findPlan, loadLibrary, reportError, store, toast, queued } from "../lib/state.svelte";
  import { formatClock } from "../lib/time";
  import { followClock } from "../lib/videosync";
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

  // The source's picture for the window, on the player's clock (the candidate audio is cut
  // from the window's start). The editing proxy seeks fastest; else the file itself.
  let video = $state<HTMLVideoElement | null>(null);
  let pictureFailed = $state(false);
  const pictureUrl = $derived(
    !source?.probe?.video ? null
      : source.prep?.proxy ? `/media/proxy?source=${encodeURIComponent(source.id)}`
      : source.exists ? media.file(source.path) : null,
  );
  const ctx = audioContext();
  const onframe = (clock: number, playing: boolean) => followClock(video, ctx, clock, playing, win?.start_sec ?? 0);

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

  // ↑/↓ step through the candidates in the table's order, so they can be flipped while watching.
  function keydown(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    if (ev.metaKey || ev.ctrlKey || ev.altKey || !files || (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
    if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
    ev.preventDefault();
    const i = rows.findIndex((r) => r.i === selected);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + (ev.key === "ArrowDown" ? 1 : -1)))];
    if (next) void select(next.i);
  }

  // ---------------------------------------------------------------- actions
  let tag = $state("");
  // Outputs already made from candidate i of this window: rendered from it directly, or
  // autotune's own render when i was its pick.
  function renderedAs(i: number): Output[] {
    if (!plan || !win) return [];
    return (source?.outputs ?? []).filter((o) => {
      const t = o.results.autotune;
      if (!o.exists || !t || t.cache !== plan.path || t.window !== label) return false;
      if (t.candidate_index !== undefined && t.candidate_index !== null) return t.candidate_index === i;
      return !!t.picked && same(t.picked as CandidateMeasure, win.candidates[i]);
    });
  }
  const renderedSet = $derived(new Set(win ? win.candidates.map((_, i) => i).filter((i) => renderedAs(i).length) : []));
  function renderJob(i: number): Job | null {
    return store.jobs.find((j) => j.type === "clean_candidate" && (j.state === "queued" || j.state === "running") &&
      j.meta?.plan_path === plan?.path && j.meta?.window === label && j.meta?.index === i) ?? null;
  }
  let again = $state(false);
  $effect(() => {
    void selected;
    again = false;
  });

  async function render() {
    if (selected === null || !plan) return;
    try {
      const job = await api.submit({ type: "clean_candidate", plan_path: plan.path, window: label,
        index: selected, tag: tag.trim() || `cand${selected}` });
      queued(job);
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
      queued(job);
    } catch (e) {
      reportError(e);
    }
  }

  const fmt = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : v.toFixed(digits));
  const hissText = (h: string) => (h === "off" ? "off" : h.replace(":", " "));
  const declickText = (d: number | boolean) => (d === false ? "off" : d === true ? "12" : String(d));
</script>

<svelte:window onkeydown={keydown} />

<div class="page col">
  {#if !store.library}
    <p class="dim">Loading…</p>
  {:else if !plan || !win}
    <div class="error-box">No tuned window {label} in {planPath}.</div>
  {:else}
    <div class="row">
      <span class="chip">{plan.source_kind}</span>
      {#if plan.separate_music}<span class="chip">music separated{win.music ? `, bed ${win.music.level_db ?? "off"} dB` : ""}</span>{/if}
    </div>

    <div class="layout">
     <div class="col left">
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
      {#if pictureUrl && !pictureFailed}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={video} src={pictureUrl} muted playsinline preload="auto" onerror={() => (pictureFailed = true)}></video>
      {/if}
      <ABDeck {player} {a} {b} bind:levelMatch {onframe} fps={source?.probe?.video?.fps ?? 30} labelA="Source" picture={source ? { source, start: win.start_sec } : null}
              labelB={selected === null ? "Candidate" : `Candidate ${selected}${loadingB ? " …" : ""}`} />
    {/if}

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
                  {#if renderedSet.has(i)}<span class="chip" title="An output of this candidate exists">rendered</span>{/if}
                  {#if reasons[i]}<span class="faint">{reasons[i]}</span>{/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
     </div>

      <aside class="col">
        {#if selected !== null}
          {@const c = win.candidates[selected]}
          {@const job = renderJob(selected)}
          {@const made = renderedAs(selected)}
          <div class="card col">
            <h3>Candidate {selected}</h3>
            <div class="dim small">atten {c.atten ?? "none"} · hiss {hissText(c.hiss)} · declick {declickText(c.declick)}
              {#if plan.separate_music} · music {c.music_level_db ?? "off"}{/if}</div>
            <dl class="metrics small">
              <dt>Fidelity</dt><dd>{fmt(c.fidelity, 3)}</dd>
              <dt>Hiss removed</dt><dd>{fmt(c.hiss_gain_db)} dB</dd>
              <dt>Warble</dt><dd>{fmt(c.warble)} <span class="faint">(source {fmt(win.source.warble)})</span></dd>
              <dt>Ticks /s</dt><dd>{fmt(c.clicks)} <span class="faint">(source {fmt(win.source.clicks)})</span></dd>
              <dt>Score</dt><dd>{fmt(c.score)}</dd>
            </dl>
            <div class="small">
              {#if selected === pickedIndex}<span class="chip done">★ {win.user_pick != null ? "your pick" : "pick"}</span>{/if}
              {#if selected === runnerIndex && selected !== pickedIndex}<span class="chip">☆ runner-up</span>{/if}
              {#if reasons[selected]}<span class="faint">{reasons[selected]}</span>{:else if selected !== pickedIndex}<span class="faint">qualifies</span>{/if}
            </div>
            {#if job}
              <div class="status col">
                <div class="row">
                  <span>{job.state === "running"
                    ? `Rendering${job.progress?.total_sec ? ` · ${Math.round((100 * job.progress.processed_sec) / job.progress.total_sec)} %` : "…"}`
                    : "Waiting in the queue"}</span>
                  <span class="spacer"></span>
                  <button class="small" onclick={() => (shell.jobsOpen = true)}>Show queue</button>
                  {#if job.state === "running"}
                    <button class="small danger" disabled={job.cancel_requested} onclick={() => api.cancel(job.id).catch(reportError)}>Cancel</button>
                  {:else}
                    <button class="small danger" onclick={() => api.removeJob(job.id).catch(reportError)}>Remove</button>
                  {/if}
                </div>
                {#if job.state === "running"}<progress max="1" value={job.progress?.total_sec ? job.progress.processed_sec / job.progress.total_sec : undefined}></progress>{/if}
              </div>
            {:else if made.length && !again}
              <div class="status col">
                <span class="ok">Already rendered</span>
                {#each made as o (o.file)}
                  <div class="row"><a class="mono ellipsis" href={links.output(o.file)} title={o.file}>{ltr(o.file)}</a>
                    <span class="spacer"></span><a href={links.output(o.file)}><button class="small primary">Compare</button></a></div>
                {/each}
                <div><button class="link small" onclick={() => (again = true)}>Render it again anyway…</button></div>
              </div>
            {:else}
              <div class="row">
                <input placeholder={`cand${selected}`} bind:value={tag} class="grow" title="Tag for the output name" />
                <button class="primary" disabled={!source?.exists} onclick={render}>Render this one</button>
              </div>
            {/if}
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
        <div class="card col">
          <Scatter candidates={win.candidates} {selected} picked={pickedIndex} onselect={select} />
        </div>
        <div class="card col small dim">
          <div>Window {formatClock(win.start_sec, false)}–{formatClock(win.end_sec, false)} · {win.candidates.length} candidates</div>
          <div><kbd>↑</kbd>/<kbd>↓</kbd> flip through the candidates in table order while it plays; the card above shows the one you hear.</div>
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
  video { width: 100%; max-height: max(240px, calc(100vh - 530px)); background: #000; border-radius: var(--radius); display: block; }
  .left { gap: 12px; min-width: 0; }
  .status { gap: 4px; padding: 6px 8px; background: var(--bg-sunken); border-radius: var(--radius); }
  .status progress { width: 100%; }
  .metrics { display: grid; grid-template-columns: auto 1fr; gap: 1px 12px; margin: 0; }
  .metrics dt { color: var(--text-faint); }
  .metrics dd { margin: 0; font-variant-numeric: tabular-nums; }
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
