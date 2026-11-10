<script lang="ts" module>
  // One place that picks lucide icons by name, so the rest of the app never imports them.
  import AudioLines from "@lucide/svelte/icons/audio-lines";
  import ChevronDown from "@lucide/svelte/icons/chevron-down";
  import CircleAlert from "@lucide/svelte/icons/circle-alert";
  import CircleCheck from "@lucide/svelte/icons/circle-check";
  import ExternalLink from "@lucide/svelte/icons/external-link";
  import Film from "@lucide/svelte/icons/film";
  import FolderOpen from "@lucide/svelte/icons/folder-open";
  import Images from "@lucide/svelte/icons/images";
  import ListVideo from "@lucide/svelte/icons/list-video";
  import LoaderCircle from "@lucide/svelte/icons/loader-circle";
  import Plus from "@lucide/svelte/icons/plus";
  import Redo2 from "@lucide/svelte/icons/redo-2";
  import RefreshCw from "@lucide/svelte/icons/refresh-cw";
  import Search from "@lucide/svelte/icons/search";
  import Send from "@lucide/svelte/icons/send";
  import Trash from "@lucide/svelte/icons/trash";
  import Undo2 from "@lucide/svelte/icons/undo-2";
  import WandSparkles from "@lucide/svelte/icons/wand-sparkles";
  import X from "@lucide/svelte/icons/x";

  const ICONS = {
    "audio-lines": AudioLines, "chevron-down": ChevronDown, "circle-alert": CircleAlert,
    "circle-check": CircleCheck, "external-link": ExternalLink, film: Film, "folder-open": FolderOpen,
    images: Images, "list-video": ListVideo, loader: LoaderCircle, plus: Plus, redo: Redo2,
    refresh: RefreshCw, search: Search, send: Send, trash: Trash, undo: Undo2,
    "wand-sparkles": WandSparkles, x: X,
  };
  export type IconName = keyof typeof ICONS;
</script>

<script lang="ts">
  let { name, size = 16, spin = false }: { name: IconName; size?: number; spin?: boolean } = $props();
  const Component = $derived(ICONS[name]);
</script>

<Component {size} strokeWidth={1.75} class={spin ? "icon-spin" : ""} aria-hidden="true" />

<style>
  :global(.icon-spin) { animation: icon-spin 1s linear infinite; }
  @keyframes icon-spin { to { transform: rotate(360deg); } }
</style>
