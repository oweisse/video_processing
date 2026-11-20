// Integrated loudness (ITU-R BS.1770 / EBU R128) and sample peak of an AudioBuffer,
// for level-matching the A/B player: louder always sounds "better", so both sides
// are played at the same loudness.

export interface Level {
  lufs: number; // -Infinity for silence
  peakDb: number;
}

/** K-weighting filters for any sample rate (the libebur128 derivation). */
export function kWeighting(fs: number): { b: number[]; a: number[] }[] {
  let f0 = 1681.974450955533, G = 3.999843853973347, Q = 0.7071752369554196;
  let K = Math.tan((Math.PI * f0) / fs);
  const Vh = 10 ** (G / 20), Vb = Vh ** 0.4996667741545416;
  let a0 = 1 + K / Q + K * K;
  const shelf = {
    b: [(Vh + (Vb * K) / Q + K * K) / a0, (2 * (K * K - Vh)) / a0, (Vh - (Vb * K) / Q + K * K) / a0],
    a: [1, (2 * (K * K - 1)) / a0, (1 - K / Q + K * K) / a0],
  };
  f0 = 38.13547087602444; Q = 0.5003270373238773;
  K = Math.tan((Math.PI * f0) / fs);
  a0 = 1 + K / Q + K * K;
  const highpass = { b: [1, -2, 1], a: [1, (2 * (K * K - 1)) / a0, (1 - K / Q + K * K) / a0] };
  return [shelf, highpass];
}

export interface ChannelSource {
  sampleRate: number;
  numberOfChannels: number;
  getChannelData(i: number): Float32Array;
}

export function measure(buf: ChannelSource): Level {
  // Streams through the samples: the two K-weighting filters run sample by sample and
  // only one energy per 100 ms step is kept, so an hour of sound needs no copies of it.
  const fs = buf.sampleRate;
  const block = Math.round(0.4 * fs), step = Math.round(0.1 * fs);
  const [f1, f2] = kWeighting(fs);
  let peak = 0;
  let energies: Float64Array | null = null;
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const raw = buf.getChannelData(c);
    const steps = Math.floor(raw.length / step);
    const sums = new Float64Array(steps + 1); // energy of each 100 ms step
    let ax1 = 0, ax2 = 0, ay1 = 0, ay2 = 0, bx1 = 0, bx2 = 0, by1 = 0, by2 = 0;
    for (let i = 0; i < raw.length; i++) {
      const xi = raw[i];
      const v = xi < 0 ? -xi : xi;
      if (v > peak) peak = v;
      const yi = f1.b[0] * xi + f1.b[1] * ax1 + f1.b[2] * ax2 - f1.a[1] * ay1 - f1.a[2] * ay2;
      ax2 = ax1; ax1 = xi; ay2 = ay1; ay1 = yi;
      const zi = f2.b[0] * yi + f2.b[1] * bx1 + f2.b[2] * bx2 - f2.a[1] * by1 - f2.a[2] * by2;
      bx2 = bx1; bx1 = yi; by2 = by1; by1 = zi;
      sums[Math.floor(i / step)] += zi * zi;
    }
    // A 400 ms block every 100 ms is four consecutive steps (block = 4 × step at the
    // rates used here; otherwise the block is rounded to whole steps).
    const per = Math.max(1, Math.round(block / step));
    const n = raw.length < block ? 0 : Math.floor((raw.length - block) / step) + 1;
    if (!energies) energies = new Float64Array(n);
    for (let j = 0; j < n && j < energies.length; j++) {
      let e = 0;
      for (let k = 0; k < per; k++) e += sums[j + k];
      energies[j] += e / (per * step);
    }
  }
  const peakDb = peak > 0 ? 20 * Math.log10(peak) : -Infinity;
  if (!energies || energies.length === 0) return { lufs: -Infinity, peakDb };
  const loud = (e: number) => -0.691 + 10 * Math.log10(e);
  const gate = (blocks: number[], threshold: number) => blocks.filter((e) => loud(e) > threshold);
  const absolute = gate(Array.from(energies), -70);
  if (!absolute.length) return { lufs: -Infinity, peakDb };
  const mean = (xs: number[]) => xs.reduce((s, v) => s + v, 0) / xs.length;
  const relative = gate(absolute, loud(mean(absolute)) - 10);
  return { lufs: loud(mean(relative.length ? relative : absolute)), peakDb };
}

export const PEAK_CEILING_DB = -1;

/**
 * Gains (dB) that bring A and B to the same loudness: the louder one's level,
 * lowered if needed so neither side's peak goes above PEAK_CEILING_DB.
 */
export function matchGains(a: Level, b: Level): { gainA: number; gainB: number } {
  if (!Number.isFinite(a.lufs) || !Number.isFinite(b.lufs)) return { gainA: 0, gainB: 0 };
  const reachA = a.lufs + (PEAK_CEILING_DB - a.peakDb);
  const reachB = b.lufs + (PEAK_CEILING_DB - b.peakDb);
  const target = Math.min(Math.max(a.lufs, b.lufs), reachA, reachB);
  return { gainA: target - a.lufs, gainB: target - b.lufs };
}
