"""Per-user idempotency keys + produce-claim lease column.

Two changes, both from the 2026-08-04 backend review:
  1. Idempotency keys become unique per (user_id, idempotency_key)
     instead of globally — a client can only ever collide with its own
     keys (Stripe-style scoping; the old global constraint let one
     user's key collision surface another user's job_id).
  2. deep_analysis_jobs.produce_claimed_at — the produce-claim lease
     timestamp that makes claim-before-produce crash-safe: a claim whose
     holder died mid-produce goes stale and is reclaimed by the retry
     loop, instead of the job being silently lost forever.

Revision ID: 002_user_idem_lease
Revises: 001_baseline
Create Date: 2026-08-04

"""
import sqlalchemy as sa
from alembic import op

revision = "002_user_idem_lease"
down_revision = "001_baseline"
branch_labels = None
depends_on = None

# Postgres's auto-generated name for the baseline's unique=True column.
_OLD_UNIQUE = "deep_analysis_jobs_idempotency_key_key"
_NEW_UNIQUE = "uq_deep_analysis_jobs_user_id_idempotency_key"


def upgrade() -> None:
    # IF EXISTS: tolerate a database where the old constraint was already
    # dropped by hand (or named differently by an older Postgres).
    op.execute(f"ALTER TABLE deep_analysis_jobs DROP CONSTRAINT IF EXISTS {_OLD_UNIQUE}")
    op.create_unique_constraint(
        _NEW_UNIQUE, "deep_analysis_jobs", ["user_id", "idempotency_key"]
    )
    op.add_column(
        "deep_analysis_jobs",
        sa.Column("produce_claimed_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deep_analysis_jobs", "produce_claimed_at")
    op.drop_constraint(_NEW_UNIQUE, "deep_analysis_jobs")
    op.create_unique_constraint(_OLD_UNIQUE, "deep_analysis_jobs", ["idempotency_key"])
