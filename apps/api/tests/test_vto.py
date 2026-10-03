import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import FashnLedger, TryOnResult
from app.services.fashn import client as fashn_client
from app.services.fashn.builder import FIRST_TEST_CONFIG, build_request, redact
from app.services.fashn.guard import AUTH_PHRASE, LiveCallBlocked, authorize_live_call
from app.services.images import ImageRejected, validate_image_bytes
from app.services.retailers.aggregator import search_all
from app.services.retailers.aliexpress import sign
from app.services.retailers.base import Retailer, RetailerProduct
from app.services.retailers.ebay import upscale_ebay_image
from app.services.verification.base import decide_overall

c = TestClient(app)


def test_env_loads_without_leaking():
    body = c.get("/api/health").json()
    assert body["fashn_live_enabled"] is False
    assert all(isinstance(v, bool) for v in body["configured"].values())
    assert settings.fashn_api_key not in str(body) or not settings.fashn_api_key


def test_image_validation(png):
    assert validate_image_bytes(png(800, 1000))[:2] == (800, 1000)
    with pytest.raises(ImageRejected):
        validate_image_bytes(png(120, 150))  # thumbnail
    with pytest.raises(ImageRejected):
        validate_image_bytes(b"not an image")


def test_ebay_upscale():
    assert upscale_ebay_image("https://i.ebayimg.com/images/g/x/s-l225.jpg").endswith("s-l1600.jpg")


def test_aliexpress_sign_is_deterministic():
    assert sign({"b": "2", "a": "1"}, "s") == sign({"a": "1", "b": "2"}, "s")


class _Fake(Retailer):
    def __init__(self, name, fail=False):
        self.name, self.fail = name, fail

    def enabled(self):
        return True

    async def search(self, q, limit=10):
        if self.fail:
            raise RuntimeError("boom")
        return [RetailerProduct(retailer=self.name, product_id="1", name="Blue dress", url="u", image_url="i")]


def test_search_aggregation_isolates_failures():
    products, status = asyncio.run(search_all("x", retailers=[_Fake("a"), _Fake("b", fail=True)]))
    assert len(products) == 1 and status["a"].startswith("ok") and status["b"].startswith("error")


def test_request_construction(png):
    req = build_request("tryon-max", png(), "png", png(), "png")
    i = req["inputs"]
    assert req["model_name"] == "tryon-max"
    assert {k: i[k] for k in FIRST_TEST_CONFIG} == FIRST_TEST_CONFIG
    assert i["model_image"].startswith("data:image/png;base64,")
    assert "base64" not in str(redact(req))


def test_live_guard_blocks_by_default():
    with SessionLocal() as db:
        with pytest.raises(LiveCallBlocked):
            authorize_live_call(db, 1)
        assert db.query(FashnLedger).count() == 0


def test_live_guard_cap(monkeypatch):
    monkeypatch.setattr(settings, "fashn_live_enabled", True)
    monkeypatch.setattr(settings, "fashn_live_authorization", AUTH_PHRASE)
    monkeypatch.setattr(settings, "fashn_api_key", "x")
    with SessionLocal() as db:
        authorize_live_call(db, 1)  # first spend allowed (2 credits)
        with pytest.raises(LiveCallBlocked):
            authorize_live_call(db, 2)  # total cap is 2; second is refused


def test_verification_never_passes_unconfirmed():
    assert decide_overall("UNVERIFIED", "UNKNOWN") == "REVIEW"
    assert decide_overall("VERIFIED", "UNKNOWN") == "REVIEW"
    assert decide_overall("VERIFIED", "CHANGED") == "FAIL"
    assert decide_overall("VERIFIED", "PRESERVED") == "PASS"


def test_full_mock_flow(png, monkeypatch):
    """upload -> select -> try-on (mock) -> raw result stored -> verification; zero network, zero credits."""
    product_png = png(900, 1100, (200, 50, 80))

    async def fake_download(urls):
        return urls[0], product_png, 900, 1100, "png"

    monkeypatch.setattr("app.api.routes.download_best_image", fake_download)

    up = c.post("/api/uploads/person", files={"file": ("p.png", png(), "image/png")}).json()
    assert c.get(up["url"]).status_code == 200

    sel = c.post("/api/products/select", json={
        "retailer": "ebay", "product_id": "1", "name": "Blue dress", "price": "10", "currency": "USD",
        "url": "https://example.com", "image_url": "https://example.com/a.png"}).json()
    assert len(sel["sha256"]) == 64

    job = c.post("/api/tryon", json={"product_id": sel["id"], "person_path": up["person_path"]}).json()
    final = c.get(f"/api/tryon/{job['id']}").json()  # TestClient runs background tasks before returning
    assert final["status"] == "VERIFIED" and final["provider"] == "mock"
    assert final["result"]["verification"]["overall"] == "REVIEW"
    with SessionLocal() as db:
        assert db.query(TryOnResult).count() == 1
        assert db.query(FashnLedger).count() == 0  # no credits recorded


def test_live_client_is_never_used_in_tests():
    assert fashn_client.get_client(live=False).provider == "mock"
    assert settings.fashn_live_enabled is False


def test_bad_person_path_rejected():
    r = c.post("/api/tryon", json={"product_id": 999, "person_path": "../.env"})
    assert r.status_code == 404
