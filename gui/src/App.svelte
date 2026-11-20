<script lang="ts">
  // The shell (docs/gui-editor-redesign.md §3): top bar, one workspace filling the window,
  // and the page bar that switches workspaces and shows the job queue.
  import { onMount } from "svelte";
  import { ltr } from "./lib/bidi";
  import { links, route, shell, WORKSPACES, type Workspace } from "./lib/router.svelte";
  import { activeJobs, addVideos, connectEvents, loadJobs, loadLibrary, nativePick, reportError, store, toast } from "./lib/state.svelte";
  import { api } from "./lib/api";
  import FolderBrowser from "./components/FolderBrowser.svelte";
  import Icon, { type IconName } from "./components/Icon.svelte";
  import JobsDrawer from "./components/JobsDrawer.svelte";
  import ProjectChooser from "./components/ProjectChooser.svelte";
  import { addToProject, VIDEO_DRAG } from "./media/projects";
  import Media from "./workspaces/Media.svelte";
  import Enhance from "./workspaces/Enhance.svelte";
  import Edit from "./workspaces/Edit.svelte";
  import Deliver from "./workspaces/Deliver.svelte";

  onMount(() => {
    api.appInfo().then((i) => {
      store.native = i.native;
      store.version = i.version;
    }).catch(() => {});
    void loadLibrary();
    void loadJobs();
    connectEvents();
    // the editor's memory, live, in the corner
    const poll = () => api.memory().then((m) => (mem = m)).catch(() => (mem = null));
    poll();
    const timer = setInterval(() => { if (!document.hidden) poll(); }, 2000);
    return () => clearInterval(timer);
  });

  let mem = $state<{ used: number | null; budget: number | null; total: number | null; machine_used: number | null } | null>(null);
  const GB = 2 ** 30;
  const memText = $derived(mem?.used != null ? `${(mem.used / GB).toFixed(1)}${mem.budget ? ` / ${Math.round(mem.budget / GB)}` : ""} GB` : "");
  const memHigh = $derived(!!mem?.used && !!mem?.budget && mem.used > mem.budget * 0.85);
  const memTitle = $derived(mem?.used != null
    ? `Memory the editor uses now (the server, its jobs and their ffmpeg runs)` +
      (mem.budget ? `, against its budget of ${Math.round(mem.budget / GB)} GB: work waits or is stopped to stay under it` : "") +
      (mem.machine_used != null && mem.total ? `.\nThe whole Mac: ${Math.round(mem.machine_used * 100)} % of ${Math.round(mem.total / GB)} GB in use.` : ".")
    : "");

  const ICONS: Record<Workspace, IconName> = { media: "images", enhance: "wand-sparkles", edit: "film", deliver: "send" };

  const running = $derived(activeJobs());
  const current = $derived(running.find((j) => j.state === "running") ?? null);
  const percent = $derived.by(() => {
    if (!current) return null;
    if (current.candidates) return Math.round((100 * current.candidates.done) / current.candidates.total);
    if (current.progress?.total_sec) return Math.round((100 * current.progress.processed_sec) / current.progress.total_sec);
    return null;
  });
  const status = $derived(
    current
      ? (current.candidates ? `Tuning candidate ${current.candidates.done}/${current.candidates.total}` : current.step?.title ?? current.title) +
          (percent !== null ? ` · ${percent} %` : "")
      : running.length ? `${running.length} queued` : "No jobs running",
  );
  const timeline = $derived(
    (route.workspace === "edit" || route.workspace === "deliver") && route.parts[1] ? route.parts[1] : null,
  );

  let switching = $state(false);
  async function startSwitch() {
    const picked = await nativePick("dir", store.library?.output_dir ?? undefined);
    if (picked === undefined) switching = true;
    else if (picked) await switchProject(picked);
  }
  async function switchProject(path: string) {
    switching = false;
    try {
      store.library = await api.openProject(path);
      await loadJobs();
      location.hash = "#/media";
    } catch (e) {
      reportError(e);
    }
  }

  // Dropping videos adds them all (and shows them in Media); dropping a folder opens it as
  // the library folder. Real paths only exist in the app window (pywebview adds pywebviewFullPath).
  // Drags inside the app (videos onto a project) are not file drops.
  const internal = (ev: DragEvent) => !!ev.dataTransfer?.types.includes(VIDEO_DRAG);
  function dragover(ev: DragEvent) {
    if (ev.dataTransfer?.types.includes("Files") && !internal(ev)) ev.preventDefault();
  }
  async function drop(ev: DragEvent) {
    if (internal(ev)) return;
    const files = [...(ev.dataTransfer?.files ?? [])] as (File & { pywebviewFullPath?: string })[];
    if (!files.length) return;
    ev.preventDefault();
    const paths = files.map((f) => f.pywebviewFullPath).filter((p): p is string => !!p);
    if (!paths.length) {
      toast("Dropping files works in the app window; here, use Add videos… in Media.", "error");
      return;
    }
    const videos = paths.filter((p) => /\.(mp4|mov|m4v|mkv|avi|mts|m2ts|wav)$/i.test(p));
    try {
      if (videos.length) {
        if (!store.library?.output_dir) {
          toast("Open a library folder first, then drop the videos again.", "error");
          return;
        }
        const ids = await addVideos({ paths: videos });
        // Dropped while Media shows a project: into that project too.
        if (ids.length && store.mediaProject) {
          const sources = ids.map((id) => store.library?.sources.find((x) => x.id === id)).filter((x) => !!x);
          await addToProject(store.mediaProject, sources);
        }
        if (route.workspace !== "edit") location.hash = "#/media";
      } else {
        await switchProject(paths[0]);
      }
    } catch (e) {
      reportError(e);
    }
  }

  function typing(target: EventTarget | null) {
    const el = target as HTMLElement | null;
    return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
  }
  function keydown(ev: KeyboardEvent) {
    if (!ev.shiftKey || ev.metaKey || ev.ctrlKey || ev.altKey || typing(ev.target)) return;
    const n = ["Digit1", "Digit2", "Digit3", "Digit4"].indexOf(ev.code);
    if (n < 0) return;
    ev.preventDefault();
    location.hash = links.workspace(WORKSPACES[n].id);
  }
