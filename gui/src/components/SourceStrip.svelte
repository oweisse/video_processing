<script lang="ts">
  // A few frames from across a source video, so it is recognisable at a glance.
  import type { Source } from "../lib/api";
  import { formatClock } from "../lib/time";

  let { source, height = 84 }: { source: Source; height?: number } = $props();

  const FRAMES = 4; // media.POSTER_FRAMES on the server
  const duration = $derived(source.probe?.duration_sec ?? null);
  const at = (i: number) => (duration === null ? null : (duration * (i + 0.5)) / FRAMES);
  let failed = $state(false);
</script>

{#if source.exists && !failed && source.probe?.video !== null}
  <div class="strip" style="height:{height}px">
    {#each Array.from({ length: FRAMES }, (_, i) => i) as i}
      <img src="/media/poster?source={encodeURIComponent(source.id)}&i={i}" alt="" loading="lazy"
           title={at(i) === null ? "" : formatClock(at(i)!, false)} onerror={() => (failed = true)} />
    {/each}
  </div>
{/if}

<style>
  .strip { display: flex; gap: 4px; }
  img {
    height: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 4px;
    background: var(--bg-sunken); border: 1px solid var(--border);
  }
</style>
