// Hash routing. Four workspaces (docs/gui-editor-redesign.md §3.2):
//   #/media
//   #/enhance[/<sourceId>]?out=<file>&plan=<planPath>&win=<label>&tab=result|candidates|new|versions
//   #/edit[/<timeline>]
//   #/deliver[/<timeline>]
// Routes from 0.5.0 (#/, #/compare/…, #/candidates/…, #/new, #/jobs) redirect.

import { loadPref, savePref } from "./prefs";

export type Workspace = "media" | "enhance" | "edit" | "deliver";
export const WORKSPACES: { id: Workspace; label: string }[] = [
  { id: "media", label: "Media" },
  { id: "enhance", label: "Enhance" },
  { id: "edit", label: "Edit" },
  { id: "deliver", label: "Deliver" },
];

type Params = Record<string, string | number | boolean | null | undefined>;

export const route = $state({
  parts: [] as string[],
  params: {} as Record<string, string>,
  workspace: "media" as Workspace,
});

/** UI state the router owns because old #/jobs links open it. */
export const shell = $state({ jobsOpen: false });

// Where each workspace was last, so the page bar returns there.
const last: Record<Workspace, string> = {
  media: "#/media",
  enhance: "#/enhance",
  edit: loadPref("route.edit", "#/edit"),
  deliver: loadPref("route.deliver", "#/deliver"),
};

export function href(parts: string[], params?: Params): string {
  let h = "#/" + parts.map(encodeURIComponent).join("/");
  if (params) {
    const qs = new URLSearchParams(
      Object.entries(params)
        .filter(([, v]) => v !== null && v !== undefined && v !== "")
        .map(([k, v]) => [k, String(v)]),
    ).toString();
    if (qs) h += "?" + qs;
  }
  return h;
}

export function go(parts: string[], params?: Params) {
  location.hash = href(parts, params);
}

/** Links into the Enhance workspace, used wherever 0.5.0 linked to Compare, Candidates or New run. */
export const links = {
  output: (file: string, params: Params = {}) => href(["enhance"], { ...params, out: file, tab: "result" }),
  candidates: (plan: string, label: string, params: Params = {}) =>
    href(["enhance"], { ...params, plan, win: label, tab: "candidates" }),
  newRun: (params: Params = {}) => {
    const { source, ...rest } = params;
    return href(source ? ["enhance", String(source)] : ["enhance"], { ...rest, tab: "new" });
  },
  workspace: (ws: Workspace) => last[ws],
};

/** The 0.5.0 route for a hash, rewritten to its workspace route; null when it is current. */
export function redirect(parts: string[], params: Record<string, string>): string | null {
  const [head, a, b] = parts;
  if (head === undefined) return "#/media";
  if (head === "compare" && a) return links.output(a, params);
  if (head === "candidates" && a && b) return links.candidates(a, b, params);
  if (head === "new") return links.newRun(params);
  if (head === "jobs") return null; // handled in parse(): opens the drawer
  if (!WORKSPACES.some((w) => w.id === head)) return "#/media";
  return null;
}

function parse() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, query] = hash.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = Object.fromEntries(new URLSearchParams(query ?? ""));
  if (parts[0] === "jobs") {
    shell.jobsOpen = true;
    location.replace(last[route.workspace] ?? "#/media");
    return;
  }
  const target = redirect(parts, params);
  if (target) {
    location.replace(target);
    return;
  }
  route.parts = parts;
  route.params = params;
  route.workspace = parts[0] as Workspace;
  last[route.workspace] = location.hash;
  if (route.workspace === "edit" || route.workspace === "deliver") savePref(`route.${route.workspace}`, location.hash);
}

window.addEventListener("hashchange", parse);
parse();
