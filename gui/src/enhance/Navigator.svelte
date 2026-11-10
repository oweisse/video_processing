<script lang="ts">
  // The left panel of Enhance: every video, each opening into its pieces.
  import type { Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { formatDuration } from "../lib/time";
  import { pieces } from "../media/pieces";
  import Icon from "../components/Icon.svelte";
  import { allVideos, newCleanHref, pieceHref, videoHref } from "./nav";

  let { sources, current, currentPiece, newRun = false }: {
    sources: Source[];
    current: Source | null;
    currentPiece: string | null;
    newRun?: boolean;
  } = $props();

  let open = $state<Record<string, boolean>>({});
  let query = $state("");
  const shown = $derived(sources.filter((s) => !query.trim() || s.name.toLowerCase().includes(query.trim().toLowerCase())));
  const isOpen = (s: Source) => open[s.id] ?? s.id === current?.id;

  // Keep the current row in view when it changes from elsewhere (keys, the piece bar).
  let list: HTMLElement;
  $effect(() => {
    void currentPiece;
    void current?.id;
    queueMicrotask(() => list?.querySelector(".on")?.scrollIntoView({ block: "nearest" }));
  });
</script>

<div class="nav">
  <label class="search row"><Icon name="search" size={13} /><input placeholder="Filter videos" bind:value={query} /></label>
  <ul bind:this={list}>
    <li><a class="item top" class:on={!current} href={allVideos()}>All videos <span class="faint">{sources.length}</span></a></li>
    {#each shown as s (s.id)}
      {@const list = pieces(s)}
      <li>
        <div class="video" class:on={current?.id === s.id && !currentPiece && !newRun}>
          <button class="twisty" aria-label={isOpen(s) ? "Collapse" : "Expand"} onclick={() => (open[s.id] = !isOpen(s))}>
            <span class:closed={!isOpen(s)}><Icon name="chevron-down" size={12} /></span>
          </button>
          <a class="item ellipsis" href={videoHref(s)} title={s.path}>{ltr(s.name)}</a>
          <span class="faint count" title="Pieces">{list.length || ""}</span>
        </div>
        {#if isOpen(s)}
          <ul class="pieces">
            {#each list as p (p.key)}
              <li><a class="item piece" class:on={currentPiece === p.key} href={pieceHref(s, p)}
                     title={p.kind === "output" ? p.output.file : `Tuned window ${p.window.label}`}>
                <span class="dot {p.kind}"></span>
                <span class="mono">{p.label}</span>
                <span class="faint ellipsis">{p.detail}</span>
              </a></li>
            {/each}
            <li><a class="item piece add" class:on={current?.id === s.id && newRun} href={newCleanHref(s)}>
              <Icon name="plus" size={12} /> New clean…</a></li>
          </ul>
        {/if}
      </li>
    {:else}
      <li class="faint pad">No video matches.</li>
    {/each}
  </ul>
  <div class="foot faint">{current?.probe?.duration_sec ? `${ltr(current.name)} · ${formatDuration(current.probe.duration_sec)}` : ""}</div>
</div>

<style>
  .nav { display: flex; flex-direction: column; height: 100%; }
  .search { gap: 4px; padding: 6px 8px; color: var(--text-faint); border-bottom: 1px solid var(--gap); }
  .search input { flex: 1; min-width: 0; padding: 1px 6px; }
  ul { list-style: none; margin: 0; padding: 0; }
  .nav > ul { flex: 1; overflow: auto; padding: 4px; }
  .item {
    display: flex; align-items: center; gap: 6px; padding: 3px 6px; color: var(--text-dim);
    border-radius: var(--radius); min-width: 0;
  }
  .item:hover { color: var(--text); background: var(--bg-hover); text-decoration: none; }
  .item.on, .video.on { color: var(--text); background: var(--bg-hover); box-shadow: inset 2px 0 0 var(--accent); }
  .top { justify-content: space-between; font-weight: 500; margin-bottom: 2px; }
  .video { display: flex; align-items: center; border-radius: var(--radius); }
  .video .item { flex: 1; color: var(--text); font-weight: 500; }
  .video.on .item { box-shadow: none; }
  .count { padding-right: 6px; font-size: 10.5px; }
  .twisty { background: none; border: none; padding: 0 2px; min-height: 0; color: var(--text-faint); display: flex; }
  .twisty .closed { display: inline-flex; transform: rotate(-90deg); }
  .twisty span { display: inline-flex; }
  .pieces { margin: 1px 0 4px 18px; }
  .piece { font-size: 11.5px; }
  .piece .mono { color: var(--text); font-size: 11px; }
  .add { color: var(--text-faint); }
  .dot { flex: 0 0 7px; height: 7px; border-radius: 50%; }
  .dot.output { background: var(--b-color); }
  .dot.tuned { background: var(--accent); }
  .pad { padding: 8px; }
  .foot { padding: 4px 10px; font-size: 10.5px; border-top: 1px solid var(--gap); min-height: 20px; }
</style>
