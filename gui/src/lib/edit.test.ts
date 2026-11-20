import { describe, expect, it } from "vitest";
import canvasTable from "../../../tests/data/canvas.json";
import joinsTable from "../../../tests/data/joins.json";
import {
  blendLead, clipAt, displayWidth, duration, gainAt, joinsOf, place, split, stitchClips, stretches, suggestCanvas, voiceShape,
  canvasFrame, glideFrame, matchFrame, zoomGlide, zoomOf, ZOOM_GLIDE_SEC, type CanvasItem, type Clip, type StitchItem } from "./edit";

const clip = (id: string, a: number, b: number, fade = 0): Clip => ({
  id, source: "s", in_sec: a, out_sec: b, audio: null,
  transition_in: fade ? { type: "crossfade", sec: fade } : null,
});

describe("edit model", () => {
  const clips = [clip("a", 0, 4), clip("b", 10, 13, 1), clip("c", 20, 22)];

  it("places clips like export.py, overlapping crossfades", () => {
    expect(place(clips).map((p) => [p.start, p.length])).toEqual([[0, 4], [3, 3], [6, 2]]);
    expect(duration(clips)).toBe(8);
  });

  it("switches the picture in the middle of a crossfade", () => {
    const p = place(clips);
    expect(clipAt(p, 3.4)!.clip.id).toBe("a");
    expect(clipAt(p, 3.6)!.clip.id).toBe("b");
    expect(clipAt(p, 7.9)!.clip.id).toBe("c");
    expect(clipAt(p, 8.5)).toBeNull();
  });

  it("splits at the playhead, keeping source times", () => {
    const out = split(clips, 5);
    expect(out.map((c) => [c.in_sec, c.out_sec])).toEqual([[0, 4], [10, 12], [12, 13], [20, 22]]);
    expect(out[2].transition_in).toBeNull();
    expect(duration(out)).toBe(8);
    expect(split(clips, 7.97)).toBe(clips); // too close to an edge
  });
});

describe("suggestCanvas (shared table with export.suggest_canvas)", () => {
  for (const c of canvasTable.cases) {
    it(c.name, () => expect(suggestCanvas(c.items as CanvasItem[])).toEqual(c.expect));
  }
  it("squares stored pixels", () => {
    expect(displayWidth({ width: 1280, sar: 16 / 15 })).toBe(1366);
    expect(displayWidth({ width: 360, sar: 16 / 15 })).toBe(384);
    expect(displayWidth({ width: 640 })).toBe(640);
  });
});

describe("joinsOf (shared table with export.joins)", () => {
  for (const c of joinsTable.cases) {
    it(c.name, () => {
      const clips = c.clips.map((x, i) => ({ id: `c${i}`, ...x }) as Clip);
      const files = c.files as Record<string, number>;
      const sources = c.sources as Record<string, number>;
      const got = joinsOf(clips, {
        sourceSeconds: (id) => sources[id],
        audioSeconds: (clip) => (clip.audio?.file ? files[clip.audio.file] : null),
      });
      expect(got).toEqual(c.expect);
    });
  }
});

describe("voiceShape and gainAt", () => {
  const soft = { type: "soft_cut" as const, sec: 1 };
  const clips: Clip[] = [
    { id: "a", source: "s", in_sec: 10, out_sec: 14, audio: null },
    { id: "b", source: "s", in_sec: 56, out_sec: 60, audio: null, transition_in: soft }, // ends with its video
    { id: "c", source: "t", in_sec: 0, out_sec: 4, audio: null, transition_in: soft },
  ];
  const info = { sourceSeconds: (id: string) => (id === "s" ? 60 : 20), audioSeconds: () => null };
  const joins = joinsOf(clips, info);
  const placed = place(clips);

  it("borrows sound for a blend and fades for a dip", () => {
    // a → b: both have sound to spare, centred; b → c: b's video ends, c's begins.
    expect(joins.map((j) => j.kind)).toEqual(["cut", "blend", "dip"]);
    const a = voiceShape(placed, joins, 0);
    expect([a.pre, a.post]).toEqual([0, 0.5]);
    expect(a.fades).toEqual([{ from: 3.5, to: 4.5, dir: "out", shape: "qsin" }]);
    const b = voiceShape(placed, joins, 1);
    expect([b.pre, b.post]).toEqual([0.5, 0]);
    expect(b.fades).toEqual([
      { from: 3.5, to: 4.5, dir: "in", shape: "qsin" },
      { from: 7.5, to: 8, dir: "out", shape: "qsin" },
    ]);
  });

  it("is equal power through a blend and silent at a dip's cut", () => {
    const out = voiceShape(placed, joins, 0).fades, inn = voiceShape(placed, joins, 1).fades;
    for (const t of [3.6, 4, 4.4]) expect(gainAt(out, t) ** 2 + gainAt(inn, t) ** 2).toBeCloseTo(1, 6);
    const b = voiceShape(placed, joins, 1).fades, c = voiceShape(placed, joins, 2).fades;
    expect(gainAt(b, 8)).toBe(0);
    expect(gainAt(c, 8)).toBe(0);
    expect(gainAt(c, 9)).toBe(1);
  });
});

