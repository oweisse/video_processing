<script lang="ts">
  // Generate subtitles for stretches of videos (one clip, or several): model, language,
  // which sound to listen to (docs/subtitles-plan.md §7.2).
  import { api, type Output } from "../lib/api";
  import { loadPref, savePref } from "../lib/prefs";
  import { reportError, toast } from "../lib/state.svelte";
  import { formatClock } from "../lib/time";
  import type { SubtitleDoc } from "../lib/subtitles";

  export interface Stretch {
    source: string;
    sourceName: string;
    start: number;
    end: number;
    cleaned?: Output | null; // the clip's cleaned version, offered as what to listen to
    clips?: string[];
  }

  let { stretches, docs, again = false, onqueued, onclose }: {
    stretches: Stretch[];
    docs: Record<string, SubtitleDoc | undefined>;
    again?: boolean; // the stretches already have subtitles: replace them
    onqueued: () => void;
    onclose: () => void;
  } = $props();

  let model = $state<"large-v3" | "ivrit">(loadPref("subtitles.model", "large-v3"));
  let language = $state<"auto" | "he" | "en">(loadPref("subtitles.language", "auto"));
  let listen = $state<"original" | "cleaned">(loadPref("subtitles.listen", "original"));
  let confirmReplace = $state(false);
  let busy = $state(false);

  const anyCleaned = $derived(stretches.some((s) => s.cleaned));
  // what "again" would replace: cues inside the stretches that were edited or translated
  const replaced = $derived.by(() => {
    let edited = 0, translated = 0;
    for (const s of stretches) {
      for (const c of docs[s.source]?.cues ?? []) {
        const mid = (c.start + c.end) / 2;
        if (mid < s.start - 2 || mid >= s.end + 2) continue;
        if (c.edited) edited++;
        if (c.tr?.en?.text || c.tr?.he?.text) translated++;
      }
    }
    return { edited, translated };
  });
  const needsConfirm = $derived(again && (replaced.edited > 0 || replaced.translated > 0));

  async function go() {
    if (needsConfirm && !confirmReplace) return;
    savePref("subtitles.model", model);
    savePref("subtitles.language", language);
    savePref("subtitles.listen", listen);
    busy = true;
    try {
      for (const s of stretches) {
        await api.submit({
          type: "subtitles", source_id: s.source, start_sec: s.start, duration_sec: s.end - s.start,
          replace: again ? "all" : "missing", model, language,
          audio: listen === "cleaned" && s.cleaned ? s.cleaned.file : "original",
        });
      }
      toast(stretches.length === 1 ? "Generating subtitles. They appear on the clip when ready."
        : `Generating subtitles for ${stretches.length} stretches, one after another.`);
      onqueued();
    } catch (e) {
      reportError(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="col gen">
  {#if stretches.length === 1}
    <p class="dim small">{stretches[0].sourceName}, {formatClock(stretches[0].start, false)}–{formatClock(stretches[0].end, false)}.
      The spoken words come out in whatever language was spoken (Hebrew, English, or both); you can correct them,
      then create English-only or Hebrew-only versions.</p>
  {:else}
    <p class="dim small">{stretches.length} stretches of {new Set(stretches.map((s) => s.source)).size} videos, one after another.</p>
  {/if}
  <label class="field">Model
    <select bind:value={model}>
      <option value="large-v3">Standard (Whisper large-v3): Hebrew, English and mixes</option>
      <option value="ivrit">Hebrew-tuned (ivrit.ai): mostly-Hebrew speech</option>
    </select></label>
  <label class="field">Language
    <select bind:value={language}>
      <option value="auto">Detect it for each stretch of speech</option>
      <option value="he">Hebrew</option>
      <option value="en">English</option>
    </select></label>
  {#if anyCleaned}
    <label class="field">Listen to
      <select bind:value={listen}>
        <option value="original">The original sound</option>
        <option value="cleaned">The clip's cleaned sound (where it has one)</option>
      </select></label>
  {/if}
  {#if needsConfirm}
    <label class="row warn small"><input type="checkbox" bind:checked={confirmReplace} />
      Replace them: {replaced.edited ? `${replaced.edited} corrected line${replaced.edited > 1 ? "s" : ""}` : ""}{replaced.edited && replaced.translated ? " and " : ""}{replaced.translated ? `${replaced.translated} translated line${replaced.translated > 1 ? "s" : ""}` : ""} will be lost</label>
  {:else if again}
    <p class="faint small">The subtitles already there are replaced.</p>
  {/if}
  <div class="row"><span class="spacer"></span>
    <button onclick={onclose}>Cancel</button>
    <button class="primary" disabled={busy || (needsConfirm && !confirmReplace)} onclick={go}>{again ? "Generate again" : "Generate"}</button>
  </div>
</div>

<style>
  .gen { gap: 10px; min-width: min(560px, 90vw); }
  .small { font-size: 12.5px; }
  .warn { color: var(--warn); gap: 6px; }
</style>
