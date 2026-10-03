import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import FashnLedger, Product, TryOnJob, TryOnResult
from app.services.fashn.builder import FIRST_TEST_CONFIG, build_request, redact
from app.services.fashn.client import LiveFashnClient
from app.services.fashn.guard import AUTH_PHRASE, LiveCallBlocked, authorize_live_call, preflight_live
from app.services.fashn.mock_client import MockFashnClient
from app.services.fashn.result import FashnError
from app.services.images import ImageRejected, validate_image_bytes
from app.services.retailers.aggregator import search_all
from app.services.retailers.aliexpress import sign
from app.services.retailers.base import Retailer, RetailerProduct
from app.services.retailers.ebay import upscale_ebay_image
from app.services.storage import local as storage
from app.services.tryon import run_job
from app.services.verification.base import CHECKS, decide_overall

c = TestClient(app)


# ---------- helpers ----------
def seed(png, retailer="ebay"):
    """Insert a stored person image + product + job directly (no network). Returns job id."""
    person_rel, person_sha = storage.save_bytes("persons", png(768, 1024, (180, 150, 130)), "png")
    prod_rel, prod_sha = storage.save_bytes("products", png(900, 1100, (200, 50, 80)), "png")
    with SessionLocal() as db:
        p = Product(retailer=retailer, retailer_product_id="1", name="Dress", product_url="u", source_image_url="i",
                    image_path=prod_rel, image_sha256=prod_sha, image_width=900, image_height=1100)
        db.add(p)
        db.commit()
        j = TryOnJob(product_id=p.id, person_image_path=person_rel, person_image_sha256=person_sha, provider="x")
        db.add(j)
        db.commit()
        return j.id


def live_env(monkeypatch):
    monkeypatch.setenv("FASHN_LIVE_ENABLED", "true")
    monkeypatch.setenv("FASHN_LIVE_AUTHORIZATION", AUTH_PHRASE)
    monkeypatch.setattr(settings, "fashn_api_key", "test-key-not-real")


class FakeFashn:
    """httpx MockTransport standing in for api.fashn.ai + its CDN. Records every request."""

    def __init__(self, result_png: bytes, run=(200, {"id": "pred-1", "error": None}), statuses=None):
        self.result_png, self.run, self.requests = result_png, run, []
        self.statuses = statuses or [{"id": "pred-1", "status": "processing"},
                                     {"id": "pred-1", "status": "completed", "output": ["https://cdn.fashn.ai/o.png"]}]
        self.polls = 0

    def transport(self):
        def handler(req: httpx.Request):
            self.requests.append(req)
            if req.url.path == "/v1/run":
                return httpx.Response(self.run[0], json=self.run[1])
            if req.url.path.startswith("/v1/status/"):
                i = min(self.polls, len(self.statuses) - 1)
                self.polls += 1
                s = self.statuses[i]
                return httpx.Response(s.pop("_http", 200) if "_http" in s else 200, json=s)
            if req.url.host == "cdn.fashn.ai":
                return httpx.Response(200, content=self.result_png)
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def client(self):
        cl = LiveFashnClient(transport=self.transport())
        cl.poll_interval = 0
        return cl

    @property
    def runs(self):
        return [r for r in self.requests if r.url.path == "/v1/run"]


def fake_live(monkeypatch, fake: FakeFashn):
    monkeypatch.setattr("app.services.tryon.get_client", lambda: fake.client())


# ---------- basics ----------
def test_health_defaults_to_mock_and_leaks_nothing():
    body = c.get("/api/health").json()
    assert body["vto_mode"] == "mock"
    assert all(isinstance(v, bool) for v in body["configured"].values())
    if settings.fashn_api_key:
        assert settings.fashn_api_key not in json.dumps(body)


def test_image_validation(png):
    assert validate_image_bytes(png(800, 1000))[:2] == (800, 1000)
    with pytest.raises(ImageRejected):
        validate_image_bytes(png(120, 150))
    with pytest.raises(ImageRejected):
        validate_image_bytes(b"not an image")


