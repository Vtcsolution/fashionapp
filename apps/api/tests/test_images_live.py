"""WebP handling before FASHN, and the live path for BOTH retailers (FASHN faked at the HTTP transport)."""
import base64
import io
import json

import pytest
from PIL import Image

from app.db import SessionLocal
from app.models import FashnLedger, Product, TryOnJob
from app.services.images import ImageRejected, prepare_for_fashn, to_png_lossless
from app.services.storage import local as storage
from tests.conftest import make_png
from tests.test_vto import FakeFashn, c, fake_live, live_env


def webp_bytes(w=900, h=1100, mode="RGB", lossless=False):
    im = Image.new(mode, (w, h), (30, 120, 200) if mode == "RGB" else (30, 120, 200, 128))
    for x in range(0, w, 7):  # some texture so a lossy encoder really changes pixels
        im.putpixel((x, x % h), (250, 10, 10) if mode == "RGB" else (250, 10, 10, 255))
    buf = io.BytesIO()
    im.save(buf, "WEBP", lossless=lossless, quality=80)
    return buf.getvalue()


def px(data):
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.mode, im.size, im.tobytes()


# ---------------------------------------------------------------- conversion
def test_webp_is_converted_to_png_with_identical_decoded_pixels():
    src = webp_bytes()
    prep = prepare_for_fashn(src, "webp")
    assert prep.converted and prep.sent_ext == "png" and prep.original_ext == "webp"
    assert prep.sent_bytes[:8] == b"\x89PNG\r\n\x1a\n" and prep.original_bytes == src  # original untouched
    assert px(prep.sent_bytes) == px(src)  # not resized, cropped, recolored or re-compressed lossy


def test_lossy_and_lossless_webp_and_alpha_all_round_trip_exactly():
    for kw in ({}, {"lossless": True}, {"mode": "RGBA"}, {"mode": "RGBA", "lossless": True}):
        src = webp_bytes(640, 480, **kw)
        assert px(to_png_lossless(src)) == px(src)


def test_jpeg_and_png_pass_through_byte_for_byte():
    jpg = io.BytesIO()
    Image.new("RGB", (800, 800), (5, 6, 7)).save(jpg, "JPEG")
    for data, ext in ((jpg.getvalue(), "jpg"), (make_png(), "png")):
        prep = prepare_for_fashn(data, ext)
        assert not prep.converted and prep.sent_bytes == data and prep.sent_ext == ext


def test_dimensions_are_never_changed():
    for w, h in ((1600, 1600), (333, 1777), (4000, 800)):
        assert px(prepare_for_fashn(webp_bytes(w, h), "webp").sent_bytes)[1] == (w, h)


def test_corrupt_data_is_refused_not_sent():
    with pytest.raises(Exception):
        prepare_for_fashn(b"RIFFxxxxWEBPnot-really", "webp")


def test_oversized_result_is_refused_not_resized(monkeypatch):
    monkeypatch.setattr("app.services.images.FASHN_MAX_BYTES", 1000)
    with pytest.raises(ImageRejected, match="30 MiB"):
        prepare_for_fashn(make_png(800, 800), "png")


# ---------------------------------------------------------------- API: select + upload keep BOTH files and hashes
def select_payload(name="Brown Sandals", retailer="aliexpress"):
    return {"retailer": retailer, "product_id": "32568", "name": name, "price": "12", "currency": "USD",
            "url": "https://shop/x", "affiliate_url": "https://aff/x", "image_url": "https://img/x.webp", "category": "shoes"}


def test_select_converts_webp_keeps_original_and_records_both_hashes(monkeypatch):
    src = webp_bytes()

    async def fake_download(urls):
        return urls[0], src, 900, 1100, "webp"

    monkeypatch.setattr("app.api.routes.download_best_image", fake_download)
    r = c.post("/api/products/select", json=select_payload()).json()
    assert r["converted"] is True and r["original_format"] == "webp" and r["sent_format"] == "png"
    assert r["sha256"] != r["original_sha256"] and r["width"] == 900 and r["height"] == 1100
    with SessionLocal() as db:
        p = db.get(Product, r["id"])
        assert p.image_path.endswith(".png") and p.original_image_path.endswith(".webp") and p.image_converted is True
        sent, orig = storage.read_bytes(p.image_path), storage.read_bytes(p.original_image_path)
        import hashlib

        assert hashlib.sha256(sent).hexdigest() == p.image_sha256 == r["sha256"]
        assert hashlib.sha256(orig).hexdigest() == p.original_image_sha256 == r["original_sha256"]
        assert orig == src and px(sent) == px(src)  # original kept byte-for-byte; sent == same pixels
        assert p.source_image_url == "https://img/x.webp"  # exact source URL recorded


def test_select_jpeg_is_not_converted(monkeypatch):
    jpg = io.BytesIO()
    Image.new("RGB", (900, 1100), (9, 9, 9)).save(jpg, "JPEG")

    async def fake_download(urls):
        return urls[0], jpg.getvalue(), 900, 1100, "jpg"

    monkeypatch.setattr("app.api.routes.download_best_image", fake_download)
    r = c.post("/api/products/select", json=select_payload("Jacket", "ebay")).json()
    assert r["converted"] is False and r["sha256"] == r["original_sha256"] and r["sent_format"] == "jpg"


