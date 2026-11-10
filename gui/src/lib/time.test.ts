import { describe, expect, it } from "vitest";
import { formatClock, formatFilename, parseTime } from "./time";

// Same table as tests/test_timeparse.py's accepted and refused inputs.
describe("parseTime", () => {
  it.each([
    ["90", 90],
    ["1.5", 1.5],
    ["8m50s", 530],
    ["5m30", 330],
    ["45s", 45],
    ["1h20m", 4800],
    ["1h", 3600],
    ["1h2m3s", 3723],
    ["  2m ", 120],
    ["0", 0],
  ])("%s → %d", (text, seconds) => {
    expect(parseTime(text)).toBe(seconds);
  });

  it.each(["1h30", "30x", "-5", "m", "s", "5s30m", "abc"])("refuses %s", (text) => {
    expect(() => parseTime(text)).toThrow();
  });

  it("treats empty as null", () => {
    expect(parseTime("  ")).toBeNull();
  });
});

describe("formatting", () => {
  it("matches the output-name format", () => {
    expect(formatFilename(1485)).toBe("24m45s");
    expect(formatFilename(3725.9)).toBe("01h02m05s");
    expect(formatFilename(null)).toBe("end");
  });
  it("formats clocks", () => {
    expect(formatClock(75.25)).toBe("1:15.3");
    expect(formatClock(3725, false)).toBe("1:02:05");
  });
});
