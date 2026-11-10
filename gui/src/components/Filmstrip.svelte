<script lang="ts">
  // Frames along a source (all of it, or `start`–`end`), as many as fit the width, so marks
  // drawn over it line up with what is in the picture at that moment.
  import { media, type Source } from "../lib/api";

  let { source, height = 34, start = 0, end = null }: {
    source: Pick<Source, "id" | "probe">;
    height?: number;
    start?: number;
    end?: number | null;
  } = $props();

  let width = $state(0);
  const duration = $derived(Math.max(0, (end ?? source.probe?.duration_sec ?? 0) - start));
  const aspect = $derived(source.probe?.video ? source.probe.video.width / source.probe.video.height : 16 / 9);
  // Rounded (whole seconds over long spans), so a resize mostly reuses frames already fetched.
  const times = $derived.by(() => {
    if (!width || !duration) return [];
    const n = Math.max(1, Math.ceil(width / (height * aspect)));
    const unit = duration / n >= 2 ? 1 : 0.1;
    return Array.from({ length: n }, (_, i) => Math.round((start + ((i + 0.5) * duration) / n) / unit) * unit);
  });
</script>

<div class="strip" style="height:{height}px" bind:clientWidth={width}>
  {#if source.probe?.video}
    {#each times as t, i (i)}
      <img src={media.frame(source.id, t)} alt="" loading="lazy" draggable="false" style="width:{100 / times.length}%" />
    {/each}
  {/if}
</div>

<style>
  .strip { display: flex; overflow: hidden; background: #000; border-radius: 2px; }
  img { height: 100%; object-fit: cover; display: block; min-width: 0; }
</style>
