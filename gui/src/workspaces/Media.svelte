<script lang="ts">
  // Media workspace (docs/gui-editor-redesign.md §5): bins | poster grid | details.
  import { api, type Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { go, href } from "../lib/router.svelte";
  import { loadLibrary, nativePick, reportError, store } from "../lib/state.svelte";
  import { loadPref, savePref } from "../lib/prefs";
  import { formatBytes, formatDuration } from "../lib/time";
  import FolderBrowser from "../components/FolderBrowser.svelte";
  import Icon from "../components/Icon.svelte";
  import Panel from "../components/Panel.svelte";
  import Split from "../components/Split.svelte";
  import PosterCard from "../media/PosterCard.svelte";
  import SourceDetails from "../media/SourceDetails.svelte";

  type Bin = "all" | "clean" | "cleaned" | "timelines" | "exports" | "other";

  const lib = $derived(store.library!);
  let bin = $state<Bin>(loadPref("media.bin", "all"));
  let selectedId = $state<string | null>(loadPref("media.selected", null));
  let query = $state("");
  let cardWidth = $state(loadPref("media.cardWidth", 220));
  $effect(() => savePref("media.bin", bin));
  $effect(() => savePref("media.selected", selectedId));
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
  const bins = $derived<{ id: Bin; label: string; count: number }[]>([
    { id: "all", label: "All videos", count: lib.sources.length },
    { id: "clean", label: "To clean", count: lib.sources.filter((s) => !cleaned(s)).length },
    { id: "cleaned", label: "Cleaned", count: lib.sources.filter(cleaned).length },
    { id: "timelines", label: "Timelines", count: lib.projects?.length ?? 0 },
    { id: "exports", label: "Exports", count: lib.exports?.length ?? 0 },
    { id: "other", label: "Other files", count: lib.unknown.length },
  ]);

  const shown = $derived.by(() => {
    const q = query.trim().toLowerCase();
    return lib.sources.filter(
      (s) =>
        (bin === "all" || (bin === "clean" ? !cleaned(s) : bin === "cleaned" ? cleaned(s) : true)) &&
        (!q || s.name.toLowerCase().includes(q)),
    );
  });
  const selected = $derived(lib.sources.find((s) => s.id === selectedId) ?? null);
  const isSourceBin = $derived(bin === "all" || bin === "clean" || bin === "cleaned");

  let browsing = $state(false);
  async function addVideo() {
    const picked = await nativePick("file", lib.output_dir ?? undefined);
    if (picked === undefined) browsing = true;
    else if (picked) await add(picked);
  }
  async function add(path: string) {
    browsing = false;
    try {
      const s = await api.addSource(path);
      await loadLibrary();
      selectedId = s.id;
      bin = "all";
    } catch (e) {
      reportError(e);
    }
  }

  function keydown(ev: KeyboardEvent) {
    if (!isSourceBin || !shown.length) return;
    const el = ev.target as HTMLElement | null;
    if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
    const i = shown.findIndex((s) => s.id === selectedId);
    if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
      ev.preventDefault();
      const next = Math.max(0, Math.min(shown.length - 1, i < 0 ? 0 : i + (ev.key === "ArrowRight" ? 1 : -1)));
      selectedId = shown[next].id;
    } else if (ev.key === "Enter" && selected) {
      go(["enhance", selected.id]);
    }
  }
</script>

<svelte:window onkeydown={keydown} />

