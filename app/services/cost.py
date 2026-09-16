"""Per-call AI cost log + budget guard.

est_cost_usd uses paid-tier list prices (USD per 1M tokens) as a "what this would cost" figure;
on the free tier the billed amount is $0. Verify prices on the Gemini pricing page.
"""
from datetime import datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AICall

PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-3.1-flash-lite": (0.10, 0.40),  # ASSUMED flash-lite rate - verify on pricing page
    "gemini-embedding-001": (0.15, 0.0),
}


class BudgetExceeded(RuntimeError):
    pass


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = PRICES_PER_MILLION.get(model, (0.0, 0.0))
    return round((input_tokens * in_price + output_tokens * out_price) / 1_000_000, 8)


def record_call(
    session: Session,
    *,
    tenant_id: int,
    kind: str,
    model: str,
    ok: bool,
    target_ref: str | None = None,
    job_id: int | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    error: str | None = None,
) -> AICall:
    call = AICall(
        tenant_id=tenant_id,
        job_id=job_id,
        kind=kind,
        model=model,
        target_ref=target_ref,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        est_cost_usd=estimate_cost(model, input_tokens, output_tokens),
        latency_ms=latency_ms,
        ok=ok,
        error=error,
    )
    session.add(call)
    return call


def calls_today(session: Session, tenant_id: int) -> int:
    start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    return session.scalar(
        select(func.count(AICall.id)).where(AICall.tenant_id == tenant_id, AICall.created_at >= start)
    ) or 0


def ensure_budget(session: Session, tenant_id: int, daily_limit: int) -> None:
    used = calls_today(session, tenant_id)
    if used >= daily_limit:
        raise BudgetExceeded(f"daily AI call budget reached ({used}/{daily_limit})")