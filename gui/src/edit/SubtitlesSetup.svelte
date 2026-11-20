<script lang="ts">
  // What subtitles need on this Mac, what is missing, and how to get it (plan §7.2).
  import { api } from "../lib/api";
  import { reportError, toast } from "../lib/state.svelte";
  import { loadSetup, subs } from "./subtitles.svelte";

  let { onclose }: { onclose: () => void } = $props();
  let checking = $state(false);

  const ORDER = ["whisper_cpp", "whisper_model", "vad", "ollama", "translate_model", "ivrit_model"];
  const DOWNLOAD: Record<string, "whisper" | "ivrit" | "translate"> = {
    whisper_model: "whisper", ivrit_model: "ivrit", translate_model: "translate",
  };

  async function check() {
    checking = true;
    await loadSetup(true);
    checking = false;
  }
  $effect(() => { if (!subs.setup) void check(); });

  async function download(what: "whisper" | "ivrit" | "translate") {
    try {
      await api.submit({ type: "subtitles_setup", what });
      toast("Downloading. Check again when it is done (see the job queue).");
    } catch (e) {
      reportError(e);
    }
  }
  const copy = (t: string) => navigator.clipboard.writeText(t).then(() => toast("Copied"));
</script>

<div class="col setup">
  <p class="dim small">Subtitles are made on this Mac: speech recognition with whisper.cpp, translation with a local model
    through Ollama. Each piece is installed once; after that everything works offline.</p>
  {#if !subs.setup}
    <p class="faint">{checking ? "Checking…" : ""}</p>
  {:else}
    <table>
      <tbody>
        {#each ORDER as key (key)}
          {@const it = subs.setup[key]}
          {#if it}
            <tr>
              <td class="mark" class:ok={it.ok} class:opt={!it.ok && it.optional}>{it.ok ? "✓" : it.optional ? "–" : "✗"}</td>
              <td><div>{it.label}{#if it.optional}<span class="faint"> (optional)</span>{/if}</div>
                <div class="faint small">{it.detail}</div></td>
              <td class="act">
                {#if !it.ok}
                  {#if DOWNLOAD[key]}<button class="small" onclick={() => download(DOWNLOAD[key])}>Download</button>
                  {:else}<button class="small" onclick={() => copy(it.command)} title={it.command}>Copy command</button>{/if}
                {/if}
              </td>
            </tr>
          {/if}
        {/each}
      </tbody>
    </table>
  {/if}
  <div class="row"><span class="spacer"></span>
    <button onclick={check} disabled={checking}>{checking ? "Checking…" : "Check again"}</button>
    <button class="primary" onclick={onclose}>Close</button>
  </div>
</div>

<style>
  .setup { gap: 10px; min-width: min(620px, 90vw); }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  td { padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .mark { width: 20px; color: var(--danger, #e5534b); font-weight: 600; }
  .mark.ok { color: var(--b-color); }
  .mark.opt { color: var(--text-faint); }
  .act { text-align: right; white-space: nowrap; }
  .small { font-size: 12px; }
</style>
