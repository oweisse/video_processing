<script lang="ts">
  // Choose more videos of the library folder to pick clips from.
  import type { Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { formatDuration } from "../lib/time";
  import Icon from "../components/Icon.svelte";

  let { available, onadd, onclose }: { available: Source[]; onadd: (ids: string[]) => void; onclose: () => void } = $props();
  let chosen = $state<Record<string, boolean>>({});
  const ids = $derived(available.filter((s) => chosen[s.id]).map((s) => s.id));
</script>

<svelte:window onkeydown={(e) => e.key === "Escape" && onclose()} />
<div class="backdrop" role="presentation" onclick={onclose}></div>
<div class="modal card" role="dialog" aria-label="Add videos">
  <div class="row"><h2>Add videos to pick clips from</h2><span class="spacer"></span>
    <button class="icon ghost" aria-label="Close" onclick={onclose}><Icon name="x" /></button></div>
  <ul>
    {#each available as s (s.id)}
      <li><label class="row">
        <input type="checkbox" bind:checked={chosen[s.id]} />
        {#if s.probe?.video}<img src="/media/poster?source={encodeURIComponent(s.id)}&i=1" alt="" />{/if}
        <span class="ellipsis grow">{ltr(s.name)}</span>
        <span class="faint">{formatDuration(s.probe?.duration_sec)}</span>
      </label></li>
    {:else}
      <li class="faint">Every video of the library folder is already here.</li>
    {/each}
  </ul>
  <div class="row"><span class="faint small">Videos not yet prepared are prepared for the preview (a light copy, made once).</span>
    <span class="spacer"></span><button onclick={onclose}>Cancel</button>
    <button class="primary" disabled={!ids.length} onclick={() => onadd(ids)}>Add {ids.length || ""}</button></div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.45); z-index: 50; }
  .modal {
    position: fixed; z-index: 51; left: 50%; top: 10vh; transform: translateX(-50%);
    width: min(560px, 92vw); max-height: 76vh; display: flex; flex-direction: column; gap: 10px;
  }
  ul { list-style: none; margin: 0; padding: 0; overflow: auto; display: flex; flex-direction: column; gap: 2px; }
  li label { padding: 4px 6px; border-radius: var(--radius); cursor: pointer; }
  li label:hover { background: var(--bg-hover); }
  img { width: 64px; aspect-ratio: 16 / 9; object-fit: contain; background: #000; border-radius: 2px; }
  .grow { flex: 1; min-width: 0; }
  .small { font-size: 11px; }
</style>
