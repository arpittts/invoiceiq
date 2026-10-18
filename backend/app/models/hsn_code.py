from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

EMBEDDING_DIMENSIONS = 384  # BAAI/bge-small-en-v1.5


class HsnCode(Base):
    """One official HSN (goods) or SAC (services) code: the RAG corpus (blueprint section 9).

    One row per code, no text splitting: each code is already a small, self-contained unit.
    """

    __tablename__ = "hsn_codes"
    __table_args__ = (
        Index("ix_hsn_codes_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_hsn_codes_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(3))  # "HSN" or "SAC"
    description: Mapped[str] = mapped_column(Text)
    # Context from parent codes, e.g. the 4-digit heading's text for an 8-digit code.
    heading: Mapped[str | None] = mapped_column(Text)
    chapter: Mapped[str] = mapped_column(String(2))
    default_gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # Where the row came from: file, download date, and the rate notification entry.
    source_note: Mapped[str] = mapped_column(Text)
    # Kept up to date by Postgres itself (generated column) for keyword search.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', code || ' ' || description || ' ' || coalesce(heading, ''))",
            persisted=True,
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
