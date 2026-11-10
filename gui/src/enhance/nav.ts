// URLs for the Enhance navigation: all videos › a video › one of its pieces.
import type { Source } from "../lib/api";
import { href } from "../lib/router.svelte";
import type { Piece } from "../media/pieces";

export const allVideos = () => href(["enhance"]);
export const videoHref = (s: Pick<Source, "id">) => href(["enhance", s.id]);
export const newCleanHref = (s: Pick<Source, "id" | "source_kind">, params: Record<string, string | number> = {}) =>
  href(["enhance", s.id], { kind: s.source_kind ?? "", ...params, tab: "new" });

export function pieceHref(s: Pick<Source, "id">, p: Piece): string {
  return p.kind === "output"
    ? href(["enhance", s.id], { out: p.output.file, tab: "result" })
    : href(["enhance", s.id], { plan: p.plan.path, win: p.window.label, tab: "candidates" });
}
