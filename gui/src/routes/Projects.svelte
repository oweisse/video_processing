<script lang="ts">
  // Edit projects in this output folder: open, create, delete.
  import { api } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { go, href } from "../lib/router.svelte";
  import { loadLibrary, reportError, store } from "../lib/state.svelte";
  import { formatDuration } from "../lib/time";

  let name = $state("");
  async function create() {
    try {
      const p = await api.createProject(name.trim());
      await loadLibrary();
      go(["edit", p.name]);
    } catch (e) {
      reportError(e);
    }
  }
  async function remove(n: string) {
    if (!confirm(`Move the project "${n}" to the _trash folder? Exported videos stay.`)) return;
    try {
      await api.deleteProject(n);
      await loadLibrary();
    } catch (e) {
      reportError(e);
    }
  }
</script>

<div class="page col narrow">
  <h1>Edit</h1>
  <p class="dim">Cut long videos into pieces, join pieces from several videos, lay music under them, and
    export one MP4 ready for Google Photos or YouTube. Clips play with their cleaned sound wherever a
    cleaned output covers them.</p>
  {#if !store.library?.output_dir}
    <div class="error-box">Open a project folder first (Library).</div>
  {:else}
    <form class="card row" onsubmit={(e) => { e.preventDefault(); create(); }}>
      <input placeholder="New project name, e.g. Wedding 1994" bind:value={name} class="grow" />
      <button class="primary" type="submit" disabled={!name.trim()}>Create</button>
    </form>
    {#each store.library.projects ?? [] as p (p.path)}
      <div class="card row">
        <a href={href(["edit", p.name])}><h2>{ltr(p.name)}</h2></a>
        <span class="dim small">{p.clips} clip{p.clips === 1 ? "" : "s"} · {formatDuration(p.duration_sec)}</span>
        {#if p.exported}
          <span class="chip done" title={p.export_path}>exported</span>
          <button class="small" onclick={() => api.reveal(p.export_path).catch(reportError)}>Reveal</button>
        {/if}
        <span class="spacer"></span>
        <span class="faint small">{p.modified?.slice(0, 16).replace("T", " ") ?? ""}</span>
        <a href={href(["edit", p.name])}><button>Open</button></a>
        <button class="small danger" onclick={() => remove(p.name)}>Delete</button>
      </div>
    {:else}
      <div class="card dim">No projects yet.</div>
    {/each}
  {/if}
</div>

<style>
  .narrow { max-width: 900px; }
  .grow { flex: 1; }
  .small { font-size: 12.5px; }
</style>
