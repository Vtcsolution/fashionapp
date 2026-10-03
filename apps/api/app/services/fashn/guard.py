import os

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import FashnLedger, Product, TryOnJob
from ..storage import local as storage
from .builder import CREDITS_PER_GENERATION, FIRST_TEST_CONFIG

AUTH_PHRASE = "RUN THE 2-CREDIT FASHN TEST"
EXPECTED_MODEL = "tryon-max"
# Retailers whose products may be tried on live: the two active product sources. CJ/Rakuten stay disabled.
LIVE_RETAILERS = ("ebay", "aliexpress")


class LiveCallBlocked(Exception):
    pass


# Both flags are read from the PROCESS environment only (never from .env), so the authorization
# exists only for the single run in which the operator supplied it.
def live_enabled() -> bool:
    return os.environ.get("FASHN_LIVE_ENABLED", "").strip().lower() == "true"


def live_authorized() -> bool:
    return os.environ.get("FASHN_LIVE_AUTHORIZATION", "") == AUTH_PHRASE


def vto_mode() -> str:
    return "live" if live_enabled() else "mock"


def credits_spent(db: Session) -> int:
    return db.scalar(select(func.coalesce(func.sum(FashnLedger.credits), 0))) or 0


def preflight_live(db: Session, job: TryOnJob, product: Product) -> None:
    """Final checks before any live FASHN call. Raises LiveCallBlocked; has no side effects."""
    if not live_enabled():
        raise LiveCallBlocked("FASHN_LIVE_ENABLED is not true; live calls are disabled")
    if not live_authorized():
        raise LiveCallBlocked("missing explicit FASHN_LIVE_AUTHORIZATION phrase")
    if not settings.fashn_api_key:
        raise LiveCallBlocked("FASHN_API_KEY not configured")
    if product.retailer not in LIVE_RETAILERS:
        raise LiveCallBlocked(f"retailer '{product.retailer}' is not enabled for live try-on")
    if product.image_path.rsplit(".", 1)[-1] not in ("jpg", "png"):  # FASHN is documented for JPEG/PNG only
        raise LiveCallBlocked("product image must be JPEG or PNG (convert WebP losslessly first)")
    # Step 1 starts from the uploaded person; later steps start from the previous step's raw result.
    allowed_base = ("persons/",) if job.step <= 1 else ("results/",)
    if not job.person_image_path.startswith(allowed_base) or not storage.abs_path(job.person_image_path).is_file():
        raise LiveCallBlocked("base image missing" if job.step > 1 else "person image missing")
    if job.person_image_path.rsplit(".", 1)[-1] not in ("jpg", "png"):
        raise LiveCallBlocked("model image must be JPEG or PNG")
    if not storage.abs_path(product.image_path).is_file():
        raise LiveCallBlocked("product image missing")
    if settings.fashn_model != EXPECTED_MODEL:
        raise LiveCallBlocked(f"model must be {EXPECTED_MODEL}")
    if FIRST_TEST_CONFIG != {"resolution": "1k", "generation_mode": "balanced", "num_images": 1, "output_format": "png"}:
        raise LiveCallBlocked("request configuration differs from the authorized first-test configuration")
    # One more generation must still fit under the cap (default cap 2 == exactly one generation).
    if credits_spent(db) + CREDITS_PER_GENERATION > settings.fashn_credit_cap:
        raise LiveCallBlocked("the live-call allowance has already been consumed (credit cap reached)")


def authorize_live_call(db: Session, job_id: int) -> None:
    """Record the spend BEFORE the call, so a crash can never allow a second charge."""
    if credits_spent(db) + CREDITS_PER_GENERATION > settings.fashn_credit_cap:
        raise LiveCallBlocked(f"credit cap of {settings.fashn_credit_cap} reached; no further live calls")
    db.add(FashnLedger(job_id=job_id, credits=CREDITS_PER_GENERATION))
    db.commit()
