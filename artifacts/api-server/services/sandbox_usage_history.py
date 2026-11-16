"""Operator-only access and storage for fixed daily operational counters."""
import hmac
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from database import Session

OPERATOR_TOKEN_ENV = "SANDBOX_OPERATOR_TOKEN"
HISTORY_DAYS = 90
# Operator routes stay out of the public API reference; they still require the operator token.
router = APIRouter(prefix="/api/sandbox/operator", tags=["Service operator"], include_in_schema=False)
COUNTERS = ("run_created", "agent_action_accepted", "run_completed")
operator_bearer = HTTPBearer(auto_error=False)


def operator_token():
    token = os.environ.get(OPERATOR_TOKEN_ENV, "")
    return token if 32 <= len(token.encode("utf-8")) <= 512 else None


def authorize_operator(
    credentials: HTTPAuthorizationCredentials | None = Depends(operator_bearer),
):
    expected = operator_token()
    if expected is None:
        raise HTTPException(503, "Service-operator access is not configured.")
    if (credentials is None
            or len(credentials.credentials) > 512
            or not hmac.compare_digest(credentials.credentials.encode("utf-8"),
                                       expected.encode("utf-8"))):
        raise HTTPException(401, "Service-operator authentication required.",
                            headers={"WWW-Authenticate": "Bearer"})


def utc_today():
    return datetime.now(timezone.utc).date()


def history_cutoff(today=None):
    return (today or utc_today()) - timedelta(days=HISTORY_DAYS - 1)


def record_history_count(db, activity_date: date, name: str, amount: int = 1):
    """Add one fixed aggregate inside the caller's successful event transaction."""
    if operator_token() is None:
        return
    if name not in COUNTERS or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise ValueError("Unsupported operational usage counter")
    increments = {
        "run_created": (amount, 0, 0),
        "agent_action_accepted": (0, amount, 0),
        "run_completed": (0, 0, amount),
    }[name]
    db.execute(text("""
        INSERT INTO sandbox_usage_daily
            (activity_date, run_created, agent_action_accepted, run_completed)
        VALUES (:day, :created, :accepted, :completed)
        ON CONFLICT (activity_date) DO UPDATE SET
            run_created = sandbox_usage_daily.run_created + EXCLUDED.run_created,
            agent_action_accepted =
                sandbox_usage_daily.agent_action_accepted + EXCLUDED.agent_action_accepted,
            run_completed = sandbox_usage_daily.run_completed + EXCLUDED.run_completed
    """), {
        "day": activity_date,
        "created": increments[0],
        "accepted": increments[1],
        "completed": increments[2],
    })


@router.get("/usage/daily")
@router.get("/usage-history", include_in_schema=False)
def read_usage_history(
    response: Response,
    _operator: None = Depends(authorize_operator),
):
    response.headers["Cache-Control"] = "no-store"
    today = utc_today()
    cutoff = history_cutoff(today)
    with Session() as db:
        rows = db.execute(text("""
            SELECT activity_date, run_created, agent_action_accepted, run_completed
            FROM sandbox_usage_daily
            WHERE activity_date >= :cutoff AND activity_date <= :today
            ORDER BY activity_date DESC
        """), {"cutoff": cutoff, "today": today}).mappings().all()
    totals = {
        row["activity_date"]: {name: row[name] for name in COUNTERS}
        for row in rows
    }
    return {"days": [
        {
            "date": (cutoff + timedelta(days=offset)).isoformat(),
            **totals.get(
                cutoff + timedelta(days=offset),
                {name: 0 for name in COUNTERS},
            ),
        }
        for offset in range(HISTORY_DAYS)
    ]}


@router.get("/health")
def read_operator_health(
    response: Response,
    _operator: None = Depends(authorize_operator),
):
    from services.sandbox_security import autovacuum_health_snapshot

    response.headers["Cache-Control"] = "no-store"
    return {"autovacuum": autovacuum_health_snapshot()}


@router.delete("/usage-history/expired")
def delete_expired_usage_history(_operator: None = Depends(authorize_operator)):
    with Session.begin() as db:
        result = db.execute(text("""
            DELETE FROM sandbox_usage_daily WHERE activity_date < :cutoff
        """), {"cutoff": history_cutoff()})
    return {"deleted_buckets": result.rowcount}
