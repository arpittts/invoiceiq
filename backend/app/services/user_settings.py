"""Per-user auto-approve settings (the Settings page). None means "server default"."""

from dataclasses import dataclass

from app.config import get_settings
from app.models import User


@dataclass(frozen=True)
class AutoApprove:
    enabled: bool
    min_confidence: float
    min_hsn_confidence: float


def server_defaults() -> AutoApprove:
    settings = get_settings()
    return AutoApprove(
        enabled=settings.auto_approve_enabled,
        min_confidence=settings.auto_approve_min_confidence,
        min_hsn_confidence=settings.auto_approve_min_hsn_confidence,
    )


def effective(user: User) -> AutoApprove:
    defaults = server_defaults()
    return AutoApprove(
        enabled=defaults.enabled
        if user.auto_approve_enabled is None
        else user.auto_approve_enabled,
        min_confidence=defaults.min_confidence
        if user.auto_approve_min_confidence is None
        else user.auto_approve_min_confidence,
        min_hsn_confidence=defaults.min_hsn_confidence
        if user.auto_approve_min_hsn_confidence is None
        else user.auto_approve_min_hsn_confidence,
    )
