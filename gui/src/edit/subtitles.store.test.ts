// The subtitle store's undo against a small stand-in for POST /api/subtitles/{id}/edit
// (it answers like the server: the changed cues and the ids removed).
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Cue, SubtitleDoc } from "../lib/subtitles";

let server: Cue[] = [];
let rev = 1;
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x));

function apply(op: any) {
  const i = server.findIndex((c) => c.id === op.id);
  if (op.op === "merge") {
    const [a, b] = [server[i], server[i + 1]];
    const tr: Cue["tr"] = {};
    for (const l of ["en", "he"] as const)
      if (a.tr?.[l]?.text && b.tr?.[l]?.text) tr[l] = { text: `${a.tr[l]!.text} ${b.tr[l]!.text}`, src: "x" };
    server.splice(i, 2, { ...a, end: b.end, text: `${a.text} ${b.text}`, tr });
  } else if (op.op === "put") {
    if (server.some((c) => c.start < op.cue.end && c.end > op.cue.start)) throw new Error("overlap");
    server.push(clone(op.cue));
  } else if (op.op === "text" && op.lang) server[i].tr = { ...server[i].tr, [op.lang]: { text: op.text, src: server[i].text } };
  else if (op.op === "text") server[i].text = op.text;
  else if (op.op === "time") Object.assign(server[i], { start: op.start, end: op.end });
  else if (op.op === "clear_tr") delete server[i].tr?.[op.lang as "en" | "he"];
  else throw new Error(op.op);
  server.sort((a, b) => a.start - b.start);
}

vi.mock("../lib/api", () => ({
  api: {
    subtitles: async () => ({ schema: 1, rev, coverage: [], cues: clone(server) }),
    editSubtitles: async (_: string, ops: unknown[]) => {
      const before = new Map(server.map((c) => [c.id, JSON.stringify(c)]));
      for (const o of ops) apply(o);
      rev += 1;
      return {
        rev, coverage: [], removed: [...before.keys()].filter((id) => !server.some((c) => c.id === id)),
        cues: clone(server.filter((c) => before.get(c.id) !== JSON.stringify(c))),
      };
    },
  },
}));
vi.mock("../lib/state.svelte", () => ({ reportError: (e: unknown) => { throw e; }, toast: () => {} }));

const { edit, undo, subs } = await import("./subtitles.svelte");

describe("subtitle undo", () => {
  beforeEach(() => {
    rev = 1;
    server = [
      { id: "a", start: 1, end: 2, text: "first", lang: "en", tr: { he: { text: "ראשון", src: "first" } } },
      { id: "b", start: 2.5, end: 4, text: "second", lang: "en", tr: { he: { text: "שני", src: "second" } } },
    ];
    subs.docs.s = { schema: 1, rev, coverage: [], cues: clone(server) } as SubtitleDoc;
  });

  it("undoing a merge brings back both lines with their translations", async () => {
    await edit("s", [{ op: "merge", id: "a" }], "a merge");
    expect(subs.docs.s.cues.map((c) => c.text)).toEqual(["first second"]);
    await undo();
    const got = subs.docs.s.cues.map((c) => [c.id, c.start, c.end, c.text, c.tr?.he?.text]);
    expect(got).toEqual([["a", 1, 2, "first", "ראשון"], ["b", 2.5, 4, "second", "שני"]]);
    expect(server.map((c) => c.id)).toEqual(["a", "b"]);
  });
});
