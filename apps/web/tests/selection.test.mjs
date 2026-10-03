import assert from "node:assert/strict";
import test from "node:test";
import {
  groupByCategory, keyOf, removeCategory, selectedList, toggleSelect, tryOnReadiness, tryOnTarget,
} from "../lib/selection.ts";

const p = (retailer, id, category) => ({ retailer, product_id: id, category, name: `${category}-${id}` });
const products = [
  p("ebay", "1", "shoes"), p("aliexpress", "2", "outerwear"), p("ebay", "3", "shoes"),
  p("ebay", "4", "bags"), p("ebay", "5", "other"), p("aliexpress", "6", "outerwear"),
];

test("groups by category in display order and omits empty categories", () => {
  const g = groupByCategory(products);
  assert.deepEqual(g.map((x) => x.category), ["outerwear", "shoes", "bags", "other"]);
  assert.equal(g[0].items.length, 2);
  assert.equal(g.find((x) => x.category === "tops"), undefined);
});

test("unknown categories are kept (never forced) and sorted last", () => {
  const g = groupByCategory([p("ebay", "1", "hats"), p("ebay", "2", "tops")]);
  assert.deepEqual(g.map((x) => x.category), ["tops", "hats"]);
});

test("one product per category; picking another in the same category replaces it", () => {
  let s = toggleSelect({}, products[0]);
  s = toggleSelect(s, products[2]);
  assert.equal(Object.keys(s).length, 1);
  assert.equal(keyOf(s.shoes), "ebay:3");
});

test("multiple categories can be selected at once", () => {
  let s = {};
  for (const i of [0, 1, 3]) s = toggleSelect(s, products[i]);
  assert.deepEqual(selectedList(s).map(keyOf), ["aliexpress:2", "ebay:1", "ebay:4"]); // outerwear, shoes, bags
});

test("clicking the selected product again deselects it; removeCategory removes", () => {
  let s = toggleSelect({}, products[0]);
  assert.deepEqual(toggleSelect(s, products[0]), {});
  s = toggleSelect(toggleSelect(s, products[1]), products[3]);
  assert.deepEqual(Object.keys(removeCategory(s, "shoes")).sort(), ["bags", "outerwear"]);
  assert.equal(Object.keys(s).length, 3); // original not mutated
});

test("one selected item is the try-on target automatically", () => {
  const s = toggleSelect({}, products[0]);
  assert.equal(keyOf(tryOnTarget(s, null)), "ebay:1");
});

test("several selected items: no target until the user marks exactly one", () => {
  let s = {};
  for (const i of [0, 1, 3]) s = toggleSelect(s, products[i]);
  assert.equal(tryOnTarget(s, null), null);
  assert.equal(keyOf(tryOnTarget(s, "ebay:4")), "ebay:4");
  assert.equal(tryOnTarget(s, "ebay:999"), null); // stale/removed target is ignored
  assert.equal(tryOnTarget(removeCategory(s, "bags"), "ebay:4"), null); // removing the target clears it
});

test("try-on readiness: one person + exactly one chosen product", () => {
  assert.equal(tryOnReadiness(false, 1, true).ok, false);
  assert.equal(tryOnReadiness(true, 0, false).ok, false);
  assert.equal(tryOnReadiness(true, 1, true).ok, true);
  const multi = tryOnReadiness(true, 4, false);
  assert.equal(multi.ok, false);
  assert.match(multi.message, /Try this one/);
  assert.equal(tryOnReadiness(true, 4, true).ok, true); // 4 selected, 1 chosen -> only that one is sent
});
