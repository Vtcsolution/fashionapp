import hashlib
import hmac
import time

import httpx

from ...config import settings
from .base import Retailer, RetailerProduct


def sign(params: dict, secret: str) -> str:
    raw = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest().upper()


class AliExpress(Retailer):
    name = "aliexpress"

    def enabled(self):
        return settings.configured()["aliexpress"]

    async def search(self, query, limit=10):
        params = {
            "app_key": settings.ali_express_app_key,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "method": "aliexpress.affiliate.product.query",
            "keywords": query,
            "tracking_id": settings.ali_express_tracking_id,
            "page_size": str(limit),
            "target_currency": "USD",
            "target_language": "EN",
            "ship_to_country": "US",
        }
        params["sign"] = sign(params, settings.ali_express_secret_api)
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.get("https://api-sg.aliexpress.com/sync", params=params)
            r.raise_for_status()
        j = r.json()
        if "error_response" in j:
            raise RuntimeError(f"aliexpress error: {j['error_response'].get('msg')}")
        res = j.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {}).get("result", {})
        out = []
        for p in res.get("products", {}).get("product", []):
            imgs = [p.get("product_main_image_url")] + (p.get("product_small_image_urls", {}) or {}).get("string", [])
            imgs = [("https:" + u if u.startswith("//") else u) for u in imgs if u]
            if not imgs:
                continue
            out.append(RetailerProduct(
                retailer=self.name, product_id=str(p["product_id"]), name=p.get("product_title", ""),
                price=str(p.get("target_sale_price", "")) or None, currency=p.get("target_sale_price_currency", "USD"),
                url=p.get("promotion_link") or p.get("product_detail_url", ""), image_url=imgs[0], image_urls=imgs,
            ))
        return out
