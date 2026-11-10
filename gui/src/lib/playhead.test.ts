import { beforeEach, describe, expect, it } from "vitest";
import { forget, remember, resumeFor } from "./playhead";

describe("playhead", () => {
  beforeEach(forget);

  it("carries the source time into another piece that covers it", () => {
    remember("v1", 12.5, "B", true, 1000);
    expect(resumeFor("v1", 4, 21, 1500)).toEqual({ t: 8.5, side: "B", playing: true });
  });

  it("starts over in another video or a piece that does not cover the moment", () => {
    remember("v1", 12.5, "A", false, 1000);
    expect(resumeFor("v2", 4, 21, 1500)).toBeNull();
    expect(resumeFor("v1", 30, 10, 1500)).toBeNull();
    expect(resumeFor("v1", 0, 12.5, 1500)).toBeNull();
  });

  it("keeps the place but does not start playing when coming back much later", () => {
    remember("v1", 12.5, "A", true, 1000);
    expect(resumeFor("v1", 4, 21, 60_000)).toEqual({ t: 8.5, side: "A", playing: false });
  });
});
