<script lang="ts">
  import { untrack } from "svelte";
  // Export settings for one project: keep the original quality, or fit a size (budget,
  // format, picture height, hardware encoder); an Estimate (samples, a job) and Export.
  // Used by Deliver and by the editor's Export dialog (docs/stitch-and-size-budget-plan.md §11).
  import { api, type ExportPlan, type SizeEstimate, type SubtitleCompleteness } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { displayWidth, type Project } from "../lib/edit";
  import { queued, reportError, store } from "../lib/state.svelte";
  import { formatClock, formatDuration } from "../lib/time";
  import {
    budgetBytes, budgetUnit, budgetValue, heightChoices, heightName, makeBudget, minutesWords, weak,
    sizeWords, type BudgetUnit,
  } from "./budget";

  let { name, project, commit, flush, onqueued }: {
    name: string;
    project: Project;
    commit: (mutate: (p: Project) => void) => void;
    flush: () => Promise<void>;
    onqueued?: () => void;
  } = $props();

  let plan = $state<ExportPlan | null>(null);
  let estimate = $state<SizeEstimate | null>(null);
  let stale = $state(false);

  const fit = $derived(!!project.export.budget);
  // The number and unit typed, kept while "keep quality" is chosen.
  let amount = $state(2);
  let unit = $state<BudgetUnit>("per_hour");
  $effect.pre(() => {
    if (project.export.budget) {
      amount = budgetValue(project.export.budget);
      unit = budgetUnit(project.export.budget);
    }
  });

  async function refresh() {
    try {
      await flush();
      // An older server has no estimate route: the settings still show.
      const [p, e] = await Promise.all([api.exportPlan(name),
        api.exportEstimate(name).catch(() => ({ estimate: null, stale: false }))]);
      plan = p;
      estimate = e.estimate;
      stale = e.stale;
    } catch (e) {
      reportError(e);
    }
  }
  // Subtitles in the export (docs/subtitles-plan.md §10.3)
  const SUB_LANGS = [["mixed", "Mixed"], ["en", "English only"], ["he", "Hebrew only"]] as const;
  type SubSetting = { srt: string[]; tracks: string[]; default_track: string | null };
  const subSetting = $derived<SubSetting>({ srt: [], tracks: [], default_track: null, ...((project.export as any).subtitles ?? {}) });
  let subDone = $state<Record<"mixed" | "en" | "he", SubtitleCompleteness> | null>(null);
  $effect(() => {
    void name;
    void project.tracks.video.length;
    api.projectSubtitles(name).then((r) => (subDone = r.completeness)).catch(() => (subDone = null));
  });
  function toggleSub(kind: "srt" | "tracks", lang: string, on: boolean) {
    commit((p) => {
      const cur: SubSetting = { srt: [], tracks: [], default_track: null, ...((p.export as any).subtitles ?? {}) };
      cur[kind] = on ? [...new Set([...cur[kind], lang])] : cur[kind].filter((x) => x !== lang);
      if (cur.default_track && !cur.tracks.includes(cur.default_track)) cur.default_track = null;
      (p.export as any).subtitles = cur;
    });
  }
  function setSubDefault(v: string) {
    commit((p) => {
      const cur: SubSetting = { srt: [], tracks: [], default_track: null, ...((p.export as any).subtitles ?? {}) };
      cur.default_track = v || null;
      (p.export as any).subtitles = cur;
    });
  }

  // Settings changed (saved first): ask again whether the saved estimate still holds.
  let timer: ReturnType<typeof setTimeout> | undefined;
  function change(mutate: (p: Project) => void) {
    commit(mutate);
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  }
  $effect(() => {
    void name;
    // only the project's name: refresh saves first, and a save must not re-run this
    untrack(() => void refresh());
    return () => clearTimeout(timer);
  });

  // This project's estimates and exports, however they were started (here, in the editor,
  // "Run again" in the queue): show the result when one finishes.
  const active = (j: { state: string }) => j.state === "queued" || j.state === "running";
  const mine = $derived(store.jobs.filter((j) => (j.type === "export_estimate" || j.type === "export")
    && j.meta?.project === name));
  const estimating = $derived(mine.find((j) => j.type === "export_estimate" && active(j)) ?? null);
  const finished = $derived(mine.filter((j) => !active(j)).map((j) => `${j.id}:${j.state}`).join(","));
  let seen: string | null = null;
  $effect(() => {
    const now = finished;
    if (seen !== null && now !== seen) void refresh();
    seen = now;
  });

  const seconds = $derived(plan?.duration ?? 0);
  const allowed = $derived(budgetBytes(fit ? makeBudget(amount, unit) : null, seconds));
  const canvasHeight = $derived(plan?.canvas.height ?? project.canvas?.height ?? 0);
  const missing = $derived(plan?.missing ?? []);

  function setFit(on: boolean) {
    change((p) => {
      p.export.budget = on ? makeBudget(amount, unit) : null;
      if (on && !p.export.codec) p.export.codec = "hevc";
    });
  }
  function setBudget() {
    change((p) => (p.export.budget = makeBudget(amount, unit)));
  }

  function setCanvas(value: string) {
    change((p) => {
      if (value === "auto") p.canvas = null;
      else {
        const [w, h] = value.split("x").map(Number);
        p.canvas = { width: w, height: h, fps: p.canvas?.fps ?? plan?.canvas.fps ?? 30 };
      }
    });
  }
  function setFps(value: number) {
    change((p) => {
      const c = p.canvas ?? plan?.canvas;
      if (c) p.canvas = { width: c.width, height: c.height, fps: value };
    });
  }
  const canvasChoices = $derived.by(() => {
    const sizes = new Map<string, string>();
    for (const src of Object.values(project.sources ?? {})) {
      const v = src.probe?.video;
      const file = src.path.split("/").pop();
      if (v) sizes.set(`${displayWidth(v)}x${v.height}`, `${displayWidth(v)}×${v.height} (as ${file})`);
    }
    for (const [w, h] of [[1280, 720], [1920, 1080], [3840, 2160]]) {
      if (!sizes.has(`${w}x${h}`)) sizes.set(`${w}x${h}`, `${w}×${h}`);
    }
    return [...sizes.entries()];
  });
  const num = (v: string) => (v.trim() === "" ? undefined : Number(v));

  async function submit(type: "export" | "export_estimate") {
    await flush();
    try {
      const job = await api.submit({ type, project: name });
      queued(job);
      if (type === "export") onqueued?.();
    } catch (e) {
      reportError(e);
    }
  }
  const metricName = (e: SizeEstimate) => (e.metric === "ssim" ? "SSIM" : "VMAF");
