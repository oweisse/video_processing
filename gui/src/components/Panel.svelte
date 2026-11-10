<script lang="ts">
  // A titled panel with a scrolling body: the building block of every workspace.
  import type { Snippet } from "svelte";

  let { title, actions, children, scroll = true, pad = false }: {
    title?: string;
    actions?: Snippet;
    children: Snippet;
    scroll?: boolean;
    pad?: boolean;
  } = $props();
</script>

<section class="panel">
  {#if title || actions}
    <header>
      {#if title}<h3>{title}</h3>{/if}
      <span class="spacer"></span>
      {@render actions?.()}
    </header>
  {/if}
  <div class="body" class:scroll class:pad>{@render children()}</div>
</section>

<style>
  .panel { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; background: var(--bg-raised); }
  header {
    flex: 0 0 28px; display: flex; align-items: center; gap: 6px; padding: 0 6px 0 10px;
    background: var(--bg-header); border-bottom: 1px solid var(--gap);
  }
  h3 { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-dim); }
  .body { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .body.scroll { overflow: auto; display: block; }
  .body.pad { padding: 10px; }
</style>
