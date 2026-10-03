import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Product, TryOnJob, TryOnResult
from ..services.fashn.guard import vto_mode
from ..services.images import ImageRejected, download_best_image, validate_image_bytes
from ..services.retailers.aggregator import ACTIVE, search_all
from ..services.retailers.base import RetailerProduct
from ..services.storage import local as storage
from ..services.tryon import run_job

router = APIRouter(prefix="/api")
MAX_PERSON_BYTES = 15 * 1024 * 1024


@router.get("/health")
def health():
    # booleans only; no secret values
    return {"ok": True, "configured": settings.configured(), "vto_mode": vto_mode(),
            "active_retailers": [r.name for r in ACTIVE]}


@router.get("/products/search")
async def search(q: str, limit: int = 8):
    if not q.strip():
        raise HTTPException(400, "empty query")
    products, status = await search_all(q.strip(), limit)
    return {"products": products, "retailers": status}


@router.post("/uploads/person")
async def upload_person(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_PERSON_BYTES:
        raise HTTPException(413, "image too large")
    try:
        _, _, ext = validate_image_bytes(data, min_side=256)
    except ImageRejected as e:
        raise HTTPException(400, str(e))
    rel, digest = storage.save_bytes("persons", data, ext)  # stored untouched
    return {"person_path": rel, "sha256": digest, "url": f"/files/{rel}"}


@router.post("/products/select")
async def select_product(p: RetailerProduct, db: Session = Depends(get_db)):
    """Download + validate the best image for the chosen product and persist exactly what will be sent."""
    candidates = list(dict.fromkeys([p.image_url, *p.image_urls]))
    try:
        url, data, w, h, ext = await download_best_image(candidates)
    except ImageRejected as e:
        raise HTTPException(422, str(e))
    rel, digest = storage.save_bytes("products", data, ext)
    row = Product(
        retailer=p.retailer, retailer_product_id=p.product_id, name=p.name, price=p.price, currency=p.currency,
        product_url=p.url, affiliate_url=p.affiliate_url, source_image_url=url, image_path=rel, image_sha256=digest, image_width=w, image_height=h,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "image_url": f"/files/{rel}", "width": w, "height": h, "sha256": digest}


class TryOnRequest(BaseModel):
    product_id: int
    person_path: str


def _job_view(db: Session, job: TryOnJob):
    res = db.query(TryOnResult).filter_by(job_id=job.id).first()
    prod = db.get(Product, job.product_id)
    return {
        "id": job.id, "status": job.status, "provider": job.provider, "error": job.error,
        "person_url": f"/files/{job.person_image_path}",
        "product": {"id": prod.id, "name": prod.name, "price": prod.price, "currency": prod.currency,
                    "retailer": prod.retailer, "url": prod.product_url, "affiliate_url": prod.affiliate_url,
                    "image_url": f"/files/{prod.image_path}", "source_image_url": prod.source_image_url},
        "result": res and {
            "url": f"/files/{res.result_path}",
            "verification": {"overall": res.verification_overall, **json.loads(res.verification_json)},
        },
    }


@router.post("/tryon")
async def create_tryon(req: TryOnRequest, db: Session = Depends(get_db)):
    prod = db.get(Product, req.product_id)
    if not prod:
        raise HTTPException(404, "product not selected")
    if not req.person_path.startswith("persons/") or ".." in req.person_path \
            or not storage.abs_path(req.person_path).is_file():
        raise HTTPException(400, "unknown person image")
    job = TryOnJob(
        product_id=prod.id, person_image_path=req.person_path,
        person_image_sha256=req.person_path.split("/")[-1].split(".")[0],
        provider=vto_mode(),
    )
    db.add(job)
    db.commit()
    await run_job(job.id)  # inline, once; never a background task
    db.refresh(job)
    return _job_view(db, job)


@router.get("/tryon/{job_id}")
def get_tryon(job_id: int, db: Session = Depends(get_db)):
    job = db.get(TryOnJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return _job_view(db, job)
