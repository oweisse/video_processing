import { describe, expect, it } from "vitest";
import { budgetBytes, heightChoices, makeBudget, modeSentence, sizeWords } from "./budget";

describe("budget", () => {
  it("turns a budget into bytes like sizing.py", () => {
    expect(budgetBytes({ per_hour_gb: 2 }, 80 * 60)).toBeCloseTo(2.6667e9, -6);
    expect(budgetBytes({ total_gb: 2.5 }, 999)).toBe(2.5e9);
    expect(budgetBytes(null, 60)).toBeNull();
  });
  it("makes budgets from the form", () => {
    expect(makeBudget(2, "per_hour")).toEqual({ per_hour_gb: 2 });
    expect(makeBudget(0.9, "total")).toEqual({ total_gb: 0.9 });
    expect(makeBudget(0, "total")).toBeNull();
  });
  it("offers only smaller standard heights", () => {
    expect(heightChoices(2160)).toEqual([1440, 1080, 720, 540, 480]);
    expect(heightChoices(720)).toEqual([540, 480]);
  });
  it("says sizes and modes plainly", () => {
    expect(sizeWords(2.67e9)).toBe("2.67 GB");
    expect(sizeWords(930e6)).toBe("930 MB");
    expect(modeSentence("size", 2.67e9)).toBe("Encoded to fit 2.67 GB.");
  });
});

import { minutesWords, weak } from "./budget";
describe("estimate words", () => {
  it("says encode times in hours and minutes", () => {
    expect(minutesWords(204)).toBe("3 h 24 min");
    expect(minutesWords(120)).toBe("2 h");
    expect(minutesWords(0.4)).toBe("1 min");
  });
  it("flags a weak part like sizing.py", () => {
    expect(weak({ metric: "vmaf", quality_mean: 71.7, quality_low: 51.6 })).toBe(true);
    expect(weak({ metric: "vmaf", quality_mean: 90, quality_low: 85 })).toBe(false);
  });
});
