"""The REAL path: what we may claim, and when.

FASHN is faked at the HTTP transport (FakeFashn) and the vision models are stubbed, so nothing paid is called.
These tests prove the status semantics: nothing is VERIFIED, PRESENT or "applied" without real analysis, and the
final image is judged on its own against every selected product.
"""
import asyncio

import pytest

from app.config import settings
from tests.test_pipeline import CAT6, seed_n, vctx, verdicts
from tests.test_vto import FakeFashn, PassBoth, c, live_env, make_run
from app.services.storage import local as storage


class StubVision(PassBoth):
    """Per-step PASS except a configurable failure; final check configurable."""

    fail_step = None
    final = None  # callable(ctx) -> dict, or None for the default all-PASS

    async def verify(self, ctx):
        v = await super().verify(ctx)
        if ctx.step == StubVision.fail_step:
            v.checks["product_presence"], v.overall, v.notes = "FAIL", "FAIL", "product_presence [openai FAIL]: not visible"
        return v

    async def verify_final(self, ctx):
        return StubVision.final(ctx) if StubVision.final else await super().verify_final(ctx)


def real_run(png, monkeypatch, n, **stub):
    StubVision.fail_step, StubVision.final = stub.get("fail_step"), stub.get("final")
    live_env(monkeypatch, verifier=StubVision)
    monkeypatch.setattr(settings, "fashn_credit_cap", n * 2)  # test-only budget; the real default stays 2
    outs = [png(1024, 1365, (10 + 30 * i, 200 - 30 * i, 90)) for i in range(n)]
    fakes = [FakeFashn(o) for o in outs]
    it = iter(fakes)
    monkeypatch.setattr("app.services.tryon.get_client", lambda: next(it).client())
    person, ids = seed_n(png, n)
    return make_run(person, ids), fakes, outs


def drive(run, n):
    for _ in range(n):
        if run["status"] in ("FINISHED", "STOPPED"):
            break
        run = c.post(f"/api/tryon/run/{run['id']}/next").json()
    return run


def final_dict(ctx, present_fail=None, matches="PASS", overall="PASS"):
    items = [{"index": i + 1, "name": n, "category": cat, "present": "FAIL" if i == present_fail else "PASS",
              "matches": matches} for i, (n, cat, _) in enumerate(ctx.products)]
    return {"overall": overall, "identity_preserved": "PASS", "items": items, "notes": "", "models": {},
            "performed_by": ["gemini", "openai"]}


def test_real_run_all_verified_gives_final_verified_with_full_tracking(png, monkeypatch):
    run, fakes, outs = real_run(png, monkeypatch, 4)
    assert run["simulated"] is False and run["credits_required"] == 8
    run = drive(run, 4)
    assert run["status"] == "FINISHED" and run["final_status"] == "VERIFIED"
    st = run["steps"]
    assert [s["status"] for s in st] == ["VERIFIED"] * 4 and [s["product_presence"] for s in st] == ["PRESENT"] * 4
    assert all(s["fashn_prediction_id"] == "pred-1" and s["simulated"] is False for s in st)
    fv = run["final_verification"]
    assert fv["overall"] == "PASS" and [i["present"] for i in fv["items"]] == ["PASS"] * 4
    assert [i["name"] for i in fv["items"]] == [f"{CAT6[i].title()} Item" for i in range(4)]
    assert run["summary"] == ("4 of 4 steps generated a FASHN image. Final image checked by gemini + openai: "
                              "4 of 4 selected products confirmed present and matching; identity pass.")
    assert run["final_url"] == st[-1]["result"]["url"]
    # the final image is the raw FASHN output of the last step, byte for byte
    assert storage.read_bytes(st[-1]["result"]["url"].replace("/files/", "")) == outs[3]
    assert [len(f.runs) for f in fakes] == [1, 1, 1, 1]


def test_final_check_catches_a_missing_product_even_though_every_job_succeeded(png, monkeypatch):
    run, _, _ = real_run(png, monkeypatch, 3, final=lambda ctx: final_dict(ctx, present_fail=1, overall="FAIL"))
    run = drive(run, 3)
    assert [s["status"] for s in run["steps"]] == ["VERIFIED"] * 3  # every job "worked"...
    assert run["final_status"] == "FAILED"  # ...but the final image is judged on its own and FAILS
    assert "3 of 3 steps generated" in run["summary"] and "2 of 3 selected products confirmed" in run["summary"]
    assert run["final_verification"]["items"][1]["present"] == "FAIL"


def test_real_run_final_uncertain_is_review_required(png, monkeypatch):
    run, _, _ = real_run(png, monkeypatch, 2, final=lambda ctx: final_dict(ctx, matches="REVIEW_REQUIRED", overall="REVIEW_REQUIRED"))
    run = drive(run, 2)
    assert run["final_status"] == "REVIEW_REQUIRED" and "0 of 2 selected products confirmed" in run["summary"]


