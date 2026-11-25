// What an output did with the background music, as New run's `music` link value.

/** "remove" when the music was taken out (not separated, or separated without being put
 * back: music_level_db null), else "" (kept), so Re-run opens with the same choice. */
export function musicChoice(params: Record<string, any> | null | undefined): string {
  if (!params) return "";
  return !params.separate_music || params.music_level_db === null ? "remove" : "";
}