def test_ebay_upscale_and_aliexpress_sign():
    assert upscale_ebay_image("https://i.ebayimg.com/images/g/x/s-l225.jpg").endswith("s-l1600.jpg")
    assert sign({"b": "2", "a": "1"}, "s") == sign({"a": "1", "b": "2"}, "s")


# ---------- product search ----------
class _Fake(Retailer):
    def __init__(self, name, fail=False):
        self.name, self.fail = name, fail

    def enabled(self):
        return True

    async def search(self, q, limit=10, max_price=None):
        if self.fail:
            raise RuntimeError("boom")
        return [RetailerProduct(retailer=self.name, product_id="1", name="Blue dress", price="9.99", currency="USD",
                                url="https://shop/x", affiliate_url="https://aff/x", image_url="https://img/x.jpg")]


def test_search_aggregation_isolates_failures():
    products, status = asyncio.run(search_all("blue dress", retailers=[_Fake("a"), _Fake("b", fail=True)]))
    assert len(products) == 1 and status["a"].startswith("ok") and status["b"].startswith("error")


def test_search_endpoint_response_schema(monkeypatch):
    async def fake_search(q, limit=8):
        return [RetailerProduct(retailer="ebay", product_id="1", name="Blue dress", price="9.99", currency="USD",
                                url="https://shop/x", affiliate_url="https://aff/x", image_url="https://img/x.jpg")], {"ebay": "ok (1)"}

    monkeypatch.setattr("app.api.routes.search_all", fake_search)
    body = c.get("/api/products/search", params={"q": "blue dress"}).json()
    p = body["products"][0]
    assert {"retailer", "product_id", "name", "price", "currency", "url", "affiliate_url", "image_url"} <= set(p)
    assert body["retailers"] == {"ebay": "ok (1)"}
    assert c.get("/api/products/search", params={"q": "  "}).status_code == 400


# ---------- uploads / selection errors ----------
def test_upload_valid_and_invalid(png):
    ok = c.post("/api/uploads/person", files={"file": ("p.png", png(), "image/png")})
    assert ok.status_code == 200 and c.get(ok.json()["url"]).status_code == 200
    bad = c.post("/api/uploads/person", files={"file": ("p.png", b"junk", "image/png")})
    assert bad.status_code == 400


def test_error_paths(png):
    assert c.post("/api/tryon", json={"product_id": 999, "person_path": "persons/x.png"}).status_code == 404  # missing product
    jid = seed(png)
    with SessionLocal() as db:
        pid = db.get(TryOnJob, jid).product_id
    assert c.post("/api/tryon", json={"product_id": pid, "person_path": "persons/missing.png"}).status_code == 400
    assert c.post("/api/tryon", json={"product_id": pid, "person_path": "../.env"}).status_code == 400
    assert c.post("/api/products/select", json={"retailer": "ebay"}).status_code == 422  # invalid retailer product


# ---------- mock flow ----------
def test_full_mock_flow_end_to_end(png, monkeypatch):
    product_png = png(900, 1100, (200, 50, 80))

    async def fake_download(urls):
        return urls[0], product_png, 900, 1100, "png"

    monkeypatch.setattr("app.api.routes.download_best_image", fake_download)
    up = c.post("/api/uploads/person", files={"file": ("p.png", png(), "image/png")}).json()
    sel = c.post("/api/products/select", json={
        "retailer": "ebay", "product_id": "1", "name": "Blue dress", "price": "10", "currency": "USD",
        "url": "https://shop/x", "affiliate_url": "https://aff/x", "image_url": "https://img/a.png"}).json()
    job = c.post("/api/tryon", json={"product_id": sel["id"], "person_path": up["person_path"]}).json()  # inline
    assert job["status"] == "VERIFIED" and job["provider"] == "mock"
    assert job["product"]["affiliate_url"] == "https://aff/x"
    v = job["result"]["verification"]
    assert v["overall"] == "REVIEW_REQUIRED" and set(v["checks"]) == set(CHECKS)
    assert set(v["checks"].values()) == {"NOT_CHECKED"}
    assert "%" not in json.dumps(v)  # never an invented percentage
    with SessionLocal() as db:
        assert db.query(FashnLedger).count() == 0


