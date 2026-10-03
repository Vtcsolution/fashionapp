import logging

from ..config import settings
from ..db import SessionLocal
from ..models import Product, TryOnJob, TryOnResult
from .fashn.client import get_client
from .fashn.guard import authorize_live_call
from .storage import local as storage
from .verification.base import get_verifier

log = logging.getLogger(__name__)


def _ext(rel: str) -> str:
    return rel.rsplit(".", 1)[-1]


async def run_job(job_id: int, client=None) -> None:
    """SELECTED -> SENT -> GENERATED -> VERIFIED | FAILED. Exactly one generation; never retried."""
    with SessionLocal() as db:
        job = db.get(TryOnJob, job_id)
        product = db.get(Product, job.product_id)
        try:
            person = storage.read_bytes(job.person_image_path)
            prod_bytes = storage.read_bytes(product.image_path)
            if client is None:
                live = settings.fashn_live_enabled
                if live:
                    authorize_live_call(db, job.id)  # records the spend before the call
                client = get_client(live)
            job.provider = client.provider
            job.status = "SENT"
            db.commit()

            res = await client.try_on(person, _ext(job.person_image_path), prod_bytes, _ext(product.image_path))
            job.fashn_job_id = res.fashn_job_id
            rel, digest = storage.save_bytes("results", res.image_bytes, "png")  # raw output, untouched
            job.status = "GENERATED"
            db.commit()

            v = await get_verifier().verify(person, prod_bytes, res.image_bytes)
            db.add(TryOnResult(
                job_id=job.id, result_path=rel, result_sha256=digest,
                verification_product=v.product, verification_identity=v.identity,
                verification_overall=v.overall, verification_notes=v.notes,
            ))
            job.status = "VERIFIED"
            db.commit()
        except Exception as e:  # any failure stops here; nothing is retried
            log.warning("job %s failed: %s", job_id, type(e).__name__)
            job.status = "FAILED"
            job.error = f"{type(e).__name__}: {str(e)[:300]}"
            db.commit()
