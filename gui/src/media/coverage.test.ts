import { describe, expect, it } from "vitest";
import { coverage, coveredSeconds } from "./coverage";

const out = (start: number, dur: number, extra: Record<string, unknown> = {}) =>
  ({ exists: true, kind: "video", window: { actual_start_sec: start, actual_duration_sec: dur }, ...extra }) as any;

describe("coverage", () => {
  it("merges overlapping windows", () => {
    expect(coveredSeconds({ outputs: [out(0, 10), out(5, 10), out(30, 5)] })).toBe(20);
  });
  it("ignores missing files, audio-only outputs and whole-file runs without a window", () => {
    const outputs = [out(0, 10, { exists: false }), out(0, 10, { kind: "audio" }), { exists: true, kind: "video", window: null } as any];
    expect(coveredSeconds({ outputs })).toBe(0);
  });
  it("is a share of the source length", () => {
    expect(coverage({ outputs: [out(0, 30)], probe: { duration_sec: 120 } as any })).toBe(0.25);
    expect(coverage({ outputs: [out(0, 30)], probe: null })).toBeNull();
  });
});