</script>


{#if !plan}
  <p class="dim">Checking the cuts…</p>
{:else}
  <div class="col settings">
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
      <label class="field">Loudness <input type="number" step="1" value={project.export.target_lufs ?? -16}
        onchange={(e) => change((p) => (p.export.target_lufs = num((e.target as HTMLInputElement).value)))} /> LUFS</label>
    </div>
    <label class="field" title="Measures each clip's sound and turns it up or down (up to 12 dB) toward the loudness above, so clips recorded at different levels play at one volume. Your own Gain on a clip stays on top. Split shots of one recording get one level.">
      <input type="checkbox" checked={!!project.export.level_clips}
             onchange={(e) => change((p) => (p.export.level_clips = (e.target as HTMLInputElement).checked))} />
      Even out loudness between clips</label>

    <label class="field"><input type="radio" name="size-{name}" checked={!fit} onchange={() => setFit(false)} />
      <b>Keep the original quality</b></label>
    {#if !fit}
      <div class="col indent">
        {#if plan.mode === "copy"}
          <div class="ok">Lossless: the picture is copied untouched and only the sound is encoded.</div>
        {:else}
          <div class="dim">The picture is re-encoded: {plan.reasons.join("; ")}.</div>
          <div class="row wrap">
            <label class="field"><input type="radio" name="enc-{name}" checked={(project.export.encoder ?? "libx264") === "libx264"}
              onchange={() => change((p) => (p.export.encoder = "libx264"))} /> Best quality (x264, slower)</label>
            <label class="field"><input type="radio" name="enc-{name}" checked={project.export.encoder === "h264_videotoolbox"}
              onchange={() => change((p) => (p.export.encoder = "h264_videotoolbox"))} /> Fast (Apple hardware encoder)</label>
          </div>
        {/if}
      </div>
    {/if}

    <label class="field"><input type="radio" name="size-{name}" checked={fit} onchange={() => setFit(true)} />
      <b>Fit a size</b></label>
    {#if fit}
      <div class="col indent">
        <div class="row wrap">
          <input class="amount" type="number" min="0.01" step="0.1" bind:value={amount} onchange={setBudget} />
          <select bind:value={unit} onchange={setBudget}>
            <option value="per_hour">GB per hour</option>
            <option value="total">GB in total</option>
          </select>
          {#if allowed}<span class="dim">= {sizeWords(allowed)} for {formatDuration(seconds)}</span>{/if}
        </div>
        <div class="row wrap">
          <label class="field">Format
            <select value={project.export.codec ?? "hevc"} onchange={(e) => change((p) => (p.export.codec = (e.target as HTMLSelectElement).value as "h264" | "hevc"))}>
              <option value="hevc">HEVC — smaller files</option>
              <option value="h264">H.264 — plays everywhere</option>
            </select></label>
          <label class="field">Size
            <select value={String(project.export.height ?? "")} onchange={(e) => { const v = (e.target as HTMLSelectElement).value; change((p) => (p.export.height = v ? Number(v) : null)); }}>
              <option value="">Same as the project ({canvasHeight}p)</option>
              {#each heightChoices(canvasHeight) as h}<option value={String(h)}>{heightName(h)}</option>{/each}
            </select></label>
          <label class="field"><input type="checkbox" checked={!!project.export.fast}
            onchange={(e) => change((p) => (p.export.fast = (e.target as HTMLInputElement).checked))} /> Faster, lower quality (hardware)</label>
        </div>

        {#if estimating}
          <div class="card est dim">Estimating… {estimating.step?.title ?? "waiting in the queue"}</div>
        {:else if estimate}
          <div class="card est col" class:stale>
            {#if stale}<div class="warn">Settings changed: estimate again.</div>{/if}
            <div class="big"><b>{sizeWords(estimate.predicted_bytes)}</b>
              <span class="dim">of your {sizeWords(estimate.budget_bytes)}</span></div>
            <dl class="facts">
              <dt>Picture</dt>
              <dd>{estimate.mode === "copy" ? "The originals, untouched" : estimate.words?.split(";")[0]}
                {#if estimate.quality_mean != null}<span class="faint small">({metricName(estimate)} {Math.round(estimate.quality_mean)} of 100)</span>{/if}
                <div class="dim small">{estimate.width}×{estimate.height} ({heightName(estimate.height)}), {estimate.codec === "hevc" ? "HEVC" : "H.264"}{#if estimate.footage && estimate.footage.height < estimate.height}; most of the footage is {estimate.footage.height} lines, enlarged{/if}</div></dd>
              {#if weak(estimate)}
                <dt>Weakest</dt>
                <dd>at {formatClock(estimate.quality_low_at_sec ?? 0, false)}{#if estimate.quality_low_video} in <span class="mono">{ltr(estimate.quality_low_video)}</span>{/if}
                  <span class="faint small">({metricName(estimate)} {Math.round(estimate.quality_low ?? 0)})</span></dd>
              {/if}
              <dt>Sound</dt>
              <dd>{#if estimate.audio}AAC {Math.round(estimate.audio.bps / 1000)} kbit/s stereo, {estimate.audio.sample_rate / 1000} kHz{:else}AAC stereo{/if}
                <div class="dim small">Not reduced by the budget{#if estimate.audio} ({sizeWords(estimate.audio.bps * estimate.duration_sec / 8)} of the file){/if}.</div></dd>
              {#if estimate.encode_minutes}
                <dt>Time</dt><dd>about {minutesWords(estimate.encode_minutes)} to encode{#if estimate.passes === 2} <span class="dim small">(two passes)</span>{/if}</dd>
              {/if}
            </dl>
            {#if estimate.suggest_height && !stale}
              <div class="row suggest"><span><b>{heightName(estimate.suggest_height)} would look better</b> at this size{#if estimate.suggest_quality != null} <span class="faint small">({metricName(estimate)} {Math.round(estimate.suggest_quality)} instead of {Math.round(estimate.quality_mean ?? 0)})</span>{/if}.</span>
                <span class="spacer"></span>
                <button class="small" onclick={() => change((p) => (p.export.height = estimate!.suggest_height))}>Use {heightName(estimate.suggest_height)}</button></div>
            {/if}
            {#each estimate.notes.filter((n) => !n.startsWith("At this size")) as note}<div class="dim small">{note}</div>{/each}
          </div>
        {:else}
          <div class="dim small">Estimate tries a few short samples to find the best quality that fits, and how long the export will take.</div>
        {/if}
      </div>
    {/if}

    <div class="subs col">
      <div class="row"><b>Subtitles</b><span class="faint small">separate .srt files beside the video, and/or tracks inside it that viewers can switch on</span></div>
      <table class="small">
        <thead><tr><th></th><th>.srt file</th><th>Track in the video</th><th></th></tr></thead>
        <tbody>
          {#each SUB_LANGS as [lang, label] (lang)}
            {@const c = subDone?.[lang]}
            <tr>
              <td>{label}</td>
              <td><input type="checkbox" checked={subSetting.srt.includes(lang)} onchange={(e) => toggleSub("srt", lang, (e.target as HTMLInputElement).checked)} /></td>
              <td><input type="checkbox" checked={subSetting.tracks.includes(lang)} onchange={(e) => toggleSub("tracks", lang, (e.target as HTMLInputElement).checked)} /></td>
              <td class="faint">{!c ? "" : !c.cues ? "none yet" : `${c.cues} lines` + (lang !== "mixed" && c.missing ? ` · ${c.missing} not translated (shown as spoken)` : "") + (c.stale ? ` · ${c.stale} out of date` : "")}</td>
            </tr>
          {/each}
        </tbody>
      </table>
      {#if subSetting.tracks.length > 1}
        <label class="field small">Shown when the video opens
          <select value={subSetting.default_track ?? ""} onchange={(e) => setSubDefault((e.target as HTMLSelectElement).value)}>
            <option value="">None (viewers switch them on)</option>
            {#each SUB_LANGS.filter(([l]) => subSetting.tracks.includes(l)) as [l, label] (l)}<option value={l}>{label}</option>{/each}
          </select></label>
      {/if}
      {#if (subSetting.srt.length || subSetting.tracks.length) && subDone && subDone.mixed.uncovered_sec >= 1}
        <div class="warn small">{formatDuration(subDone.mixed.uncovered_sec)} of the video has no subtitles yet: in Edit,
          <a href="#/edit/{encodeURIComponent(name)}">Subtitles… → Generate for clips without subtitles</a>.</div>
      {/if}
    </div>

    {#if missing.length}
      <div class="err">Missing files — export is not possible:
        {#each missing as m}<div class="mono small">{ltr(m)}</div>{/each}</div>
    {/if}
    <div class="dim small">{formatDuration(plan.duration)} → <span class="mono">{ltr(plan.output)}</span>
      {#if plan.output_exists}<span class="warn"> (exists: will be replaced)</span>{/if}</div>
    <div class="row"><span class="spacer"></span>
      {#if fit}<button disabled={!!estimating || !!missing.length} onclick={() => submit("export_estimate")}>Estimate</button>{/if}
      <button class="primary" disabled={!!missing.length} onclick={() => submit("export")}>Export</button>
    </div>
  </div>
{/if}

<style>
  .subs { gap: 4px; padding-top: 6px; border-top: 1px solid var(--border); }
  .subs table { border-collapse: collapse; }
  .subs th { font-weight: 500; color: var(--text-dim); text-align: left; padding: 2px 10px 2px 0; }
  .subs td { padding: 2px 10px 2px 0; }
  .settings { gap: 8px; }
  .indent { padding-left: 22px; gap: 6px; }
  .amount { width: 70px; }
  .est { padding: 8px 10px; gap: 4px; }
  .est.stale { opacity: 0.6; }
  .big b { font-size: 15px; }
  .facts { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; margin: 4px 0; }
  .facts dt { color: var(--text-dim, inherit); opacity: 0.7; }
  .facts dd { margin: 0; }
  .suggest { padding: 6px 8px; border-radius: var(--radius); background: var(--bg-hover); }
  .err { color: var(--danger); }
</style>
