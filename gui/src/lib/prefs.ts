// Per-viewer layout memory (panel sizes, viewer mode, last timeline). Browser storage can be
// missing or throw (private windows, blocked site data), so every access is guarded and the
// app works the same without it.

const PREFIX = "vac.";

export function loadPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function savePref(key: string, value: unknown) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* layout memory is a convenience only */
  }
}