<Split key="media.bins" initial={180} min={140} max={320}>
  {#snippet first()}
    <Panel title="Bins">
      <ul class="bins">
        {#each bins as b (b.id)}
          <li><button class:on={bin === b.id} onclick={() => (bin = b.id)}>
            <span>{b.label}</span><span class="faint">{b.count}</span></button></li>
        {/each}
      </ul>
    </Panel>
  {/snippet}
  {#snippet second()}
    <Split key="media.details" initial={320} min={260} max={520} fixed="second">
      {#snippet first()}
        <Panel title={bins.find((b) => b.id === bin)?.label}>
          {#snippet actions()}
            {#if isSourceBin}
              <label class="search row" title="Filter by name">
                <Icon name="search" size={13} /><input placeholder="Filter" bind:value={query} />
              </label>
              <input type="range" min="160" max="320" step="20" bind:value={cardWidth} title="Card size" aria-label="Card size" />
              <button class="small" onclick={addVideo}><Icon name="plus" size={12} /> Add video…</button>
            {/if}
            <button class="icon ghost" title="Rescan the folder" aria-label="Rescan" onclick={() => loadLibrary(true)}>
              <Icon name="refresh" size={14} /></button>
          {/snippet}
          {#if isSourceBin}
            <div class="grid" style="grid-template-columns: repeat(auto-fill, minmax({cardWidth}px, 1fr))">
              {#each shown as s (s.id)}
                <PosterCard source={s} selected={s.id === selectedId} onselect={() => (selectedId = s.id)}
                            onopen={() => go(["enhance", s.id])} />
              {:else}
                <p class="faint pad">{query ? "No video matches." : bin === "all" ? "No videos in this project yet. Add one, or drop it onto the window." : "None."}</p>
              {/each}
            </div>
          {:else if bin === "timelines"}
            <table>
              <thead><tr><th>Timeline</th><th class="num">Clips</th><th class="num">Length</th><th>Changed</th><th></th></tr></thead>
              <tbody>
                {#each lib.projects ?? [] as p (p.path)}
                  <tr>
                    <td><a href={href(["edit", p.name])}>{ltr(p.name)}</a></td>
                    <td class="num">{p.clips}</td>
                    <td class="num">{formatDuration(p.duration_sec)}</td>
                    <td class="faint">{p.modified?.slice(0, 16).replace("T", " ") ?? ""}</td>
                    <td class="right"><a href={href(["edit", p.name])}><button class="small">Open in Edit</button></a></td>
                  </tr>
                {:else}
                  <tr><td colspan="5" class="faint">No timelines yet. Create one in <a href="#/edit">Edit</a>.</td></tr>
                {/each}
              </tbody>
            </table>
          {:else}
            {@const files = bin === "exports" ? (lib.exports ?? []) : lib.unknown}
            {#if bin === "other" && files.length}
              <p class="faint pad">No <code>.params.json</code> beside these, so their source and settings are unknown.</p>
            {/if}
            <table>
              <thead><tr><th>File</th>{#if bin === "exports"}<th>Timeline</th>{/if}<th class="num">Size</th><th>Made</th><th></th></tr></thead>
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
          {#if selected && isSourceBin}
            {#key selected.id}<SourceDetails source={selected} />{/key}
          {:else}
            <p class="faint pad">Select a video to see its details. Double-click to open it in Enhance.</p>
          {/if}
        </Panel>
      {/snippet}
    </Split>
  {/snippet}
</Split>

{#if browsing}
  <FolderBrowser mode="file" title="Add a video" start={lib.output_dir ?? "~"} onpick={add} onclose={() => (browsing = false)} />
{/if}

<style>
  .bins { list-style: none; margin: 0; padding: 4px; }
  .bins button {
    display: flex; justify-content: space-between; width: 100%; background: none; border-color: transparent;
    text-align: left; color: var(--text-dim);
  }
  .bins button:hover { color: var(--text); background: var(--bg-hover); }
  .bins button.on { color: var(--text); background: var(--bg-hover); box-shadow: inset 2px 0 0 var(--accent); }
  .grid { display: grid; gap: 8px; padding: 8px; align-content: start; }
  .pad { padding: 10px; }
  .search { gap: 4px; color: var(--text-faint); }
  .search input { width: 120px; padding: 1px 6px; }
  input[type="range"] { width: 70px; }
  table td, table th { padding: 4px 10px; }
  .right { text-align: right; }
  tr.missing td { opacity: 0.5; }
</style>