def test_upload_webp_person_is_converted_original_kept():
    src = webp_bytes(768, 1024)
    r = c.post("/api/uploads/person", files={"file": ("me.webp", src, "image/webp")}).json()
    assert r["converted"] is True and r["person_path"].endswith(".png") and r["original_format"] == "webp"
    assert px(storage.read_bytes(r["person_path"])) == px(src)


# ---------------------------------------------------------------- live path: both retailers, exact bytes sent
def run_live_single(png, monkeypatch, retailer, source_webp):
    live_env(monkeypatch)
    out = png(1024, 1365, (10, 200, 10))
    fake = FakeFashn(out)
    fake_live(monkeypatch, fake)
    src = webp_bytes() if source_webp else png(900, 1100, (200, 50, 80))

    async def fake_download(urls):
        return urls[0], src, 900, 1100, "webp" if source_webp else "png"

    monkeypatch.setattr("app.api.routes.download_best_image", fake_download)
    person = c.post("/api/uploads/person", files={"file": ("p.png", png(768, 1024, (180, 150, 130)), "image/png")}).json()
    sel = c.post("/api/products/select", json=select_payload("Item", retailer)).json()
    job = c.post("/api/tryon", json={"product_id": sel["id"], "person_path": person["person_path"]}).json()
    return job, fake, src, out, sel


@pytest.mark.parametrize("retailer,webp", [("ebay", False), ("aliexpress", False), ("aliexpress", True)])
def test_live_try_on_works_for_ebay_and_aliexpress(png, monkeypatch, retailer, webp):
    job, fake, src, out, sel = run_live_single(png, monkeypatch, retailer, webp)
    assert job["status"] == "VERIFIED" and job["provider"] == "fashn" and job["error"] is None
    assert len(fake.runs) == 1  # exactly one FASHN generation
    body = json.loads(fake.runs[0].content)
    head, b64 = body["inputs"]["product_image"].split(",", 1)
    sent = base64.b64decode(b64)
    assert head == "data:image/png;base64"  # FASHN always receives PNG, never WebP
    if webp:
        assert sent != src and px(sent) == px(src)  # converted, identical pixels
    else:
        assert sent == src  # untouched
    assert body["inputs"]["resolution"] == "1k" and body["inputs"]["generation_mode"] == "balanced"
    assert body["inputs"]["num_images"] == 1 and body["inputs"]["output_format"] == "png"
    with SessionLocal() as db:
        assert sum(r.credits for r in db.query(FashnLedger)) == 2  # cap unchanged: one generation = 2 credits


def test_live_cap_is_still_two_credits_for_both_retailers(png, monkeypatch):
    from app.config import settings

    assert settings.fashn_credit_cap == 2
    job, fake, *_ = run_live_single(png, monkeypatch, "aliexpress", True)
    assert job["status"] == "VERIFIED"
    second = c.post("/api/tryon", json={"product_id": job["product"]["id"], "person_path": "persons/" + job["person_url"].split("/")[-1]}).json()
    assert second["status"] == "FAILED" and "allowance" in second["error"] and len(fake.runs) == 1


def test_single_live_try_on_refused_without_both_vision_models(png, monkeypatch):
    from app.config import settings

    live_env(monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key", "")
    person = c.post("/api/uploads/person", files={"file": ("p.png", png(), "image/png")}).json()
    seed_row = seed_product_row(png)
    r = c.post("/api/tryon", json={"product_id": seed_row, "person_path": person["person_path"]})
    assert r.status_code == 400 and "OpenAI + Gemini" in r.json()["detail"]


def seed_product_row(png):
    rel, sha = storage.save_bytes("products", png(900, 1100), "png")
    with SessionLocal() as db:
        p = Product(retailer="ebay", retailer_product_id="1", name="x", product_url="u", source_image_url="i",
                    image_path=rel, image_sha256=sha, image_width=900, image_height=1100)
        db.add(p)
        db.commit()
        return p.id


def test_webp_model_image_is_blocked_by_the_guard(png, monkeypatch):
    """Defence in depth: even if a WebP slipped through, the guard refuses to send it to FASHN."""
    from app.services.fashn.guard import LiveCallBlocked, preflight_live

    live_env(monkeypatch)
    rel, sha = storage.save_bytes("products", webp_bytes(), "webp")
    prel, psha = storage.save_bytes("persons", png(), "png")
    with SessionLocal() as db:
        p = Product(retailer="aliexpress", retailer_product_id="1", name="x", product_url="u", source_image_url="i",
                    image_path=rel, image_sha256=sha, image_width=900, image_height=1100)
        db.add(p)
        db.commit()
        j = TryOnJob(product_id=p.id, person_image_path=prel, person_image_sha256=psha, provider="x")
        db.add(j)
        db.commit()
        with pytest.raises(LiveCallBlocked, match="JPEG or PNG"):
            preflight_live(db, j, p)
