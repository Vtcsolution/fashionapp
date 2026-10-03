import asyncio
import logging
import re

import httpx

from ..categorize import categorize
from ..relevance import is_relevant
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


COLORS = {"black", "white", "red", "blue", "green", "yellow", "pink", "purple", "brown", "grey", "gray", "beige",
          "navy", "orange", "gold", "silver", "cream", "tan", "khaki", "burgundy", "olive"}
MAX_PARTS = 4
_SPLIT = r"\s*(?:,|;|\+|&|\bwith\b|\band\b)\s*"  # \b keeps 'handbag', 'sandals', 'within' intact


def split_prompt(text: str) -> list[str]:
    """'red dress with black heels and handbag' -> ['red dress', 'black heels', 'handbag'].

    Each outfit item is searched on its own so every category gets real results. 'and' between two
    colors ('black and white sneakers') is NOT a split point.
    """
    tokens = re.split(_SPLIT, text.strip(), flags=re.I)
    seps = re.findall(_SPLIT, text.strip(), flags=re.I)
    parts: list[str] = []
    for i, tok in enumerate(tokens):
        tok = tok.strip()
        if not tok:
            continue
        prev = parts[-1] if parts else ""
        joined_and = i > 0 and seps[i - 1].strip().lower() == "and"
        if joined_and and prev and prev.split()[-1].lower() in COLORS and tok.split()[0].lower() in COLORS:
            parts[-1] = f"{prev} and {tok}"  # keep 'black and white ...' together
        else:
            parts.append(tok)
    return (parts or [text.strip()])[:MAX_PARTS]


async def search_all(query: str, limit: int = 8, retailers=None) -> tuple[list[RetailerProduct], dict[str, str]]:
    """Search the active retailers in parallel and merge. One failing retailer never affects the other."""
    text, max_price = parse_query(query)
    parts = split_prompt(text)
    per_part = limit if len(parts) == 1 else max(4, limit // 2)
    active = [r for r in (retailers if retailers is not None else ACTIVE) if r.enabled()]
    jobs = [(r, part) for r in active for part in parts]
    results = await asyncio.gather(*(r.search(part, per_part, max_price) for r, part in jobs), return_exceptions=True)

    products: list[RetailerProduct] = []
    seen: set[tuple[str, str]] = set()
    ok: dict[str, int] = {}
    failed: dict[str, list[str]] = {}
    for (r, part), res in zip(jobs, results):
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
        kept = [p for p in res if is_relevant(part, p.name)]  # only products that match what was asked
        res = kept
        ok[r.name] = ok.get(r.name, 0) + len(res)
        for p in res:
            key = (p.retailer, p.product_id)
            if key not in seen:
                seen.add(key)
                p.category = categorize(part)  # the item the user asked for, not a guess from the title
                products.append(p)

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
