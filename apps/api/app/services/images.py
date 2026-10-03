import io
from dataclasses import dataclass

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


# ---------------------------------------------------------------------------------------------------------
# FASHN input formats. The docs name JPEG and PNG only (data URIs), and do not confirm WebP, so anything else
# is converted losslessly to PNG before it is sent. The original is always kept; both hashes are recorded.
# ---------------------------------------------------------------------------------------------------------
FASHN_NATIVE = {"jpg", "png"}
FASHN_MAX_BYTES = 30 * 1024 * 1024  # documented per-image limit


@dataclass
class PreparedImage:
    sent_bytes: bytes  # exactly what is sent to FASHN
    sent_ext: str
    original_bytes: bytes  # the retailer/user file as downloaded, never modified
    original_ext: str
    converted: bool


def _normalized(im: Image.Image) -> Image.Image:
    """Same pixels in a mode PNG can store. No resize, crop or color adjustment."""
    if im.mode in ("RGB", "RGBA", "L", "LA"):
        return im
    if im.mode == "P":
        return im.convert("RGBA" if "transparency" in im.info else "RGB")
    return im.convert("RGB")  # e.g. CMYK


def to_png_lossless(data: bytes) -> bytes:
    """Decode (first frame only) and re-encode as PNG; then PROVE the decoded pixels are identical."""
    src = Image.open(io.BytesIO(data))
    src.load()
    px = _normalized(src)
    buf = io.BytesIO()
    px.save(buf, "PNG", icc_profile=src.info.get("icc_profile"))
    out = buf.getvalue()
    back = Image.open(io.BytesIO(out))
    back.load()
    if back.size != px.size or back.mode != px.mode or back.tobytes() != px.tobytes():
        raise ImageRejected("lossless conversion could not be verified; refusing to send a possibly altered image")
    return out


def prepare_for_fashn(data: bytes, ext: str) -> PreparedImage:
    """JPEG/PNG pass through byte for byte. Anything else (WebP, ...) -> lossless PNG, original kept."""
    if ext in FASHN_NATIVE:
        sent, sent_ext, converted = data, ext, False
    else:
        sent, sent_ext, converted = to_png_lossless(data), "png", True
    if len(sent) > FASHN_MAX_BYTES:
        raise ImageRejected("image exceeds FASHN's 30 MiB limit; it is not resized automatically")
    return PreparedImage(sent, sent_ext, data, ext, converted)
