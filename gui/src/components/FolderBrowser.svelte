<script lang="ts">
  // Pick a folder or a media file inside the home folder (phase 1 has no native dialog).
  import { api, type FsList } from "../lib/api";
  import { ltr } from "../lib/bidi";
  import { formatBytes } from "../lib/time";

  let {
    mode,
    start = "~",
    title,
    onpick,
    onclose,
    onpickmany,
  }: {
    mode: "dir" | "file";
    start?: string;
    title: string;
    onpick: (path: string) => void;
    onclose: () => void;
    /** Files mode: tick several files, or take every video of the folder shown. */
    onpickmany?: (sel: { paths?: string[]; folder?: string }) => void;
  } = $props();
  let ticked = $state<string[]>([]);
  function tick(path: string) {
    ticked = ticked.includes(path) ? ticked.filter((p) => p !== path) : [...ticked, path];
  }

  let listing = $state<FsList | null>(null);
  let error = $state<string | null>(null);
  let typed = $state("");

  async function open(dir: string) {
    try {
      listing = await api.fsList(dir);
      typed = listing.dir;
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }
  // svelte-ignore state_referenced_locally
  open(start);

  const entries = $derived(
    (listing?.entries ?? []).filter((e) => mode === "file" || e.is_dir),
  );
</script>

<div class="backdrop" onclick={onclose} role="presentation"></div>
<div class="modal card" role="dialog" aria-label={title}>
  <div class="row"><h2>{title}</h2><span class="spacer"></span><button class="small" onclick={onclose}>✕</button></div>
  <form class="row" onsubmit={(e) => { e.preventDefault(); open(typed); }}>
    <input class="mono path" bind:value={typed} spellcheck="false" />
    <button type="submit">Go</button>
  </form>
  {#if error}<div class="error-box">{error}</div>{/if}
  <div class="list">
    {#if listing?.parent}
      <button class="entry" onclick={() => open(listing!.parent!)}>⬑ ..</button>
    {/if}
    {#each entries as e (e.path)}
      {#if e.is_dir}
        <button class="entry" ondblclick={() => mode === "dir" && onpick(e.path)} onclick={() => open(e.path)}>
          📁 {ltr(e.name)}
        </button>
      {:else}
        <button class="entry" class:ticked={ticked.includes(e.path)} onclick={() => (onpickmany ? tick(e.path) : onpick(e.path))}>
          {onpickmany ? (ticked.includes(e.path) ? "☑" : "☐") : ""} 🎞 {ltr(e.name)} <span class="faint">{formatBytes(e.size)}</span>
        </button>
      {/if}
    {:else}
      <div class="faint pad">{mode === "file" ? "No folders or videos here." : "No sub-folders."}</div>
    {/each}
  </div>
  {#if mode === "file" && onpickmany}
    <div class="row">
      <span class="faint ellipsis">{ticked.length ? `${ticked.length} chosen` : "Click videos to choose them"}</span>
      <span class="spacer"></span>
      <button disabled={!listing} onclick={() => listing && onpickmany({ folder: listing.dir })}>Add every video in this folder</button>
      <button class="primary" disabled={!ticked.length} onclick={() => onpickmany({ paths: ticked })}>Add {ticked.length || ""}</button>
    </div>
  {/if}
  {#if mode === "dir"}
    <div class="row">
      <span class="faint ellipsis">{listing?.dir}</span>
      <span class="spacer"></span>
      <button class="primary" disabled={!listing} onclick={() => listing && onpick(listing.dir)}>Open this folder</button>
    </div>
  {/if}
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.45); z-index: 50; }
  .modal {
    position: fixed; z-index: 51; left: 50%; top: 8vh; transform: translateX(-50%);
    width: min(720px, 92vw); max-height: 80vh; display: flex; flex-direction: column; gap: 10px;
  }
  .path { flex: 1; }
  .list { overflow: auto; flex: 1; min-height: 200px; border: 1px solid var(--border); border-radius: var(--radius); }
  .entry {
    display: block; width: 100%; text-align: left; border: none; border-radius: 0;
    background: none; padding: 5px 10px; border-bottom: 1px solid var(--border);
  }
  .entry:hover { background: var(--bg-hover); }
  .entry.ticked { background: var(--bg-hover); }
  .pad { padding: 10px; }
</style>
