<script lang="ts">
  // Enhance workspace (docs/gui-editor-redesign.md §6): all videos › a video › one of its
  // pieces (a cleaned output or a tuned window). The left panel lists every video and its
  // pieces; the bar at the bottom shows the whole current video with all its pieces.
  import { route, shell } from "../lib/router.svelte";
  import { ltr } from "../lib/bidi";
  import { findOutput, findPlan, sourceById, store } from "../lib/state.svelte";
  import { neighbour, outputKey, pieces, tunedKey } from "../media/pieces";
  import Icon from "../components/Icon.svelte";
  import Panel from "../components/Panel.svelte";
  import Split from "../components/Split.svelte";
  import SourceDetails from "../media/SourceDetails.svelte";
  import Navigator from "../enhance/Navigator.svelte";
  import Overview from "../enhance/Overview.svelte";
  import PieceBar from "../enhance/PieceBar.svelte";
  import SourcePlayer from "../enhance/SourcePlayer.svelte";
  import { allVideos, pieceHref, videoHref } from "../enhance/nav";
  import Compare from "../routes/Compare.svelte";
  import Candidates from "../routes/Candidates.svelte";
  import NewRun from "../routes/NewRun.svelte";

  const p = $derived(route.params);
  const sources = $derived(store.library?.sources ?? []);
  // Links from 0.5.0 pages name only the output or plan; find the video from it.
  const source = $derived(
    sourceById(route.parts[1]) ??
      (p.out ? findOutput(p.out)?.source : null) ??
      (p.plan ? findPlan(p.plan)?.source : null) ??
      null,
  );
  const view = $derived<"all" | "video" | "output" | "tuned" | "new">(
    p.tab === "new" ? "new" : p.out ? "output" : p.plan && p.win ? "tuned" : source ? "video" : "all",
  );
  const pieceKey = $derived(view === "output" ? outputKey(p.out) : view === "tuned" ? tunedKey(p.plan, p.win) : null);
  const list = $derived(source ? pieces(source) : []);
  const piece = $derived(list.find((x) => x.key === pieceKey) ?? null);
  const index = $derived(piece ? list.indexOf(piece) : -1);
  const key = $derived(route.parts.join("/") + "?" + new URLSearchParams(p).toString());

  // The video view's player, shared with the bar at the bottom.
  let player = $state<SourcePlayer | null>(null);
  let playerTime = $state(0);
  let selection = $state<{ start: number; end: number } | null>(null);
  $effect(() => {
    void source?.id;
    selection = null;
  });

  function step(dir: -1 | 1) {
    if (!source) return;
    const next = neighbour(list, pieceKey, dir);
    if (next) location.hash = pieceHref(source, next);
  }
  function stepVideo(dir: -1 | 1) {
    if (!sources.length) return;
    const i = source ? sources.findIndex((s) => s.id === source.id) : -1;
    const next = sources[i < 0 ? (dir > 0 ? 0 : sources.length - 1) : i + dir];
    if (next) location.hash = videoHref(next);
  }

  function keydown(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    if (shell.jobsOpen || ev.metaKey || ev.ctrlKey || ev.altKey || (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
    if (ev.key === "[") step(-1);
    else if (ev.key === "]") step(1);
    else if (ev.key === "{") stepVideo(-1);
    else if (ev.key === "}") stepVideo(1);
    else if (ev.key === "Escape" && view !== "all") location.hash = view === "video" || !source ? allVideos() : videoHref(source);
    else return;
    ev.preventDefault();
  }
</script>

<svelte:window onkeydown={keydown} />

<Split key="enhance.nav" initial={260} min={200} max={420}>
  {#snippet first()}
    <Panel title="Videos" scroll={false}>
      <Navigator {sources} current={source} currentPiece={pieceKey} newRun={view === "new"} />
    </Panel>
  {/snippet}
  {#snippet second()}
    <div class="main">
      <header class="crumbs">
        <a href={allVideos()} class:here={view === "all"} title="All videos (Esc from a video)">All videos</a>
        {#if source}
          <span class="sep">›</span>
          <a href={videoHref(source)} class="ellipsis" class:here={view === "video"} title="This video and all its pieces (Esc from a piece)">{ltr(source.name)}</a>
        {/if}
        {#if piece}
          <span class="sep">›</span>
          <span class="here mono">{piece.label}</span>
          <span class="faint">{piece.kind === "output" ? piece.detail : `tuned · ${piece.detail}`}</span>
        {:else if view === "new"}
          <span class="sep">›</span><span class="here">New clean</span>
        {/if}
        <span class="spacer"></span>
        {#if source && list.length}
          <span class="faint">{index >= 0 ? `${index + 1} of ${list.length}` : `${list.length} pieces`}</span>
          <button class="icon ghost" title="Previous piece ([)" aria-label="Previous piece" disabled={index === 0} onclick={() => step(-1)}>‹</button>
          <button class="icon ghost" title="Next piece (])" aria-label="Next piece" disabled={index === list.length - 1} onclick={() => step(1)}>›</button>
        {/if}
      </header>

      <div class="content scroll">
        {#key key}
          {#if view === "output"}
            <Compare file={p.out} />
          {:else if view === "tuned"}
            <Candidates planPath={p.plan} label={p.win} />
          {:else if view === "new"}
            <NewRun params={{ ...p, source: source?.id ?? p.source ?? "" }} />
          {:else if view === "video" && source}
            <div class="fill"><Split key="enhance.videoDetails" initial={340} min={260} max={520} fixed="second">
              {#snippet first()}
                <SourcePlayer bind:this={player} {source} start={p.t ? Number(p.t) : null}
                              bind:time={playerTime} bind:selection />
              {/snippet}
              {#snippet second()}
                <Panel title="Details"><SourceDetails {source} /></Panel>
              {/snippet}
            </Split></div>
          {:else}
            <Overview {sources} />
          {/if}
        {/key}
      </div>

      {#if source}
        {#if view === "video"}
          <PieceBar {source} time={playerTime} {selection} onseek={(t) => player?.seek(t)}
                    onrange={(a, b) => (selection = { start: a, end: b })} />
        {:else}
          <PieceBar {source} current={pieceKey} />
        {/if}
      {:else}
        <div class="hint faint"><Icon name="images" size={13} /> Pick a video to see all its pieces along its length.
          Keys: <kbd>[</kbd> <kbd>]</kbd> previous/next piece · <kbd>{"{"}</kbd> <kbd>{"}"}</kbd> previous/next video · <kbd>Esc</kbd> up a level</div>
      {/if}
    </div>
  {/snippet}
</Split>

<style>
  .main { display: flex; flex-direction: column; flex: 1; min-height: 0; background: var(--bg); }
  .crumbs {
    flex: 0 0 32px; display: flex; align-items: center; gap: 6px; padding: 0 8px 0 12px;
    background: var(--bg-header); border-bottom: 1px solid var(--gap); font-size: 12.5px; min-width: 0;
  }
  .crumbs a { color: var(--text-dim); max-width: 40%; }
  .crumbs a:hover { color: var(--text); }
  .crumbs .here { color: var(--text); font-weight: 600; }
  .sep { color: var(--text-faint); }
  .crumbs button.icon { font-size: 18px; line-height: 1; }
  .content { flex: 1; display: flex; flex-direction: column; }
  .fill { flex: 1 1 0; min-height: 0; display: flex; }
  .hint { padding: 8px 12px; border-top: 1px solid var(--gap); background: var(--bg-raised); display: flex; gap: 6px; align-items: center; }
</style>
