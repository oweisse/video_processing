<script lang="ts">
  // The job queue, sliding up over the workspace from the page bar.
  import { shell } from "../lib/router.svelte";
  import Icon from "./Icon.svelte";
  import JobList from "./JobList.svelte";

  function key(ev: KeyboardEvent) {
    if (ev.key === "Escape" && shell.jobsOpen) shell.jobsOpen = false;
  }
</script>

<svelte:window onkeydown={key} />

{#if shell.jobsOpen}
  <div class="backdrop" role="presentation" onclick={() => (shell.jobsOpen = false)}></div>
  <aside class="drawer" aria-label="Jobs">
    <header>
      <h3>Jobs</h3>
      <span class="spacer"></span>
      <button class="icon ghost" title="Close (Esc)" aria-label="Close" onclick={() => (shell.jobsOpen = false)}><Icon name="x" /></button>
    </header>
    <div class="body"><JobList /></div>
  </aside>
{/if}

<style>
  .backdrop { position: fixed; inset: var(--topbar-h) 0 var(--pagebar-h) 0; background: rgb(0 0 0 / 0.35); z-index: 40; }
  .drawer {
    position: fixed; left: 0; right: 0; bottom: var(--pagebar-h); max-height: 60vh; z-index: 41;
    display: flex; flex-direction: column; background: var(--bg-raised); border-top: 1px solid var(--border);
    box-shadow: 0 -8px 24px rgb(0 0 0 / 0.4);
  }
  header { flex: 0 0 28px; display: flex; align-items: center; padding: 0 6px 0 12px; background: var(--bg-header); }
  h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); }
  .body { overflow: auto; padding: 8px 12px 12px; }
</style>