def test_mock_is_deterministic(png):
    a = asyncio.run(MockFashnClient().try_on(png(), "png", png(), "png"))
    b = asyncio.run(MockFashnClient().try_on(png(), "png", png(), "png"))
    assert a.fashn_job_id == b.fashn_job_id and a.image_bytes == b.image_bytes


# ---------- request construction ----------
def test_request_matches_documented_spec(png):
    req = build_request("tryon-max", png(), "png", png(), "jpg")
    i = req["inputs"]
    assert req["model_name"] == "tryon-max"
    assert FIRST_TEST_CONFIG == {"resolution": "1k", "generation_mode": "balanced", "num_images": 1, "output_format": "png"}
    assert set(i) == {"model_image", "product_image", *FIRST_TEST_CONFIG}
    assert i["model_image"].startswith("data:image/png;base64,") and i["product_image"].startswith("data:image/jpeg;base64,")
    assert "base64" not in str(redact(req))


# ---------- guard / authorization ----------
def test_no_live_call_by_default(png):
    jid = seed(png)
    asyncio.run(run_job(jid))  # live not enabled -> mock
    with SessionLocal() as db:
        assert db.get(TryOnJob, jid).provider == "mock" and db.query(FashnLedger).count() == 0


def test_enabled_without_authorization_fails_and_never_falls_back(png, monkeypatch):
    monkeypatch.setenv("FASHN_LIVE_ENABLED", "true")
    monkeypatch.setattr(settings, "fashn_api_key", "k")
    jid = seed(png)
    asyncio.run(run_job(jid))
    with SessionLocal() as db:
        j = db.get(TryOnJob, jid)
        assert j.status == "FAILED" and "AUTHORIZATION" in j.error
        assert db.query(FashnLedger).count() == 0 and db.query(TryOnResult).count() == 0  # no mock fallback


def test_wrong_phrase_blocked(png, monkeypatch):
    live_env(monkeypatch)
    monkeypatch.setenv("FASHN_LIVE_AUTHORIZATION", "run the 2-credit fashn test")
    jid = seed(png)
    with SessionLocal() as db, pytest.raises(LiveCallBlocked):
        preflight_live(db, db.get(TryOnJob, jid), db.get(Product, 1))


def test_authorization_in_dotenv_settings_is_not_honored():
    assert not hasattr(settings, "fashn_live_authorization") and not hasattr(settings, "fashn_live_enabled")


def test_preflight_requires_ebay_product(png, monkeypatch):
    live_env(monkeypatch)
    jid = seed(png, retailer="aliexpress")
    with SessionLocal() as db:
        with pytest.raises(LiveCallBlocked, match="eBay"):
            preflight_live(db, db.get(TryOnJob, jid), db.get(Product, 1))


def test_preflight_passes_for_ready_ebay_job_without_side_effects(png, monkeypatch):
    live_env(monkeypatch)
    jid = seed(png)
    with SessionLocal() as db:
        preflight_live(db, db.get(TryOnJob, jid), db.get(Product, 1))
        assert db.query(FashnLedger).count() == 0


def test_credit_cap_blocks_second_authorization(monkeypatch):
    live_env(monkeypatch)
    with SessionLocal() as db:
        authorize_live_call(db, 1)
        with pytest.raises(LiveCallBlocked):
            authorize_live_call(db, 2)


