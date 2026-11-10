// Hash routing: #/compare/<output>, #/candidates/<plan>/<window>, #/new?source=…, #/jobs.

export const route = $state({ parts: [] as string[], params: {} as Record<string, string> });

function parse() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, query] = hash.split("?");
  route.parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  route.params = Object.fromEntries(new URLSearchParams(query ?? ""));
}

window.addEventListener("hashchange", parse);
parse();

export function href(parts: string[], params?: Record<string, string | number | boolean | null | undefined>): string {
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

export function go(parts: string[], params?: Record<string, string | number | boolean | null | undefined>) {
  location.hash = href(parts, params);
}
