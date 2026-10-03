import hashlib
import io

from PIL import Image, ImageDraw

from ...config import settings
from .builder import build_request
from .result import FashnResult


class MockFashnClient:
    """Zero-cost stand-in. Never touches the network. Deterministic: same inputs -> same output."""

    provider = "mock"

    async def try_on(self, person, person_ext, product, product_ext, on_prediction_id=None):
        build_request(settings.fashn_model, person, person_ext, product, product_ext)  # exercise the builder
        pid = "mock-" + hashlib.sha256(person + product).hexdigest()[:12]
        if on_prediction_id:
            on_prediction_id(pid)
        im = Image.open(io.BytesIO(person)).convert("RGB")
        ImageDraw.Draw(im).rectangle([0, 0, im.width, 28], fill=(200, 30, 30))
        ImageDraw.Draw(im).text((8, 8), "MOCK RESULT - NOT A REAL TRY-ON", fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return FashnResult(pid, buf.getvalue())