def test_real_run_rejected_step_stops_everything_and_labels_it_absent(png, monkeypatch):
    run, fakes, _ = real_run(png, monkeypatch, 4, fail_step=2)
    run = drive(run, 4)
    assert [s["status"] for s in run["steps"]] == ["VERIFIED", "REJECTED", "SKIPPED", "SKIPPED"]
    assert run["status"] == "STOPPED" and run["final_status"] == "STOPPED" and run["final_verification"] is None
    assert run["steps"][1]["product_presence"] == "ABSENT" and run["steps"][2]["product_presence"] == "NOT_GENERATED"
    assert run["summary"].startswith("2 of 4 steps generated") and "Stopped at step 2 (rejected)" in run["summary"]
    assert [len(f.runs) for f in fakes] == [1, 1, 0, 0]  # no FASHN calls (and no credits) for skipped steps


def test_final_verification_outage_is_review_required_never_verified(png, monkeypatch):
    class Boom(PassBoth):
        async def verify_final(self, ctx):
            raise RuntimeError("HTTP 500")

    live_env(monkeypatch, verifier=Boom)
    monkeypatch.setattr(settings, "fashn_credit_cap", 4)
    fakes = iter([FakeFashn(png(1024, 1365)), FakeFashn(png(1024, 1365))])
    monkeypatch.setattr("app.services.tryon.get_client", lambda: next(fakes).client())
    person, ids = seed_n(png, 2)
    run = drive(make_run(person, ids), 2)
    assert run["final_status"] == "REVIEW_REQUIRED" and run["final_verification"]["overall"] == "REVIEW_REQUIRED"
    assert "unavailable" in run["final_verification"]["notes"] and run["final_verification"]["performed_by"] == []


def test_live_run_refused_without_real_verification_models(png, monkeypatch):
    live_env(monkeypatch)
    person, ids = seed_n(png, 1)
    monkeypatch.setattr(settings, "gemini_api_key", "")  # Gemini not configured
    r = c.post("/api/tryon/run", json={"person_path": person, "product_ids": ids})
    assert r.status_code == 400 and "OpenAI + Gemini" in r.json()["detail"] and "Nothing was started" in r.json()["detail"]
    monkeypatch.setattr(settings, "gemini_api_key", "gk")
    monkeypatch.setattr(settings, "verification_mode", "placeholder")  # verification switched off
    assert c.post("/api/tryon/run", json={"person_path": person, "product_ids": ids}).status_code == 400
    monkeypatch.setattr(settings, "verification_mode", "auto")
    assert c.post("/api/tryon/run", json={"person_path": person, "product_ids": ids}).status_code == 200


def test_mock_output_is_stamped_and_each_step_differs(png):
    from app.services.fashn.mock_client import MockFashnClient

    a = asyncio.run(MockFashnClient().try_on(png(), "png", png(900, 900, (1, 2, 3)), "png"))
    b = asyncio.run(MockFashnClient().try_on(png(), "png", png(900, 900, (9, 8, 7)), "png"))
    assert a.image_bytes != b.image_bytes and a.fashn_job_id.startswith("mock-") and a.fashn_job_id != b.fashn_job_id


def test_one_model_alone_can_never_pass():
    from app.services.verification.llm import combine

    v = combine(vctx(1), {"openai": verdicts(1)})
    assert v.overall == "REVIEW_REQUIRED" and "gemini not configured" in v.notes and v.performed_by == ["openai"]
    v = combine(vctx(1), {"openai": verdicts(1), "gemini": verdicts(1)})
    assert v.overall == "PASS" and v.performed_by == ["gemini", "openai"]


def test_final_combination_rules():
    from app.services.verification.base import FinalContext
    from app.services.verification.llm import combine_final

    ctx = FinalContext(original=b"o", result=b"r", products=[("Jacket", "outerwear", b"j"), ("Bag", "bags", b"b")])

    def ans(present_bag="PASS", identity="PASS"):
        ok = {"verdict": "PASS", "reason": ""}
        return {"identity_preserved": {"verdict": identity, "reason": ""},
                "items": [{"present": ok, "matches": ok}, {"present": {"verdict": present_bag, "reason": f"bag {present_bag}"}, "matches": ok}]}

    both = combine_final(ctx, {"openai": ans(), "gemini": ans()})
    assert both["overall"] == "PASS" and [i["present"] for i in both["items"]] == ["PASS", "PASS"]
    one_fail = combine_final(ctx, {"openai": ans(), "gemini": ans(present_bag="FAIL")})
    assert one_fail["overall"] == "FAIL" and one_fail["items"][1]["present"] == "FAIL" and "gemini FAIL" in one_fail["notes"]
    outage = combine_final(ctx, {"openai": ans(), "gemini": RuntimeError("x")})
    assert outage["overall"] == "REVIEW_REQUIRED" and outage["performed_by"] == ["openai"]
    assert combine_final(ctx, {"openai": ans(identity="FAIL"), "gemini": ans()})["overall"] == "FAIL"
    assert "%" not in str(both)
