import { describe, expect, it } from "vitest";
import { ltr } from "./bidi";

const FSI = "⁨", PDI = "⁩";

describe("ltr", () => {
  it("isolates each Hebrew run so neighbouring digits stay put", () => {
    expect(ltr("02-חתונה_04m37s-10m08s.mp4")).toBe(`02-${FSI}חתונה${PDI}_04m37s-10m08s.mp4`);
  });
  it("keeps words of one Hebrew phrase in one run", () => {
    expect(ltr("קלטת חתונה 2.mp4")).toBe(`${FSI}קלטת חתונה${PDI} 2.mp4`);
  });
  it("leaves other names alone", () => {
    expect(ltr("fixture_00m05s-00m25s.mp4")).toBe("fixture_00m05s-00m25s.mp4");
    expect(ltr(null)).toBe("");
  });
});
