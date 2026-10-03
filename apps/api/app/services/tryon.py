import json
import logging

from ..db import SessionLocal
from ..models import Product, TryOnJob, TryOnResult
from .fashn.client import get_client
from .fashn.guard import authorize_live_call, live_enabled, preflight_live
from .storage import local as storage
from .verification.base import get_verifier

log = logging.getLogger(__name__)


def _ext(rel: str) -> str:
    return rel.rsplit(".", 1)[-1]


async def run_job(job_id: int, client=None) -> None:
    """SELECTED -> SENT -> GENERATED -> VERIFIED | FAILED.

    Exactly one generation per job. Any failure ends the job as FAILED; nothing is retried, and a failed
    live job never falls back to another provider or to mock. Awaited inline by the request handler
    (never scheduled in the background).
    """
    with SessionLocal() as db:
        job = db.get(TryOnJob, job_id)
        product = db.get(Product, job.product_id)
        try:
            person = storage.read_bytes(job.person_image_path)
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

            res = await client.try_on(person, _ext(job.person_image_path), prod_bytes, _ext(product.image_path),
                                      on_prediction_id=remember_prediction)
            rel, digest = storage.save_bytes("results", res.image_bytes, "png")  # raw output, untouched
            job.status = "GENERATED"
            db.commit()

            v = await get_verifier().verify(person, prod_bytes, res.image_bytes)
            db.add(TryOnResult(
                job_id=job.id, result_path=rel, result_sha256=digest, verification_overall=v.overall,
                verification_json=json.dumps({"checks": v.checks, "notes": v.notes}),
            ))
            job.status = "VERIFIED"
            db.commit()
        except Exception as e:  # terminal: report clearly, never retry
            log.warning("job %s failed: %s", job_id, type(e).__name__)
            job.status = "FAILED"
            job.error = f"{type(e).__name__}: {str(e)[:300]}"
            db.commit()
