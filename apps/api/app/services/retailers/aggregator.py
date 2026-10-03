import asyncio
import logging
import re

import httpx

from ..prompt_items import PromptItem, fallback_items, split_prompt  # noqa: F401  (split_prompt re-exported)
from ..relevance import color_rank, is_relevant
from .aliexpress import AliExpress
from .base import Retailer, RetailerProduct
from .cj import CJ
from .ebay import Ebay
from .rakuten import Rakuten

log = logging.getLogger(__name__)

# Active product sources for the current MVP: ONLY eBay + AliExpress.
ACTIVE: list[Retailer] = [Ebay(), AliExpress()]
# Kept for future integration; deliberately NOT searched and NOT required to be configured.
FUTURE: list[Retailer] = [CJ(), Rakuten()]

_PRICE = re.compile(r"\b(?:under|below|less than|max|up to)\s*\$?\s*(\d+(?:\.\d+)?)", re.I)


def parse_query(q: str) -> tuple[str, float | None]:
    """'black sneakers under $100' -> ('black sneakers', 100.0). Price becomes a real retailer filter."""
    m = _PRICE.search(q)
    if not m:
        return q.strip(), None
    clean = (q[: m.start()] + q[m.end():]).strip(" ,-")
    return (clean or q.strip()), float(m.group(1))


async def search_all(query: str, limit: int = 8, retailers=None, items: list[PromptItem] | None = None) -> tuple[list[RetailerProduct], dict[str, str]]:
    """Search the active retailers in parallel and merge. One failing retailer never affects the other."""
    text, max_price = parse_query(query)
    items = items or fallback_items(text)  # items from the OpenAI parser when available
    per_part = limit if len(items) == 1 else max(4, limit // 2)
    active = [r for r in (retailers if retailers is not None else ACTIVE) if r.enabled()]
    jobs = [(r, it) for r in active for it in items]
    results = await asyncio.gather(*(r.search(it.query, per_part, max_price) for r, it in jobs), return_exceptions=True)

    ranked: list[tuple[int, int, RetailerProduct]] = []
    seen: set[tuple[str, str]] = set()
    ok: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    for (r, it), res in zip(jobs, results):
        if isinstance(res, Exception):
            if isinstance(res, httpx.HTTPStatusError):  # its message embeds the URL (query may hold credentials)
                msg = f"HTTP {res.response.status_code}: {res.response.text[:120]}"
            else:
                msg = str(res)[:120]
            failed.setdefault(r.name, []).append(f"{type(res).__name__}: {msg}")
            log.warning("retailer %s failed: %s", r.name, type(res).__name__)
            continue
        if max_price is not None:  # defensive: enforce the cap even if a retailer ignored the filter
            res = [p for p in res if _price_ok(p, max_price)]
        kept = [p for p in res if is_relevant(it.query, p.name)]  # only products that match what was asked
        res = kept
        ok[r.name] = ok.get(r.name, 0) + len(res)
        for p in res:
            key = (p.retailer, p.product_id)
            if key not in seen:
                seen.add(key)
                p.category = it.category  # the item the user asked for, not a guess from the title
                ranked.append((items.index(it), color_rank(it.query, p.name), p))
    ranked.sort(key=lambda t: (t[0], t[1]))  # stable: per item, requested-color matches first
    products = [p for _, _, p in ranked]

    status: dict[str, str] = {}
    for r in active:
        if r.name in ok:
            extra = f", {len(failed[r.name])} sub-search(es) failed" if r.name in failed else ""
            status[r.name] = f"ok ({ok[r.name]}{extra})"
        else:
            status[r.name] = f"error: {failed[r.name][0]}"
    return _interleave(products), status


def _price_ok(p: RetailerProduct, cap: float) -> bool:
    try:
        return float(p.price) <= cap
    except (TypeError, ValueError):
        return False


def _interleave(products: list[RetailerProduct]) -> list[RetailerProduct]:
    """Alternate retailers so neither source buries the other in the combined list."""
    by: dict[str, list[RetailerProduct]] = {}
    for p in products:
        by.setdefault(p.retailer, []).append(p)
    out: list[RetailerProduct] = []
    while any(by.values()):
        for lst in by.values():
            if lst:
                out.append(lst.pop(0))
    return out
