// Pure selection/grouping logic (no React) so it can be unit-tested with `npm test`.
// One product per category. Multi-category selection is supported by the UI today; the current
// try-on backend accepts exactly ONE product, so tryOnReadiness() enforces that limit in one place.

export const CATEGORY_ORDER = ["outerwear", "tops", "dresses", "bottoms", "shoes", "bags", "accessories", "other"];

export const CATEGORY_LABEL: Record<string, string> = {
  outerwear: "Outerwear",
  tops: "Tops",
  dresses: "Dresses",
  bottoms: "Bottoms",
  shoes: "Shoes",
  bags: "Bags",
  accessories: "Accessories",
  other: "Other",
};

type Item = { retailer: string; product_id: string; category: string };
export type Selection<T> = Record<string, T>; // category -> the one selected product

export const keyOf = (p: { retailer: string; product_id: string }) => `${p.retailer}:${p.product_id}`;

const rank = (c: string) => {
  const i = CATEGORY_ORDER.indexOf(c);
  return i === -1 ? CATEGORY_ORDER.length : i;
};

/** Group products by their category, in a stable display order, omitting empty categories. */
export function groupByCategory<T extends Item>(products: T[]): { category: string; items: T[] }[] {
  const map = new Map<string, T[]>();
  for (const p of products) {
    const c = p.category || "other";
    map.set(c, [...(map.get(c) ?? []), p]);
  }
  return [...map.entries()].sort((a, b) => rank(a[0]) - rank(b[0])).map(([category, items]) => ({ category, items }));
}

/** Select a product for its category (replacing any previous pick there); clicking the pick again deselects. */
export function toggleSelect<T extends Item>(sel: Selection<T>, p: T): Selection<T> {
  const cat = p.category || "other";
  const next = { ...sel };
  if (next[cat] && keyOf(next[cat]) === keyOf(p)) delete next[cat];
  else next[cat] = p;
  return next;
}

export function removeCategory<T>(sel: Selection<T>, category: string): Selection<T> {
  const next = { ...sel };
  delete next[category];
  return next;
}

export function selectedList<T>(sel: Selection<T>): T[] {
  return Object.keys(sel).sort((a, b) => rank(a) - rank(b)).map((c) => sel[c]);
}

/**
 * The product that will actually be tried on. Current MVP limit: exactly ONE product per try-on.
 * With one item selected it is that item; with several, the user must mark one ("Try this one").
 */
export function tryOnTarget<T extends Item>(sel: Selection<T>, targetKey: string | null): T | null {
  const list = selectedList(sel);
  if (list.length === 1) return list[0];
  return list.find((p) => keyOf(p) === targetKey) ?? null;
}

export function tryOnReadiness(hasPerson: boolean, selectedCount: number, hasTarget: boolean): { ok: boolean; message: string } {
  if (!hasPerson) return { ok: false, message: "Upload your photo first." };
  if (selectedCount === 0) return { ok: false, message: "Pick a product to try on." };
  if (!hasTarget)
    return {
      ok: false,
      message: `You picked ${selectedCount} items. Choose the ONE to try on now with “Try this one” — trying on all items together is coming soon.`,
    };
  return { ok: true, message: "" };
}
