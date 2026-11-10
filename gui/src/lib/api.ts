// Typed access to the clean-audio-gui server (docs/gui-design.md §5.6).

export type SourceKind = "tape" | "digital";

export interface Probe {
  path: string;
  id: string;
  size: number;
  duration_sec: number | null;
  video: { codec: string; width: number; height: number; fps: number | null; field_order: string } | null;
  audio: { codec: string; sample_rate: number; channels: number } | null;
  keyframes: { mean_interval_sec: number; max_interval_sec: number } | null;
}

export interface Window {
  requested_start_sec: number;
  requested_duration_sec: number | null;
  actual_start_sec: number;
  actual_duration_sec: number | null;
  keyframe_snap_sec?: number;
}

export interface CandidateMeasure {
  atten: number | null;
  hiss: string;
  declick: number | boolean;
  separate_music?: boolean;
  music_level_db?: number | null;
  fidelity: number | null;
  speech_to_hiss: number | null;
  warble: number | null;
  clicks: number | null;
  hiss_gain_db?: number | null;
  click_gain_db?: number | null;
  score?: number | null;
  qualifies?: boolean;
  noise_sample_suspect?: boolean;
}

export interface AutotuneRecord {
  cache?: string;
  window?: string;
  reused?: boolean;
  chosen_by?: "user";
  candidate_index?: number;
  picked?: Partial<CandidateMeasure>;
  music_bed?: { level_db: number | null } | null;
  candidates_tried?: number;
}

export interface Output {
  file: string;
  path: string;
  exists: boolean;
  sidecar: string | null;
  created: string | null;
  window: Window | null;
  label: string | null;
  tag: string | null;
  params: Record<string, any>;
  results: { autotune?: AutotuneRecord; final_lufs?: number; lufs_before_normalize?: number; [k: string]: any };
  reproduce: string | null;
  size: number | null;
  kind: "video" | "audio" | "compare";
  source_path: string | null;
}

export interface CandidateFiles {
  dir: string;
  source: { file: string; lufs: number | null };
  candidates: { file: string; lufs: number | null }[];
}

export interface PlanWindow {
  label: string;
  start_sec: number;
  end_sec: number;
  picked: CandidateMeasure;
  runner_up: CandidateMeasure | null;
  candidates: CandidateMeasure[];
  source: { speech_to_hiss: number; warble: number; clicks: number };
  music: { level_db: number | null; table?: { level_db: number | null; warble: number }[]; source_warble?: number } | null;
  candidate_files?: CandidateFiles | null;
  user_pick?: number | null;
}

export interface Plan {
  path: string;
  dir: string;
  source_path: string;
  separate_music: boolean;
  source_kind: SourceKind;
  method: number;
  criteria: { fidelity_tolerance: number; max_warble_increase: number; click_weight: number; min_fidelity: number | null };
  windows: PlanWindow[];
}

export interface Source {
  id: string;
  path: string;
  name: string;
  exists: boolean;
  outputs: Output[];
  plans: Plan[];
  source_kind: SourceKind | null;
  probe: Probe | null;
  prep?: { proxy: boolean; thumbs: boolean; peaks: boolean };
}

export interface ProjectSummary {
  name: string;
  path: string;
  modified: string | null;
  duration_sec: number;
  clips: number;
  export_path: string;
  exported: boolean;
}

export interface ExportPlan {
  mode: "copy" | "encode";
  reasons: string[];
  duration: number;
  canvas: { width: number; height: number; fps: number };
  output: string;
  output_exists: boolean;
}

export interface Library {
  output_dir: string | null;
  projects?: ProjectSummary[];
  sources: Source[];
  unknown: Output[];
  recent_output_dirs: string[];
  audio_cache_bytes?: number;
}

export type JobState = "queued" | "running" | "done" | "failed" | "cancelled" | "lost";

export interface Job {
  id: string;
  type: string;
  title: string;
  argv?: string[];
  display: string | null;
  output_dir: string;
  state: JobState;
  created: string;
  started: string | null;
  finished: string | null;
  returncode: number | null;
  step: { index: number; total: number; title: string } | null;
  progress: { prefix: string; processed_sec: number; total_sec: number | null; speed: string | null; eta_sec: number | null } | null;
  candidates: { window: string; done: number; total: number } | null;
  windows_done: string[];
  messages: { level: string; text: string }[];
  result: { output: string; sidecar: string | null; replaced: boolean; results: any } | null;
  error: string | null;
  meta: Record<string, any>;
  cancel_requested?: boolean;
}

