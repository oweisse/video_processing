<script lang="ts" module>
  const open: symbol[] = [];
</script>

<script lang="ts">
  // A large panel over the page, for showing a whole view (the clean form, a window's
  // candidates) without leaving where you are. Esc or the backdrop closes it.
  import { onDestroy, type Snippet } from "svelte";
  import Icon from "./Icon.svelte";

  let { title, onclose, children, width = "min(1200px, 94vw)" }: {
    title: string;
    onclose: () => void;
    children: Snippet;
    width?: string;
  } = $props();

  // Modals can open over one another (the clean form over a window's candidates): Esc
  // closes only the one on top.
  const me = Symbol();
  open.push(me);
  onDestroy(() => open.splice(open.indexOf(me), 1));
  function key(ev: KeyboardEvent) {
    if (ev.key === "Escape" && open[open.length - 1] === me) {
      ev.stopImmediatePropagation();
      ev.preventDefault();
      onclose();
    }
  }
</script>

<!-- capture: the dialog sees Esc before the page under it (which may use Esc to go back) -->
<svelte:window onkeydowncapture={key} />
<div class="backdrop" role="presentation" onclick={onclose}></div>
<div class="modal" role="dialog" aria-label={title} style:width>
  <header class="row">
    <h3>{title}</h3><span class="spacer"></span>
    <button class="icon ghost" aria-label="Close" title="Close (Esc)" onclick={onclose}><Icon name="x" /></button>
  </header>
  <div class="body">{@render children()}</div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 0.5); z-index: 60; }
  .modal {
    position: fixed; z-index: 61; left: 50%; top: 4vh; bottom: 4vh; transform: translateX(-50%);
    display: flex; flex-direction: column; background: var(--bg); border: 1px solid var(--border);
    border-radius: var(--radius); box-shadow: 0 12px 40px rgb(0 0 0 / 0.6);
  }
  header { flex: 0 0 32px; padding: 0 6px 0 12px; background: var(--bg-header); border-bottom: 1px solid var(--gap); }
  h3 { font-size: 12px; }
  .body { flex: 1; min-height: 0; overflow: auto; }
</style>
