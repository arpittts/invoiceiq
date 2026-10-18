"""Add hsn_codes (the RAG corpus) and HSN suggestion columns on line_items.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hsn_codes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(3), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("heading", sa.Text),
        sa.Column("chapter", sa.String(2), nullable=False),
        sa.Column("default_gst_rate", sa.Numeric(5, 2)),
        sa.Column("source_note", sa.Text, nullable=False),
        sa.Column(
            "tsv",
            TSVECTOR,
            sa.Computed(
                "to_tsvector('english', code || ' ' || description || ' ' || "
                "coalesce(heading, ''))",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("embedding", Vector(384)),
    )
    op.create_index("ix_hsn_codes_code", "hsn_codes", ["code"])
    op.create_index("ix_hsn_codes_tsv", "hsn_codes", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_hsn_codes_embedding",
        "hsn_codes",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_foreign_key(
        "fk_line_items_hsn_source",
        "line_items",
        "hsn_codes",
        ["hsn_source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("line_items", sa.Column("suggested_hsn_code", sa.String(10)))
    op.add_column("line_items", sa.Column("suggested_gst_rate", sa.Numeric(5, 2)))
    op.add_column("line_items", sa.Column("hsn_confidence", sa.Float))
    op.add_column("line_items", sa.Column("hsn_reason", sa.Text))


def downgrade() -> None:
    for column in ("hsn_reason", "hsn_confidence", "suggested_gst_rate", "suggested_hsn_code"):
        op.drop_column("line_items", column)
    op.drop_constraint("fk_line_items_hsn_source", "line_items", type_="foreignkey")
    op.drop_table("hsn_codes")