</script>

<svelte:window ondragover={dragover} ondrop={drop} onkeydown={keydown} />

<div class="app">
  <header class="topbar">
    {#if store.library?.output_dir}
      <button class="project" title="Switch library folder · {store.library.output_dir}" onclick={startSwitch}>
        <Icon name="folder-open" size={14} />
        <span class="ellipsis">{store.library.output_dir.split("/").pop()}</span>
        <Icon name="chevron-down" size={12} />
      </button>
    {/if}
    <span class="spacer"></span>
    {#if timeline}<span class="timeline ellipsis" title="Project">{ltr(timeline)}</span>{/if}
    <span class="spacer"></span>
    {#if !store.connected && !store.error}<span class="warn">reconnecting…</span>{/if}
  </header>

  <main class="workspace">
    {#if store.error}
      <div class="page"><div class="error-box">Cannot reach the server: {store.error}</div></div>
    {:else if !store.library}
      <div class="page dim">Loading…</div>
    {:else if !store.library.output_dir}
      <ProjectChooser />
    {:else if route.workspace === "enhance"}
      <Enhance />
    {:else if route.workspace === "edit"}
      <Edit />
    {:else if route.workspace === "deliver"}
      <Deliver />
    {:else}
      <Media />
    {/if}
  </main>

  <nav class="pagebar" aria-label="Workspaces">
    <button class="jobs" class:active={running.length > 0} title="Show the job queue" onclick={() => (shell.jobsOpen = !shell.jobsOpen)}>
      <Icon name={current ? "loader" : "list-video"} size={15} spin={!!current} />
      <span class="ellipsis">{status}</span>
      {#if running.length}<span class="badge">{running.length}</span>{/if}
    </button>
    <div class="pages">
      {#each WORKSPACES as w, i (w.id)}
        <a class="page-btn" class:on={route.workspace === w.id} href={links.workspace(w.id)}
           title="{w.label} (⇧{i + 1})" aria-current={route.workspace === w.id ? "page" : undefined}>
          <Icon name={ICONS[w.id]} size={18} />
          <span>{w.label}</span>
        </a>
      {/each}
    </div>
    <span class="version faint">
      {#if memText}<span class="mem mono" class:high={memHigh} title={memTitle}>{memText}</span> · {/if}{store.version ? `v${store.version}` : ""}</span>
  </nav>
</div>

<JobsDrawer />

<div class="toasts">
  {#each store.toasts as t (t.id)}
    <div class="toast" class:error={t.level === "error"}>
      <span>{t.text}</span>
      {#if t.action}
        {@const action = t.action}
        <button class="small" onclick={() => { action.run(); store.toasts = store.toasts.filter((x) => x.id !== t.id); }}>{action.label}</button>
      {/if}
    </div>
  {/each}
</div>

{#if switching}
  <FolderBrowser mode="dir" title="Open a library folder" start={store.library?.output_dir ?? "~"}
                 onpick={switchProject} onclose={() => (switching = false)} />
{/if}

<style>
  .app { display: flex; flex-direction: column; height: 100vh; }
  .topbar {
    flex: 0 0 var(--topbar-h); display: flex; align-items: center; gap: 10px; padding: 0 8px;
    background: var(--bg-header); border-bottom: 1px solid var(--gap);
  }
  .project {
    display: inline-flex; align-items: center; gap: 6px; max-width: 280px; min-height: 22px;
    background: none; border-color: transparent; color: var(--text-dim);
  }
  .project:hover { color: var(--text); background: var(--bg-hover); }
  .timeline { font-weight: 600; max-width: 40%; }
  .workspace { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
  .pagebar {
    flex: 0 0 var(--pagebar-h); display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
    padding: 0 8px; background: var(--bg-header); border-top: 1px solid var(--gap);
  }
  .jobs {
    justify-self: start; display: inline-flex; align-items: center; gap: 6px; max-width: 100%;
    background: none; border-color: transparent; color: var(--text-faint);
  }
  .jobs.active { color: var(--text-dim); }
  .jobs:hover { color: var(--text); background: var(--bg-hover); }
  .badge { background: var(--accent-dim); color: var(--text); border-radius: 8px; padding: 0 6px; font-size: 10.5px; }
  .pages { display: flex; gap: 4px; }
  .page-btn {
    display: flex; flex-direction: column; align-items: center; gap: 1px; width: 76px; padding: 4px 0 3px;
    color: var(--text-faint); border-radius: var(--radius); font-size: 11px;
  }
  .page-btn:hover { text-decoration: none; color: var(--text); background: var(--bg-hover); }
  .page-btn.on { color: var(--text); background: var(--bg-hover); box-shadow: inset 0 -2px 0 var(--accent); }
  .version { justify-self: end; font-size: 11px; }
  .mem.high { color: var(--warn, #e0a030); }
  .toasts {
    position: fixed; right: 12px; bottom: calc(var(--pagebar-h) + 12px); display: flex; flex-direction: column;
    gap: 8px; z-index: 100;
  }
  .toast {
    background: var(--bg-raised); border: 1px solid var(--border); border-left: 3px solid var(--ok);
    padding: 8px 12px; border-radius: var(--radius); max-width: 460px; box-shadow: 0 4px 16px rgb(0 0 0 / 0.3);
  }
  .toast.error { border-left-color: var(--danger); white-space: pre-wrap; }
  .toast { display: flex; align-items: center; gap: 10px; }
</style>
