<script lang="ts">
  import { onMount } from "svelte";
  import { route, href } from "./lib/router.svelte";
  import { activeJobs, connectEvents, loadJobs, loadLibrary, nativePick, store, toast } from "./lib/state.svelte";
  import { go } from "./lib/router.svelte";
  import Library from "./routes/Library.svelte";
  import Compare from "./routes/Compare.svelte";
  import Candidates from "./routes/Candidates.svelte";
  import NewRun from "./routes/NewRun.svelte";
  import Jobs from "./routes/Jobs.svelte";
  import Projects from "./routes/Projects.svelte";
  import Editor from "./routes/Editor.svelte";
  import FolderBrowser from "./components/FolderBrowser.svelte";
  import { api } from "./lib/api";
  import { reportError } from "./lib/state.svelte";

  onMount(() => {
    api.appInfo().then((i) => {
      store.native = i.native;
      store.version = i.version;
    }).catch(() => {});
    void loadLibrary();
    void loadJobs();
    connectEvents();
  });

  const view = $derived(route.parts[0] ?? "");
  $effect(() => {
    void route.parts.join("/");
    window.scrollTo(0, 0);
  });
  const running = $derived(activeJobs());
  const current = $derived(running.find((j) => j.state === "running") ?? null);
  const percent = $derived.by(() => {
    if (!current) return null;
    if (current.candidates) return Math.round((100 * current.candidates.done) / current.candidates.total);
    if (current.progress?.total_sec) return Math.round((100 * current.progress.processed_sec) / current.progress.total_sec);
    return null;
  });

  let switching = $state(false);
  async function startSwitch() {
    const picked = await nativePick("dir", store.library?.output_dir ?? undefined);
    if (picked === undefined) switching = true;
    else if (picked) await switchProject(picked);
  }

  // Dropping a video starts a run on it; dropping a folder opens it as the project.
  // Real paths only exist in the app window (pywebview adds pywebviewFullPath).
  function dragover(ev: DragEvent) {
    if (ev.dataTransfer?.types.includes("Files")) ev.preventDefault();
  }
  async function drop(ev: DragEvent) {
    const file = ev.dataTransfer?.files?.[0] as (File & { pywebviewFullPath?: string }) | undefined;
    if (!file) return;
    ev.preventDefault();
    const path = file.pywebviewFullPath;
    if (!path) {
      toast("Dropping files works in the app window; here, use Add file… or Browse…", "error");
      return;
    }
    try {
      if (/\.(mp4|mov|m4v|mkv|wav)$/i.test(path)) {
        if (!store.library?.output_dir) {
          toast("Open a project folder first, then drop the video again.", "error");
          return;
        }
        const s = await api.addSource(path);
        await loadLibrary();
        go(["new"], { source: s.id, kind: s.source_kind ?? "" });
      } else {
        await switchProject(path);
      }
    } catch (e) {
      reportError(e);
    }
  }
  async function switchProject(path: string) {
    switching = false;
    try {
      store.library = await api.openProject(path);
      await loadJobs();
      location.hash = "#/";
    } catch (e) {
      reportError(e);
    }
  }
</script>

<svelte:window ondragover={dragover} ondrop={drop} />

<header class="row">
  <a class="brand" href="#/">Video Audio Cleaner</a>
  <nav class="row">
    <a href="#/" class:on={view === ""}>Library</a>
    <a href={href(["new"])} class:on={view === "new"}>New run</a>
    <a href={href(["edit"])} class:on={view === "edit"}>Edit</a>
    <a href={href(["jobs"])} class:on={view === "jobs"}>Jobs{running.length ? ` (${running.length})` : ""}</a>
  </nav>
  <span class="spacer"></span>
  {#if current}
    <a class="status ellipsis" href={href(["jobs"])} title={current.title}>
      ⏳ {current.candidates ? `Tuning candidate ${current.candidates.done}/${current.candidates.total}` : current.step?.title ?? current.title}{percent !== null ? ` · ${percent}%` : ""}
    </a>
  {/if}
  {#if store.library?.output_dir}
    <button class="project ellipsis" title="Switch project folder" onclick={startSwitch}>
      📁 {store.library.output_dir.split("/").pop()}
    </button>
  {/if}
</header>

<main>
  {#if store.error}
    <div class="page"><div class="error-box">Cannot reach the server: {store.error}</div></div>
  {:else if view === "compare" && route.parts[1]}
    {#key route.parts[1]}<Compare file={route.parts[1]} />{/key}
  {:else if view === "candidates" && route.parts[2]}
    {#key route.parts[1] + route.parts[2]}<Candidates planPath={route.parts[1]} label={route.parts[2]} />{/key}
  {:else if view === "new"}
    {#key JSON.stringify(route.params)}<NewRun params={route.params} />{/key}
  {:else if view === "edit" && route.parts[1]}
    {#key route.parts[1]}<Editor name={route.parts[1]} />{/key}
  {:else if view === "edit"}
    <Projects />
  {:else if view === "jobs"}
    <Jobs />
  {:else}
    <Library />
  {/if}
</main>

<div class="toasts">
  {#each store.toasts as t (t.id)}
    <div class="toast" class:error={t.level === "error"}>{t.text}</div>
  {/each}
</div>

{#if switching}
  <FolderBrowser mode="dir" title="Open a project folder" start={store.library?.output_dir ?? "~"}
                 onpick={switchProject} onclose={() => (switching = false)} />
{/if}

<style>
  header {
    position: sticky; top: 0; z-index: 10; height: 46px; padding: 0 20px; gap: 18px;
    background: var(--bg-raised); border-bottom: 1px solid var(--border);
  }
  .brand { font-weight: 700; color: var(--text); }
  nav { gap: 4px; }
  nav a { color: var(--text-dim); padding: 4px 10px; border-radius: var(--radius); }
  nav a.on { color: var(--text); background: var(--bg-hover); }
  nav a:hover { text-decoration: none; color: var(--text); }
  .status { max-width: 420px; font-size: 12.5px; color: var(--text-dim); }
  .project { max-width: 260px; font-size: 12.5px; }
  .toasts { position: fixed; right: 16px; bottom: 16px; display: flex; flex-direction: column; gap: 8px; z-index: 100; }
  .toast {
    background: var(--bg-raised); border: 1px solid var(--border); border-left: 3px solid var(--ok);
    padding: 8px 12px; border-radius: var(--radius); max-width: 460px; box-shadow: 0 4px 16px rgb(0 0 0 / 0.3);
  }
  .toast.error { border-left-color: var(--danger); white-space: pre-wrap; }
</style>
