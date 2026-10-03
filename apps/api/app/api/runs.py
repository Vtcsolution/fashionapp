"""Multi-product try-on runs.

POST /api/tryon/run              plan N products (ordered); nothing is generated yet
POST /api/tryon/run/{id}/next    execute the next planned step: exactly ONE FASHN generation per call
GET  /api/tryon/run/{id}         full tracking view

The plan is stored server-side before any step runs, so a selected product can never be dropped
silently. Step N starts from the raw result of step N-1. The first failed step stops the run and marks
every later step SKIPPED. Nothing is retried.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Product, TryOnJob, TryOnResult, TryOnRun
from ..services.fashn.builder import CREDITS_PER_GENERATION
from ..services.fashn.guard import credits_spent, live_enabled, vto_mode
from ..services.storage import local as storage
from ..services.tryon import run_job
from .routes import _job_view

router = APIRouter(prefix="/api")
MAX_ITEMS = 8


class RunRequest(BaseModel):
    person_path: str
    product_ids: list[int]  # in application order


def _presence_label(step: dict) -> str:
    """What we can honestly say about 'is this product in the image?'."""
    res = step["result"]
    if not res:
        return "NOT_GENERATED"
    presence = res["verification"]["checks"].get("product_presence", "NOT_CHECKED")
    return {"PASS": "PRESENT", "FAIL": "ABSENT"}.get(presence, "REVIEW_REQUIRED")


def _run_view(db: Session, run: TryOnRun) -> dict:
    db.expire_all()
    jobs = db.query(TryOnJob).filter_by(run_id=run.id).order_by(TryOnJob.step).all()
    steps = []
    for j in jobs:
        v = _job_view(db, j)
        v["product_presence"] = _presence_label(v)
        steps.append(v)
    done = [s for s in steps if s["status"] == "VERIFIED" and s["result"]]
    present = sum(1 for s in done if s["product_presence"] == "PRESENT")
    summary = f"{len(done)} of {len(steps)} products applied"
    summary += f"; {present} confirmed present." if present else "; none automatically confirmed — manual review required."
    return {
        "id": run.id, "status": run.status, "total_steps": len(steps), "person_url": f"/files/{run.person_image_path}",
        "steps": steps, "final_url": done[-1]["result"]["url"] if done else None, "summary": summary,
    }


@router.post("/tryon/run")
def create_run(req: RunRequest, db: Session = Depends(get_db)):
    if not req.product_ids:
        raise HTTPException(400, "select at least one product")
    if len(req.product_ids) > MAX_ITEMS:
        raise HTTPException(400, f"at most {MAX_ITEMS} products per run")
    if len(set(req.product_ids)) != len(req.product_ids):
        raise HTTPException(400, "the same product was selected twice")
    if not req.person_path.startswith("persons/") or ".." in req.person_path \
            or not storage.abs_path(req.person_path).is_file():
        raise HTTPException(400, "unknown person image")
    products = [db.get(Product, pid) for pid in req.product_ids]
    if any(p is None for p in products):
        raise HTTPException(404, "a selected product was not found")
    if live_enabled():  # never start a paid chain that cannot finish under the credit budget
        need = len(products) * CREDITS_PER_GENERATION
        left = settings.fashn_credit_cap - credits_spent(db)
        if need > left:
            raise HTTPException(400, f"{len(products)} live generations need {need} credits but only "
                                     f"{max(left, 0)} remain in the budget. Nothing was started.")
    run = TryOnRun(person_image_path=req.person_path, status="PLANNED")
    db.add(run)
    db.commit()
    prev_id = None
    for i, p in enumerate(products, start=1):
        job = TryOnJob(run_id=run.id, product_id=p.id, step=i, parent_job_id=prev_id, status="PLANNED",
                       provider=vto_mode(), person_image_path=req.person_path,
                       person_image_sha256=req.person_path.split("/")[-1].split(".")[0])
        db.add(job)
        db.commit()
        prev_id = job.id
    return _run_view(db, run)


@router.post("/tryon/run/{run_id}/next")
async def run_next(run_id: int, db: Session = Depends(get_db)):
    run = db.get(TryOnRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status in ("COMPLETE", "FAILED"):
        raise HTTPException(400, f"run is already {run.status}")
    jobs = db.query(TryOnJob).filter_by(run_id=run.id).order_by(TryOnJob.step).all()
    idx = next((i for i, j in enumerate(jobs) if j.status == "PLANNED"), None)
    if idx is None:
        run.status = "COMPLETE"
        db.commit()
        return _run_view(db, run)
    job = jobs[idx]

    def stop_run(from_idx: int, reason: str):
        for j in jobs[from_idx:]:
            if j.status == "PLANNED":
                j.status, j.error = "SKIPPED", reason
        run.status = "FAILED"
        db.commit()

    if idx > 0:  # base image = raw result of the previous step
        prev = jobs[idx - 1]
        pres = db.query(TryOnResult).filter_by(job_id=prev.id).first()
        if prev.status != "VERIFIED" or not pres:
            stop_run(idx, f"skipped: step {prev.step} did not finish")
            return _run_view(db, run)
        job.person_image_path = pres.result_path
        job.person_image_sha256 = pres.result_sha256
    run.status = "RUNNING"
    db.commit()

    await run_job(job.id)  # exactly one generation; awaited inline, never in the background
    db.refresh(job)
    if job.status != "VERIFIED":
        stop_run(idx + 1, f"skipped: step {job.step} failed")
    elif all(j.status == "VERIFIED" for j in jobs):
        run.status = "COMPLETE"
        db.commit()
    return _run_view(db, run)


@router.get("/tryon/run/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(TryOnRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return _run_view(db, run)
