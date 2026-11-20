import { describe, expect, it } from "vitest";
import { sectionFor } from "./sections";

describe("sectionFor", () => {
  it("shows the moment a minute into a section, in tens of seconds, inside the output", () => {
    expect(sectionFor(0, 3000)).toBe(0);
    expect(sectionFor(65, 3000)).toBe(0);
    expect(sectionFor(1234, 3000)).toBe(1170);
    expect(sectionFor(2990, 3000)).toBe(2700);
    expect(sectionFor(100, 200)).toBe(0);
  });
});
