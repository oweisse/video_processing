<script lang="ts">
  // "All videos" in Enhance: each video with all its pieces, one click from any of them.
  import type { Source } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { formatDuration } from "../lib/time";
  import { pieces } from "../media/pieces";
  import { newCleanHref, pieceHref, videoHref } from "./nav";

  let { sources }: { sources: Source[] } = $props();
</script>

<div class="overview">
  {#each sources as s (s.id)}
    {@const list = pieces(s)}
    <div class="video">
      <a class="poster" href={videoHref(s)} title="Open {s.name}">
        {#if s.exists && s.probe?.video}<img src="/media/poster?source={encodeURIComponent(s.id)}&i=1" alt="" loading="lazy" />{/if}
      </a>
      <div class="col body">
        <div class="row">
          <a class="name ellipsis" href={videoHref(s)}>{ltr(s.name)}</a>
          <span class="faint">{formatDuration(s.probe?.duration_sec)}</span>
          {#if !s.exists}<span class="chip failed">missing</span>{/if}
          <span class="spacer"></span>
          <a href={newCleanHref(s)}><button class="small" disabled={!s.exists}>New clean…</button></a>
        </div>
        <div class="chips">
          {#each list as p (p.key)}
            <a class="piece {p.kind}" href={pieceHref(s, p)} title={p.kind === "output" ? p.output.file : `Tuned window, ${p.detail}`}>
              <span class="mono">{p.label}</span> <span class="faint">{p.detail}</span></a>
          {:else}
            <span class="faint">Not cleaned yet.</span>
          {/each}
        </div>
      </div>
    </div>
  {:else}
    <p class="faint">No videos in this project yet. Add one in Media.</p>
  {/each}
</div>

<style>
  .overview { display: flex; flex-direction: column; gap: 6px; padding: 10px; }
  .video { display: flex; gap: 10px; padding: 6px; background: var(--bg-raised); border-radius: var(--radius); }
  .poster { flex: 0 0 128px; aspect-ratio: 16 / 9; background: #000; border-radius: 2px; overflow: hidden; }
  .poster img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .body { flex: 1; min-width: 0; gap: 6px; }
  .name { font-weight: 600; color: var(--text); font-size: 13px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .piece {
    display: inline-flex; gap: 6px; align-items: center; padding: 2px 8px; border-radius: var(--radius);
    background: var(--bg-sunken); color: var(--text); border-left: 3px solid var(--b-color); font-size: 11px;
  }
  .piece.tuned { border-left-color: var(--accent); }
  .piece:hover { background: var(--bg-hover); text-decoration: none; }
</style>
