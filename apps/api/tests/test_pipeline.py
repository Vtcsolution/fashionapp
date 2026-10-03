"""OpenAI prompt parsing, OpenAI+Gemini verification, and 1-6 product orchestration.

FASHN is always mocked. Real OpenAI/Gemini/FASHN hosts are blocked at the transport level by conftest.
"""
import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import FashnLedger, Product, TryOnJob
from app.services.prompt_items import PromptItem
from app.services.prompt_parser import parse_items
from app.services.retailers import aggregator
from app.services.retailers.base import RetailerProduct
from app.services.storage import local as storage
from app.services.verification import llm as vllm
from app.services.verification.base import (
    EXTRA_CHECKS, PlaceholderVerifier, Verification, VerifyContext, checks_for_step, get_verifier,
)
from app.services.verification.llm import GeminiJudge, LlmVerifier, OpenAIJudge
from tests.test_vto import FakeFashn, _Fake, c, fake_live, live_env, make_run, seed_products

PROMPT = "beige jacket, white dress, brown sandals, brown handbag, sunglasses"


# ---------------------------------------------------------------- OpenAI prompt parsing
def openai_reply(content: dict):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(content)}}]})


def run_parse(text, handler):
    return asyncio.run(parse_items(text, transport=httpx.MockTransport(handler)))


