import base64
import re
import time

import httpx

from ...config import settings
from .base import Retailer, RetailerProduct

_token: dict = {"value": None, "exp": 0.0}


def upscale_ebay_image(url: str) -> str:
    """eBay image URLs embed a size (s-l225.jpg); s-l1600 is the largest variant."""
    return re.sub(r"s-l\d+(\.\w+)$", r"s-l1600\1", url)


class Ebay(Retailer):
    name = "ebay"

    def enabled(self):
        return settings.configured()["ebay"]

    async def _token(self, c: httpx.AsyncClient) -> str:
        if _token["value"] and _token["exp"] > time.time() + 60:
            return _token["value"]
        basic = base64.b64encode(f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode()).decode()
        r = await c.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        )
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], exp=time.time() + int(j.get("expires_in", 7200)))
        return _token["value"]

    async def search(self, query, limit=10, max_price=None):
        async with httpx.AsyncClient(timeout=20) as c:
            tok = await self._token(c)
            r = await c.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                params={"q": query, "limit": limit, "category_ids": "11450",  # Clothing, Shoes & Accessories
                        **({"filter": f"price:[..{max_price:g}],priceCurrency:USD"} if max_price else {})},
                headers={"Authorization": f"Bearer {tok}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
            )
            r.raise_for_status()
        out = []
        for it in r.json().get("itemSummaries", []):
            imgs = [it.get("image", {}).get("imageUrl")] + [i.get("imageUrl") for i in it.get("additionalImages", [])]
            imgs = [upscale_ebay_image(u) for u in imgs if u]
            if not imgs:
                continue
            price = it.get("price", {})
            out.append(RetailerProduct(
                retailer=self.name, product_id=it["itemId"], name=it.get("title", ""),
                price=price.get("value"), currency=price.get("currency"),
                url=it.get("itemWebUrl", ""), image_url=imgs[0], image_urls=imgs,
            ))
        return out
