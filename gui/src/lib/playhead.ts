// Where the user is in a source video, kept across pieces: opening another version of the
// same stretch (or any piece that covers the moment) carries on from there instead of
// starting over.

export interface Resume {
  /** Seconds into the new piece. */
  t: number;
  side: "A" | "B";
  playing: boolean;
}

let last: { source: string; at: number; side: "A" | "B"; playing: boolean; when: number } | null = null;

/** Only keep playing after a switch, not when coming back much later. */
const CONTINUE_MS = 3000;

export function remember(source: string, at: number, side: "A" | "B", playing: boolean, now = Date.now()) {
  last = { source, at, side, playing, when: now };
}

/** Where to start a piece of `source` that covers `start`…`start + duration`, if anywhere. */
export function resumeFor(source: string, start: number, duration: number, now = Date.now()): Resume | null {
  if (!last || last.source !== source) return null;
  const t = last.at - start;
  if (t < 0 || t >= duration - 0.05) return null;
  return { t, side: last.side, playing: last.playing && now - last.when < CONTINUE_MS };
}

export function forget() {
  last = null;
}
