"""Baseline: the schema exactly as it exists in production today.

This is the starting point for Alembic taking over schema management from
init_db's old create_all. The LIVE database already has all of this —
run `alembic stamp 001_baseline` against it once (never `upgrade`, which
would try to re-create existing tables). Fresh databases run it for real.

Revision ID: 001_baseline
Revises:
Create Date: 2026-08-04

"""
import sqlalchemy as sa
from alembic import op

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# Matches the enum type SQLAlchemy's create_all generated from db.JobStatus
# (Mapped[JobStatus] -> native Postgres enum named after the class).
_jobstatus = sa.Enum(
    "queued", "fetching", "simulating", "summarizing", "done", "failed", name="jobstatus"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("auth_provider_id", sa.String, nullable=False, unique=True),
        sa.Column("email", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
        sa.Column("title", sa.String, nullable=True),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("projections", sa.JSON, nullable=True),
        sa.Column("news", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "deep_analysis_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # Baseline keeps the ORIGINAL global unique constraint —
        # migration 002 replaces it with the per-user composite one.
        sa.Column("idempotency_key", sa.String, nullable=False, unique=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("player_name", sa.String, nullable=False),
        sa.Column("player_id", sa.Integer, nullable=True),
        sa.Column("status", _jobstatus, nullable=False),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("error", sa.String, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False),
        sa.Column("produced", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("deep_analysis_jobs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
    _jobstatus.drop(op.get_bind(), checkfirst=True)
