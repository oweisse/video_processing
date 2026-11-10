<script lang="ts">
  // Which parts of a source have outputs: one block per output window, stacked when they overlap.
  import type { Output } from "../lib/api";
  import { href } from "../lib/router.svelte";
  import { formatClock } from "../lib/time";

  let { duration, outputs }: { duration: number | null; outputs: Output[] } = $props();

  const total = $derived(
    duration ??
      Math.max(1, ...outputs.map((o) => (o.window?.actual_start_sec ?? 0) + (o.window?.actual_duration_sec ?? 0))),
  );

  const blocks = $derived.by(() => {
    const rows: number[] = []; // end time per row
    return outputs
      .filter((o) => o.window && o.window.actual_duration_sec)
      .map((o) => {
        const start = o.window!.actual_start_sec;
        const end = start + (o.window!.actual_duration_sec ?? 0);
        let row = rows.findIndex((e) => e <= start + 0.5);
        if (row < 0) row = rows.push(end) - 1;
        else rows[row] = end;
        return { o, start, end, row };
      });
  });
  const rowCount = $derived(Math.max(1, ...blocks.map((b) => b.row + 1)));
</script>

<div class="bar" style="height:{rowCount * 12 + 6}px">
  {#each blocks as { o, start, end, row } (o.file)}
    <a
      class="block"
      class:tagged={!!o.tag}
      href={href(["compare", o.file])}
      style="left:{(start / total) * 100}%;width:{Math.max(0.3, ((end - start) / total) * 100)}%;top:{row * 12 + 3}px"
      title="{o.file}  ({formatClock(start, false)}–{formatClock(end, false)})"
    ></a>
  {/each}
</div>

<style>
  .bar { position: relative; background: var(--bg-sunken); border-radius: 4px; border: 1px solid var(--border); }
  .block {
    position: absolute; height: 9px; border-radius: 2px;
    background: var(--b-color); opacity: 0.8;
  }
  .block.tagged { background: var(--accent); }
  .block:hover { opacity: 1; outline: 1px solid var(--text); }
</style>
