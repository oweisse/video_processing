// Size-budget helpers for the export settings (docs/stitch-and-size-budget-plan.md §10–§11).
// 1 GB = 10^9 bytes, as in sizing.py.

export type Budget = { per_hour_gb?: number; total_gb?: number } | null | undefined;
export type BudgetUnit = "per_hour" | "total";

const GB = 1e9;
const STANDARD = [2160, 1440, 1080, 720, 540, 480];

/** The file size a budget allows for a timeline of `seconds`. */
export function budgetBytes(budget: Budget, seconds: number): number | null {
  if (!budget) return null;
  if (budget.total_gb) return budget.total_gb * GB;
  if (budget.per_hour_gb) return (budget.per_hour_gb * GB * seconds) / 3600;
  return null;
}

export function budgetUnit(budget: Budget): BudgetUnit {
  return budget?.total_gb ? "total" : "per_hour";
}

export function budgetValue(budget: Budget): number {
  return budget?.total_gb ?? budget?.per_hour_gb ?? 2;
}

/** A budget from the form's number and unit; null when the number is not positive. */
export function makeBudget(value: number, unit: BudgetUnit): Budget {
  if (!(value > 0)) return null;
  return unit === "total" ? { total_gb: value } : { per_hour_gb: value };
}

/** Heights the picture can be scaled down to: the standard ones below the canvas. */
export function heightChoices(canvasHeight: number): number[] {
  return STANDARD.filter((h) => h < canvasHeight);
}

export function heightName(h: number): string {
  return h >= 2160 ? "4K" : `${h}p`;
}

/** Bytes as people say them: 2.67 GB, 930 MB. */
export function sizeWords(n: number): string {
  return n >= GB ? `${(n / GB).toFixed(2)} GB` : `${Math.round(n / 1e6)} MB`;
}

/** One sentence for how the export will be made. */
export function modeSentence(mode: "copy" | "quality" | "size", budget: number): string {
  if (mode === "copy") return "Joined without re-encoding.";
  if (mode === "quality") return "Encoded at top quality: smaller than your budget.";
  return `Encoded to fit ${sizeWords(budget)}.`;
}

/** Minutes as people say them: 45 min, 3 h 24 min. */
export function minutesWords(m: number): string {
  const total = Math.max(1, Math.round(m));
  const h = Math.floor(total / 60);
  return h ? `${h} h${total % 60 ? ` ${total % 60} min` : ""}` : `${total} min`;
}

/** Whether one sample is noticeably weaker than the rest (as sizing.words says). */
export function weak(e: { metric: string | null; quality_mean: number | null; quality_low: number | null }): boolean {
  if (e.quality_mean == null || e.quality_low == null) return false;
  return e.quality_mean - e.quality_low > (e.metric === "ssim" ? 0.02 : 10);
}
