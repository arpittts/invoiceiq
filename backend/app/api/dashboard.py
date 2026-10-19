from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.invoices import summarise
from app.deps import CurrentUser, DbSession
from app.models import ExtractionRun, Invoice, InvoiceStatus
from app.schemas.invoice import InvoiceSummary
from app.services import budget
from app.services.validation import india_today

router = APIRouter(tags=["dashboard"])


class AiUsage(BaseModel):
    extraction_calls: int  # yours, all time
    cost_usd: Decimal  # yours, all time
    calls_today: int  # the whole demo server, today (India time)
    call_limit: int
    cap_reached: bool


class DashboardResponse(BaseModel):
    counts: dict[str, int]  # by status
    approved_total: Decimal
    approved_gst: Decimal
    approved_this_month: Decimal
    recent: list[InvoiceSummary]
    ai: AiUsage


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(user: CurrentUser, db: DbSession) -> DashboardResponse:
    counts = dict(
        db.execute(
            select(Invoice.status, func.count())
            .where(Invoice.user_id == user.id)
            .group_by(Invoice.status)
        ).all()
    )
    approved = (Invoice.user_id == user.id) & (Invoice.status == InvoiceStatus.APPROVED)
    gst = (
        func.coalesce(Invoice.cgst, 0)
        + func.coalesce(Invoice.sgst, 0)
        + func.coalesce(Invoice.igst, 0)
    )
    total, total_gst = db.execute(
        select(func.coalesce(func.sum(Invoice.total), 0), func.coalesce(func.sum(gst), 0)).where(
            approved
        )
    ).one()
    month_start = india_today().replace(day=1)
    this_month = db.scalar(
        select(func.coalesce(func.sum(Invoice.total), 0)).where(
            approved, Invoice.invoice_date >= month_start
        )
    )
    recent = db.scalars(
        select(Invoice)
        .where(Invoice.user_id == user.id)
        .options(selectinload(Invoice.file), selectinload(Invoice.validation_issues))
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .limit(5)
    ).all()
    calls, cost = db.execute(
        select(func.count(ExtractionRun.id), func.coalesce(func.sum(ExtractionRun.cost_usd), 0))
        .join(Invoice, Invoice.id == ExtractionRun.invoice_id)
        .where(Invoice.user_id == user.id)
    ).one()
    usage = budget.usage_today()
    return DashboardResponse(
        counts={s.value: counts.get(s.value, 0) for s in InvoiceStatus},
        approved_total=Decimal(total),
        approved_gst=Decimal(total_gst),
        approved_this_month=Decimal(this_month),
        recent=[summarise(inv) for inv in recent],
        ai=AiUsage(
            extraction_calls=calls,
            cost_usd=Decimal(cost),
            calls_today=usage.calls,
            call_limit=usage.call_limit,
            cap_reached=usage.cap_reached,
        ),
    )
