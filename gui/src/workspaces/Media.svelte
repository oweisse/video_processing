<script lang="ts">
  // Media workspace (docs/gui-editor-redesign.md §5): bins | poster grid | details.
  import { api, type Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { go, href } from "../lib/router.svelte";
  import { addVideos, loadLibrary, nativePick, nativePickMany, prepareVideos, reportError, store } from "../lib/state.svelte";
  import { loadPref, savePref } from "../lib/prefs";
  import { formatBytes, formatDuration } from "../lib/time";
  import FolderBrowser from "../components/FolderBrowser.svelte";
  import Icon from "../components/Icon.svelte";
  import Panel from "../components/Panel.svelte";
  import Split from "../components/Split.svelte";
  import PosterCard from "../media/PosterCard.svelte";
  import SourceDetails from "../media/SourceDetails.svelte";
  import StitchDialog from "../media/StitchDialog.svelte";
  import AddToProject from "../media/AddToProject.svelte";
  import { addToProject, VIDEO_DRAG } from "../media/projects";
  import { select } from "../media/selection";

  // Library bins, one bin per project (its videos), and the non-video lists.
  type Bin = "all" | "clean" | "cleaned" | "exports" | "other" | `project:${string}`;

  const lib = $derived(store.library!);
  let bin = $state<Bin>(loadPref("media.bin", "all"));
  // svelte-ignore state_referenced_locally
  let picked = $state<{ ids: string[]; anchor: string | null }>((() => {
    const one = loadPref<string | null>("media.selected", null);
    return { ids: one ? [one] : [], anchor: one };
  })());
  const selectedId = $derived(picked.ids.length === 1 ? picked.ids[0] : null);
  let stitching = $state(false);
  let query = $state("");
  let cardWidth = $state(loadPref("media.cardWidth", 220));
  $effect(() => savePref("media.bin", bin));
  $effect(() => {
    if (picked.ids.length <= 1) savePref("media.selected", selectedId);
  });
  $effect(() => savePref("media.cardWidth", cardWidth));

  // Probe sources once (cached server-side); cards fill in as results arrive.
  const probing = new Set<string>();
  $effect(() => {
    for (const s of lib.sources) {
      if (!s.probe && s.exists && !probing.has(s.id)) {
        probing.add(s.id);
        api.probe(s.id).then((p) => (s.probe = p)).catch(() => {});
      }
    }
  });

  const cleaned = (s: Source) => s.outputs.some((o) => o.kind === "video" || o.kind === "audio");
  const videoBins = $derived<{ id: Bin; label: string; count: number }[]>([
    { id: "all", label: "All videos", count: lib.sources.length },
    { id: "clean", label: "To clean", count: lib.sources.filter((s) => !cleaned(s)).length },
    { id: "cleaned", label: "Cleaned", count: lib.sources.filter(cleaned).length },
  ]);
  const projectBins = $derived<{ id: Bin; label: string; count: number }[]>(
    // By name, so the list stays put while videos are dropped onto it (the library lists
    // them by last change).
    [...(lib.projects ?? [])]
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }))
      .map((p) => ({ id: `project:${p.name}` as Bin, label: p.name, count: p.videos?.length ?? 0 })),
  );
  const fileBins = $derived<{ id: Bin; label: string; count: number }[]>([
    { id: "exports", label: "Exports", count: lib.exports?.length ?? 0 },
    { id: "other", label: "Other files", count: lib.unknown.length },
  ]);
  const bins = $derived([...videoBins, ...projectBins, ...fileBins]);
  // A remembered bin of a project that is gone falls back to all videos.
  $effect(() => {
    if (!bins.some((b) => b.id === bin)) bin = "all";
  });
  $effect(() => {
    store.mediaProject = project?.name ?? null;
    return () => (store.mediaProject = null);
  });
  const project = $derived(bin.startsWith("project:") ? (lib.projects ?? []).find((p) => `project:${p.name}` === bin) ?? null : null);

  const shown = $derived.by(() => {
    const q = query.trim().toLowerCase();
    const match = (s: Source) => !q || s.name.toLowerCase().includes(q);
    if (project) return (project.videos ?? []).map((id) => lib.sources.find((s) => s.id === id)).filter((s): s is Source => !!s && match(s));
    return lib.sources.filter(
      (s) => (bin === "all" || (bin === "clean" ? !cleaned(s) : bin === "cleaned" ? cleaned(s) : true)) && match(s),
    );
  });
  const selected = $derived(lib.sources.find((s) => s.id === selectedId) ?? null);
  const many = $derived(shown.filter((s) => picked.ids.includes(s.id)));
  // Dragging videos onto a project in the left column (the selection, when the dragged
  // video is part of it), or onto "New project…".
  let dropOn = $state<string | null>(null);
  function dragVideos(ev: DragEvent, s: Source) {
    const ids = picked.ids.includes(s.id) ? picked.ids : [s.id];
    const dt = ev.dataTransfer;
    if (!dt) return;
    dt.setData(VIDEO_DRAG, JSON.stringify(ids));
    dt.effectAllowed = "copy";
    // A small label beside the pointer instead of a copy of the whole card, so the
    // project under the pointer stays visible.
    const tag = document.createElement("div");
    tag.className = "drag-tag";
    tag.textContent = ids.length === 1 ? ltr(s.name) : `${ids.length} videos`;
    document.body.appendChild(tag);
    dt.setDragImage(tag, -14, -10);
    setTimeout(() => tag.remove(), 0);
  }
  function dragOver(ev: DragEvent, target: string) {
    if (!ev.dataTransfer?.types.includes(VIDEO_DRAG)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
    dropOn = target;
  }
  async function dropVideos(ev: DragEvent, project: string | null) {
    const raw = ev.dataTransfer?.getData(VIDEO_DRAG);
    dropOn = null;
    if (!raw) return;
    ev.preventDefault();
    ev.stopPropagation();
    const ids: string[] = JSON.parse(raw);
    const sources = ids.map((id) => lib.sources.find((x) => x.id === id)).filter((x): x is Source => !!x);
    if (!sources.length) return;
    if (project) await addToProject(project, sources);
    else {
      picked = { ids: sources.map((x) => x.id), anchor: sources[0].id };
      stitching = true;
    }
  }

  const isSourceBin = $derived(bin === "all" || bin === "clean" || bin === "cleaned" || !!project);

  // Adding videos: several files from the dialog (⌘/⇧-click there), or a whole folder.
  let browsing = $state<"files" | "folder" | null>(null);
  async function addFiles() {
    const paths = await nativePickMany("file", lib.output_dir ?? undefined);
    if (paths === undefined) browsing = "files";
    else if (paths.length) await add({ paths });
  }
  async function addFolder() {
    const folder = await nativePick("dir", lib.output_dir ?? undefined);
    if (folder === undefined) browsing = "folder";
    else if (folder) await add({ folder });
  }
  async function add(sel: { paths?: string[]; folder?: string }) {
    browsing = null;
    const into = project?.name ?? null;
    const ids = await addVideos(sel);
    if (!ids.length) return;
    if (into) {
      // Shown a project: the videos go into it as well, and it stays in view.
      const sources = ids.map((id) => store.library?.sources.find((x) => x.id === id)).filter((x): x is Source => !!x);
      await addToProject(into, sources);
    } else bin = "all";
    picked = { ids, anchor: ids[0] };
  }

  function keydown(ev: KeyboardEvent) {
    if (!isSourceBin || !shown.length || stitching) return;
    const el = ev.target as HTMLElement | null;
    if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
    const current = picked.anchor ?? selectedId;
    const i = shown.findIndex((s) => s.id === current);
    if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
      ev.preventDefault();
      const next = Math.max(0, Math.min(shown.length - 1, i < 0 ? 0 : i + (ev.key === "ArrowRight" ? 1 : -1)));
      picked = select(picked, shown[next].id, shown.map((s) => s.id), { range: ev.shiftKey });
    } else if (ev.key === "a" && (ev.metaKey || ev.ctrlKey)) {
      ev.preventDefault();
      picked = { ids: shown.map((s) => s.id), anchor: shown[0]?.id ?? null };
    } else if (ev.key === "Escape" && picked.ids.length > 1) {
      picked = { ids: [], anchor: null };
    } else if (ev.key === "Enter" && selected) {
      go(["enhance", selected.id]);
    }
  }
