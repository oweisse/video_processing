import { describe, expect, it } from "vitest";
import { neighbour, pieces } from "./pieces";

const output = (file: string, start: number, dur: number, tag: string | null = null) =>
  ({ file, kind: "video", tag, exists: true, window: { actual_start_sec: start, actual_duration_sec: dur } }) as any;
const plan = { path: "/p/plan.json", windows: [{ label: "00m05s-00m25s", start_sec: 5, end_sec: 25, candidates: [{}, {}], candidate_files: {} }] } as any;

describe("pieces", () => {
  const source = { outputs: [output("late.mp4", 60, 10), output("a.mp4", 5, 20, "gui")], plans: [plan], probe: { duration_sec: 120 } } as any;
  const list = pieces(source);

  it("lists outputs and tuned windows in time order, outputs first at the same start", () => {
    expect(list.map((p) => p.key)).toEqual(["out:a.mp4", "tuned:/p/plan.json#00m05s-00m25s", "out:late.mp4"]);
    expect(list[0].detail).toBe("gui");
    expect(list[1].detail).toBe("2 candidates");
  });

  it("skips comparison renders and treats a run without a window as the whole video", () => {
    const s = { outputs: [{ file: "c.mp4", kind: "compare" }, { file: "w.mp4", kind: "video", window: null }], plans: [], probe: { duration_sec: 90 } } as any;
    const l = pieces(s);
    expect(l).toHaveLength(1);
    expect(l[0]).toMatchObject({ label: "Whole video", start: 0, end: 90 });
  });

  it("steps between neighbours", () => {
    expect(neighbour(list, "out:a.mp4", 1)?.key).toBe("tuned:/p/plan.json#00m05s-00m25s");
    expect(neighbour(list, "out:a.mp4", -1)).toBeNull();
    expect(neighbour(list, "out:late.mp4", 1)).toBeNull();
    expect(neighbour(list, null, 1)?.key).toBe("out:a.mp4");
    expect(neighbour(list, null, -1)?.key).toBe("out:late.mp4");
  });
});
