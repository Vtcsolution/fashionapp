import json
import logging

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Product, TryOnJob, TryOnResult
from .fashn.client import get_client
from .fashn.guard import authorize_live_call, live_enabled, preflight_live
from .storage import local as storage
from .verification.base import VerifyContext, get_verifier

log = logging.getLogger(__name__)

# Statuses that mean "an image was generated and the run may continue".
ACCEPTED = ("VERIFIED", "REVIEW_REQUIRED")


def _ext(rel: str) -> str:
    return rel.rsplit(".", 1)[-1]


def original_person_path(db: Session, job: TryOnJob) -> str:
    """The user's uploaded photo: follow parent steps back to step 1, whose base image is that upload."""
    cur = job
    while cur.parent_job_id is not None:
        parent = db.get(TryOnJob, cur.parent_job_id)
        if parent is None:
            break
        cur = parent
    return cur.person_image_path


async def run_job(job_id: int, client=None) -> None:
    """PLANNED/SELECTED -> SENT -> GENERATED -> VERIFIED | REVIEW_REQUIRED | REJECTED, or FAILED.

    Exactly one FASHN generation per job.
      VERIFIED         only when OpenAI AND Gemini both analysed the images and every check passed
      REVIEW_REQUIRED  an image exists but nothing confirmed it (uncertain, outage, mock): the run may continue
      REJECTED         a model clearly found a problem: the raw result is kept and shown, the run stops
      FAILED           an error (FASHN, storage, ...): nothing is retried, and a failed live job never falls back
                       to another provider or to mock
    Awaited inline by the request handler (never in the background).
    """
    with SessionLocal() as db:
        job = db.get(TryOnJob, job_id)
        product = db.get(Product, job.product_id)
        try:
            base = storage.read_bytes(job.person_image_path)  # step 1: the upload; step N: previous raw result
            prod_bytes = storage.read_bytes(product.image_path)
            if client is None:
                if live_enabled():
                    preflight_live(db, job, product)
                    authorize_live_call(db, job.id)  # records the spend before the call
                client = get_client()
            job.provider = client.provider
            job.status = "SENT"
            db.commit()

            def remember_prediction(pid: str):  # saved as soon as FASHN issues it, even if the job later fails
                job.fashn_job_id = pid
                db.commit()

            res = await client.try_on(base, _ext(job.person_image_path), prod_bytes, _ext(product.image_path),
                                      on_prediction_id=remember_prediction)
            rel, digest = storage.save_bytes("results", res.image_bytes, "png")  # raw output, untouched
            job.status = "GENERATED"  # an image exists; it has NOT been checked yet
            db.commit()

            ctx = VerifyContext(
                step=job.step, product_name=product.name, category=product.category,
                original=storage.read_bytes(original_person_path(db, job)), previous=base,
                product=prod_bytes, result=res.image_bytes,
            )
            v = await get_verifier(client.provider).verify(ctx)
            db.add(TryOnResult(
                job_id=job.id, result_path=rel, result_sha256=digest, verification_overall=v.overall,
                verification_json=json.dumps({"checks": v.checks, "notes": v.notes, "models": v.models,
                                              "performed_by": v.performed_by}),
            ))
            if v.overall == "FAIL":
                job.status = "REJECTED"
                job.error = f"verification failed: {v.notes[:250]}"
            elif v.overall == "PASS" and v.performed_by:
                job.status = "VERIFIED"
            else:
                job.status = "REVIEW_REQUIRED"
            db.commit()
        except Exception as e:  # terminal: report clearly, never retry
            log.warning("job %s failed: %s", job_id, type(e).__name__)
            job.status = "FAILED"
            job.error = f"{type(e).__name__}: {str(e)[:300]}"
            db.commit()
