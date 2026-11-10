<script lang="ts">
  // Deliver workspace. Until phase G of docs/gui-editor-redesign.md (§9) exports start from
  // the editor's Export… button; here are the timelines and the render queue.
  import type { Job } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { href, route } from "../lib/router.svelte";
  import { reportError, store } from "../lib/state.svelte";
  import { api } from "../lib/api";
  import { formatDuration } from "../lib/time";
  import JobList from "../components/JobList.svelte";
  import Panel from "../components/Panel.svelte";
  import Split from "../components/Split.svelte";

  const name = $derived(route.parts[1] ?? null);
  const renders = (j: Job) => j.type === "export";
</script>

<Split key="deliver.queue" initial={420} min={300} max={640} fixed="second">
  {#snippet first()}
    <Panel title="Timelines" pad>
      <p class="dim">Open a timeline in Edit and use <b>Export…</b> there. Export presets move here in a later version.</p>
      <table>
        <thead><tr><th>Timeline</th><th class="num">Length</th><th>Exported</th><th></th></tr></thead>
        <tbody>
          {#each store.library?.projects ?? [] as p (p.path)}
            <tr class:on={p.name === name}>
              <td>{ltr(p.name)}</td>
              <td class="num">{formatDuration(p.duration_sec)}</td>
              <td>{#if p.exported}<button class="small" onclick={() => api.reveal(p.export_path).catch(reportError)}>Reveal</button>{:else}<span class="faint">not yet</span>{/if}</td>
              <td class="right"><a href={href(["edit", p.name])}><button class="small">Open in Edit</button></a></td>
            </tr>
          {:else}
            <tr><td colspan="4" class="faint">No timelines yet.</td></tr>
          {/each}
        </tbody>
      </table>
    </Panel>
  {/snippet}
  {#snippet second()}
    <Panel title="Render queue" pad><JobList filter={renders} /></Panel>
  {/snippet}
</Split>

<style>
  .right { text-align: right; }
  tr.on td { background: var(--bg-hover); }
</style>
