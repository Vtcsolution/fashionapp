import base64
import time
import xml.etree.ElementTree as ET

import httpx

from ...config import settings
from .base import Retailer, RetailerProduct

_token: dict = {"value": None, "exp": 0.0}


class Rakuten(Retailer):
    name = "rakuten"

    def enabled(self):
        return settings.configured()["rakuten"]

    async def _token(self, c: httpx.AsyncClient) -> str:
        if _token["value"] and _token["exp"] > time.time() + 60:
            return _token["value"]
        basic = base64.b64encode(f"{settings.rakuten_client_id}:{settings.rakuten_client_secret}".encode()).decode()
        r = await c.post(
            "https://api.linksynergy.com/token",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": settings.rakuten_account_id},
        )
        r.raise_for_status()
        j = r.json()
        _token.update(value=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)))
        return _token["value"]

    async def search(self, query, limit=10):
        async with httpx.AsyncClient(timeout=25) as c:
            tok = await self._token(c)
            r = await c.get(
                "https://api.linksynergy.com/productsearch/1.0",
                params={"keyword": query, "max": limit},
                headers={"Authorization": f"Bearer {tok}"},
            )
            r.raise_for_status()
        root = ET.fromstring(r.text)
        out = []
        for p in root.iter("item"):
            def g(tag):
                return (p.findtext(tag) or "").strip()

            if not g("imageurl"):
                continue
            price = p.find("price")
            out.append(RetailerProduct(
                retailer=self.name, product_id=g("sku") or g("upccode") or g("linkid"), name=g("productname"),
                price=(price.text.strip() if price is not None and price.text else None),
                currency=(price.get("currency") if price is not None else None),
                url=g("linkurl"), affiliate_url=g("linkurl"), image_url=g("imageurl"), image_urls=[g("imageurl")],
            ))
        return out
