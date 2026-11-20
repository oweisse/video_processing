// Clips waiting for a clean they asked for: when a new cleaned version covering one
// appears, the editor switches the clip to it. Per project, clip id → the versions that
// already covered it when the clean was asked for. Kept in the browser, so a reload or a
// server restart while a clean runs does not lose the switch.
import { loadPref, savePref } from "../lib/prefs";

const KEY = "edit.awaiting";
export const awaiting = $state<Record<string, Record<string, string[]>>>(loadPref(KEY, {}));

const save = () => savePref(KEY, $state.snapshot(awaiting));

export function await_(project: string, clip: string, known: string[]) {
  (awaiting[project] ??= {})[clip] = known;
  save();
}

export function forget(project: string, clip: string) {
  if (awaiting[project]?.[clip] === undefined) return;
  delete awaiting[project][clip];
  save();
}
