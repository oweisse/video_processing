import { beforeEach, describe, expect, it } from "vitest";
import { ABPlayer } from "./abplayer";

// A minimal AudioContext stand-in that records what the player schedules.
class FakeParam {
  value = 1;
  events: [string, number, number][] = [];
  cancelScheduledValues() {}
  setValueAtTime(v: number, t: number) {
    this.events.push(["set", v, t]);
    this.value = v;
  }
  linearRampToValueAtTime(v: number, t: number) {
    this.events.push(["ramp", v, t]);
    this.value = v;
  }
}
class FakeNode {
  connect() {}
  disconnect() {}
}
class FakeGain extends FakeNode {
  gain = new FakeParam();
}
class FakeSource extends FakeNode {
  buffer: any = null;
  loop = false;
  loopStart = 0;
  loopEnd = 0;
  started: [number, number, number | undefined] | null = null;
  stopped = false;
  onended: (() => void) | null = null;
  start(when: number, offset: number, duration?: number) {
    this.started = [when, offset, duration];
  }
  stop() {
    this.stopped = true;
  }
}
class FakeContext {
  currentTime = 10;
  state = "running";
  destination = new FakeNode();
  sources: FakeSource[] = [];
  createGain() {
    return new FakeGain();
  }
  createBufferSource() {
    const s = new FakeSource();
    this.sources.push(s);
    return s;
  }
  async resume() {}
}

const buffer = (duration: number) => ({ duration }) as AudioBuffer;

let ctx: FakeContext;
let player: ABPlayer;
beforeEach(() => {
  ctx = new FakeContext();
  player = new ABPlayer(ctx as unknown as AudioContext);
  player.setBuffers(buffer(60), buffer(59.5));
});

describe("ABPlayer", () => {
  it("starts both sides at the same instant and offset", async () => {
    player.seek(12);
    await player.play();
    const [a, b] = ctx.sources;
    expect(a.started).toEqual(b.started);
    expect(a.started![1]).toBe(12);
    expect(a.started![2]).toBeCloseTo(47.5); // stops with the shorter side
  });

  it("tracks time from the audio clock", async () => {
    player.seek(5);
    await player.play();
    ctx.currentTime += 2.03;
    expect(player.time).toBeCloseTo(7, 5);
    player.pause();
    ctx.currentTime += 10;
    expect(player.time).toBeCloseTo(7, 5);
  });

  it("switches with a short gain ramp, keeping both sources running", async () => {
    await player.play();
    const before = ctx.sources.length;
    player.setLevels(8, 0);
    player.setActive("A");
    expect(ctx.sources.length).toBe(before);
    const gains = (player as any).gainA.gain as FakeParam;
    const ramp = gains.events.filter((e) => e[0] === "ramp").pop()!;
    expect(ramp[1]).toBeCloseTo(10 ** (8 / 20));
    expect(ramp[2] - ctx.currentTime).toBeCloseTo(0.008);
    expect(((player as any).gainB.gain as FakeParam).value).toBe(0);
  });

  it("loops both sides identically and wraps time", async () => {
    player.setLoop(10, 14);
    await player.play();
    const [a, b] = ctx.sources.slice(-2);
    for (const s of [a, b]) {
      expect([s.loop, s.loopStart, s.loopEnd]).toEqual([true, 10, 14]);
    }
    ctx.currentTime += 0.03 + 5; // 10 → 15 = 1 s past the loop end
    expect(player.time).toBeCloseTo(11, 5);
  });

  it("replaces B in step with A", async () => {
    player.seek(20);
    await player.play();
    ctx.currentTime += 1.03;
    player.replaceB(buffer(60));
    const fresh = ctx.sources[ctx.sources.length - 1];
    expect(fresh.started![1]).toBeCloseTo(21.03, 5); // position at its scheduled start
    expect(fresh.started![0]).toBeCloseTo(ctx.currentTime + 0.03, 5);
  });

  it("restarts from the top after the end", async () => {
    player.seek(59.5);
    await player.play();
    expect(ctx.sources[0].started![1]).toBe(0);
  });
});
