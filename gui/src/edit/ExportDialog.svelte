<script lang="ts">
  // The editor's Export dialog: the same settings as Deliver (deliver/ExportSettings.svelte).
  import Modal from "../components/Modal.svelte";
  import ExportSettings from "../deliver/ExportSettings.svelte";
  import { ltr } from "../lib/bidi";
  import { href } from "../lib/router.svelte";
  import type { Project } from "../lib/edit";

  let { name, project, commit, flush, onclose }: {
    name: string;
    project: Project;
    commit: (mutate: (p: Project) => void) => void;
    flush: () => Promise<void>;
    onclose: () => void;
  } = $props();
</script>

<Modal title={`Export ${ltr(project.name)}`} {onclose} width="min(720px, 94vw)">
  <div class="pad">
    <ExportSettings {name} {project} {commit} {flush} onqueued={onclose} />
    <div class="faint small more">Exports and estimates also live in <a href={href(["deliver", name])} onclick={onclose}>Deliver</a>.</div>
  </div>
</Modal>

<style>
  .pad { padding: 12px 14px; }
  .more { margin-top: 10px; }
</style>