describe("stitching", () => {
  const out = (file: string, a: number, b: number, created: string, extra = {}) =>
    ({ file, path: `/o/${file}`, exists: true, kind: "video", created,
       window: { actual_start_sec: a, actual_duration_sec: b - a }, ...extra }) as any;
  const src = (id: string, length: number, outputs: any[] = []) =>
    ({ id, path: `/v/${id}.mp4`, probe: { duration_sec: length } as any, outputs }) as StitchItem["source"];

  it("cuts a video where its cleaned stretches begin and end, newest sound first", () => {
    const s = src("a", 100, [out("old.mp4", 10, 40, "2026-01-01"), out("new.mp4", 30, 60, "2026-02-01")]);
    expect(stretches(s).map((x) => [x.a, x.b, x.output?.file ?? null])).toEqual([
      [0, 10, null], [10, 30, "old.mp4"], [30, 60, "new.mp4"], [60, 100, null],
    ]);
  });

  it("folds slivers into a neighbour whose sound covers them", () => {
    const s = src("a", 100, [out("x.mp4", 0, 50.2, "2026-01-01"), out("y.mp4", 50, 100, "2026-02-01")]);
    expect(stretches(s).map((x) => [x.a, x.b, x.output?.file])).toEqual([[0, 50, "x.mp4"], [50, 100, "y.mp4"]]);
    const t = src("t", 100, [out("z.mp4", 0, 99.7, "2026-01-01")]);
    expect(stretches(t).map((x) => [x.a, x.b, x.output?.file ?? null])).toEqual([[0, 99.7, "z.mp4"], [99.7, 100, null]]);
  });

  it("ignores missing files and audio-only outputs", () => {
    const s = src("a", 30, [out("gone.mp4", 0, 30, "2026-01-01", { exists: false }), out("a.wav", 0, 30, "2026-01-01", { kind: "audio" })]);
    expect(stretches(s).map((x) => x.output)).toEqual([null]);
  });

  it("stitches whole videos and pieces with soft cuts where the sound changes", () => {
    const a = src("a", 100, [out("a1.mp4", 0, 60, "2026-01-01")]);
    const b = src("b", 50, [out("b1.mp4", 10, 20, "2026-01-01"), out("b2.mp4", 30, 40, "2026-01-01")]);
    const clips = stitchClips([{ source: a, mode: "whole" }, { source: b, mode: "pieces" }], 1);
    expect(clips.map((c) => [c.source, c.in_sec, c.out_sec, c.audio?.output ?? null, c.transition_in?.type ?? null])).toEqual([
      ["a", 0, 60, "a1.mp4", null],
      ["a", 60, 100, null, "soft_cut"],
      ["b", 10, 20, "b1.mp4", "soft_cut"],
      ["b", 30, 40, "b2.mp4", "soft_cut"],
    ]);
    expect(clips[0].audio).toMatchObject({ file: "/o/a1.mp4", start_sec: 0, duration_sec: 60 });
    expect(stitchClips([{ source: a, mode: "whole" }, { source: b, mode: "whole" }], 0).every((c) => !c.transition_in)).toBe(true);
  });

  it("skips videos whose length is not known yet", () => {
    expect(stitchClips([{ source: { ...src("a", 1), probe: null }, mode: "whole" }], 1)).toEqual([]);
  });
});

describe("blendLead", () => {
  it("centres the overlap, else shifts it to the side with sound to spare", () => {
    expect(blendLead(1, 5, 5)).toBe(0.5);
    expect(blendLead(1, 30, 0)).toBe(1); // only the incoming sound goes back before the cut
    expect(blendLead(1, 0, 30)).toBe(0); // only the outgoing sound goes on after it
    expect(blendLead(1, 0.8, 0.3)).toBe(0.7);
    expect(blendLead(1, 0.2, 0.3)).toBeNull();
  });
});


