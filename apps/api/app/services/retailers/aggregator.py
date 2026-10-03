import asyncio
import logging
import re

import httpx

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


async def search_all(query: str, limit: int = 8, retailers=None) -> tuple[list[RetailerProduct], dict[str, str]]:
    """Search the active retailers in parallel and merge. One failing retailer never affects the other."""
    text, max_price = parse_query(query)
    active = [r for r in (retailers if retailers is not None else ACTIVE) if r.enabled()]
    results = await asyncio.gather(*(r.search(text, limit, max_price) for r in active), return_exceptions=True)
    products: list[RetailerProduct] = []
    status: dict[str, str] = {}
    for r, res in zip(active, results):
        if isinstance(res, Exception):
            if isinstance(res, httpx.HTTPStatusError):  # its message embeds the URL (query may hold credentials)
                msg = f"HTTP {res.response.status_code}: {res.response.text[:120]}"
            else:
                msg = str(res)[:120]
            status[r.name] = f"error: {type(res).__name__}: {msg}"
            log.warning("retailer %s failed: %s", r.name, type(res).__name__)
        else:
            if max_price is not None:  # defensive: enforce the cap even if a retailer ignored the filter
                res = [p for p in res if _price_ok(p, max_price)]
            status[r.name] = f"ok ({len(res)})"
            products.extend(res)
    products = _interleave(products)
    return products, status


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
