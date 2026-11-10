// App-wide state: the open project and the job list, kept live over SSE.
import { api, type Job, type Library, type Output, type Source } from "./api";

export const store = $state({
  native: false,
  version: "",
  library: null as Library | null,
  jobs: [] as Job[],
  connected: false,
  error: null as string | null,
  toasts: [] as { id: number; text: string; level: "info" | "error" }[],
});

let toastId = 0;
export function toast(text: string, level: "info" | "error" = "info") {
  const id = ++toastId;
  store.toasts.push({ id, text, level });
  setTimeout(() => {
    store.toasts = store.toasts.filter((t) => t.id !== id);
  }, level === "error" ? 8000 : 4000);
}

export function reportError(e: unknown) {
  toast(e instanceof Error ? e.message : String(e), "error");
}

export async function loadLibrary(rescan = false) {
  try {
    store.library = rescan ? await api.rescan() : await api.library();
    store.error = null;
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e);
  }
}

export async function loadJobs() {
  try {
    store.jobs = await api.jobs();
  } catch (e) {
    reportError(e);
  }
}

const FINISHED = new Set(["done", "failed", "cancelled", "lost"]);

function upsert(job: Job) {
  const i = store.jobs.findIndex((j) => j.id === job.id);
  const before = i >= 0 ? store.jobs[i] : null;
  if (i >= 0) store.jobs[i] = { ...store.jobs[i], ...job };
  else store.jobs = [job, ...store.jobs];
  if (FINISHED.has(job.state) && before && !FINISHED.has(before.state)) {
    if (job.state === "done") toast(`Finished: ${job.title}`);
    else if (job.state === "failed") toast(`Failed: ${job.title}`, "error");
    void loadLibrary();
  }
}

let source: EventSource | null = null;
export function connectEvents() {
  source?.close();
  source = new EventSource("/api/events");
  source.addEventListener("open", () => {
    store.connected = true;
    void loadJobs();
  });
  source.addEventListener("job", (ev) => upsert(JSON.parse((ev as MessageEvent).data)));
  source.addEventListener("error", () => {
    store.connected = false;
  });
}

// ------------------------------------------------------------------ lookups
export function findOutput(name: string): { output: Output; source: Source | null } | null {
  const lib = store.library;
  if (!lib) return null;
  for (const s of lib.sources) {
    const o = s.outputs.find((o) => o.file === name);
    if (o) return { output: o, source: s };
  }
  const o = lib.unknown.find((o) => o.file === name);
  return o ? { output: o, source: null } : null;
}

export function findPlan(path: string) {
  for (const s of store.library?.sources ?? []) {
    const plan = s.plans.find((p) => p.path === path);
    if (plan) return { plan, source: s };
  }
  return null;
}

export function sourceById(id: string | undefined) {
  return store.library?.sources.find((s) => s.id === id) ?? null;
}

export function activeJobs() {
  return store.jobs.filter((j) => j.state === "running" || j.state === "queued");
}

/**
 * A path from the native dialog in the app window. Returns undefined in a plain
 * browser (the caller shows the in-page browser instead), null when cancelled.
 */
export async function nativePick(kind: "file" | "dir" | "audio", start?: string): Promise<string | null | undefined> {
  if (!store.native) return undefined;
  try {
    return (await api.pick(kind, start)).path;
  } catch (e) {
    reportError(e);
    return null;
  }
}