def test_openai_prompt_parsing_returns_structured_items(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-real")
    seen = {}

    def handler(req):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return openai_reply({"items": [
            {"query": "beige jacket", "category": "outerwear"}, {"query": "white dress", "category": "dresses"},
            {"query": "brown sandals", "category": "shoes"}, {"query": "brown handbag", "category": "bags"},
            {"query": "sunglasses", "category": "accessories"}]})

    items, source = run_parse(PROMPT, handler)
    assert source == "openai"
    assert [(i.query, i.category) for i in items] == [
        ("beige jacket", "outerwear"), ("white dress", "dresses"), ("brown sandals", "shoes"),
        ("brown handbag", "bags"), ("sunglasses", "accessories")]
    assert seen["auth"] == "Bearer sk-test-not-real" and seen["body"]["response_format"]["type"] == "json_schema"
    assert PROMPT in json.dumps(seen["body"]["messages"])  # text only: no images are sent or requested


def test_openai_parsing_caps_at_six_and_repairs_bad_categories(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "k")
    many = [{"query": f"item {i}", "category": "nonsense"} for i in range(9)]
    items, source = run_parse("x", lambda r: openai_reply({"items": many}))
    assert source == "openai" and len(items) == 6 and all(i.category == "other" for i in items)
    items, _ = run_parse("x", lambda r: openai_reply({"items": [{"query": "black sneakers", "category": "bogus"}]}))
    assert items[0].category == "shoes"  # repaired by the keyword classifier


@pytest.mark.parametrize("handler", [
    lambda r: httpx.Response(500, json={}), lambda r: httpx.Response(401, json={}),
    lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
    lambda r: openai_reply({"items": []}),
])
def test_prompt_parsing_falls_back_on_any_problem(monkeypatch, handler):
    monkeypatch.setattr(settings, "openai_api_key", "k")
    items, source = run_parse("red dress with black heels", handler)
    assert source == "fallback" and [i.query for i in items] == ["red dress", "black heels"]


def test_prompt_parsing_without_key_is_offline():
    items, source = asyncio.run(parse_items(PROMPT))  # conftest blocks real OpenAI; no key -> no call attempted
    assert source == "fallback" and len(items) == 5  # all five items survive (cap is 6, not 4)


def test_search_endpoint_uses_parsed_items(monkeypatch):
    async def fake_parse(text):
        assert text == "beige jacket, white dress"
        return [PromptItem("beige jacket", "outerwear"), PromptItem("white dress", "dresses")], "openai"

    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            return [RetailerProduct(retailer=self.name, product_id=q, name=q.title(), price="20", url="u", image_url="i")]

    monkeypatch.setattr("app.api.routes.parse_items", fake_parse)
    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay"), R("aliexpress")])
    body = c.get("/api/products/search", params={"q": "beige jacket, white dress under $50"}).json()
    assert body["parser"] == "openai" and body["searched"] == ["beige jacket", "white dress"]
    assert {(p["retailer"], p["category"]) for p in body["products"]} == {
        (r, cat) for r in ("ebay", "aliexpress") for cat in ("outerwear", "dresses")}


# ---------------------------------------------------------------- verification: combination rule
def vctx(step=1, n=b"x"):
    return VerifyContext(step=step, product_name="Beige Jacket", category="outerwear",
                         original=n, previous=n, product=n, result=n)


def verdicts(step, **over):
    base = {k: {"verdict": "PASS", "reason": ""} for k in checks_for_step(step)}
    for k, v in over.items():
        base[k] = {"verdict": v, "reason": f"{k} is {v}"}
    return base


class FakeJudge:
    def __init__(self, name, result):
        self.name, self.result = name, result

    def enabled(self):
        return True

    async def judge(self, ctx):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def verify_with(step, a, b):
    return asyncio.run(LlmVerifier([FakeJudge("openai", a), FakeJudge("gemini", b)]).verify(vctx(step)))


def test_both_models_pass_is_pass():
    v = verify_with(1, verdicts(1), verdicts(1))
    assert v.overall == "PASS" and set(v.checks.values()) == {"PASS"} and set(v.checks) == set(checks_for_step(1))
    assert set(v.checks) == {"product_presence", "product_correspondence", "color", "details", "placement",
                             "identity_preserved"}


def test_any_model_fail_is_fail_and_names_the_reason():
    v = verify_with(1, verdicts(1), verdicts(1, product_presence="FAIL"))
    assert v.overall == "FAIL" and v.checks["product_presence"] == "FAIL"
    assert "gemini FAIL" in v.notes and "product_presence is FAIL" in v.notes


def test_uncertain_or_disagreement_is_review_never_pass():
    v = verify_with(1, verdicts(1), verdicts(1, color="UNCERTAIN"))
    assert v.overall == "REVIEW_REQUIRED" and v.checks["color"] == "REVIEW_REQUIRED" and v.checks["details"] == "PASS"


def test_a_failing_model_can_never_produce_pass():
    v = verify_with(1, verdicts(1), RuntimeError("HTTP 500"))
    assert v.overall == "REVIEW_REQUIRED" and "gemini unavailable" in v.notes and "PASS" not in v.checks.values()
    v = verify_with(1, RuntimeError("a"), RuntimeError("b"))
    assert v.overall == "REVIEW_REQUIRED" and set(v.checks.values()) == {"REVIEW_REQUIRED"}
    assert v.models == {"openai": {"error": "RuntimeError"}, "gemini": {"error": "RuntimeError"}}


def test_a_fail_beats_an_outage_of_the_other_model():
    assert verify_with(1, verdicts(1, identity_preserved="FAIL"), RuntimeError("x")).overall == "FAIL"


def test_earlier_items_check_only_from_step_two():
    assert "earlier_items_preserved" not in checks_for_step(1) and EXTRA_CHECKS[0] in checks_for_step(2)
    v = verify_with(3, verdicts(3, earlier_items_preserved="FAIL"), verdicts(3))
    assert v.overall == "FAIL" and v.checks["earlier_items_preserved"] == "FAIL"


def test_no_percentages_anywhere_in_verification():
    v = verify_with(2, verdicts(2, details="UNCERTAIN"), verdicts(2))
    assert "%" not in json.dumps(v.checks) and "%" not in v.notes


def test_verifier_selection_rules(monkeypatch):
    assert isinstance(get_verifier("mock"), PlaceholderVerifier)  # mock output is never sent to a vision API
    assert isinstance(get_verifier("fashn"), LlmVerifier)
    monkeypatch.setattr(settings, "verification_mode", "placeholder")
    assert isinstance(get_verifier("fashn"), PlaceholderVerifier)
    monkeypatch.setattr(settings, "verification_mode", "llm")
    assert isinstance(get_verifier("mock"), LlmVerifier)


def test_verifier_with_no_keys_is_review_required():
    v = asyncio.run(LlmVerifier().verify(vctx(1)))  # keys blank in tests
    assert v.overall == "REVIEW_REQUIRED" and set(v.checks.values()) == {"NOT_CHECKED"}


# ---------------------------------------------------------------- verification: real HTTP shapes
def images_ctx(png):
    return VerifyContext(step=2, product_name="Brown Sandals", category="shoes", original=png(900, 1200, (1, 2, 3)),
                         previous=png(900, 1200, (4, 5, 6)), product=png(800, 800, (7, 8, 9)),
                         result=png(1024, 1365, (10, 11, 12)))


def test_openai_judge_sends_four_images_in_order_and_parses(png, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-real")
    seen = {}

    def handler(req):
        seen["h"], seen["b"] = req.headers["authorization"], json.loads(req.content)
        return openai_reply({k: {"verdict": "PASS", "reason": "ok"} for k in checks_for_step(2)})

    out = asyncio.run(OpenAIJudge(httpx.MockTransport(handler)).judge(images_ctx(png)))
    parts = seen["b"]["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["text"] + ["image_url"] * 4
    assert "Brown Sandals" in parts[0]["text"] and "earlier_items_preserved" in parts[0]["text"]
    assert set(out) == set(checks_for_step(2)) and seen["h"] == "Bearer sk-test-not-real"
    assert set(seen["b"]["response_format"]["json_schema"]["schema"]["required"]) == set(checks_for_step(2))
    assert "image" not in seen["b"]["model"] and "dall" not in seen["b"]["model"]  # a chat/vision model only


def test_gemini_judge_uses_header_key_four_images_and_handles_fences(png, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gk-test-not-real")
    seen = {}

    def handler(req):
        seen["url"], seen["key"], seen["b"] = str(req.url), req.headers["x-goog-api-key"], json.loads(req.content)
        text = "```json\n" + json.dumps({k: {"verdict": "fail", "reason": "bad"} for k in checks_for_step(2)}) + "\n```"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})

    out = asyncio.run(GeminiJudge(httpx.MockTransport(handler)).judge(images_ctx(png)))
    parts = seen["b"]["contents"][0]["parts"]
    assert len(parts) == 5 and sum("inline_data" in p for p in parts) == 4
    assert "gk-test-not-real" not in seen["url"] and seen["key"] == "gk-test-not-real"  # never in the URL
    assert settings.gemini_vision_model in seen["url"] and "image" not in settings.gemini_vision_model
    assert all(v["verdict"] == "FAIL" for v in out.values())  # case-normalized


def test_judge_http_errors_do_not_leak_keys(png, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "gk-secret")
    with pytest.raises(RuntimeError) as e:
        asyncio.run(GeminiJudge(httpx.MockTransport(lambda r: httpx.Response(403, json={}))).judge(images_ctx(png)))
    assert "gk-secret" not in str(e.value) and "403" in str(e.value)


def test_prepare_makes_a_separate_downscaled_copy(png):
    original = png(2000, 2600)
    jpeg = vllm.prepare(original)
    assert jpeg[:2] == b"\xff\xd8" and jpeg != original  # JPEG copy for the API only; the stored result is never touched


# ---------------------------------------------------------------- orchestration: 1..6 products, FASHN mocked
CAT6 = ["outerwear", "dresses", "shoes", "bags", "accessories", "tops"]


def seed_n(png, n, retailer="ebay"):
    person, ids = seed_products(png, n, retailer)
    with SessionLocal() as db:
        for i, pid in enumerate(ids):
            p = db.get(Product, pid)
            p.category, p.name, p.retailer_product_id = CAT6[i], f"{CAT6[i].title()} Item", f"R-{i}-{CAT6[i]}"
            p.affiliate_url, p.product_url = f"https://aff/{i}", f"https://shop/{i}"
        db.commit()
    return person, ids


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
def test_mock_orchestration_for_one_to_six_products(png, n):
    person, ids = seed_n(png, n)
    run = make_run(person, ids)
    assert run["total_steps"] == n and [s["status"] for s in run["steps"]] == ["PLANNED"] * n
    for _ in range(n):
        run = c.post(f"/api/tryon/run/{run['id']}/next").json()
    assert run["status"] == "FINISHED" and run["simulated"] is True and run["final_status"] == "SIMULATED"
    assert run["summary"].startswith("MOCK / DEMO") and run["final_verification"] is None
    st = run["steps"]
    assert [s["product"]["id"] for s in st] == ids  # exact products, exact order, none dropped or substituted
    assert [s["product"]["retailer_product_id"] for s in st] == [f"R-{i}-{CAT6[i]}" for i in range(n)]
    assert [s["product"]["affiliate_url"] for s in st] == [f"https://aff/{i}" for i in range(n)]
    assert [s["product"]["url"] for s in st] == [f"https://shop/{i}" for i in range(n)]
    assert all(s["product"]["image_url"].startswith("/files/products/") for s in st)
    assert all(s["status"] == "REVIEW_REQUIRED" and s["result"] for s in st)  # generated, never "VERIFIED" in mock
    assert all(s["product_presence"] == "SIMULATED" and s["simulated"] for s in st)
    assert len({s["result"]["url"] for s in st}) == n  # every mock step looks different (no reused image)
    for k in range(1, n):
        assert st[k]["person_url"] == st[k - 1]["result"]["url"]
    assert run["final_url"] == st[-1]["result"]["url"]
    with SessionLocal() as db:
        assert db.query(FashnLedger).count() == 0
        jobs = db.query(TryOnJob).filter_by(run_id=run["id"]).order_by(TryOnJob.step).all()
        assert len(jobs) == n and all((j.fashn_job_id or "").startswith("mock-") for j in jobs)  # prediction id per step


def test_verifier_runs_per_step_with_correct_context_in_a_chain(png, monkeypatch):
    """Each step is verified against: the ORIGINAL photo, the previous image, that step's product, the new result."""
    seen = []

    class Spy:
        async def verify(self, ctx):
            seen.append(ctx)
            return Verification({k: "PASS" for k in checks_for_step(ctx.step)}, "PASS", "")

    monkeypatch.setattr("app.services.tryon.get_verifier", lambda provider: Spy())
    person, ids = seed_n(png, 3)
    run = make_run(person, ids)
    for _ in range(3):
        run = c.post(f"/api/tryon/run/{run['id']}/next").json()
    assert [x.step for x in seen] == [1, 2, 3]
    assert [x.product_name for x in seen] == ["Outerwear Item", "Dresses Item", "Shoes Item"]
    original = storage.read_bytes(person)
    assert all(x.original == original for x in seen)  # identity reference is ALWAYS the uploaded photo
    assert seen[0].previous == original
    st = run["steps"]
    res = [storage.read_bytes(s["result"]["url"].replace("/files/", "")) for s in st]
    assert seen[1].previous == res[0] and seen[2].previous == res[1] and seen[2].result == res[2]
    assert all(len(x.product) > 0 for x in seen)


def test_verification_fail_stops_the_sequence_and_skips_the_rest(png, monkeypatch):
    class FailAtThree:
        async def verify(self, ctx):
            checks = {k: "PASS" for k in checks_for_step(ctx.step)}
            if ctx.step != 3:
                return Verification(checks, "PASS", "", {}, ["gemini", "openai"])
            checks["product_presence"] = "FAIL"
            return Verification(checks, "FAIL", "product_presence [gemini FAIL]: no sandals visible", {}, ["gemini", "openai"])

    monkeypatch.setattr("app.services.tryon.get_verifier", lambda provider: FailAtThree())
    person, ids = seed_n(png, 5)
    run = make_run(person, ids)
    for _ in range(3):
        run = c.post(f"/api/tryon/run/{run['id']}/next").json()
    assert [s["status"] for s in run["steps"]] == ["VERIFIED", "VERIFIED", "REJECTED", "SKIPPED", "SKIPPED"]
    assert run["status"] == "STOPPED" and "no sandals visible" in run["steps"][2]["error"]
    assert run["steps"][2]["result"] is not None  # the rejected raw image is still available to inspect
    assert run["final_status"] == "SIMULATED"  # mock provider: never a real claim, whatever a stub verifier says
    assert run["final_url"] == run["steps"][1]["result"]["url"]  # last ACCEPTED image, not the rejected one
    assert c.post(f"/api/tryon/run/{run['id']}/next").status_code == 400  # stopped: no silent continuation


def test_unverified_steps_continue_but_are_never_called_verified(png, monkeypatch):
    class Unsure:
        async def verify(self, ctx):
            return Verification({k: "REVIEW_REQUIRED" for k in checks_for_step(ctx.step)}, "REVIEW_REQUIRED", "uncertain",
                                {}, ["gemini", "openai"])

    monkeypatch.setattr("app.services.tryon.get_verifier", lambda provider: Unsure())
    person, ids = seed_n(png, 3)
    run = make_run(person, ids)
    for _ in range(3):
        run = c.post(f"/api/tryon/run/{run['id']}/next").json()
    assert run["status"] == "FINISHED" and [s["status"] for s in run["steps"]] == ["REVIEW_REQUIRED"] * 3


def test_no_analysis_means_no_verified_even_if_the_verdict_says_pass(png, monkeypatch):
    class Liar:  # claims PASS but no model actually analysed anything
        async def verify(self, ctx):
            return Verification({k: "PASS" for k in checks_for_step(ctx.step)}, "PASS", "", {}, [])

    monkeypatch.setattr("app.services.tryon.get_verifier", lambda provider: Liar())
    person, ids = seed_n(png, 1)
    run = c.post(f"/api/tryon/run/{make_run(person, ids)['id']}/next").json()
    assert run["steps"][0]["status"] == "REVIEW_REQUIRED"


def test_multi_product_live_is_still_capped_at_two_credits(png, monkeypatch):
    live_env(monkeypatch)
    fake = FakeFashn(png(1024, 1365))
    fake_live(monkeypatch, fake)
    person, ids = seed_n(png, 5)
    r = c.post("/api/tryon/run", json={"person_path": person, "product_ids": ids})
    assert r.status_code == 400 and "Nothing was started" in r.json()["detail"]
    assert settings.fashn_credit_cap == 2 and fake.requests == []  # cap untouched, zero FASHN traffic


# ---------------------------------------------------------------- color-aware ranking
def test_requested_color_ranks_first_but_nothing_is_hidden(monkeypatch):
    from app.services.relevance import color_rank

    assert color_rank("brown handbag", "Brown Leather Handbag") == 0
    assert color_rank("brown handbag", "Leather Handbag Large") == 1
    assert color_rank("brown handbag", "Red Hobo Handbag") == 2
    assert color_rank("handbag", "Red Hobo Handbag") == 0  # no color requested -> no ranking

    class R(_Fake):
        async def search(self, q, limit=10, max_price=None):
            titles = ["Red Hobo Handbag", "Leather Handbag Large", "Brown Leather Handbag"]
            return [RetailerProduct(retailer=self.name, product_id=str(i), name=t, price="9", url="u", image_url="i")
                    for i, t in enumerate(titles)]

    monkeypatch.setattr(aggregator, "ACTIVE", [R("ebay")])
    names = [p["name"] for p in c.get("/api/products/search", params={"q": "brown handbag"}).json()["products"]]
    assert names == ["Brown Leather Handbag", "Leather Handbag Large", "Red Hobo Handbag"]  # all three still shown
