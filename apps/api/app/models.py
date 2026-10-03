from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    retailer: Mapped[str] = mapped_column(String(32))
    retailer_product_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(Text)
    price: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    product_url: Mapped[str] = mapped_column(Text)
    affiliate_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="other")
    source_image_url: Mapped[str] = mapped_column(Text)  # exact URL we downloaded
    image_path: Mapped[str] = mapped_column(Text)  # exact bytes we send to FASHN (PNG if the source was WebP)
    image_sha256: Mapped[str] = mapped_column(String(64))  # hash of the SENT file
    # The retailer file as downloaded, kept untouched. Same as the sent file unless a lossless conversion happened.
    original_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    sent_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    image_converted: Mapped[bool] = mapped_column(Boolean, default=False)
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Per-product state is kept on the job so 1 -> N products only needs more jobs/items later.
# States: SELECTED -> SENT -> GENERATED -> VERIFIED | FAILED
class TryOnRun(Base):
    """A multi-product try-on plan. All steps are stored up front, so no selected product can be dropped silently.

    PLANNED -> RUNNING -> COMPLETE | FAILED. Each step is one TryOnJob (one FASHN generation).
    """

    __tablename__ = "tryon_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    person_image_path: Mapped[str] = mapped_column(Text)
    # PLANNED -> RUNNING -> FINISHED (every step generated) | STOPPED (a step failed or was rejected).
    # FINISHED says nothing about quality: that is final_verification_json (real vision check of the final image).
    status: Mapped[str] = mapped_column(String(16), default="PLANNED")
    final_verification_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TryOnJob(Base):
    __tablename__ = "tryon_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # set when part of a multi-product run
    # Step N uses the raw result of step N-1 (parent_job_id) as its base image; step 1 uses the uploaded person.
    # Job states: PLANNED -> SELECTED -> SENT -> GENERATED (an image exists; NOT yet checked) ->
    #   VERIFIED         OpenAI AND Gemini both analysed it and passed every check (the only "verified")
    #   REVIEW_REQUIRED  generated, but not confirmed (uncertain, model outage, or mock/no verification)
    #   REJECTED         a vision model clearly found a problem; the raw image is kept, the run stops
    #   FAILED | SKIPPED
    step: Mapped[int] = mapped_column(Integer, default=1)
    parent_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    person_image_path: Mapped[str] = mapped_column(Text)  # base image sent as model_image
    person_image_sha256: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(16))  # mock | fashn
    fashn_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="SELECTED")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TryOnResult(Base):
    __tablename__ = "tryon_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("tryon_jobs.id"))
    result_path: Mapped[str] = mapped_column(Text)
    result_sha256: Mapped[str] = mapped_column(String(64))
    verification_overall: Mapped[str] = mapped_column(String(24))
    verification_json: Mapped[str] = mapped_column(Text)  # {"checks": {...}, "notes": "..."}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FashnLedger(Base):
    """Credits are recorded BEFORE a live call, so a crash can never allow a second spend."""

    __tablename__ = "fashn_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
