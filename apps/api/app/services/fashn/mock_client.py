import hashlib
import io

from PIL import Image, ImageDraw, ImageFont

from ...config import settings
from .builder import build_request
from .result import FashnResult


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


class MockFashnClient:
    """Simulation for orchestration tests ONLY. Never touches the network. Deterministic.

    The image is NOT a try-on: it is the base image stamped with a large "MOCK / DEMO" banner and a per-product
    code (so every step looks different). Nothing is applied, nothing can be verified from it.
    """

    provider = "mock"

    async def try_on(self, person, person_ext, product, product_ext, on_prediction_id=None):
        build_request(settings.fashn_model, person, person_ext, product, product_ext)  # exercise the builder
        code = hashlib.sha256(person + product).hexdigest()[:8]
        pid = "mock-" + code
        if on_prediction_id:
            on_prediction_id(pid)
        im = Image.open(io.BytesIO(person)).convert("RGB")
        d = ImageDraw.Draw(im)
        big, small = _font(max(18, im.width // 14)), _font(max(12, im.width // 28))
        d.rectangle([0, 0, im.width, big.size * 2 + 12 if hasattr(big, "size") else 60], fill=(200, 30, 30))
        d.text((10, 6), "MOCK / DEMO", fill=(255, 255, 255), font=big)
        d.text((10, (big.size if hasattr(big, "size") else 24) + 8), "simulated - NOT a real try-on", fill=(255, 255, 255), font=small)
        y = im.height - (small.size if hasattr(small, "size") else 16) * 2 - 12
        d.rectangle([0, y - 6, im.width, im.height], fill=(200, 30, 30))
        d.text((10, y), f"MOCK step code {code} - nothing applied or verified", fill=(255, 255, 255), font=small)
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return FashnResult(pid, buf.getvalue())
