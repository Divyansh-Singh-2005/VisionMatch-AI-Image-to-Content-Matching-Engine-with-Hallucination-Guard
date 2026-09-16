from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

EMBED_DIM = 768


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = _created()


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256", name="uq_images_tenant_sha256"),
        CheckConstraint("status IN ('pending','tagged','flagged','failed')", name="ck_images_status"),
        Index("ix_images_tenant_status", "tenant_id", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    file_path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default="pending", default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ImageMetadata(Base):
    __tablename__ = "image_metadata"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_image_metadata_confidence"),
        Index("ix_image_metadata_subject_canonical", "subject_canonical"),
    )
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    subject: Mapped[str] = mapped_column(String(80))
    subject_canonical: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(16))
    attributes: Mapped[list] = mapped_column(JSONB, default=list)
    caption: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    model: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _created()


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_posts_tenant_slug"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    slug: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    target_subject: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created()


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "model", name="uq_embeddings_owner_model"),
        CheckConstraint("owner_type IN ('image','post')", name="ck_embeddings_owner_type"),
        Index("ix_embeddings_tenant_owner_type", "tenant_id", "owner_type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    owner_type: Mapped[str] = mapped_column(String(8))
    owner_id: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(80))
    text_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), server_default="queued", default="queued")
    total: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    done: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    flagged: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    failed: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobItem(Base):
    __tablename__ = "job_items"
    __table_args__ = (
        UniqueConstraint("job_id", "target_id", name="uq_job_items_job_target"),
        Index("ix_job_items_job_status", "job_id", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="queued", default="queued")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class AICall(Base):
    __tablename__ = "ai_calls"
    __table_args__ = (
        Index("ix_ai_calls_tenant_created", "tenant_id", "created_at"),
        Index("ix_ai_calls_kind", "kind"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(80))
    target_ref: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, server_default="0", default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, server_default="0", default=0)
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Suggestion(Base):
    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('SUGGESTED','REJECTED','NO_CONFIDENT_MATCH')", name="ck_suggestions_decision"
        ),
        Index("ix_suggestions_post_created", "post_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    image_id: Mapped[int | None] = mapped_column(ForeignKey("images.id", ondelete="SET NULL"))
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(24))
    reasons: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (CheckConstraint("action IN ('approve','reject')", name="ck_reviews_action"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suggestion_id: Mapped[int] = mapped_column(
        ForeignKey("suggestions.id", ondelete="CASCADE"), unique=True
    )
    action: Mapped[str] = mapped_column(String(8))
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = _created()