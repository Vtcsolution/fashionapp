from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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
    source_image_url: Mapped[str] = mapped_column(Text)  # exact URL we downloaded
    image_path: Mapped[str] = mapped_column(Text)  # exact bytes we will send
    image_sha256: Mapped[str] = mapped_column(String(64))
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Per-product state is kept on the job so 1 -> N products only needs more jobs/items later.
# States: SELECTED -> SENT -> GENERATED -> VERIFIED | FAILED
class TryOnJob(Base):
    __tablename__ = "tryon_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    person_image_path: Mapped[str] = mapped_column(Text)
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
    verification_product: Mapped[str] = mapped_column(String(16))
    verification_identity: Mapped[str] = mapped_column(String(16))
    verification_overall: Mapped[str] = mapped_column(String(16))
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class FashnLedger(Base):
    """Credits are recorded BEFORE a live call, so a crash can never allow a second spend."""

    __tablename__ = "fashn_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer)
    credits: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
