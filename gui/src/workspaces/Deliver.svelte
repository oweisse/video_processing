<script lang="ts">
  // Deliver workspace: where exports start (docs/stitch-and-size-budget-plan.md §11). The
  // projects, the chosen project's export settings (keep the quality, or fit a size, with an
  // estimate), and the render queue (exports and estimates).
  import type { Job } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { href, route } from "../lib/router.svelte";
  import { reportError, store } from "../lib/state.svelte";
  import { api } from "../lib/api";
  import type { Project } from "../lib/edit";
  import { formatDuration } from "../lib/time";
  import ExportSettings from "../deliver/ExportSettings.svelte";
  import JobList from "../components/JobList.svelte";
  import Panel from "../components/Panel.svelte";
  import Split from "../components/Split.svelte";

  const name = $derived(route.parts[1] ?? null);
  const renders = (j: Job) => j.type === "export" || j.type === "export_estimate";

  // The chosen project, loaded here and saved back after each change.
  let project = $state<Project | null>(null);
  let loadError = $state<string | null>(null);
  $effect(() => {
    const n = name;
    project = null;
    loadError = null;
    if (!n) return;
    api.getProject(n).then((p) => { if (n === name) project = p; })
      .catch((e) => (loadError = e instanceof Error ? e.message : String(e)));
  });
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  function commit(mutate: (p: Project) => void) {
    if (!project) return;
    const next = JSON.parse(JSON.stringify($state.snapshot(project))) as Project;
    mutate(next);
    project = next;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 400);
  }
  async function flush() {
    clearTimeout(saveTimer);
    if (!project || !name) return;
    try {
      await api.saveProject(name, $state.snapshot(project));
    } catch (e) {
      reportError(e);
    }
  }
</script>

<Split key="deliver.queue" initial={420} min={300} max={640} fixed="second">
  {#snippet first()}
    <Split key="deliver.list" initial={340} min={220} max={560}>
      {#snippet first()}
        <Panel title="Projects" pad>
          <table>
            <thead><tr><th>Project</th><th class="num">Length</th><th>Exported</th></tr></thead>
            <tbody>
              {#each store.library?.projects ?? [] as p (p.path)}
                <tr class:on={p.name === name}>
                  <td><a href={href(["deliver", p.name])}>{ltr(p.name)}</a></td>
                  <td class="num">{formatDuration(p.duration_sec)}</td>
                  <td>{#if p.exported}<button class="small" onclick={() => api.reveal(p.export_path).catch(reportError)}>Reveal</button>{:else}<span class="faint">not yet</span>{/if}</td>
                </tr>
              {:else}
                <tr><td colspan="3" class="faint">No projects yet.</td></tr>
              {/each}
            </tbody>
          </table>
        </Panel>
      {/snippet}
      {#snippet second()}
        <Panel title={name ? `Export settings for ${name}` : "Export settings"} pad>
          {#if !name}
            <p class="dim">Choose a project.</p>
          {:else if loadError}
            <p class="err">{loadError}</p>
          {:else if !project}
            <p class="dim">Loading…</p>
          {:else}
            {#key name}
              <ExportSettings {name} {project} {commit} {flush} />
            {/key}
            <p class="small"><a href={href(["edit", name])}>Preview in Edit</a></p>
          {/if}
        </Panel>
      {/snippet}
    </Split>
  {/snippet}
  {#snippet second()}
    <Panel title="Render queue" pad><JobList filter={renders} /></Panel>
  {/snippet}
</Split>

<style>
  tr.on td { background: var(--bg-hover); }
  .err { color: var(--danger); }
</style>
