<script lang="ts">
  // The tuning trade-off of a window: warble (x) against hiss removed (y).
  import type { CandidateMeasure } from "../lib/api";

  let {
    candidates,
    selected,
    picked,
    onselect,
  }: {
    candidates: CandidateMeasure[];
    selected: number | null;
    picked: number | null;
    onselect: (i: number) => void;
  } = $props();

  const W = 360, H = 240, PAD = 34;
  const pts = $derived(
    candidates.map((c, i) => ({ i, x: c.warble ?? 0, y: c.hiss_gain_db ?? 0, c })),
  );
  const xs = $derived(pts.map((p) => p.x));
  const ys = $derived(pts.map((p) => p.y));
  const x0 = $derived(Math.min(...xs) - 0.3), x1 = $derived(Math.max(...xs) + 0.3);
  const y0 = $derived(Math.min(0, ...ys) - 0.5), y1 = $derived(Math.max(...ys) + 0.5);
  const sx = (v: number) => PAD + ((v - x0) / (x1 - x0 || 1)) * (W - PAD - 8);
  const sy = (v: number) => H - PAD - ((v - y0) / (y1 - y0 || 1)) * (H - PAD - 8);
  const attens = $derived([...new Set(candidates.map((c) => String(c.atten)))]);
  const hue = (c: CandidateMeasure) => (attens.indexOf(String(c.atten)) * 360) / Math.max(1, attens.length) + 30;
</script>

<svg viewBox="0 0 {W} {H}" width={W} height={H} role="img" aria-label="Candidates: warble against hiss removed">
  <line x1={PAD} y1={H - PAD} x2={W - 8} y2={H - PAD} class="axis" />
  <line x1={PAD} y1={8} x2={PAD} y2={H - PAD} class="axis" />
  <text x={(W + PAD) / 2} y={H - 8} class="label" text-anchor="middle">warble → (calmer left)</text>
  <text x={10} y={(H - PAD) / 2} class="label" text-anchor="middle" transform="rotate(-90 10 {(H - PAD) / 2})">hiss removed (dB) →</text>
  <text x={PAD} y={H - PAD + 13} class="tick">{x0.toFixed(1)}</text>
  <text x={W - 8} y={H - PAD + 13} class="tick" text-anchor="end">{x1.toFixed(1)}</text>
  <text x={PAD - 4} y={H - PAD} class="tick" text-anchor="end">{y0.toFixed(0)}</text>
  <text x={PAD - 4} y={14} class="tick" text-anchor="end">{y1.toFixed(0)}</text>
  {#each pts as p (p.i)}
    <circle
      cx={sx(p.x)}
      cy={sy(p.y)}
      r={3 + Math.min(6, (p.c.clicks ?? 0) / 6)}
      fill={p.c.qualifies ? `hsl(${hue(p.c)} 70% 60%)` : "none"}
      stroke={`hsl(${hue(p.c)} 70% 60%)`}
      stroke-width={p.i === selected ? 3 : 1.3}
      class:picked={p.i === picked}
      onclick={() => onselect(p.i)}
      role="button"
      tabindex="-1"
      onkeydown={() => {}}
    >
      <title>#{p.i}: warble {p.x.toFixed(2)}, hiss {p.y.toFixed(1)} dB, clicks {(p.c.clicks ?? 0).toFixed(1)}/s</title>
    </circle>
  {/each}
</svg>
<div class="legend faint">
  {#each attens as a, k}<span style="color:hsl({(k * 360) / Math.max(1, attens.length) + 30} 70% 60%)">● atten {a === "null" ? "none" : a}</span>{/each}
  · filled = qualifies · size = clicks · ring = the pick
</div>

<style>
  svg { background: var(--bg-sunken); border-radius: var(--radius); border: 1px solid var(--border); }
  .axis { stroke: var(--border); }
  .label { fill: var(--text-dim); font-size: 11px; }
  .tick { fill: var(--text-faint); font-size: 10px; }
  circle { cursor: pointer; }
  circle.picked { stroke: var(--text); stroke-width: 2.5; }
  .legend { font-size: 12px; display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
</style>
