"""Free smoke test: real retailer search only (no FASHN, no OpenAI/Gemini). Prints no secrets."""
import asyncio
import sys

from app.services.images import ImageRejected, download_best_image
from app.services.retailers.aggregator import ACTIVE, search_all


async def main(q: str):
    for r in ACTIVE:
        prods, status = await search_all(q, 5, retailers=[r])
        print(f"[{r.name}] enabled={r.enabled()} -> {status.get(r.name, 'skipped (not configured)')}")
        for p in prods[:2]:
            print(f"    {p.name[:60]} | {p.price} {p.currency} | {p.image_url[:90]}")
        if prods:
            try:
                url, data, w, h, ext = await download_best_image([prods[0].image_url, *prods[0].image_urls])
                print(f"    image OK {w}x{h} {ext}")
            except ImageRejected as e:
                print(f"    image REJECTED: {str(e)[:160]}")


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "blue floral women's dress"))
