// Mirrors video_audio_cleaner/timeparse.py: '1h20m30s', '8m50s', '5m30', '90', '1.5'.

const NUM = String.raw`(\d+(?:\.\d+)?)`;
const PATTERN = new RegExp(
  String.raw`^\s*(?:${NUM}\s*h\s*)?(?:${NUM}\s*m\s*)?(?:${NUM}\s*(s)?\s*)?$`,
  "i",
);

/** Seconds, null for empty input; throws Error with a readable message. */
export function parseTime(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  if (/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(text)) {
    const n = Number(text);
    if (!Number.isFinite(n)) throw new Error(`'${value}' is not a finite number`);
    if (n < 0) throw new Error(`'${value}' is negative`);
    return n;
  }
  const m = PATTERN.exec(text);
  if (!m) throw new Error(`'${value}' is not a time (e.g. 90, 8m50s, 1h20m)`);
  const [, h, min, s, unit] = m;
  if (h === undefined && min === undefined && s === undefined)
    throw new Error(`'${value}' is not a time (e.g. 90, 8m50s, 1h20m)`);
  if (s !== undefined && unit === undefined && min === undefined)
    throw new Error(`'${value}': a bare number may only follow minutes`);
  return Number(h ?? 0) * 3600 + Number(min ?? 0) * 60 + Number(s ?? 0);
}

/** As in output names: 07m00s, 01h05m00s. */
export function formatFilename(seconds: number | null): string {
  if (seconds === null) return "end";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  return h ? `${p(h)}h${p(m)}m${p(s)}s` : `${p(m)}m${p(s)}s`;
}

/** Clock display: 1:02:03.4 or 2:03.4 (tenths optional). */
export function formatClock(seconds: number, tenths = true): string {
  const neg = seconds < 0;
  let t = Math.abs(seconds);
  const h = Math.floor(t / 3600);
  t -= h * 3600;
  const m = Math.floor(t / 60);
  t -= m * 60;
  const s = tenths ? t.toFixed(1).padStart(4, "0") : String(Math.floor(t)).padStart(2, "0");
  const body = h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
  return neg ? `-${body}` : body;
}

/** Short human durations: 45 s, 4 min 18 s, 1 h 02 min. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const t = Math.round(seconds);
  if (t < 60) return `${t} s`;
  if (t < 3600) return `${Math.floor(t / 60)} min${t % 60 ? ` ${t % 60} s` : ""}`;
  return `${Math.floor(t / 3600)} h ${String(Math.floor((t % 3600) / 60)).padStart(2, "0")} min`;
}

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  return `${v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}
