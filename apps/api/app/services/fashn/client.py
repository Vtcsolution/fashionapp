import asyncio

import httpx

from ...config import settings
from .builder import build_request
from .guard import live_enabled
from .mock_client import MockFashnClient
from .result import FashnError, FashnResult

HTTP_KINDS = {400: "BadRequest", 401: "UnauthorizedAccess", 404: "NotFound", 429: "RateLimited", 500: "InternalServerError"}
PENDING = {"starting", "in_queue", "processing"}


def _error_from_response(r: httpx.Response) -> FashnError:
    kind, msg = HTTP_KINDS.get(r.status_code, f"HTTP{r.status_code}"), ""
    try:
        e = r.json().get("error")
        if isinstance(e, dict):
            kind, msg = e.get("name") or kind, str(e.get("message", ""))
        elif isinstance(e, str):
            kind = e
    except Exception:
        msg = r.text
    return FashnError(kind, msg[:200], r.status_code)  # never includes request headers or the key


class LiveFashnClient:
    """Real FASHN: ONE submit, then status polling only. Never retries or resubmits.

    Must only be constructed after guard.preflight_live + guard.authorize_live_call have passed.
    """

    provider = "fashn"
    base = "https://api.fashn.ai/v1"
    poll_interval = 3.0
    max_polls = 60

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport  # tests inject a mock transport; production uses the real one

    async def try_on(self, person, person_ext, product, product_ext, on_prediction_id=None):
        payload = build_request(settings.fashn_model, person, person_ext, product, product_ext)
        headers = {"Authorization": f"Bearer {settings.fashn_api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60, transport=self.transport) as c:
            r = await c.post(f"{self.base}/run", json=payload, headers=headers)
            if r.status_code != 200:
                raise _error_from_response(r)
            pid = r.json().get("id")
            if not pid:
                raise FashnError("MissingPredictionId", "run response had no id")
            if on_prediction_id:
                on_prediction_id(pid)

            for _ in range(self.max_polls):  # polling is read-only; a new generation is never started
                await asyncio.sleep(self.poll_interval)
                s = await c.get(f"{self.base}/status/{pid}", headers={"Authorization": headers["Authorization"]})
                if s.status_code != 200:
                    raise _error_from_response(s)
                j = s.json()
                st = j.get("status")
                if st == "completed":
                    out = j.get("output")
                    if not out:
                        raise FashnError("MissingOutput", "completed without output")
                    img = await c.get(out[0])  # CDN download: no Authorization header sent
                    if img.status_code != 200 or not img.content:
                        raise FashnError("OutputDownloadFailed", f"CDN returned HTTP {img.status_code}", img.status_code)
                    return FashnResult(pid, img.content)
                if st == "failed":
                    e = j.get("error") or {}
                    raise FashnError(e.get("name", "PredictionFailed"), str(e.get("message", ""))[:200])
                if st not in PENDING:
                    raise FashnError("UnknownStatus", str(st)[:50])
        raise FashnError("Timeout", f"prediction {pid} not finished after {self.max_polls} polls")


def get_client():
    return LiveFashnClient() if live_enabled() else MockFashnClient()