# ---------- one call, no retry, raw output ----------
def test_live_success_one_call_raw_bytes_and_second_job_blocked(png, monkeypatch):
    live_env(monkeypatch)
    out = png(1024, 1365, (10, 200, 10))
    fake = FakeFashn(out)
    fake_live(monkeypatch, fake)

    j1 = seed(png)
    asyncio.run(run_job(j1))
    with SessionLocal() as db:
        job = db.get(TryOnJob, j1)
        res = db.query(TryOnResult).one()
        assert job.status == "VERIFIED" and job.provider == "fashn" and job.fashn_job_id == "pred-1"
        assert storage.read_bytes(res.result_path) == out  # raw FASHN bytes, byte-for-byte
        assert db.query(FashnLedger).one().credits == 2
        assert json.loads(res.verification_json)["checks"]["identity_preserved"] == "NOT_CHECKED"
    assert len(fake.runs) == 1
    body = json.loads(fake.runs[0].content)
    assert body["model_name"] == "tryon-max" and body["inputs"]["resolution"] == "1k"
    assert body["inputs"]["generation_mode"] == "balanced" and body["inputs"]["num_images"] == 1
    assert body["inputs"]["output_format"] == "png"
    assert fake.runs[0].headers["authorization"] == "Bearer test-key-not-real"
    cdn = [r for r in fake.requests if r.url.host == "cdn.fashn.ai"]
    assert cdn and "authorization" not in cdn[0].headers  # key never sent to the CDN

    j2 = seed(png)  # allowance consumed -> blocked before any request
    asyncio.run(run_job(j2))
    with SessionLocal() as db:
        assert db.get(TryOnJob, j2).status == "FAILED" and "allowance" in db.get(TryOnJob, j2).error
    assert len(fake.runs) == 1


@pytest.mark.parametrize("code,kind", [(400, "BadRequest"), (401, "UnauthorizedAccess"), (404, "NotFound"),
                                       (429, "RateLimited"), (500, "InternalServerError")])
def test_api_errors_are_terminal_with_single_request(png, monkeypatch, code, kind):
    live_env(monkeypatch)
    fake = FakeFashn(b"", run=(code, {}))
    fake_live(monkeypatch, fake)
    jid = seed(png)
    asyncio.run(run_job(jid))
    with SessionLocal() as db:
        j = db.get(TryOnJob, jid)
        assert j.status == "FAILED" and kind in j.error and f"HTTP {code}" in j.error
        assert db.query(TryOnResult).count() == 0
    assert len(fake.runs) == 1 and fake.polls == 0  # no retry, no resubmit


def test_named_api_error_body_is_surfaced(png, monkeypatch):
    live_env(monkeypatch)
    fake = FakeFashn(b"", run=(429, {"error": {"name": "OutOfCredits", "message": "No API credits remaining"}}))
    fake_live(monkeypatch, fake)
    jid = seed(png)
    asyncio.run(run_job(jid))
    with SessionLocal() as db:
        assert "OutOfCredits" in db.get(TryOnJob, jid).error
    assert len(fake.runs) == 1


def test_failed_prediction_is_terminal_and_keeps_prediction_id(png, monkeypatch):
    live_env(monkeypatch)
    fake = FakeFashn(b"", statuses=[{"id": "pred-1", "status": "starting"},
                                    {"id": "pred-1", "status": "failed", "error": {"name": "ImageLoadError", "message": "bad"}}])
    fake_live(monkeypatch, fake)
    jid = seed(png)
    asyncio.run(run_job(jid))
    with SessionLocal() as db:
        j = db.get(TryOnJob, jid)
        assert j.status == "FAILED" and "ImageLoadError" in j.error and j.fashn_job_id == "pred-1"
    assert len(fake.runs) == 1


