<script lang="ts">
  // Projects in this library folder: each a set of videos and the video stitched from
  // them. Open, create, delete.
  import { api } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { go, href } from "../lib/router.svelte";
  import { loadLibrary, reportError, sourceById, store } from "../lib/state.svelte";
  import { formatDuration } from "../lib/time";
  import Modal from "../components/Modal.svelte";
  import StorageView from "../components/StorageView.svelte";

  let name = $state("");
  let showStorage = $state(false);
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
    if (!confirm(`Move the project "${n}" to the _trash folder? Its videos and exported files stay.`)) return;
    try {
      await api.deleteProject(n);
      await loadLibrary();
    } catch (e) {
      reportError(e);
    }
  }
</script>

<div class="page col narrow">
  <div class="row"><h1>Projects</h1><span class="spacer"></span>
    <button onclick={() => (showStorage = true)} title="Disk used by every video in this library folder">Storage…</button></div>
  {#if showStorage}
    <Modal title="Storage · whole library" onclose={() => (showStorage = false)}><StorageView /></Modal>
  {/if}
  <p class="dim">A project is a set of videos and the one video you stitch from them: pick clips from its
    videos, lay music under them, and export one MP4 ready for Google Photos or YouTube. Clips play with
    their cleaned sound wherever a cleaned version covers them. To start one from videos, select them in
    <a href="#/media">Media</a> and choose <i>New project from these…</i>.</p>
  {#if !store.library?.output_dir}
    <div class="error-box">Open a library folder first (Media).</div>
  {:else}
    <form class="card row" onsubmit={(e) => { e.preventDefault(); create(); }}>
      <input placeholder="New project name, e.g. Wedding 1994" bind:value={name} class="grow" />
      <button class="primary" type="submit" disabled={!name.trim()}>Create</button>
    </form>
    {#each store.library.projects ?? [] as p (p.path)}
      {@const vids = (p.videos ?? []).map((id) => sourceById(id)).filter((s) => !!s)}
      <div class="card row project">
        <a class="posters" href={href(["edit", p.name])} title="Open">
          {#each vids.slice(0, 4) as v (v.id)}
            {#if v.probe?.video}<img src="/media/poster?source={encodeURIComponent(v.id)}&i=1" alt="" loading="lazy" />{/if}
          {:else}<span class="faint small">no videos yet</span>{/each}
        </a>
        <div class="col info">
          <a href={href(["edit", p.name])}><h2>{ltr(p.name)}</h2></a>
          <span class="dim small">{vids.length} video{vids.length === 1 ? "" : "s"} · {p.clips} clip{p.clips === 1 ? "" : "s"} on its timeline · {formatDuration(p.duration_sec)}</span>
          <span class="faint small ellipsis">{vids.map((v) => ltr(v.name)).join(" · ")}</span>
        </div>
        <span class="spacer"></span>
        {#if p.exported}
          <span class="chip done" title={p.export_path}>exported</span>
          <button class="small" onclick={() => api.reveal(p.export_path).catch(reportError)}>Reveal</button>
        {/if}
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
  .narrow { max-width: 1000px; }
  .grow { flex: 1; }
  .small { font-size: 11.5px; }
  .project { gap: 12px; }
  .posters { display: flex; gap: 2px; width: 248px; flex: 0 0 248px; }
  .posters img { width: 60px; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 2px; background: #000; }
  .info { gap: 2px; min-width: 0; flex: 1; }
</style>
