<script lang="ts">
  // Shown instead of any workspace until a library folder is open.
  import { api } from "../lib/api";
  import { nativePick, reportError, store } from "../lib/state.svelte";
  import FolderBrowser from "./FolderBrowser.svelte";

  let browsing = $state(false);
  let typed = $state("");

  async function browse() {
    const picked = await nativePick("dir");
    if (picked === undefined) browsing = true;
    else if (picked) await open(picked);
  }

  async function open(path: string) {
    browsing = false;
    try {
      store.library = await api.openProject(path);
      location.hash = "#/media";
    } catch (e) {
      reportError(e);
    }
  }
</script>

<div class="wrap">
  <div class="card col chooser">
    <h1>Open a library folder</h1>
    <p class="dim">The library folder is where cleaned videos are written: every cleaned video there, with
      the videos it came from, its tuning and your projects, is shown here. Pick the folder you pass to <code>--output_dir</code>{store.native ? ", or drop it onto this window" : ""}.</p>
    {#each store.library?.recent_output_dirs ?? [] as dir}
      <button class="recent" onclick={() => open(dir)}>{dir}</button>
    {/each}
    <form class="row" onsubmit={(e) => { e.preventDefault(); open(typed); }}>
      <input class="mono grow" placeholder="/Users/…/video_processing_output" bind:value={typed} />
      <button type="submit" disabled={!typed}>Open</button>
      <button type="button" onclick={browse}>Browse…</button>
    </form>
  </div>
</div>

{#if browsing}
  <FolderBrowser mode="dir" title="Open a library folder" onpick={open} onclose={() => (browsing = false)} />
{/if}

<style>
  .wrap { height: 100%; overflow: auto; padding: 40px 20px; }
  .chooser { max-width: 760px; margin: 0 auto; }
  .recent { text-align: left; font-family: var(--mono); font-size: 11.5px; }
  .grow { flex: 1; }
</style>
