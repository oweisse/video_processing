<script lang="ts">
  // Disk used per video: the original, and what the editor added on top of it (editing
  // copies, tuning audio, rendered versions), with totals for the project and the trash.
  import { api, type StorageUsage } from "../lib/api";
  import { reportError, toast } from "../lib/state.svelte";
  import { formatBytes } from "../lib/time";

  let { project = null }: { project?: string | null } = $props();

  let data = $state<StorageUsage | null>(null);
  let loading = $state(false);
  async function load() {
    loading = true;
    try {
      data = await api.storage(project);
    } catch (e) {
      reportError(e);
    } finally {
      loading = false;
    }
  }
  $effect(() => {
    void project;
    void load();
  });

  async function emptyTrash() {
    if (!data || !confirm(`Delete everything in the library's _trash folder (${formatBytes(data.trash)}) for good? This cannot be undone.`)) return;
    try {
      const r = await api.emptyTrash();
      toast(`Freed ${formatBytes(r.freed)}`);
      await load();
    } catch (e) {
      reportError(e);
    }
  }

  const rows = $derived([...(data?.videos ?? [])].sort((a, b) => b.extra - a.extra));
</script>

<div class="col storage">
  {#if !data}
    <p class="faint">{loading ? "Measuring…" : ""}</p>
  {:else}
    <p class="dim small">Per video: the original file, and what this editor added for it. <b>Editing copies</b> are the
      light preview video, thumbnails and cached frames (remade when needed). <b>Tuning audio</b> is each tuning run's
      candidate sound (blue stripes). <b>Rendered versions</b> are the cleaned videos (green stripes).</p>
    <div class="scroll-x">
      <table>
        <thead>
          <tr><th>Video</th><th class="num">Original</th><th class="num">Editing copies</th><th class="num">Tuning audio</th>
            <th class="num">Rendered versions</th><th class="num">Added in all</th></tr>
        </thead>
        <tbody>
          {#each rows as r (r.id)}
            <tr>
              <td class="name" title={r.name}>{r.name}{#if !r.exists}<span class="warn small"> · missing</span>{/if}</td>
              <td class="num">{formatBytes(r.original)}</td>
              <td class="num">{r.editing ? formatBytes(r.editing) : "—"}</td>
              <td class="num">{r.tuning ? formatBytes(r.tuning) : "—"}<span class="faint small">{r.tunings ? ` · ${r.tunings} run${r.tunings > 1 ? "s" : ""}` : ""}</span></td>
              <td class="num">{r.rendered ? formatBytes(r.rendered) : "—"}<span class="faint small">{r.versions ? ` · ${r.versions}` : ""}</span></td>
              <td class="num strong">{r.extra ? formatBytes(r.extra) : "—"}</td>
            </tr>
          {/each}
        </tbody>
        <tfoot>
          <tr>
            <td>{project ? `${project}: ${rows.length} videos` : `Library: ${rows.length} videos`}</td>
            <td class="num">{formatBytes(data.totals.original)}</td>
            <td class="num">{formatBytes(data.totals.editing)}</td>
            <td class="num">{formatBytes(data.totals.tuning)}</td>
            <td class="num">{formatBytes(data.totals.rendered)}</td>
            <td class="num strong">{formatBytes(data.totals.extra)}</td>
          </tr>
        </tfoot>
      </table>
    </div>
    <div class="row wrap small">
      {#if project}
        <span>Project folder (copied music, project file): <b>{formatBytes(data.totals.project_files)}</b></span>
        {#if data.totals.exported}<span>· Exported video: <b>{formatBytes(data.totals.exported)}</b></span>{/if}
        <span>· Added by the editor for this project in all:
          <b>{formatBytes(data.totals.extra + data.totals.project_files + data.totals.exported)}</b>
          on top of {formatBytes(data.totals.original)} of originals</span>
      {/if}
    </div>
    <div class="row small trash">
      <span>In <code>_trash</code> (deleted versions and tuning runs, still on disk): <b>{formatBytes(data.trash)}</b></span>
      <span class="spacer"></span>
      <button class="small" onclick={load} disabled={loading}>{loading ? "Measuring…" : "Measure again"}</button>
      <button class="small danger" disabled={!data.trash} onclick={emptyTrash}>Empty _trash…</button>
    </div>
    <p class="faint small">Delete a rendered version from a clip's sound menu, its candidates page or Media; a tuning run
      from its candidates page. Videos shared by several projects count in each.</p>
  {/if}
</div>

<style>
  .storage { gap: 10px; }
  .scroll-x { overflow-x: auto; }
  table { font-size: 13px; width: 100%; border-collapse: collapse; }
  th, td { padding: 4px 8px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { text-align: left; color: var(--text-dim); font-weight: 500; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .name { max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
  .strong { font-weight: 600; }
  tfoot td { font-weight: 600; border-bottom: none; border-top: 2px solid var(--border); }
  .trash { gap: 8px; align-items: center; }
  .small { font-size: 12px; }
  .wrap { flex-wrap: wrap; gap: 6px; }
</style>
