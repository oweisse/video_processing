// Integrated loudness (ITU-R BS.1770 / EBU R128) and sample peak of an AudioBuffer,
// for level-matching the A/B player: louder always sounds "better", so both sides
// are played at the same loudness.

export interface Level {
  lufs: number; // -Infinity for silence
  peakDb: number;
}

function biquad(x: Float32Array, b: number[], a: number[]): Float32Array {
  const y = new Float32Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const xi = x[i];
    const yi = b[0] * xi + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2;
    x2 = x1; x1 = xi; y2 = y1; y1 = yi;
    y[i] = yi;
  }
  return y;
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
  const fs = buf.sampleRate;
  const block = Math.round(0.4 * fs), step = Math.round(0.1 * fs);
  const filters = kWeighting(fs);
  let peak = 0;
  let energies: Float64Array | null = null;
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const raw = buf.getChannelData(c);
    for (let i = 0; i < raw.length; i++) {
      const v = Math.abs(raw[i]);
      if (v > peak) peak = v;
    }
    let y = raw;
    for (const f of filters) y = biquad(y, f.b, f.a);
    // cumulative sum of squares for O(1) block energies
    const cum = new Float64Array(y.length + 1);
    for (let i = 0; i < y.length; i++) cum[i + 1] = cum[i] + y[i] * y[i];
    const n = y.length < block ? 0 : Math.floor((y.length - block) / step) + 1;
    if (!energies) energies = new Float64Array(n);
    for (let j = 0; j < n && j < energies.length; j++) energies[j] += (cum[j * step + block] - cum[j * step]) / block;
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
