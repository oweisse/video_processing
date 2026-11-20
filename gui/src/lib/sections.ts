// Comparing a long cleaned output a section at a time (docs/stitch-and-size-budget-plan.md
// §6): two decoded copies of an hour of sound would take gigabytes.

export const SECTION_SEC = 300;
export const SECTIONED_AFTER = 420; // outputs longer than this are compared in sections
const SNAP = 10;

/** The section start (whole tens of seconds) that shows time t a minute in. */
export function sectionFor(t: number, total: number): number {
  const latest = Math.max(0, total - SECTION_SEC);
  return Math.max(0, Math.min(latest, Math.floor((t - 60) / SNAP) * SNAP));
}
