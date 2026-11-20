import { describe, expect, it } from "vitest";
import table from "../../../tests/data/subtitle_mapping.json";
import { clipStatus, cueAt, isStale, scriptLang, timelineCues, uncovered, type SubtitleDoc } from "./subtitles";

describe("subtitles on the timeline (shared table with tests/test_subs.py)", () => {
  for (const c of (table as any).cases) {
    it(c.name, () => {
      const got = timelineCues(c.clips, (table as any).docs, c.lang).map((it) =>
        [it.start, it.end, it.text, it.cue, ...(c.lang !== "mixed" ? [it.missing, it.stale] : [])]);
      expect(got).toEqual(c.expect);
    });
  }
});

describe("subtitle helpers", () => {
  it("finds the cue at a time", () => {
    const items = timelineCues((table as any).cases[0].clips, (table as any).docs, "mixed");
    expect(cueAt(items, 3)?.cue).toBe("c1");
    expect(cueAt(items, 5)).toBeNull();
    expect(cueAt(items, 14.5)?.cue).toBe("c4");
  });
  it("knows languages, staleness and gaps", () => {
    expect(scriptLang("שלום everyone")).toBe("mixed");
    const c = (table as any).docs.s.cues;
    expect(isStale(c[0], "en")).toBe(false);
    expect(isStale(c[3], "en")).toBe(true);
    expect(uncovered([[0, 10], [20, 30]], 5, 25)).toEqual([[10, 20]]);
  });
  it("summarises a clip", () => {
    const doc = (table as any).docs.s as SubtitleDoc;
    const st = clipStatus({ id: "x", source: "s", in_sec: 40, out_sec: 50, audio: null }, doc);
    expect(st.cues).toBe(2); // 41–43 and 44–46; 49.8–52 only touches the clip's last moment
    expect(st.covered).toBe(true);
    expect(st.en).toEqual({ done: 1, of: 1, stale: 1 }); // "hello there" is already English
  });
});
