import assert from "node:assert/strict";
import test from "node:test";
import {
  groupByCategory, keyOf, removeCategory, selectedList, toggleSelect, tryOnQueue, tryOnReadiness,
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

test("try-on queue: base garments first, layers and accessories after", () => {
  let s = {};
  for (const [r, id, c] of [["ebay", "1", "bags"], ["ebay", "2", "shoes"], ["ebay", "3", "outerwear"], ["ebay", "4", "tops"], ["ebay", "5", "bottoms"]])
    s = toggleSelect(s, p(r, id, c));
  assert.deepEqual(tryOnQueue(s).map((x) => x.category), ["tops", "bottoms", "outerwear", "shoes", "bags"]);
  assert.equal(tryOnQueue({}).length, 0);
});

test("readiness (mock): needs a person and at least one product, any number allowed", () => {
  const q = (n) => Array.from({ length: n }, (_, i) => ({ retailer: i % 2 ? "aliexpress" : "ebay" }));
  assert.equal(tryOnReadiness(false, q(1), null).ok, false);
  assert.equal(tryOnReadiness(true, q(0), null).ok, false);
  assert.equal(tryOnReadiness(true, q(1), null).ok, true);
  assert.equal(tryOnReadiness(true, q(6), null).ok, true);
});

test("readiness (live): whole queue must fit the credit budget and be eBay-only", () => {
  const ebay = (n) => Array.from({ length: n }, () => ({ retailer: "ebay" }));
  const credits = (spent, cap) => ({ per_generation: 2, cap, spent });
  assert.equal(tryOnReadiness(true, ebay(1), credits(0, 2)).ok, true);
  const tooMany = tryOnReadiness(true, ebay(3), credits(0, 2));
  assert.equal(tooMany.ok, false);
  assert.match(tooMany.message, /allows 1 more generation /);
  assert.equal(tryOnReadiness(true, ebay(3), credits(0, 6)).ok, true);
  assert.match(tryOnReadiness(true, ebay(1), credits(2, 2)).message, /used up/);
  assert.match(tryOnReadiness(true, [{ retailer: "aliexpress" }], credits(0, 6)).message, /eBay products only/);
});
