import { describe, expect, it } from "vitest";
import { matchGains, measure } from "./loudness";

function sine(freq: number, amp: number, seconds: number, fs = 48000, channels = 1) {
  const data = new Float32Array(Math.round(seconds * fs));
  for (let i = 0; i < data.length; i++) data[i] = amp * Math.sin((2 * Math.PI * freq * i) / fs);
  return { sampleRate: fs, numberOfChannels: channels, getChannelData: () => data };
}

describe("measure", () => {
  it("reads a full-scale 1 kHz sine as -3.01 LUFS (BS.1770 reference)", () => {
    const { lufs, peakDb } = measure(sine(1000, 1, 5));
    expect(lufs).toBeCloseTo(-3.01, 1);
    expect(peakDb).toBeCloseTo(0, 2);
  });

  it("is 6 dB lower at half amplitude and sums channels", () => {
    const half = measure(sine(1000, 0.5, 5)).lufs;
    expect(half).toBeCloseTo(-9.03, 1);
    expect(measure(sine(1000, 0.5, 5, 48000, 2)).lufs).toBeCloseTo(-6.02, 1);
  });

  it("works at 44.1 kHz", () => {
    expect(measure(sine(1000, 1, 5, 44100)).lufs).toBeCloseTo(-3.01, 1);
  });

  it("calls silence -Infinity", () => {
    expect(measure(sine(1000, 0, 2)).lufs).toBe(-Infinity);
  });
});

describe("matchGains", () => {
  it("raises the quieter side to the louder one", () => {
    expect(matchGains({ lufs: -25, peakDb: -10 }, { lufs: -17, peakDb: -3 })).toEqual({ gainA: 8, gainB: 0 });
  });
  it("lowers both rather than clip", () => {
    const g = matchGains({ lufs: -25, peakDb: -4 }, { lufs: -17, peakDb: -3 });
    expect(g.gainA).toBeCloseTo(3); // A's peak reaches -1
    expect(g.gainB).toBeCloseTo(-5);
    expect(-25 + g.gainA).toBeCloseTo(-17 + g.gainB);
  });
});
