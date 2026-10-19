from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Auto-approve settings (Settings page). None means "use the server default".
    auto_approve_enabled: Mapped[bool | None] = mapped_column(Boolean)
    auto_approve_min_confidence: Mapped[float | None] = mapped_column(Float)
    auto_approve_min_hsn_confidence: Mapped[float | None] = mapped_column(Float)
