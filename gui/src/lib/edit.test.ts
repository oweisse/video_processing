import { describe, expect, it } from "vitest";
import { clipAt, duration, place, split, type Clip } from "./edit";

const clip = (id: string, a: number, b: number, fade = 0): Clip => ({
  id, source: "s", in_sec: a, out_sec: b, audio: null,
  transition_in: fade ? { type: "crossfade", sec: fade } : null,
});

describe("edit model", () => {
  const clips = [clip("a", 0, 4), clip("b", 10, 13, 1), clip("c", 20, 22)];

  it("places clips like export.py, overlapping crossfades", () => {
    expect(place(clips).map((p) => [p.start, p.length])).toEqual([[0, 4], [3, 3], [6, 2]]);
    expect(duration(clips)).toBe(8);
  });

  it("switches the picture in the middle of a crossfade", () => {
    const p = place(clips);
    expect(clipAt(p, 3.4)!.clip.id).toBe("a");
    expect(clipAt(p, 3.6)!.clip.id).toBe("b");
    expect(clipAt(p, 7.9)!.clip.id).toBe("c");
    expect(clipAt(p, 8.5)).toBeNull();
  });

  it("splits at the playhead, keeping source times", () => {
    const out = split(clips, 5);
    expect(out.map((c) => [c.in_sec, c.out_sec])).toEqual([[0, 4], [10, 12], [12, 13], [20, 22]]);
    expect(out[2].transition_in).toBeNull();
    expect(duration(out)).toBe(8);
    expect(split(clips, 7.97)).toBe(clips); // too close to an edge
  });
});
