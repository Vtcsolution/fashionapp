from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import FashnLedger
from .builder import CREDITS_PER_GENERATION

AUTH_PHRASE = "RUN THE 2-CREDIT FASHN TEST"


class LiveCallBlocked(Exception):
    pass


def credits_spent(db: Session) -> int:
    return db.scalar(select(func.coalesce(func.sum(FashnLedger.credits), 0))) or 0


def authorize_live_call(db: Session, job_id: int) -> None:
    """Raise unless every condition holds; then record the spend BEFORE the call is made."""
    if not settings.fashn_live_enabled:
        raise LiveCallBlocked("FASHN_LIVE_ENABLED is not true; live calls are disabled")
    if settings.fashn_live_authorization != AUTH_PHRASE:
        raise LiveCallBlocked("missing explicit authorization phrase")
    if not settings.fashn_api_key:
        raise LiveCallBlocked("FASHN_API_KEY not configured")
    if credits_spent(db) + CREDITS_PER_GENERATION > settings.fashn_credit_cap:
        raise LiveCallBlocked(f"credit cap of {settings.fashn_credit_cap} reached; no further live calls")
    db.add(FashnLedger(job_id=job_id, credits=CREDITS_PER_GENERATION))
    db.commit()