describe("zoom frames", () => {
  it("keeps the output's shape, stays inside the picture, and reads back its zoom", async () => {
    const { frameAt, zoomOf } = await import("./edit");
    expect(frameAt(1, 0.5, 0.5, 16 / 9, 16 / 9)).toBeNull();
    const f = frameAt(2, 0.9, 0.1, 16 / 9, 16 / 9)!;
    expect(f).toEqual({ x: 0.5, y: 0, w: 0.5, h: 0.5 });
    expect(zoomOf(f, 16 / 9, 16 / 9)).toBeCloseTo(2);
    // a 4:3 picture in a 16:9 result: the frame is full width, 3/4 of the height at zoom 1
    const g = frameAt(1.5, 0.5, 0.5, 16 / 9, 4 / 3)!;
    expect(g.w).toBeCloseTo(1 / 1.5);
    expect(g.h).toBeCloseTo(0.75 / 1.5);
    expect((g.w * 4) / 3 / g.h).toBeCloseTo(16 / 9); // its shape on screen
    expect(zoomOf(g, 16 / 9, 4 / 3)).toBeCloseTo(1.5);
  });
});

describe("zoom glides", () => {
  const base = { source: "s", audio: null } as const;
  const wide = { ...base, id: "a", in_sec: 0, out_sec: 10 } as any;
  const close = { ...base, id: "b", in_sec: 10, out_sec: 20, frame: { x: 0.25, y: 0.25, w: 0.5, h: 0.5 } } as any;

  it("glides only where a shot continues with another framing", () => {
    expect(zoomGlide(wide, close)).toEqual({ from: null, to: close.frame, sec: ZOOM_GLIDE_SEC });
    expect(zoomGlide(wide, { ...close, in_sec: 12 })).toBeNull();               // a gap: a real cut
    expect(zoomGlide(wide, { ...close, source: "t" })).toBeNull();              // another video
    expect(zoomGlide(close, { ...close, id: "c", in_sec: 20, out_sec: 30 })).toBeNull(); // same framing
    expect(zoomGlide(wide, { ...close, zoom_glide: false })).toBeNull();        // opted out
    expect(zoomGlide(wide, { ...close, zoom_glide: { sec: 2 } })!.sec).toBe(2);
    expect(zoomGlide(wide, { ...close, out_sec: 10.5 })!.sec).toBeCloseTo(0.5); // never longer than the clip
  });

  it("eases between the framings, from the whole picture's canvas", () => {
    const g = zoomGlide(wide, close)!;
    const shape = 16 / 9, picture = 4096 / 2160;
    const start = glideFrame(g, 0, shape, picture), mid = glideFrame(g, 0.5, shape, picture), end = glideFrame(g, 1, shape, picture);
    expect(start).toEqual(canvasFrame(shape, picture));
    expect(start.h).toBeGreaterThan(1);                                         // letterboxed: bars above and below
    expect(end).toEqual(close.frame);
    expect(mid.w).toBeCloseTo((start.w + end.w) / 2, 6);                        // halfway in time = halfway eased
    expect(glideFrame(g, 0.1, shape, picture).w).toBeGreaterThan(start.w - (start.w - end.w) * 0.1); // slow start
  });
});

describe("matching another clip's zoom", () => {
  it("keeps the zoom and the point at the centre", () => {
    const shape = 16 / 9;
    const f = { x: 0.3, y: 0.2, w: 0.5, h: 0.5 * (4096 / 2160) / shape } as any;
    // same camera: the very same frame
    const same = matchFrame(f, shape, 4096 / 2160, 4096 / 2160)!;
    (["x", "y", "w", "h"] as const).forEach((k) => expect(same[k]).toBeCloseTo(f[k], 6));
    // another shape: same zoom, same centre
    const other = matchFrame(f, shape, 4096 / 2160, 16 / 9)!;
    expect(zoomOf(other, shape, 16 / 9)).toBeCloseTo(zoomOf(f, shape, 4096 / 2160), 6);
    expect(other.x + other.w / 2).toBeCloseTo(f.x + f.w / 2, 6);
    expect(matchFrame(null, shape, 1.8, 1.8)).toBeNull();
  });
});
