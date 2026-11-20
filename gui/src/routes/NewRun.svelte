<script lang="ts">
  // A form over clean-audio that always shows the command it will run (docs/gui-design.md §6.4).
  import { api, type CliOption, type SourceKind } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { go, shell } from "../lib/router.svelte";
  import { loadLibrary, nativePick, reportError, sourceById, store, toast, queued } from "../lib/state.svelte";
  import { formatDuration, parseTime } from "../lib/time";
  import FolderBrowser from "../components/FolderBrowser.svelte";
  import SourceStrip from "../components/SourceStrip.svelte";
  import type { BatchRow } from "../edit/cleanclips";

  // onqueued: called once the runs are queued (the form inside the editor closes then),
  // with the batch rows queued; without it the job queue opens.
  // batch: stretches of several videos to clean with one set of settings (the editor's
  // "Clean clips…"); the source and window fields give way to a list of them.
  let { params, onqueued, batch }: {
    params: Record<string, string>;
    onqueued?: (rows: BatchRow[]) => void;
    batch?: BatchRow[];
  } = $props();

  // svelte-ignore state_referenced_locally
  const initial = params;
  let sourceId = $state(initial.source ?? "");
  let kind = $state<SourceKind | "">((initial.kind as SourceKind) || "");
  // Background music stays unless asked to go (then the speech denoiser takes it out).
  let removeMusic = $state(initial.music === "remove");
  let autotune = $state(true);
  let tag = $state(initial.tag ?? "");
  let rows = $state([{ start: initial.start ? String(initial.start) : "", duration: initial.duration ? String(initial.duration) : "" }]);
  let overrides = $state<Record<string, any>>({});
  let showAdvanced = $state(false);
  let schema = $state<CliOption[]>([]);
  let browsing = $state(false);
  let submitting = $state(false);
  // svelte-ignore state_referenced_locally
  let picked = $state((batch ?? []).map(() => true));
  // svelte-ignore state_referenced_locally
  if (batch?.length && !kind) {
    const kinds = new Set(batch.map((b) => sourceById(b.source)?.source_kind).filter((k) => !!k));
    if (kinds.size === 1) kind = [...kinds][0]!;
  }

  const source = $derived(sourceById(sourceId));
  const sources = $derived((store.library?.sources ?? []).filter((s) => s.exists));
  const fileDuration = $derived(source?.probe?.duration_sec ?? null);

  $effect(() => {
    if (!sourceId && sources.length === 1) sourceId = sources[0].id;
  });
  $effect(() => {
    if (source && !source.probe) api.probe(source.id).then((p) => (source.probe = p)).catch(() => {});
    if (source && !kind && source.source_kind) kind = source.source_kind;
  });

  api.cliSchema().then((s) => (schema = s)).catch(reportError);

  // ---------------------------------------------------------------- parsing
  type Parsed = { start: number; duration: number | null; error: string | null; source?: string };
  function parseRow(r: { start: string; duration: string }): Parsed {
    try {
      const start = parseTime(r.start) ?? 0;
      const duration = parseTime(r.duration);
      if (duration !== null && duration <= 0) return { start, duration, error: "Length must be more than zero" };
      if (fileDuration !== null && start >= fileDuration) return { start, duration, error: "Starts after the end of the video" };
      return { start, duration, error: null };
    } catch (e) {
      return { start: 0, duration: null, error: (e as Error).message };
    }
  }
  const parsed = $derived<Parsed[]>(
    batch
      ? batch.filter((_, i) => picked[i]).map((b) => ({ start: b.start, duration: b.duration, error: null, source: b.source }))
      : rows.map(parseRow),
  );

  function request(p: Parsed) {
    return {
      type: "clean", source_id: p.source ?? sourceId, start_sec: p.start, duration_sec: p.duration,
      autotune, source_kind: kind || null, remove_music: removeMusic, tag: tag.trim() || null,
      overrides: $state.snapshot(overrides),
    };
  }

  // ---------------------------------------------------------------- previews
  let command = $state<string | null>(null);
  let commandError = $state<string | null>(null);
  let names = $state<{ name: string; exists: boolean }[]>([]);
  let timer: ReturnType<typeof setTimeout>;
  $effect(() => {
    const reqs = parsed.map((p) => ({ p, req: request(p) }));
    clearTimeout(timer);
    if (!sourceId && !batch) {
      command = null;
      names = [];
      return;
    }
    timer = setTimeout(async () => {
      const first = reqs[0];
      if (autotune && !kind) {
        command = null;
        commandError = null; // the form says what is missing
      } else if (!first) {
        command = null;
        commandError = null;
      } else if (first.p.error) {
        commandError = first.p.error;
        command = null;
      } else {
        try {
          command = (await api.preview(first.req)).display;
          commandError = null;
        } catch (e) {
          commandError = (e as Error).message;
          command = null;
        }
      }
      names = await Promise.all(
        reqs.map(({ p }) =>
          p.error ? Promise.resolve({ name: "", exists: false })
            : batch ? Promise.resolve({ name: "", exists: false })
            : api.previewName(sourceId, p.start, p.duration, tag.trim() || null).catch(() => ({ name: "", exists: false })),
        ),
      );
    }, 250);
  });

  // Rough cost from measured runs: tuning ≈ 0.8 s per second of audio, the render ≈ 0.35 s/s,
  // Demucs adds about 0.5 s/s.
  const estimate = $derived(
    parsed.reduce((sum, p) => {
      const len = p.duration ?? (p.source ? 0 : fileDuration !== null ? fileDuration - p.start : 0);
      return sum + len * ((autotune ? 0.8 : 0) + 0.35 + (removeMusic ? 0 : 0.5));
    }, 0),
  );

  const ready = $derived(
    (!!sourceId || (!!batch && parsed.length > 0)) && (!autotune || !!kind) && parsed.every((p) => !p.error) && !commandError && !submitting,
  );

  async function submit(stay: boolean) {
    submitting = true;
    try {
      for (const p of parsed) {
        const job = await api.submit(request(p));
        if (!batch) queued(job);
      }
      if (batch) toast(`Queued ${parsed.length} clean${parsed.length === 1 ? "" : "s"}`, "info", { label: "Show queue", run: () => (shell.jobsOpen = true) });
      if (stay) rows = [{ start: "", duration: "" }];
      else if (onqueued) onqueued(batch ? batch.filter((_, i) => picked[i]) : []);
      else shell.jobsOpen = true;
    } catch (e) {
      reportError(e);
    } finally {
      submitting = false;
    }
  }

  async function chooseFile() {
    const picked = await nativePick("file", source ? source.path.replace(/\/[^/]*$/, "") : undefined);
    if (picked === undefined) browsing = true;
    else if (picked) await addSource(picked);
  }

  async function addSource(path: string) {
    browsing = false;
    try {
      const s = await api.addSource(path);
      await loadLibrary();
      sourceId = s.id;
    } catch (e) {
      reportError(e);
    }
  }

  // ---------------------------------------------------------------- advanced
  const groups = $derived.by(() => {
    const g = new Map<string, CliOption[]>();
    for (const o of schema) {
      if (["--normalize", "--verbose", "--compare", "--filter_audio_only"].includes(o.flag)) continue;
      const key = o.group === "options" || o.group === "optional arguments" || !o.group ? "Options" : o.group;
      if (!g.has(key)) g.set(key, []);
      g.get(key)!.push(o);
    }
    return [...g.entries()];
  });
  function setOverride(o: CliOption, value: any) {
    const isDefault = o.kind === "flag" ? value === false : String(value) === String(o.default ?? "");
    if (isDefault || value === "") delete overrides[o.flag];
    else overrides[o.flag] = value;
  }
  const overrideCount = $derived(Object.keys(overrides).length);
