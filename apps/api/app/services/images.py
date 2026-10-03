import io

import httpx
from PIL import Image

from ..config import settings


class ImageRejected(Exception):
    pass


def validate_image_bytes(data: bytes, min_side: int | None = None) -> tuple[int, int, str]:
    """Return (width, height, ext). Raises ImageRejected for corrupt, unsupported or too-small images."""
    min_side = min_side or settings.min_product_image_side
    try:
        Image.open(io.BytesIO(data)).verify()
        im = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ImageRejected(f"not a valid image: {type(e).__name__}") from e
    w, h = im.size
    if min(w, h) < min_side:
        raise ImageRejected(f"image too small ({w}x{h}); minimum short side is {min_side}px")
    ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get((im.format or "").upper())
    if not ext:
        raise ImageRejected(f"unsupported image format {im.format}")
    return w, h, ext


async def download_best_image(candidates: list[str]) -> tuple[str, bytes, int, int, str]:
    """Try candidate URLs in order; return the first that validates: (url, bytes, w, h, ext)."""
    errors = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as c:
        for url in candidates:
            try:
                r = await c.get(url)
                r.raise_for_status()
                w, h, ext = validate_image_bytes(r.content)
                return url, r.content, w, h, ext
            except Exception as e:
                errors.append(f"{url}: {e}")
    raise ImageRejected("no usable product image: " + " | ".join(errors)[:500])
