// One AudioContext for the app, and a small cache of decoded windows.
import { fetchArrayBuffer } from "./api";
import { measure, type Level } from "./loudness";

let ctx: AudioContext | null = null;
export function audioContext(): AudioContext {
  // 48 kHz: what every file here is, so decoding never resamples.
  if (!ctx) ctx = new AudioContext({ sampleRate: 48000, latencyHint: "interactive" });
  return ctx;
}

export interface Decoded {
  buffer: AudioBuffer;
  level: Level;
}

const MAX_CACHED = 24; // an edit may use many clips
const cache = new Map<string, Promise<Decoded>>();

export function decode(url: string): Promise<Decoded> {
  const hit = cache.get(url);
  if (hit) {
    cache.delete(url);
    cache.set(url, hit);
    return hit;
  }
  const p = (async () => {
    const data = await fetchArrayBuffer(url);
    const buffer = await audioContext().decodeAudioData(data);
    return { buffer, level: measure(buffer) };
  })();
  p.catch(() => cache.delete(url));
  cache.set(url, p);
  while (cache.size > MAX_CACHED) cache.delete(cache.keys().next().value!);
  return p;
}
