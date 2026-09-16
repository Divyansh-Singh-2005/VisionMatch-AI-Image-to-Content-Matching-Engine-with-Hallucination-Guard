"""initial schema: tenants, images, metadata, posts, embeddings, jobs, ai_calls, suggestions, reviews

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBED_DIM = 768


def _created() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        _created(),
    )
    op.execute("INSERT INTO tenants (id, name) VALUES (1, 'demo')")
    op.execute("SELECT setval(pg_get_serial_sequence('tenants', 'id'), 1)")

    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text()),
        _created(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_images_tenant_sha256"),
        sa.CheckConstraint("status IN ('pending','tagged','flagged','failed')", name="ck_images_status"),
    )
    op.create_index("ix_images_tenant_status", "images", ["tenant_id", "status"])

    op.create_table(
        "image_metadata",
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("images.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("subject", sa.String(80), nullable=False),
        sa.Column("subject_canonical", sa.String(32), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        _created(),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_image_metadata_confidence"),
    )
    op.create_index("ix_image_metadata_subject_canonical", "image_metadata", ["subject_canonical"])

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_subject", sa.String(32)),
        _created(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_posts_tenant_slug"),
    )

    op.create_table(
        "embeddings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("owner_type", sa.String(8), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("vector", Vector(EMBED_DIM), nullable=False),
        _created(),
        sa.UniqueConstraint("owner_type", "owner_id", "model", name="uq_embeddings_owner_model"),
        sa.CheckConstraint("owner_type IN ('image','post')", name="ck_embeddings_owner_type"),
    )
    op.create_index("ix_embeddings_tenant_owner_type", "embeddings", ["tenant_id", "owner_type"])
    op.execute(
        "CREATE INDEX ix_embeddings_vector_hnsw ON embeddings USING hnsw (vector vector_cosine_ops)"
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flagged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        _created(),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_tenant_created", "jobs", ["tenant_id", "created_at"])

    op.create_table(
        "job_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.UniqueConstraint("job_id", "target_id", name="uq_job_items_job_target"),
    )
    op.create_index("ix_job_items_job_status", "job_items", ["job_id", "status"])

    op.create_table(
        "ai_calls",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("target_ref", sa.String(64)),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("est_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        _created(),
    )
    op.create_index("ix_ai_calls_tenant_created", "ai_calls", ["tenant_id", "created_at"])
    op.create_index("ix_ai_calls_kind", "ai_calls", ["kind"])

    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_id", sa.Integer(), sa.ForeignKey("images.id", ondelete="SET NULL")),
        sa.Column("rank", sa.Integer()),
        sa.Column("score", sa.Float()),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        _created(),
        sa.CheckConstraint(
            "decision IN ('SUGGESTED','REJECTED','NO_CONFIDENT_MATCH')", name="ck_suggestions_decision"
        ),
    )
    op.create_index("ix_suggestions_post_created", "suggestions", ["post_id", "created_at"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "suggestion_id",
            sa.Integer(),
            sa.ForeignKey("suggestions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        _created(),
        sa.CheckConstraint("action IN ('approve','reject')", name="ck_reviews_action"),
    )


def downgrade() -> None:
    for table in (
        "reviews", "suggestions", "ai_calls", "job_items", "jobs",
        "embeddings", "posts", "image_metadata", "images", "tenants",
    ):
        op.drop_table(table)