def test_timeout_and_missing_output_and_unknown_status(png, monkeypatch):
    live_env(monkeypatch)
    cases = {
        "Timeout": [{"id": "pred-1", "status": "processing"}],
        "MissingOutput": [{"id": "pred-1", "status": "completed", "output": []}],
        "UnknownStatus": [{"id": "pred-1", "status": "weird"}],
    }
    for kind, statuses in cases.items():
        with SessionLocal() as db:
            db.query(FashnLedger).delete()
            db.commit()
        fake = FakeFashn(b"", statuses=statuses)
        cl = fake.client()
        cl.max_polls = 3
        monkeypatch.setattr("app.services.tryon.get_client", lambda cl=cl: cl)
        jid = seed(png)
        asyncio.run(run_job(jid))
        with SessionLocal() as db:
            j = db.get(TryOnJob, jid)
            assert j.status == "FAILED" and kind in j.error, (kind, j.error)
        assert len(fake.runs) == 1


def test_live_client_direct_error_type(png):
    fake = FakeFashn(b"", run=(401, {}))
    with pytest.raises(FashnError) as e:
        asyncio.run(fake.client().try_on(png(), "png", png(), "png"))
    assert e.value.http_status == 401 and "test-key" not in str(e.value)


# ---------- verification ----------
def test_verification_states():
    nc = {c_: "NOT_CHECKED" for c_ in CHECKS}
    assert decide_overall(nc) == "REVIEW_REQUIRED"
    assert decide_overall({**nc, "identity_preserved": "PASS"}) == "REVIEW_REQUIRED"
    assert decide_overall({**nc, "placement": "FAIL"}) == "FAIL"
    assert decide_overall({c_: "PASS" for c_ in CHECKS}) == "PASS"


def test_tests_cannot_reach_real_fashn(png):
    async def go():
        async with httpx.AsyncClient() as cl:
            await cl.get("https://api.fashn.ai/v1/credits")

    with pytest.raises(AssertionError, match="REAL FASHN"):
        asyncio.run(go())


# ---------- active sources: eBay + AliExpress only ----------
from app.services.retailers import aggregator  # noqa: E402
from app.services.retailers.aggregator import parse_query  # noqa: E402
from app.services.retailers.cj import CJ  # noqa: E402
from app.services.retailers.rakuten import Rakuten  # noqa: E402


def test_active_sources_are_only_ebay_and_aliexpress():
    assert [r.name for r in aggregator.ACTIVE] == ["ebay", "aliexpress"]
    assert {r.name for r in aggregator.FUTURE} == {"cj", "rakuten"}  # kept for later, not searched
    assert c.get("/api/health").json()["active_retailers"] == ["ebay", "aliexpress"]


def test_parse_query_price_cap():
    assert parse_query("black sneakers under $100") == ("black sneakers", 100.0)
    assert parse_query("blue dress") == ("blue dress", None)
    assert parse_query("red bag below 49.5") == ("red bag", 49.5)


def test_combined_search_never_calls_cj_or_rakuten(monkeypatch):
    async def boom(self, *a, **k):
        raise AssertionError("CJ/Rakuten must not be called")

    monkeypatch.setattr(CJ, "search", boom)
    monkeypatch.setattr(Rakuten, "search", boom)
    seen = {}

    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            seen[self.name] = (q, max_price)
            return [RetailerProduct(retailer=self.name, product_id=f"{self.name}{i}", name="Black sneaker",
                                    price="59.00", currency="USD", url="u", image_url="i") for i in (1, 2)]

    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay"), R("aliexpress")])
    body = c.get("/api/products/search", params={"q": "black sneakers under $100"}).json()
    assert seen == {"ebay": ("black sneakers", 100.0), "aliexpress": ("black sneakers", 100.0)}
    assert [p["retailer"] for p in body["products"]] == ["ebay", "aliexpress", "ebay", "aliexpress"]  # interleaved
    assert set(body["retailers"]) == {"ebay", "aliexpress"}


