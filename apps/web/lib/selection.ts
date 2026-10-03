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

// Order items are applied in: base garments first, layers and accessories on top of the previous result.
export const TRYON_ORDER = ["dresses", "tops", "bottoms", "outerwear", "shoes", "bags", "accessories", "other"];

const tryRank = (c: string) => {
  const i = TRYON_ORDER.indexOf(c);
  return i === -1 ? TRYON_ORDER.length : i;
};

/** The selected products in the order they will be tried on, one generation each. */
export function tryOnQueue<T extends Item>(sel: Selection<T>): T[] {
  return Object.values(sel).sort((a, b) => tryRank(a.category) - tryRank(b.category));
}

export type CreditInfo = { per_generation: number; cap: number; spent: number };

/**
 * Can the queue run? Mock mode is free. In live mode every step is a paid FASHN generation, so the
 * whole queue must fit in the remaining credit budget (otherwise it would stop half-way after paying),
 * and the backend guard currently only allows eBay products.
 */
export function tryOnReadiness(
  hasPerson: boolean,
  queue: { retailer: string }[],
  live: CreditInfo | null,
): { ok: boolean; message: string } {
  if (!hasPerson) return { ok: false, message: "Upload your photo first." };
  if (queue.length === 0) return { ok: false, message: "Pick at least one product to try on." };
  if (live) {
    if (queue.some((p) => p.retailer !== "ebay"))
      return { ok: false, message: "Live mode currently allows eBay products only. Remove the non-eBay items." };
    const remaining = Math.max(0, live.cap - live.spent);
    const allowed = Math.floor(remaining / live.per_generation);
    if (queue.length > allowed)
      return {
        ok: false,
        message:
          allowed === 0
            ? "The live credit budget is used up."
            : `The live credit budget allows ${allowed} more generation${allowed === 1 ? "" : "s"} (${remaining} credits left) but you picked ${queue.length}. Your selection is kept: run it in demo mode, or authorize more credits for a live multi-product run.`,
      };
  }
  return { ok: true, message: "" };
}
