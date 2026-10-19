"""A durable job queue in Postgres: invoice reads survive restarts and retry.

- jobs: one row per piece of work (read an invoice, or a demo invoice replay).
  status: queued -> running -> done | failed. A running job holds a lease
  (locked_until); if its process dies, the lease expires and another run takes it.
- One active job per invoice (partial unique index), so a double-click on
  "Read again" can't queue the same invoice twice.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "invoice_id",
            sa.Integer,
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')", name="ck_jobs_status"
        ),
    )
    op.create_index("ix_jobs_invoice_id", "jobs", ["invoice_id"])
    op.create_index("ix_jobs_due", "jobs", ["status", "run_after"])
    op.create_index(
        "uq_jobs_one_active_per_invoice",
        "jobs",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