</script>

<svelte:window onkeydown={keydown} />

<Split key="media.bins" initial={180} min={140} max={320}>
  {#snippet first()}
    <Panel title="Library">
      <ul class="bins">
        {#each videoBins as b (b.id)}
          <li><button class:on={bin === b.id} onclick={() => (bin = b.id)}>
            <span>{b.label}</span><span class="faint">{b.count}</span></button></li>
        {/each}
        <li class="group">Projects</li>
        {#each projectBins as b (b.id)}
          <li><button class:on={bin === b.id} class:drop={dropOn === b.id} onclick={() => (bin = b.id)}
                      title="This project's videos · drop videos here to add them"
                      ondragover={(e) => dragOver(e, b.id)} ondragleave={() => (dropOn = dropOn === b.id ? null : dropOn)}
                      ondrop={(e) => dropVideos(e, b.label)}>
            <span class="ellipsis">{ltr(b.label)}</span><span class="faint">{b.count}</span></button></li>
        {/each}
        <li><button class="new" class:drop={dropOn === "new"} onclick={() => (picked.ids.length ? (stitching = true) : go(["edit"]))}
                    title={picked.ids.length ? "A new project with the selected videos" : "Create a project in Edit"}
                    ondragover={(e) => dragOver(e, "new")} ondragleave={() => (dropOn = dropOn === "new" ? null : dropOn)}
                    ondrop={(e) => dropVideos(e, null)}>＋ New project…</button></li>
        <li class="group">Files</li>
        {#each fileBins as b (b.id)}
          <li><button class:on={bin === b.id} onclick={() => (bin = b.id)}>
            <span>{b.label}</span><span class="faint">{b.count}</span></button></li>
        {/each}
      </ul>
    </Panel>
  {/snippet}
  {#snippet second()}
    <Split key="media.details" initial={320} min={260} max={520} fixed="second">
      {#snippet first()}
        <Panel title={project ? `Project · ${project.name}` : bins.find((b) => b.id === bin)?.label}>
          {#snippet actions()}
            {#if project}
              <a href={href(["edit", project.name])}><button class="small primary">Open in Edit</button></a>
            {/if}
            {#if isSourceBin}
              <label class="search row" title="Filter by name">
                <Icon name="search" size={13} /><input placeholder="Filter" bind:value={query} />
              </label>
              <input type="range" min="160" max="320" step="20" bind:value={cardWidth} title="Card size" aria-label="Card size" />
              <button class="small" onclick={addFiles}
                      title={project ? `Choose video files to add to the library and to ${project.name}` : "Choose one or several video files (⌘-click or ⇧-click in the dialog)"}>
                <Icon name="plus" size={12} /> {project ? "Add videos to this project…" : "Add videos…"}</button>
              <button class="small" onclick={addFolder}
                      title={project ? `Add every video in a folder to the library and to ${project.name}` : "Add every video in a folder"}>Add a folder…</button>
            {/if}
            <button class="icon ghost" title="Rescan the folder" aria-label="Rescan" onclick={() => loadLibrary(true)}>
              <Icon name="refresh" size={14} /></button>
          {/snippet}
          {#if isSourceBin}
            <div class="grid" style="grid-template-columns: repeat(auto-fill, minmax({cardWidth}px, 1fr))">
              {#each shown as s (s.id)}
                <PosterCard source={s} selected={picked.ids.includes(s.id)}
                            onselect={(e) => (picked = select(picked, s.id, shown.map((x) => x.id), { toggle: e.metaKey || e.ctrlKey, range: e.shiftKey }))}
                            onopen={() => go(["enhance", s.id])} ondragstart={(e) => dragVideos(e, s)} />
              {:else}
                <p class="faint pad">{query ? "No video matches." : project ? "No videos in this project yet: select some in All videos and use Add to project ▾." : bin === "all" ? "No videos in this library folder yet. Add some, or drop them onto the window." : "None."}</p>
              {/each}
            </div>
          {:else}
            {@const files = bin === "exports" ? (lib.exports ?? []) : lib.unknown}
            {#if bin === "other" && files.length}
              <p class="faint pad">No <code>.params.json</code> beside these, so their source and settings are unknown.</p>
            {/if}
            <table>
              <thead><tr><th>File</th>{#if bin === "exports"}<th>Project</th>{/if}<th class="num">Size</th><th>Made</th><th></th></tr></thead>
              <tbody>
                {#each files as f (f.file)}
                  <tr class:missing={!f.exists}>
                    <td class="mono">{ltr(f.file)}</td>
                    {#if bin === "exports"}<td>{"project" in f && f.project ? ltr(f.project) : ""}</td>{/if}
                    <td class="num">{formatBytes(f.size)}</td>
                    <td class="faint">{f.created?.slice(0, 10) ?? ""}</td>
                    <td class="right"><button class="small" disabled={!f.exists} onclick={() => api.reveal(f.path).catch(reportError)}>Reveal</button></td>
                  </tr>
                {:else}
                  <tr><td colspan="5" class="faint">None.</td></tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </Panel>
      {/snippet}
      {#snippet second()}
        <Panel title="Details">
          {#if many.length > 1 && isSourceBin}
            <div class="many col">
              <h2>{many.length} videos selected</h2>
              <span class="faint">{formatDuration(many.reduce((t, s) => t + (s.probe?.duration_sec ?? 0), 0))} in all</span>
              <ul>{#each many as s (s.id)}<li class="ellipsis">{ltr(s.name)}</li>{/each}</ul>
              <button class="primary" onclick={() => (stitching = true)}>New project from these…</button>
              <AddToProject sources={many} small={false} label="Add to project" />
              {#if many.some((s) => !(s.prep?.proxy && s.prep?.thumbs))}
                <button onclick={() => prepareVideos(many)} title="Make the light editing copies, one video after another">
                  Prepare for editing ({many.filter((s) => !(s.prep?.proxy && s.prep?.thumbs)).length})</button>
              {/if}
              <span class="faint small">⌘-click adds or removes a video, ⇧-click selects a range, ⌘A all, Esc none.</span>
            </div>
          {:else if selected && isSourceBin}
            {#key selected.id}<SourceDetails source={selected} />{/key}
          {:else}
            <p class="faint pad">Select a video to see its details. Double-click to open it in Enhance.
              ⌘-click or ⇧-click selects several, to start a project with them or add them to one. You can also
              drag videos onto a project on the left.</p>
          {/if}
        </Panel>
      {/snippet}
    </Split>
  {/snippet}
</Split>

{#if stitching}
  <StitchDialog sources={many} onclose={() => (stitching = false)} />
{/if}

{#if browsing}
  {#if browsing === "files"}
    <FolderBrowser mode="file" title="Add videos" start={lib.output_dir ?? "~"} onpick={(path) => add({ paths: [path] })}
                   onpickmany={add} onclose={() => (browsing = null)} />
  {:else}
    <FolderBrowser mode="dir" title="Add every video in a folder" start={lib.output_dir ?? "~"} onpick={(folder) => add({ folder })}
                   onclose={() => (browsing = null)} />
  {/if}
{/if}

<style>
  .bins { list-style: none; margin: 0; padding: 4px; }
  .bins button {
    display: flex; justify-content: space-between; width: 100%; background: none; border-color: transparent;
    text-align: left; color: var(--text-dim);
  }
  .bins button:hover { color: var(--text); background: var(--bg-hover); }
  .bins .group { padding: 10px 8px 2px; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-faint); }
  .bins button.new { color: var(--accent); }
  :global(.drag-tag) {
    position: fixed; top: -100px; left: 0; max-width: 220px; padding: 3px 8px; border-radius: var(--radius);
    background: var(--accent); color: #fff; font: 600 12px -apple-system, system-ui, sans-serif;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; direction: ltr;
  }
  .bins button.drop { color: var(--text); background: var(--accent-dim); box-shadow: inset 0 0 0 1px var(--accent); }
  .bins button.on { color: var(--text); background: var(--bg-hover); box-shadow: inset 2px 0 0 var(--accent); }
  .many { gap: 8px; padding: 10px; }
  .many ul { margin: 0; padding-left: 18px; max-height: 40vh; overflow: auto; }
  .small { font-size: 11.5px; }
  .grid { display: grid; gap: 8px; padding: 8px; align-content: start; }
  .pad { padding: 10px; }
  .search { gap: 4px; color: var(--text-faint); }
  .search input { width: 120px; padding: 1px 6px; }
  input[type="range"] { width: 70px; }
  table td, table th { padding: 4px 10px; }
  .right { text-align: right; }
  tr.missing td { opacity: 0.5; }
</style>
