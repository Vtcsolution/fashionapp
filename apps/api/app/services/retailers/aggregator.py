import asyncio
import logging

import httpx

from .aliexpress import AliExpress
from .base import RetailerProduct
from .cj import CJ
from .ebay import Ebay
from .rakuten import Rakuten

log = logging.getLogger(__name__)
ALL = [Ebay(), AliExpress(), CJ(), Rakuten()]


async def search_all(query: str, limit: int = 8, retailers=None) -> tuple[list[RetailerProduct], dict[str, str]]:
    """Search every enabled retailer in parallel. One failing retailer never breaks the others."""
    active = [r for r in (retailers or ALL) if r.enabled()]
    results = await asyncio.gather(*(r.search(query, limit) for r in active), return_exceptions=True)
    products: list[RetailerProduct] = []
    status: dict[str, str] = {}
    for r, res in zip(active, results):
        if isinstance(res, Exception):
            # exception type + short message only; never request headers
            if isinstance(res, httpx.HTTPStatusError):  # its message embeds the URL (query may hold credentials)
                msg = f"HTTP {res.response.status_code}: {res.response.text[:120]}"
            else:
                msg = str(res)[:120]
            status[r.name] = f"error: {type(res).__name__}: {msg}"
            log.warning("retailer %s failed: %s", r.name, type(res).__name__)
        else:
            status[r.name] = f"ok ({len(res)})"
            products.extend(res)
    return products, status
