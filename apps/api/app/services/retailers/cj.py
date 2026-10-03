import httpx

from ...config import settings
from .base import Retailer, RetailerProduct

QUERY = """
query($cid: String!, $kw: [String!], $limit: Int) {
  products(companyId: $cid, keywords: $kw, limit: $limit) {
    resultList { id title link imageLink advertiserName price { amount currency } }
  }
}
"""


class CJ(Retailer):
    """CJ Affiliate Product Feed (GraphQL). Needs the publisher Company ID (CID) in CJ_COMPANY_ID."""

    name = "cj"

    def enabled(self):
        return settings.configured()["cj"]

    async def search(self, query, limit=10):
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(
                "https://ads.api.cj.com/query",
                json={"query": QUERY, "variables": {"cid": settings.cj_company_id, "kw": query.split(), "limit": limit}},
                headers={"Authorization": f"Bearer {settings.cj_api_token}"},
            )
            r.raise_for_status()
        j = r.json()
        if j.get("errors"):
            raise RuntimeError(f"cj graphql error: {str(j['errors'][0].get('message'))[:120]}")
        out = []
        for p in (j.get("data", {}).get("products") or {}).get("resultList", []):
            if not p.get("imageLink"):
                continue
            price = p.get("price") or {}
            out.append(RetailerProduct(
                retailer=self.name, product_id=str(p["id"]), name=p.get("title", ""),
                price=str(price.get("amount")) if price.get("amount") is not None else None,
                currency=price.get("currency"), url=p.get("link", ""),
                image_url=p["imageLink"], image_urls=[p["imageLink"]],
            ))
        return out
