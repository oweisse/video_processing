// Selecting several videos in a grid: click picks one, ⌘-click toggles, ⇧-click picks the
// range from the anchor, in the order shown.

export interface Selection {
  ids: string[];
  anchor: string | null;
}

export function select(sel: Selection, id: string, order: string[], mods: { toggle?: boolean; range?: boolean } = {}): Selection {
  if (mods.range && sel.anchor && order.includes(sel.anchor)) {
    const a = order.indexOf(sel.anchor), b = order.indexOf(id);
    const [lo, hi] = a < b ? [a, b] : [b, a];
    return { ids: order.slice(lo, hi + 1), anchor: sel.anchor };
  }
  if (mods.toggle) {
    const ids = sel.ids.includes(id) ? sel.ids.filter((x) => x !== id) : [...sel.ids, id];
    return { ids: order.filter((x) => ids.includes(x)), anchor: id };
  }
  return { ids: [id], anchor: id };
}
