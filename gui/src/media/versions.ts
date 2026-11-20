// Deleting a rendered version (a green stripe) from wherever it is shown.
import { api, type Output } from "../lib/api";
import { loadLibrary, reportError, toast } from "../lib/state.svelte";
import { versionName } from "./pieces";

const mb = (b: number | null | undefined) => (b ? `${(b / 2 ** 20).toFixed(0)} MB` : "");

/** Asks first (saying which clips use it), then moves it to _trash. True when deleted. */
export async function deleteVersion(o: Output): Promise<boolean> {
  let used: { project: string; clips: number }[] = [];
  try {
    used = (await api.outputUsedBy(o.file)).used_by;
  } catch {
    /* asked anyway below */
  }
  const users = used.map((u) => `${u.project} (${u.clips} clip${u.clips > 1 ? "s" : ""})`).join(", ");
  const ok = confirm(
    `Delete the version "${versionName(o)}"${o.size ? ` (${mb(o.size)})` : ""}?\n\n${o.file}\n\n` +
      (users ? `Used by ${users}: those clips go back to their original sound.\n\n` : "") +
      "It moves to the library's _trash folder (empty it from Storage to free the space).",
  );
  if (!ok) return false;
  try {
    const r = await api.deleteOutput(o.file);
    await loadLibrary();
    toast(`Deleted ${versionName(o)}` + (r.projects.length ? ` · ${r.projects.join(", ")} back to original sound` : ""));
    return true;
  } catch (e) {
    reportError(e);
    return false;
  }
}

/** Asks for a new label, then renames the version's file. The new {file, path}, or null. */
export async function renameVersion(o: Output): Promise<{ file: string; path: string } | null> {
  const tag = prompt(`New label for ${o.file}\n(empty = no label)`, o.tag ?? "");
  if (tag === null || tag.trim() === (o.tag ?? "")) return null;
  try {
    const r = await api.renameOutput(o.file, tag.trim());
    await loadLibrary();
    toast(`Renamed to ${r.file}`);
    return { file: r.file, path: r.path };
  } catch (e) {
    reportError(e);
    return null;
  }
}
