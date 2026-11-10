// Waveform overview of an AudioBuffer: per pixel column, the min and max sample.

const memo = new WeakMap<AudioBuffer, Map<number, Float32Array>>();

export function peaks(buffer: AudioBuffer, columns: number): Float32Array {
  columns = Math.max(1, Math.floor(columns));
  let byWidth = memo.get(buffer);
  if (!byWidth) memo.set(buffer, (byWidth = new Map()));
  const hit = byWidth.get(columns);
  if (hit) return hit;
  const out = new Float32Array(columns * 2);
  const channels = Array.from({ length: buffer.numberOfChannels }, (_, c) => buffer.getChannelData(c));
  const per = buffer.length / columns;
  for (let col = 0; col < columns; col++) {
    const from = Math.floor(col * per);
    const to = Math.max(from + 1, Math.floor((col + 1) * per));
    let lo = 0, hi = 0;
    // stride keeps very long buffers fast; 4000 samples per column is plenty
    const stride = Math.max(1, Math.floor((to - from) / 4000));
    for (const data of channels) {
      for (let i = from; i < to; i += stride) {
        const v = data[i];
        if (v < lo) lo = v;
        else if (v > hi) hi = v;
      }
    }
    out[col * 2] = lo;
    out[col * 2 + 1] = hi;
  }
  byWidth.set(columns, out);
  return out;
}
