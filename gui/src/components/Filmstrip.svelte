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
  const length = $derived(source.probe?.duration_sec ?? 0);
  const v0 = $derived(start);
  const v1 = $derived(end ?? length);
  const span = $derived(Math.max(0, v1 - v0));
  const aspect = $derived(source.probe?.video ? source.probe.video.width / source.probe.video.height : 16 / 9);
  // Frames sit on a fixed grid of times (a "nice" step chosen from the zoom, no wider than a frame), so panning
  // reuses frames already fetched and only asks for the new ones at the edge; a resize or
  // a small zoom change mostly keeps the same step. Each frame shows the middle of its cell.
  const NICE = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];
  const cells = $derived.by(() => {
    if (!width || !span) return [];
    const fit = Math.max(1, Math.ceil(width / (height * aspect)));
    // A cell is never wider than a frame of the picture's shape (a wider one would crop the
    // frame's top and bottom); at most it trims a little off the sides.
    const ideal = span / fit;
    const step = [...NICE].reverse().find((s) => s <= ideal) ?? ideal;
    const first = Math.floor(v0 / step), last = Math.ceil(v1 / step);
    const out = [];
    for (let k = first; k < last && out.length < 400; k++) {
      const a = k * step;
      const t = Math.min(Math.max(0, length - 0.2), a + step / 2);
      out.push({ k, t: Math.round(t * 10) / 10, left: ((a - v0) / span) * 100, w: (step / span) * 100 });
    }
    return out;
  });
</script>

<div class="strip" style="height:{height}px" bind:clientWidth={width}>
  {#if source.probe?.video}
    {#each cells as c (c.k)}
      <img src={media.frame(source.id, c.t)} alt="" loading="lazy" draggable="false" style="left:{c.left}%;width:{c.w}%" />
    {/each}
  {/if}
</div>

<style>
  .strip { position: relative; overflow: hidden; background: #000; border-radius: 2px; }
  img { position: absolute; top: 0; height: 100%; object-fit: cover; display: block; }
</style>
