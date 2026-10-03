import asyncio
import io
from typing import Protocol

import httpx
from PIL import Image, ImageDraw

from ...config import settings
from .builder import build_request


class FashnResult:
    def __init__(self, fashn_job_id: str, image_bytes: bytes):
        self.fashn_job_id = fashn_job_id
        self.image_bytes = image_bytes  # exactly as returned, never post-processed


class FashnClient(Protocol):
    provider: str

    async def try_on(self, person: bytes, person_ext: str, product: bytes, product_ext: str) -> FashnResult: ...


class MockFashnClient:
    """Zero-cost stand-in. Returns the person photo with a clear MOCK banner; no network."""

    provider = "mock"

    async def try_on(self, person, person_ext, product, product_ext):
        build_request(settings.fashn_model, person, person_ext, product, product_ext)  # exercise the builder
        im = Image.open(io.BytesIO(person)).convert("RGB")
        ImageDraw.Draw(im).rectangle([0, 0, im.width, 28], fill=(200, 30, 30))
        ImageDraw.Draw(im).text((8, 8), "MOCK RESULT - NOT A REAL TRY-ON", fill=(255, 255, 255))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return FashnResult("mock-job", buf.getvalue())


class LiveFashnClient:
    """Real FASHN. ONE submit, polling only. No retries. Caller must pass the spend guard first."""

    provider = "fashn"
    base = "https://api.fashn.ai/v1"

    async def try_on(self, person, person_ext, product, product_ext):
        payload = build_request(settings.fashn_model, person, person_ext, product, product_ext)
        headers = {"Authorization": f"Bearer {settings.fashn_api_key}"}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{self.base}/run", json=payload, headers=headers)
            r.raise_for_status()
            job_id = r.json()["id"]
            for _ in range(60):  # poll status only (free); never resubmit
                await asyncio.sleep(3)
                s = await c.get(f"{self.base}/status/{job_id}", headers=headers)
                s.raise_for_status()
                j = s.json()
                if j["status"] == "completed":
                    out = j["output"][0]
                    img = await c.get(out)
                    img.raise_for_status()
                    return FashnResult(job_id, img.content)
                if j["status"] == "failed":
                    raise RuntimeError(f"FASHN job failed: {str(j.get('error'))[:200]}")
        raise TimeoutError(f"FASHN job {job_id} did not finish in time")


def get_client(live: bool) -> FashnClient:
    return LiveFashnClient() if live else MockFashnClient()
