from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LedgerQueryContext(Base):
    """Which user a database connection is answering ledger questions for.

    Rows only ever exist inside the always-rolled-back transaction that runs one
    question (see app/services/ledger_qa.py); the ledger views read from here.
    """

    __tablename__ = "ledger_query_context"

    backend_pid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(Integer)
