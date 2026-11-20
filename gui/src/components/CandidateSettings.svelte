<script lang="ts">
  // Every setting one tuned candidate stands for, grouped by where it came from: what this
  // candidate chose, what the tuning run fixed, and the defaults. It follows the candidate
  // being looked at.
  import { api, type CandidateSettings } from "../lib/api";
  import { toast } from "../lib/state.svelte";

  let { planPath, window: label, index }: { planPath: string; window: string; index: number } = $props();

  let data = $state<CandidateSettings | null>(null);
  let error = $state<string | null>(null);
  let showUnused = $state(false);
  let showCommand = $state(false);

  $effect(() => {
    const [p, w, i] = [planPath, label, index];
    error = null;
    api.candidateSettings(p, w, i)
      .then((d) => { if (p === planPath && w === label && i === index) data = d; })
      .catch((e) => (error = String(e?.message ?? e)));
  });

  const GROUPS = [
    { origin: "candidate", title: "Chosen by this candidate", note: "what differs between the candidates" },
    { origin: "tuning", title: "Set by the tuning run", note: "the same for every candidate of this window" },
    { origin: "default", title: "Everything else", note: "clean-audio's defaults" },
  ] as const;

  const visible = $derived((data?.rows ?? []).filter((r) => showUnused || r.applies));
  const hiddenCount = $derived((data?.rows ?? []).filter((r) => !r.applies).length);
  const musicOff = $derived(data?.rows.find((r) => r.name === "separate_music")?.value === false);

  function copy(text: string) {
    navigator.clipboard.writeText(text).then(() => toast("Copied"), () => toast("Could not copy"));
  }
</script>

<div class="card col settings">
  <div class="row head">
    <h3>Settings of candidate {index}</h3>
    {#if data}<span class="dim small">{data.describe}</span>{/if}
    <span class="spacer"></span>
    {#if hiddenCount}
      <label class="row small"><input type="checkbox" bind:checked={showUnused} /> show the {hiddenCount} that do nothing here</label>
    {/if}
  </div>
  {#if error}
    <p class="warn small">{error}</p>
  {:else if !data}
    <p class="faint small">Loading…</p>
  {:else}
    {#if musicOff}
      <p class="note small">Music separation is off, so the whole sound (voices and music) goes through the
        speech denoiser, which treats music as noise and turns it down, by up to the noise reduction limit
        ({data.rows.find((r) => r.name === "atten_lim_db")?.shown}). To keep music, tune with music separation on:
        the voices are cleaned apart and the music is mixed back at a level you choose.</p>
    {/if}
    <div class="groups">
      {#each GROUPS as g (g.origin)}
        {@const rows = visible.filter((r) => r.origin === g.origin)}
        {#if rows.length}
          <section class="group {g.origin}">
            <div class="gtitle">{g.title} <span class="faint">· {g.note}</span></div>
            <dl>
              {#each rows as r (r.name)}
                <dt class:unused={!r.applies} title={r.help}>{r.label}{#if r.advanced}<span class="faint"> ·adv</span>{/if}</dt>
                <dd class:unused={!r.applies} title={`--${r.name}${r.applies ? "" : " (does nothing with the settings above)"}`}>{r.shown}</dd>
              {/each}
            </dl>
          </section>
        {/if}
      {/each}
    </div>
    <div class="row small">
      <button class="link small" onclick={() => (showCommand = !showCommand)}>{showCommand ? "Hide" : "Show"} the command</button>
    </div>
    {#if showCommand}
      <div class="col cmds">
        <div class="row"><span class="faint small grow">Render this candidate (what “Render this one” runs):</span>
          <button class="small" onclick={() => copy(data!.render_command)}>Copy</button></div>
        <code>{data.render_command}</code>
        {#if data.explicit_command}
          <div class="row"><span class="faint small grow">The same with every setting spelled out:</span>
            <button class="small" onclick={() => copy(data!.explicit_command!)}>Copy</button></div>
          <code>{data.explicit_command}</code>
        {/if}
      </div>
    {/if}
  {/if}
</div>

<style>
  .settings { gap: 10px; }
  .head { gap: 10px; flex-wrap: wrap; }
  .groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; }
  .group { background: var(--bg-sunken); border-radius: var(--radius); padding: 8px 10px; border-left: 3px solid var(--border); }
  .group.candidate { border-left-color: var(--b-color); }
  .group.tuning { border-left-color: var(--accent); }
  .gtitle { font-size: 12.5px; font-weight: 600; margin-bottom: 6px; }
  .gtitle .faint { font-weight: 400; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 14px; margin: 0; font-size: 12.5px; }
  dt { color: var(--text-dim); cursor: help; }
  dd { margin: 0; font-variant-numeric: tabular-nums; }
  .unused { opacity: 0.45; }
  .note { margin: 0; padding: 6px 10px; border-radius: var(--radius); background: var(--bg-sunken); border-left: 3px solid var(--warn); }
  .cmds { gap: 4px; }
  code { display: block; white-space: pre-wrap; word-break: break-all; font-size: 11.5px; padding: 6px 8px; background: var(--bg-sunken); border-radius: var(--radius); user-select: all; }
  .small { font-size: 12px; }
  .grow { flex: 1; }
</style>
