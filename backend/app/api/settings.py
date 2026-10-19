from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import CurrentUser, DbSession
from app.services import user_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class AutoApproveSettings(BaseModel):
    auto_approve_enabled: bool
    # Below 0.5 a "confident" read means little, so lower values aren't accepted.
    auto_approve_min_confidence: float = Field(ge=0.5, le=1.0)
    auto_approve_min_hsn_confidence: float = Field(ge=0.5, le=1.0)


class SettingsResponse(AutoApproveSettings):
    defaults: AutoApproveSettings  # the server's defaults, for "reset"


def _as_model(values: user_settings.AutoApprove) -> AutoApproveSettings:
    return AutoApproveSettings(
        auto_approve_enabled=values.enabled,
        auto_approve_min_confidence=values.min_confidence,
        auto_approve_min_hsn_confidence=values.min_hsn_confidence,
    )


def _response(user) -> SettingsResponse:
    current = _as_model(user_settings.effective(user))
    return SettingsResponse(
        **current.model_dump(), defaults=_as_model(user_settings.server_defaults())
    )


@router.get("", response_model=SettingsResponse)
def get_settings_for_user(user: CurrentUser) -> SettingsResponse:
    return _response(user)


@router.put("", response_model=SettingsResponse)
def update_settings(
    body: AutoApproveSettings, user: CurrentUser, db: DbSession
) -> SettingsResponse:
    """Applies to invoices read from now on; nothing already processed changes."""
    user.auto_approve_enabled = body.auto_approve_enabled
    user.auto_approve_min_confidence = body.auto_approve_min_confidence
    user.auto_approve_min_hsn_confidence = body.auto_approve_min_hsn_confidence
    db.commit()
    return _response(user)
