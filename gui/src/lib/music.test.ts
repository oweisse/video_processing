import { describe, expect, it } from "vitest";
import { musicChoice } from "./music";

describe("Re-run keeps the music choice", () => {
  it("removed music stays removed, kept music stays kept", () => {
    expect(musicChoice({ separate_music: false })).toBe("remove");
    expect(musicChoice({ separate_music: true, music_level_db: null })).toBe("remove");
    expect(musicChoice({ separate_music: true, music_level_db: 0 })).toBe("");
    expect(musicChoice({ separate_music: true })).toBe("");
    expect(musicChoice(undefined)).toBe("");
  });
});
