<script lang="ts">
  // Names the speech recognizer should expect and the translation should spell the same way
  // every time (docs/subtitles-plan.md §2.1): one list for the whole library.
  import { api } from "../lib/api";
  import { reportError, toast } from "../lib/state.svelte";

  let { onclose }: { onclose: () => void } = $props();
  let rows = $state<{ he: string; en: string }[]>([]);
  let loaded = $state(false);

  $effect(() => {
    api.glossary().then((g) => { rows = g.length ? g : [{ he: "", en: "" }]; loaded = true; }).catch(reportError);
  });

  async function save() {
    try {
      rows = await api.saveGlossary(rows);
      toast("Names saved");
      onclose();
    } catch (e) {
      reportError(e);
    }
  }
</script>

<div class="col names">
  <p class="dim small">Names of people and places, in Hebrew and in English. They help recognize them in speech, and
    the translations spell them this way. Either side may stay empty.</p>
  {#if loaded}
    <div class="grid hdr"><span>Hebrew</span><span>English</span><span></span></div>
    {#each rows as r, i (i)}
      <div class="grid">
        <input dir="rtl" bind:value={r.he} placeholder="אופיר" />
        <input bind:value={r.en} placeholder="Ofir" />
        <button class="small" onclick={() => (rows = rows.filter((_, k) => k !== i))} title="Remove">✕</button>
      </div>
    {/each}
    <div><button class="small" onclick={() => (rows = [...rows, { he: "", en: "" }])}>＋ Add a name</button></div>
  {/if}
  <div class="row"><span class="spacer"></span>
    <button onclick={onclose}>Cancel</button>
    <button class="primary" onclick={save}>Save</button>
  </div>
</div>

<style>
  .names { gap: 8px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr 32px; gap: 6px; }
  .hdr { font-size: 12px; color: var(--text-dim); }
  .small { font-size: 12.5px; }
</style>