def test_one_retailer_failing_still_returns_the_other(monkeypatch):
    monkeypatch.setattr(aggregator, "ACTIVE", [_Fake("ebay", fail=True), _Fake("aliexpress")])
    body = c.get("/api/products/search", params={"q": "dress"}).json()
    assert [p["retailer"] for p in body["products"]] == ["aliexpress"]
    assert body["retailers"]["ebay"].startswith("error") and body["retailers"]["aliexpress"].startswith("ok")


def test_price_cap_enforced_even_if_retailer_ignores_it(monkeypatch):
    class Pricey(_Fake):
        async def search(self, q, limit=10, max_price=None):
            return [RetailerProduct(retailer=self.name, product_id=str(i), name="Blue Dress", price=pr, url="u", image_url="i")
                    for i, pr in enumerate(["50", "150", None])]

    monkeypatch.setattr(aggregator, "ACTIVE", [Pricey("ebay")])
    prices = [p["price"] for p in c.get("/api/products/search", params={"q": "dress under $100"}).json()["products"]]
    assert prices == ["50"]


def test_unconfigured_cj_rakuten_do_not_affect_health_or_search():
    assert c.get("/api/health").status_code == 200  # works regardless of CJ/Rakuten credentials


# ---------- categories + multi-item prompts ----------
from app.services.categorize import categorize  # noqa: E402
from app.services.retailers.aggregator import split_prompt  # noqa: E402


@pytest.mark.parametrize("title,cat", [
    ("Men Leather Biker Jacket", "outerwear"), ("Women's Wool Coat Long", "outerwear"),
    ("Cotton Crew Neck T-Shirt", "tops"), ("Oversized Hoodie Sweatshirt", "tops"),
    ("Floral Maxi Dress", "dresses"), ("Skinny Stretch Jeans", "bottoms"), ("Pleated Mini Skirt", "bottoms"),
    ("Nike Court Vision Black Sneakers", "shoes"), ("Block Heels Ankle Boots", "shoes"),
    ("Leather Crossbody Handbag", "bags"), ("Silver Wrist Watch", "accessories"), ("Polarized Sunglasses", "accessories"),
    ("Dress Shoes Oxford", "shoes"),          # priority: shoes before dresses
    ("Top Handle Bag Women", "bags"),         # priority: bags before tops
    ("Denim Jacket", "outerwear"),
    ("Milwaukee Women's Black Leather Motorcycle Rider Jacket with Belt Size XL", "outerwear"),  # tail ignored
    ("Anime Cosplay King of Fighters Leather Jacket Gloves Pants Belt", "other"),  # costume set: not guessed
    ("Red Michael Jackson Thriller Costume Full Set", "other"),
    ("Gaming Mouse Pad", "other"), ("Water Aqua Sock Beach", "other"), ("", "other"),  # never forced
])
def test_categorize(title, cat):
    assert categorize(title) == cat


def test_split_prompt_items():
    assert split_prompt("black leather jacket with white sneakers") == ["black leather jacket", "white sneakers"]
    assert split_prompt("red dress with black heels and handbag") == ["red dress", "black heels", "handbag"]
    assert split_prompt("black and white sneakers") == ["black and white sneakers"]  # colors stay together
    assert split_prompt("leather sandals") == ["leather sandals"] and split_prompt("handbag") == ["handbag"]
    assert len(split_prompt("a, b, c, d, e, f")) == 4  # capped


def test_search_groups_each_item_and_labels_categories(monkeypatch):
    calls = []

    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            calls.append((self.name, q))
            title = {"black leather jacket": "Black Leather Jacket", "white sneakers": "White Sneakers Low"}[q]
            return [RetailerProduct(retailer=self.name, product_id=f"{self.name}-{q}", name=title, price="40",
                                    currency="USD", url="u", image_url="i")]

    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay"), R("aliexpress")])
    body = c.get("/api/products/search", params={"q": "black leather jacket with white sneakers"}).json()
    assert body["searched"] == ["black leather jacket", "white sneakers"]
    assert sorted(calls) == sorted((r, q) for r in ("ebay", "aliexpress") for q in body["searched"])
    cats = {(p["retailer"], p["category"]) for p in body["products"]}
    assert cats == {("ebay", "outerwear"), ("aliexpress", "outerwear"), ("ebay", "shoes"), ("aliexpress", "shoes")}
    assert body["retailers"] == {"ebay": "ok (2)", "aliexpress": "ok (2)"}


