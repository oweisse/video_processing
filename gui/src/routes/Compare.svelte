<script lang="ts">
  // Original vs cleaned, one picture, instant gapless switching (docs/gui-design.md §6.2).
  import { onDestroy } from "svelte";
  import { ltr } from "../lib/bidi";
  import { api, media, type Output } from "../lib/api";
  import { ABPlayer } from "../lib/abplayer";
  import { audioContext, decode, type Decoded } from "../lib/audio";
  import { links } from "../lib/router.svelte";
  import { findOutput, reportError, store } from "../lib/state.svelte";
  import { formatClock } from "../lib/time";
  import { followClock } from "../lib/videosync";
  import { remember, resumeFor } from "../lib/playhead";
  import { SECTION_SEC, SECTIONED_AFTER, sectionFor } from "../lib/sections";
  import ABDeck from "../components/ABDeck.svelte";
  import Filmstrip from "../components/Filmstrip.svelte";

  let { file }: { file: string } = $props();

  const found = $derived(store.library ? findOutput(file) : null);
  const output = $derived(found?.output ?? null);
  const source = $derived(found?.source ?? null);

  // Other outputs of the same window (e.g. _no-music vs _v2) can stand in as B.
  const alternatives = $derived(
    (source?.outputs ?? []).filter(
      (o) =>
        o.exists && o.kind !== "compare" &&
        Math.abs((o.window?.actual_start_sec ?? -1) - (output?.window?.actual_start_sec ?? -2)) < 0.05,
    ),
  );
  let bFile = $state<string | null>(null);
  $effect(() => {
    if (output && bFile === null) bFile = output.file;
  });
  const bOutput = $derived(alternatives.find((o) => o.file === bFile) ?? output);

  const player = new ABPlayer(audioContext());
  onDestroy(() => player.dispose());
  let a = $state<Decoded | null>(null);
  let b = $state<Decoded | null>(null);
  let error = $state<string | null>(null);
  let levelMatch = $state(true);
  let video = $state<HTMLVideoElement | null>(null);

  // Long outputs are compared a section at a time; the section follows where the
  // video was last heard (lib/playhead.ts), else starts at the beginning.
  const windowStart = $derived(output?.window?.actual_start_sec ?? 0);
  const windowLength = $derived(output?.window?.actual_duration_sec ?? output?.results?.duration_sec ?? 0);
  const sectioned = $derived(windowLength > SECTIONED_AFTER);
  let sectionStart = $state<number | null>(null);
  $effect(() => {
    if (!output || !source || sectionStart !== null) return;
    const r = sectioned ? resumeFor(source.id, windowStart, windowLength) : null;
    sectionStart = r ? sectionFor(r.t, windowLength) : 0;
  });
  const section = $derived(sectioned ? sectionStart ?? 0 : 0);

  /** Move to the section around output time t, and start there. */
  function goTo(t: number) {
    if (!source) return;
    t = Math.max(0, Math.min(windowLength - 0.1, t));
    remember(source.id, windowStart + t, player.active, player.playing);
    sectionStart = sectionFor(t, windowLength);
  }
  function step(dir: -1 | 1) {
    goTo(section + dir * SECTION_SEC + 60);
  }

  let loadedFor = "";
  $effect(() => {
    if (!output || !bOutput || (sectioned && sectionStart === null)) return;
    const key = `${output.file}|${bOutput.file}|${section}`;
    if (key === loadedFor) return;
    const first = !loadedFor.startsWith(output.file + "|") || !loadedFor.endsWith(`|${section}`);
    loadedFor = key;
    error = null;
    if (first) {
      a = null;
      b = null;
    }
    const [urlA, urlB] = sectioned
      ? [media.originalFor(output.file, section, SECTION_SEC), media.outputAudio(bOutput.file, section, SECTION_SEC)]
      : [media.originalFor(output.file), media.outputAudio(bOutput.file)];
    Promise.all([decode(urlA), decode(urlB)])
      .then(([da, db]) => {
        if (loadedFor !== key) return;
        if (first || !a) player.setBuffers(da.buffer, db.buffer);
        else player.replaceB(db.buffer);
        a = da;
        b = db;
      })
      .catch((e) => (error = e instanceof Error ? e.message : String(e)));
  });

  const ctx = audioContext();
  const onframe = (clock: number, playing: boolean) => followClock(video, ctx, clock, playing, section);

  function sectionKeys(ev: KeyboardEvent) {
    const el = ev.target as HTMLElement | null;
    if (!sectioned || (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
    if (ev.key === "PageUp") step(-1);
    else if (ev.key === "PageDown") step(1);
    else return;
    ev.preventDefault();
  }
  let bar = $state<HTMLDivElement | null>(null);
  function pickSection(ev: MouseEvent) {
    if (!bar) return;
    const r = bar.getBoundingClientRect();
    goTo(((ev.clientX - r.left) / r.width) * windowLength);
  }

  const fps = $derived(source?.probe?.video?.fps ?? 30);

  function settings(o: Output) {
    const p = o.params ?? {};
    const rows: [string, string][] = [
      ["Attenuation limit", p.atten_lim_db === null ? "none" : `${p.atten_lim_db} dB`],
      ["Hiss reduction", p.reduce_hiss ? `${p.hiss_method} (${p.hiss_method === "profile" ? `nr ${p.hiss_nr}` : `strength ${p.hiss_strength}`})` : "off"],
      ["Declick", p.declick ? `${p.declick_nr} dB` : "off"],
      ["Music", p.separate_music ? (p.music_level_db === null ? "removed" : `bed at ${p.music_level_db} dB`) : "not separated"],
      ["Loudness", o.results.final_lufs !== undefined ? `${o.results.final_lufs.toFixed(1)} LUFS (target ${p.target_lufs})` : "—"],
    ];
    return rows;
  }

  const tuning = $derived(output?.results.autotune ?? null);
  const plan = $derived(source?.plans.find((p) => p.path === tuning?.cache) ?? null);
  const planWindow = $derived(plan?.windows.find((w) => w.label === tuning?.window) ?? null);

  async function copy(text: string) {
    await navigator.clipboard.writeText(text);
  }
</script>

<svelte:window onkeydown={sectionKeys} />

<div class="page col">
  {#if !store.library}
    <p class="dim">Loading…</p>
  {:else if !output}
    <div class="error-box">No output named {file} in this project.</div>
  {:else}
    <div class="layout">
      <div class="col main">
        {#if output.kind === "video"}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video bind:this={video} src={media.file(output.path)} muted playsinline preload="auto"></video>
        {/if}
        {#if error}<div class="error-box">{error}</div>{/if}
        {#if sectioned && source}
          <div class="sections">
            <div class="row small">
              <span>Section {formatClock(section, false)}–{formatClock(Math.min(windowLength, section + SECTION_SEC), false)}
                of {formatClock(windowLength, false)}</span>
              <span class="faint">a long output is compared five minutes at a time · click the strip, or <kbd>PgUp</kbd>/<kbd>PgDn</kbd></span>
              <span class="spacer"></span>
              <button class="small" disabled={section <= 0} onclick={() => step(-1)}>‹ Earlier</button>
              <button class="small" disabled={section + SECTION_SEC >= windowLength} onclick={() => step(1)}>Later ›</button>
            </div>
            <div class="strip" bind:this={bar} role="presentation" onclick={pickSection}>
              <Filmstrip {source} start={windowStart} end={windowStart + windowLength} height={30} />
              <div class="shade" style="left:0;width:{(section / windowLength) * 100}%"></div>
              <div class="shade" style="left:{((section + SECTION_SEC) / windowLength) * 100}%;right:0"></div>
              <div class="here" style="left:{(section / windowLength) * 100}%;width:{(SECTION_SEC / windowLength) * 100}%"></div>
            </div>
          </div>
        {/if}
        {#key section}
          <ABDeck {player} {a} {b} {fps} bind:levelMatch
                  labelB={bOutput && bOutput.file !== output.file ? bOutput.tag ?? "Other" : "Cleaned"} {onframe}
                  picture={source ? { source, start: windowStart + section } : null} />
        {/key}
      </div>

      <aside class="col">
        <div class="card col">
          <h3>Window</h3>
          <div class="dim small">
            {output.label} · starts at {formatClock(output.window?.actual_start_sec ?? 0)}
            {#if output.window?.keyframe_snap_sec}(snapped {output.window.keyframe_snap_sec.toFixed(2)} s back to a keyframe){/if}
          </div>
          {#if alternatives.length > 1}
            <label class="col small">B side
              <select bind:value={bFile}>
                {#each alternatives as o (o.file)}<option value={o.file}>{o.tag ?? "(no tag)"} — {ltr(o.file)}</option>{/each}
              </select>
            </label>
          {/if}
        </div>

        <div class="card col">
          <h3>Settings{#if tuning?.chosen_by === "user"} <span class="chip">your pick</span>{:else if tuning} <span class="chip">autotune</span>{/if}</h3>
          <table class="small">
            <tbody>
              {#each settings(bOutput ?? output) as [k, v]}<tr><td class="dim">{k}</td><td>{v}</td></tr>{/each}
            </tbody>
          </table>
          {#if tuning?.picked && planWindow}
            <table class="small">
              <thead><tr><th></th><th class="num">source</th><th class="num">picked</th></tr></thead>
              <tbody>
                <tr><td class="dim">Fidelity</td><td class="num">1.000</td><td class="num">{tuning.picked.fidelity?.toFixed(3)}</td></tr>
                <tr><td class="dim">Hiss removed</td><td class="num">—</td><td class="num">{tuning.picked.hiss_gain_db?.toFixed(1)} dB</td></tr>
                <tr><td class="dim">Warble</td><td class="num">{planWindow.source.warble.toFixed(1)}</td><td class="num">{tuning.picked.warble?.toFixed(1)}</td></tr>
                <tr><td class="dim">Ticks /s</td><td class="num">{planWindow.source.clicks.toFixed(1)}</td><td class="num">{tuning.picked.clicks?.toFixed(1)}</td></tr>
              </tbody>
            </table>
          {/if}
          <div class="row wrap">
            {#if plan && planWindow}
              <a href={links.candidates(plan.path, planWindow.label, { b: output.file })}><button>Candidates for this window</button></a>
            {/if}
            {#if source}
              <a href={links.newRun({ source: source.id, start: output.window?.requested_start_sec ?? 0,
                  duration: output.window?.requested_duration_sec ?? "", kind: output.params?.source ?? source.source_kind ?? "",
                  music: output.params?.separate_music ? 1 : "" })}><button>Re-run…</button></a>
            {/if}
            <button onclick={() => api.reveal(output.path).catch(reportError)}>Reveal</button>
          </div>
        </div>

        {#if output.reproduce}
          <div class="card col">
            <div class="row"><h3>Command</h3><span class="spacer"></span>
              <button class="small" onclick={() => copy(output.reproduce!)}>Copy</button></div>
            <code class="cmd">{output.reproduce}</code>
          </div>
        {/if}
      </aside>
    </div>
  {/if}
</div>

<style>
  .layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 18px; align-items: start; }
  /* Leave room for the A/B deck under it inside the fixed workspace. */
  video { width: 100%; max-height: max(200px, calc(100vh - 470px)); background: #000; border-radius: var(--radius); }
  .small { font-size: 12.5px; }
  .wrap { flex-wrap: wrap; }
  .sections { display: flex; flex-direction: column; gap: 4px; }
  .sections .strip { position: relative; cursor: pointer; }
  .sections .shade { position: absolute; top: 0; bottom: 0; background: rgb(0 0 0 / 0.6); pointer-events: none; }
  .sections .here { position: absolute; top: 0; bottom: 0; outline: 2px solid var(--select); outline-offset: -1px; pointer-events: none; }
  .cmd { display: block; max-height: 160px; overflow: auto; word-break: break-all; color: var(--text-dim); }
  aside td { border: none; padding: 2px 6px 2px 0; }
</style>