export interface CliOption {
  dest: string;
  flag: string;
  kind: "flag" | "choice" | "value";
  default: any;
  choices: string[] | null;
  help: string | null;
  group: string | null;
  autotuned: boolean;
}

export interface FsList {
  dir: string;
  parent: string | null;
  entries: { name: string; path: string; is_dir: boolean; size: number | null }[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { "x-vac": "1", ...(body !== undefined ? { "content-type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      message = (await res.text().catch(() => "")) || message;
    }
    throw new ApiError(res.status, message);
  }
  const type = res.headers.get("content-type") || "";
  return (type.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

const q = (params: Record<string, string | number | null | undefined>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();

export const api = {
  appInfo: () => call<{ native: boolean; version: string }>("GET", "/api/app"),
  pick: (kind: "file" | "dir" | "audio", start?: string) =>
    call<{ path: string | null }>("GET", `/api/fs/pick?${q({ kind, start })}`),
  library: () => call<Library>("GET", "/api/library"),
  rescan: () => call<Library>("POST", "/api/library/rescan"),
  openProject: (output_dir: string) => call<Library>("POST", "/api/library/open", { output_dir }),
  probe: (id: string) => call<Probe>("GET", `/api/sources/${id}/probe`),
  addSource: (path: string) => call<Source>("POST", "/api/sources", { path }),
  fsList: (dir: string) => call<FsList>("GET", `/api/fs/list?${q({ dir })}`),
  reveal: (path: string) => call("POST", "/api/reveal", { path }),
  deleteOutput: (name: string) => call<{ moved: string[] }>("POST", `/api/outputs/${encodeURIComponent(name)}/delete`),
  previewName: (source_id: string, start_sec: number, duration_sec: number | null, tag: string | null) =>
    call<{ name: string; exists: boolean }>("GET", `/api/outputs/preview-name?${q({ source_id, start_sec, duration_sec, tag })}`),
  setPick: (plan_path: string, window: string, index: number | null) =>
    call<PlanWindow>("POST", "/api/plans/pick", { plan_path, window, index }),
  deleteCandidates: (plan_path: string, window: string) =>
    call("POST", "/api/plans/candidates/delete", { plan_path, window }),
  cliSchema: () => call<CliOption[]>("GET", "/api/cli/schema"),
  projects: () => call<ProjectSummary[]>("GET", "/api/projects"),
  createProject: (name: string) => call<any>("POST", "/api/projects", { name }),
  getProject: (name: string) => call<any>("GET", `/api/projects/${encodeURIComponent(name)}`),
  saveProject: (name: string, project: object) =>
    call<{ modified: string }>("PUT", `/api/projects/${encodeURIComponent(name)}`, project),
  deleteProject: (name: string) => call("DELETE", `/api/projects/${encodeURIComponent(name)}`),
  addMusic: (name: string, path: string) =>
    call<{ path: string; duration_sec: number | null }>("POST", `/api/projects/${encodeURIComponent(name)}/music`, { path }),
  exportPlan: (name: string) => call<ExportPlan>("GET", `/api/projects/${encodeURIComponent(name)}/plan`),
  thumbs: (source: string) => call<{ interval: number; count: number }>("GET", `/media/thumbs?${q({ source })}`),
  jobs: () => call<Job[]>("GET", "/api/jobs"),
  job: (id: string) => call<Job>("GET", `/api/jobs/${id}`),
  jobLog: (id: string) => call<string>("GET", `/api/jobs/${id}/log`),
  preview: (req: object) => call<{ argv: string[]; display: string }>("POST", "/api/jobs/preview", req),
  submit: (req: object) => call<Job>("POST", "/api/jobs", req),
  cancel: (id: string) => call<Job>("DELETE", `/api/jobs/${id}`),
  retry: (id: string) => call<Job>("POST", `/api/jobs/${id}/retry`),
};

export const media = {
  file: (path: string) => `/media/file?${q({ path })}`,
  outputAudio: (file: string) => `/media/audio?${q({ kind: "output", file })}`,
  originalFor: (file: string) => `/media/audio?${q({ kind: "original_for", file })}`,
  original: (source: string, start: number, duration: number | null) =>
    `/media/audio?${q({ kind: "original", source, start, duration })}`,
};

export async function fetchArrayBuffer(url: string): Promise<ArrayBuffer> {
  const res = await fetch(url);
  if (!res.ok) {
    let message = res.statusText;
    try {
      message = (await res.json()).detail ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return res.arrayBuffer();
}