def test_multi_item_search_survives_one_retailer_failing(monkeypatch):
    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            if self.fail:
                raise RuntimeError("down")
            name = {"red dress": "Red Dress", "black heels": "Black Heels Pumps"}[q]
            return [RetailerProduct(retailer=self.name, product_id=q, name=name, url="u", image_url="i")]

    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay", fail=True), R("aliexpress")])
    body = c.get("/api/products/search", params={"q": "red dress with black heels"}).json()
    assert {p["retailer"] for p in body["products"]} == {"aliexpress"} and len(body["products"]) == 2
    assert body["retailers"]["ebay"].startswith("error") and body["retailers"]["aliexpress"].startswith("ok")


def test_try_on_still_accepts_exactly_one_product():
    from app.api.routes import TryOnRequest

    assert set(TryOnRequest.model_fields) == {"product_id", "person_path"}  # no list of products


# ---------- relevance: only products that match the prompt are shown ----------
from app.services.relevance import is_relevant, main_noun  # noqa: E402


def test_main_noun():
    assert main_noun("white sneakers") == "sneaker" and main_noun("pakistani embroidered kurti") == "kurti"
    assert main_noun("women long maxi dress") == "dress" and main_noun("blue jeans") == "jean"


@pytest.mark.parametrize("part,title,ok", [
    ("white sneakers", "Nike Court Vision White Sneakers Men", True),
    ("handbag", "Women Leather Handbag Large Tote", True),
    ("handbag", "Zipper Felt Bag Organizer Insert For Loewe Tote Handbag", False),   # organizer, not a bag
    ("black leather jacket", "Womens Circus Ringmaster Tailcoat Halloween Cosplay Jacket", False),  # costume
    ("black leather jacket", "Black Leather Biker Jacket", True),
    ("black leather jacket", "Black Leather Wallet Men", False),                     # main noun missing
    ("maxi dress", "Boho Floral Long Maxi Dresses Summer", True),
    ("blue jeans", "Skinny Jeans Stretch Denim", True),
    ("blue jeans", "Jean Paul Gaultier Le Male Perfume Spray 4.2 oz", False),         # fragrance, not jeans
    ("handbag", "Ladies Bag Handle Wrap Silk Scarf", False),
    ("halloween costume", "Adult Halloween Costume Witch", True),                    # user asked for it
])
def test_is_relevant(part, title, ok):
    assert is_relevant(part, title) is ok


def test_search_hides_irrelevant_products(monkeypatch):
    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            titles = ["Leather Crossbody Handbag", "Bag Organizer Insert For Handbag", "Halloween Cosplay Handbag Prop",
                      "Silk Scarf Women"]
            return [RetailerProduct(retailer=self.name, product_id=str(i), name=t, price="10", url="u", image_url="i")
                    for i, t in enumerate(titles)]

    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay")])
    names = [p["name"] for p in c.get("/api/products/search", params={"q": "handbag"}).json()["products"]]
    assert names == ["Leather Crossbody Handbag"]


def test_category_comes_from_the_searched_item_not_the_title(monkeypatch):
    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            # a scarf whose title also mentions a bag word must still land under the "scarf" item's category
            return [RetailerProduct(retailer=self.name, product_id=q, name="Silk Scarf Bag Handle Wrap", url="u", image_url="i")]

    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay")])
    body = c.get("/api/products/search", params={"q": "silk scarf"}).json()
    assert [p["category"] for p in body["products"]] == ["accessories"]
