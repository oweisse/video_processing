<script lang="ts">
  // Edit workspace: the timeline list, or the editor on one timeline. Phase D of
  // docs/gui-editor-redesign.md replaces the editor's layout (§7).
  import { route } from "../lib/router.svelte";
  import Editor from "../routes/Editor.svelte";
  import Projects from "../routes/Projects.svelte";

  const name = $derived(route.parts[1] ?? null);
  const known = $derived(name !== null);
</script>

{#if known}
  {#key name}<div class="scroll fill"><Editor name={name!} /></div>{/key}
{:else}
  <div class="scroll fill"><Projects /></div>
{/if}

<style>
  .fill { flex: 1; }
</style>
