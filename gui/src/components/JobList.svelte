<script lang="ts">
  // Queue and history: live progress, cancel, logs, and where the result went (§6.5).
  // Shown in the Jobs drawer; `filter` narrows it (Deliver shows only renders).
  import { api, type Job } from "../lib/api";
  import { links } from "../lib/router.svelte";
  import Icon from "./Icon.svelte";
  import { reportError, store } from "../lib/state.svelte";
  import { formatDuration } from "../lib/time";

  let { filter }: { filter?: (j: Job) => boolean } = $props();

  let open = $state<Record<string, boolean>>({});
  const shown = $derived(filter ? store.jobs.filter(filter) : store.jobs);
  const finished = $derived(shown.filter((j) => j.state !== "running" && j.state !== "queued"));
  async function clear() {
    try {
      // A filtered list (Deliver's renders) clears only what it shows.
      if (filter) for (const j of finished) await api.removeJob(j.id);
      else await api.clearJobs();
    } catch (e) {
      reportError(e);
    }
  }
  let logs = $state<Record<string, string>>({});

  function elapsed(j: Job, clock: number) {
    if (!j.started) return null;
    const end = j.finished ? Date.parse(j.finished) : clock;
    return (end - Date.parse(j.started)) / 1000;
  }

  function fraction(j: Job): number | null {
    if (j.candidates) return j.candidates.done / j.candidates.total;
    if (j.progress?.total_sec) return Math.min(1, j.progress.processed_sec / j.progress.total_sec);
    return null;
  }

  let now = $state(Date.now());
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(t);
  });

  async function refreshLog(id: string) {
    try {
      logs[id] = await api.jobLog(id);
    } catch (e) {
      reportError(e);
    }
  }
  $effect(() => {
    const ids = Object.keys(open).filter((id) => open[id]);
    ids.forEach(refreshLog);
    const t = setInterval(() => {
      for (const id of ids) {
        if (store.jobs.find((j) => j.id === id)?.state === "running") void refreshLog(id);
      }
    }, 2000);
    return () => clearInterval(t);
  });

  const outputName = (j: Job) => j.result?.output?.split("/").pop() ?? null;
  const copy = (text: string) => navigator.clipboard.writeText(text);
</script>

<div class="col list">
  {#if !store.connected}<span class="warn small">Reconnecting to the server…</span>{/if}
  {#if finished.length}
    <div class="row head">
      <span class="faint small">{shown.length - finished.length} waiting or running · {finished.length} finished</span>
      <span class="spacer"></span>
      <button class="small" onclick={clear}>Clear finished</button>
    </div>
  {/if}
  {#each shown as j (j.id)}
    {@const f = fraction(j)}
    {@const secs = elapsed(j, now)}
    <div class="card col job">
      <div class="row">
        <span class="chip {j.state}">{j.cancel_requested ? "cancelling" : j.state}</span>
        <button class="link title ellipsis" onclick={() => (open[j.id] = !open[j.id])}>{j.title}</button>
        <span class="spacer"></span>
        {#if secs !== null}<span class="faint small">{formatDuration(secs)}</span>{/if}
        {#if j.state === "running"}
          <button class="small danger" disabled={j.cancel_requested} onclick={() => api.cancel(j.id).catch(reportError)}>Cancel</button>
        {:else if j.state === "queued"}
          <button class="small danger" title="Take it off the queue" onclick={() => api.removeJob(j.id).catch(reportError)}>Remove</button>
        {:else}
          <button class="small" onclick={() => api.retry(j.id).catch(reportError)}>Run again</button>
        {/if}
        {#if j.state === "done" && j.type === "export" && j.result?.output}
          <button class="small" onclick={() => api.reveal(j.result!.output).catch(reportError)}>Reveal</button>
        {:else if j.state === "done" && outputName(j)}
          <a href={links.output(outputName(j)!)}><button class="small primary">Compare</button></a>
        {/if}
        {#if j.state !== "running" && j.state !== "queued"}
          <button class="icon ghost" title="Remove from the list" aria-label="Remove from the list"
                  onclick={() => api.removeJob(j.id).catch(reportError)}><Icon name="x" size={14} /></button>
        {/if}
      </div>
      {#if j.state === "running"}
        <div class="row small">
          {#if j.step}<span class="dim">[{j.step.index}/{j.step.total}] {j.step.title}</span>{/if}
          {#if j.candidates}<span class="dim">tuning {j.candidates.window}: candidate {j.candidates.done}/{j.candidates.total}</span>{/if}
          <span class="spacer"></span>
          {#if j.progress?.eta_sec != null}<span class="faint">ETA {formatDuration(j.progress.eta_sec)}</span>{/if}
          {#if j.progress?.speed}<span class="faint">{j.progress.speed}</span>{/if}
        </div>
        <progress max="1" value={f ?? undefined}></progress>
      {/if}
      {#if j.error && j.state !== "done"}<div class="error-box small">{j.error}</div>{/if}
      {#if open[j.id]}
        <div class="col details">
          {#if j.display}
            <div class="row"><code class="cmd grow">{j.display}</code><button class="small" onclick={() => copy(j.display!)}>Copy</button></div>
          {/if}
          {#each j.messages.filter((m) => m.level !== "info") as m}
            <div class="small {m.level === 'warning' ? 'warn' : m.level === 'error' ? 'danger' : 'ok'}">{m.text}</div>
          {/each}
          <pre class="log">{logs[j.id] ?? "…"}</pre>
        </div>
      {/if}
    </div>
  {:else}
    <div class="dim empty">No jobs yet.</div>
  {/each}
</div>

<style>
  .list { gap: 6px; }
  .job { gap: 6px; padding: 8px 12px; }
  .empty { padding: 12px; }
  .title { color: var(--text); font-weight: 500; text-align: left; max-width: 70%; }
  .small { font-size: 12.5px; }
  .grow { flex: 1; }
  progress { width: 100%; }
  .cmd { display: block; word-break: break-all; color: var(--text-dim); }
  .log {
    max-height: 320px; overflow: auto; background: var(--bg-sunken); padding: 8px 10px; margin: 0;
    border-radius: var(--radius); font-size: 11.5px; white-space: pre-wrap; word-break: break-all;
  }
</style>