</script>

<div class="page col narrow">
  {#if !onqueued}<h1>New run</h1>{/if}
  {#if !store.library?.output_dir}
    <div class="error-box">Open a library folder first (Media).</div>
  {:else}
    <div class="card col">
      {#if batch}
        <div class="field">
          <span>Stretches to clean <span class="faint">(each clip with a little sound to spare; nearby clips of a video together)</span></span>
          {#each batch as b, i}
            <label class="row small">
              <input type="checkbox" bind:checked={picked[i]} />
              <span class="ellipsis grow">{ltr(sourceById(b.source)?.name ?? b.source)}</span>
              <span class="mono faint">{formatDuration(b.start)}–{formatDuration(b.start + b.duration)}</span>
              {#if b.note}<span class="faint">{b.note}</span>{/if}
            </label>
          {/each}
        </div>
      {:else}
      <label class="field">
        <span>Source video</span>
        <div class="row">
          <select bind:value={sourceId} class="grow">
            <option value="" disabled>Choose a video…</option>
            {#each sources as s (s.id)}<option value={s.id}>{ltr(s.name)}</option>{/each}
          </select>
          <button onclick={chooseFile} title="Or drop a video onto the window">Add file…</button>
        </div>
        {#if source}
          <span class="faint mono small ellipsis">{ltr(source.path)}
            {#if fileDuration} · {formatDuration(fileDuration)}{/if}</span>
          {#key source.id}<SourceStrip {source} height={64} />{/key}
        {/if}
      </label>

      <div class="field">
        <span>Windows <span class="faint">(start and length: 24m45s, 5m, 1h05m, 90; empty length = to the end)</span></span>
        {#each rows as r, i}
          <div class="row">
            <input placeholder="start (0)" bind:value={r.start} class="time" />
            <input placeholder="length (to end)" bind:value={r.duration} class="time" />
            <span class="small {names[i]?.exists ? 'warn' : 'faint'} ellipsis grow">
              {#if parsed[i].error}<span class="danger">{parsed[i].error}</span>
              {:else if names[i]?.name}→ {ltr(names[i].name)}{names[i].exists ? " (exists: will be replaced)" : ""}{/if}
            </span>
            {#if rows.length > 1}<button class="small" onclick={() => rows.splice(i, 1)}>✕</button>{/if}
          </div>
        {/each}
        <div><button class="small" onclick={() => rows.push({ start: "", duration: "" })}>+ Add window</button></div>
      </div>
      {/if}

      <div class="field">
        <span>What did {batch ? "these videos" : "it"} come from?</span>
        <div class="row">
          <label class="row"><input type="radio" bind:group={kind} value="tape" /> Tape <span class="faint small">(VHS, Super-8, other tape transfers: tunes hiss and tick removal)</span></label>
        </div>
        <div class="row">
          <label class="row"><input type="radio" bind:group={kind} value="digital" /> Digital <span class="faint small">(camera or phone video: leaves hiss and declick off)</span></label>
        </div>
      </div>

      <label class="row"><input type="checkbox" bind:checked={removeMusic} /> Remove background music
        <span class="faint small">{removeMusic
          ? "music is treated as noise and taken out with it"
          : "off: the music stays as it is; only the voices are cleaned (they are separated from the music with Demucs first)"}</span></label>
      <label class="row"><input type="checkbox" bind:checked={autotune} /> Autotune
        <span class="faint small">try the settings grid on this window and use the best; every candidate is kept for listening</span></label>
      <label class="field">
        <span>Tag <span class="faint">(added to the output name, e.g. v3)</span></span>
        <input bind:value={tag} class="tag" />
      </label>

      <details bind:open={showAdvanced}>
        <summary>Advanced options {#if overrideCount}<span class="chip">{overrideCount} changed</span>{/if}</summary>
        <div class="col adv">
          {#each groups as [group, opts]}
            <h3>{group}</h3>
            <div class="grid">
              {#each opts as o (o.flag)}
                {@const disabled = autotune && o.autotuned}
                <label class="opt" title={o.help ?? ""} class:disabled>
                  <span class="mono small">{o.flag}</span>
                  {#if o.kind === "flag"}
                    <input type="checkbox" {disabled} checked={!!overrides[o.flag]}
                           onchange={(e) => setOverride(o, (e.target as HTMLInputElement).checked)} />
                  {:else if o.kind === "choice"}
                    <select {disabled} value={overrides[o.flag] ?? o.default ?? ""}
                            onchange={(e) => setOverride(o, (e.target as HTMLSelectElement).value)}>
                      {#each o.choices ?? [] as c}<option value={c}>{c}</option>{/each}
                    </select>
                  {:else}
                    <input {disabled} placeholder={o.default === null ? "auto" : String(o.default)}
                           value={overrides[o.flag] ?? ""}
                           oninput={(e) => setOverride(o, (e.target as HTMLInputElement).value.trim())} />
                  {/if}
                  {#if disabled}<span class="faint small">autotune</span>{/if}
                </label>
              {/each}
            </div>
          {/each}
        </div>
      </details>
    </div>

    <div class="card col">
      <div class="row"><h3>Command</h3><span class="spacer"></span>
        {#if estimate > 0}<span class="dim small">about {formatDuration(estimate)} of work</span>{/if}</div>
      {#if commandError}<div class="danger small">{commandError}</div>
      {:else if command}<code class="cmd">{command}</code>{#if parsed.length > 1}<span class="faint small">…and {parsed.length - 1} more like it</span>{/if}
      {:else if batch ? !parsed.length : !sourceId}<span class="faint small">{batch ? "Choose at least one stretch." : "Choose a source."}</span>{/if}
      {#if autotune && !kind}<div class="warn small">Choose tape or digital: autotune tunes differently for each.</div>{/if}
      <div class="row">
        <span class="spacer"></span>
        {#if !batch}<button disabled={!ready} onclick={() => submit(true)}>Queue and add another</button>{/if}
        <button class="primary" disabled={!ready} onclick={() => submit(false)}>
          Queue {parsed.length > 1 ? `${parsed.length} runs` : "run"}
        </button>
      </div>
    </div>
  {/if}
</div>

{#if browsing}
  <FolderBrowser mode="file" title="Add a source video" start={source ? source.path.replace(/\/[^/]*$/, "") : "~"}
                 onpick={addSource} onclose={() => (browsing = false)} />
{/if}

<style>
  .narrow { max-width: 920px; }
  .field { display: flex; flex-direction: column; gap: 6px; }
  .field > span:first-child { font-weight: 500; }
  .grow { flex: 1; min-width: 0; }
  .time { width: 140px; font-family: var(--mono); }
  .tag { width: 200px; }
  .small { font-size: 12.5px; }
  .cmd { display: block; word-break: break-all; background: var(--bg-sunken); padding: 8px 10px; border-radius: var(--radius); }
  summary { cursor: pointer; color: var(--text-dim); }
  .adv { margin-top: 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 6px 14px; }
  .opt { display: grid; grid-template-columns: 1fr 110px; align-items: center; gap: 6px; }
  .opt input:not([type="checkbox"]), .opt select { width: 110px; }
  .opt.disabled { opacity: 0.5; }
  h3 { margin-top: 6px; color: var(--text-dim); font-size: 12.5px; text-transform: uppercase; letter-spacing: 0.04em; }
</style>
