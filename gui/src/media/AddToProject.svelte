<script lang="ts">
  // "Add to project ▾": puts videos in a project's list of videos (to pick clips from in
  // Edit), or a single cleaned piece straight onto its timeline. Prepares the videos for
  // the preview when needed.
  import type { Output, Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { store } from "../lib/state.svelte";
  import { addToProject } from "./projects";
  import Icon from "../components/Icon.svelte";

  let { sources, piece = null, small = true, label = "Add to project", onnew }: {
    sources: Source[];
    piece?: Output | null; // with one source: put this cleaned piece on the timeline
    small?: boolean;
    label?: string;
    onnew?: () => void; // "New project…" (a dialog the parent shows)
  } = $props();

  let open = $state(false);
  const projects = $derived(store.library?.projects ?? []);

  async function add(name: string) {
    open = false;
    await addToProject(name, sources, piece);
  }
</script>

<span class="wrap">
  <button class:small onclick={() => (open = !open)} disabled={!sources.length || sources.some((s) => !s.exists)} aria-expanded={open}
          title={piece ? "Put this cleaned piece at the end of a project's timeline" : "Add to a project's videos, to pick clips from in Edit"}>
    {label} <Icon name="chevron-down" size={11} /></button>
  {#if open}
    <div class="backdrop" role="presentation" onclick={() => (open = false)}></div>
    <div class="menu" role="menu">
      {#each projects as t (t.name)}
        {@const has = sources.every((s) => t.videos?.includes(s.id))}
        <button role="menuitem" onclick={() => add(t.name)} disabled={has && !piece}>
          <span class="ellipsis">{ltr(t.name)}</span>
          <span class="faint">{has && !piece ? "already in it" : `${t.videos?.length ?? 0} videos`}</span></button>
      {/each}
      {#if onnew}
        <button role="menuitem" onclick={() => { open = false; onnew(); }}><span>＋ New project…</span></button>
      {:else if !projects.length}
        <span class="faint pad">No projects yet. Create one in Edit, or select videos in Media and choose New project from these….</span>
      {/if}
    </div>
  {/if}
</span>

<style>
  .wrap { position: relative; display: inline-block; }
  .wrap > button { display: inline-flex; align-items: center; gap: 4px; }
  .backdrop { position: fixed; inset: 0; z-index: 30; }
  .menu {
    position: absolute; right: 0; top: calc(100% + 2px); z-index: 31; min-width: 240px; max-width: 340px;
    display: flex; flex-direction: column; padding: 4px; background: var(--bg-header);
    border: 1px solid var(--border); border-radius: var(--radius); box-shadow: 0 6px 18px rgb(0 0 0 / 0.5);
  }
  .menu button {
    display: flex; justify-content: space-between; gap: 10px; background: none; border-color: transparent; text-align: left;
  }
  .menu button:hover:not(:disabled) { background: var(--bg-hover); }
  .pad { padding: 6px; white-space: normal; }
</style>
