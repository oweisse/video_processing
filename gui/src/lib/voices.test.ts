import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { driftAction, PRELOAD } from "./voices";
import { Program } from "./program";
import type { Clip } from "./edit";

describe("driftAction", () => {
  it("jumps when far off, nudges the rate when a little off, settles when close", () => {
    expect(driftAction(0.4, 1)).toEqual({ rate: 1, seek: true });
    expect(driftAction(0.1, 1)).toEqual({ rate: 0.95, seek: false });
    expect(driftAction(-0.05, 1).rate).toBeCloseTo(1.05);
    expect(driftAction(0.01, 0.97)).toEqual({ rate: 1, seek: false });
    expect(driftAction(0.03, 0.97)).toEqual({ rate: 0.97, seek: false }); // keep nudging
  });
});

// ------------------------------------------------------------------ the player streams long sound
class FakeParam {
  value = 1;
  setValueAtTime() {}
  setValueCurveAtTime() {}
  linearRampToValueAtTime() {}
}
class FakeNode {
  connect() {}
  disconnect() {}
}
class FakeContext {
  currentTime = 0;
  state = "running";
  outputLatency = 0;
  destination = new FakeNode();
  createGain() {
    return Object.assign(new FakeNode(), { gain: new FakeParam() });
  }
  createBufferSource() {
    return Object.assign(new FakeNode(), { buffer: null, start() {}, stop() {} });
  }
  createMediaElementSource() {
    return new FakeNode();
  }
  resume() {
    return Promise.resolve();
  }
}
const elements: FakeAudio[] = [];
class FakeAudio {
  src = "";
  preload = "";
  crossOrigin = "";
  currentTime = 0;
  playbackRate = 1;
  readyState = 4;
  seeking = false;
  paused = true;
  volume = 1;
  constructor() {
    elements.push(this);
  }
  play() {
    this.paused = false;
    return Promise.resolve();
  }
  pause() {
    this.paused = true;
  }
  removeAttribute(name: string) {
    if (name === "src") this.src = "";
  }
  load() {}
}
const fakeVideo = () =>
  ({ muted: false, playsInline: false, style: {}, src: "", currentTime: 0, paused: true, seeking: false,
     playbackRate: 1, pause() {}, play: () => Promise.resolve(), removeAttribute() {}, load() {} }) as unknown as HTMLVideoElement;

describe("Program with long clips", () => {
  beforeEach(() => {
    elements.length = 0;
    vi.stubGlobal("Audio", FakeAudio);
  });
  afterEach(() => vi.unstubAllGlobals());

  // Three 10-minute clips of three videos, original sound, plain cuts.
  const clips: Clip[] = ["a", "b", "c"].map((s) => ({ id: s, source: s, in_sec: 0, out_sec: 600, audio: null }));
  function program() {
    const ctx = new FakeContext();
    const p = new Program(ctx as unknown as AudioContext, [fakeVideo(), fakeVideo()], {
      proxy: (id) => `/proxy/${id}`,
      clipAudio: (c) => ({ url: `/proxy/${c.source}`, offset: c.in_sec, seconds: 3600 }),
      music: () => "",
      joinInfo: { sourceSeconds: () => 3600, audioSeconds: () => null },
    });
    return { p, ctx };
  }

  it("streams instead of decoding, and keeps only the voices near the playhead", async () => {
    const { p, ctx } = program();
    await p.setEdit(clips, []); // would hang on decode if it tried to fetch
    await p.play();
    expect(elements.map((e) => e.src)).toEqual(["/proxy/a"]);
    ctx.currentTime = 600 - PRELOAD + 1; // just before b starts
    p.frame();
    expect(elements.filter((e) => e.src).map((e) => e.src)).toEqual(["/proxy/a", "/proxy/b"]);
    ctx.currentTime = 700; // into b: a has played and is let go
    p.frame();
    expect(elements.filter((e) => e.src).map((e) => e.src)).toEqual(["/proxy/b"]);
  });

  it("seeking while playing starts over at the new place", async () => {
    const { p } = program();
    await p.setEdit(clips, []);
    await p.play();
    p.seek(1500);
    const live = elements.filter((e) => e.src);
    expect(live.map((e) => e.src)).toEqual(["/proxy/c"]);
    expect(live[0].currentTime).toBeCloseTo(300, 0);
  });
});
