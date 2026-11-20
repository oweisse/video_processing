import { describe, expect, it } from "vitest";
import { cleanStretches, clipStretch } from "./cleanclips";
import type { Clip } from "../lib/edit";

const clip = (id: string, source: string, a: number, b: number): Clip => ({ id, source, in_sec: a, out_sec: b, audio: null });

describe("clipStretch", () => {
  it("pads the clip to whole seconds and stays inside the video", () => {
    expect(clipStretch({ in_sec: 10.4, out_sec: 20.2 }, 100)).toEqual({ start: 8, end: 23 });
    expect(clipStretch({ in_sec: 0.5, out_sec: 99.5 }, 100)).toEqual({ start: 0, end: 100 });
  });
});

describe("cleanStretches", () => {
  const len = () => 1000;
  it("merges nearby clips of one video and keeps far ones apart", () => {
    const s = cleanStretches([clip("1", "a", 10, 20), clip("2", "b", 0, 5), clip("3", "a", 30, 40), clip("4", "a", 300, 310)], len);
    expect(s).toEqual([
      { source: "a", start: 8, end: 42, clips: ["1", "3"] },
      { source: "b", start: 0, end: 7, clips: ["2"] },
      { source: "a", start: 298, end: 312, clips: ["4"] },
    ]);
  });
  it("lists the clips in timeline order even when the video's order differs", () => {
    const s = cleanStretches([clip("late", "a", 50, 60), clip("early", "a", 40, 45)], len);
    expect(s).toEqual([{ source: "a", start: 38, end: 62, clips: ["late", "early"] }]);
  });
});
