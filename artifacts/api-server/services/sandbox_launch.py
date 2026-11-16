"""Shared, transactional admission bounds. No user identifiers in capacity data."""
import time
from sqlalchemy import text
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Literal
from database import Session
from services.sandbox_security import retention_intake_paused

DAILY_RUN_LIMIT = 100
RETAINED_RUN_LIMIT = 500
MAX_RUN_BYTES = 262144
DAILY_KEY = "daily:launch-runs"
TASK_VERSION = "purchasing-authority.1"
CONTRACT_VERSION = "http-mcp.1"
MAX_OWNER_RECORDS = 2000
MAX_OAUTH_RECORDS = 4000


class LaunchStatus(BaseModel):
    available: bool
    reason: Literal["open", "paused", "daily_limit", "retained_limit"]
    daily_limit: int = DAILY_RUN_LIMIT
    retained_limit: int = RETAINED_RUN_LIMIT
    resets_at: int


def capacity(db, amount=1, lock_retention_state=False, now=None):
    now = int(time.time()) if now is None else int(now)
    paused = (db.execute(text("SELECT paused FROM sandbox_control WHERE id=1")).scalar_one()
              or retention_intake_paused(db, lock=lock_retention_state))
    today = now // 86400
    used = db.execute(text("SELECT count FROM sandbox_limits WHERE key=:key AND bucket=:bucket"),
                      {"key": DAILY_KEY, "bucket": today}).scalar_one_or_none() or 0
    retained = db.execute(text("SELECT count(*) FROM sandbox_runs")).scalar_one()
    reason = ("paused" if paused else "daily_limit" if used + amount > DAILY_RUN_LIMIT
              else "retained_limit" if retained + amount > RETAINED_RUN_LIMIT else "open")
    return LaunchStatus(available=reason == "open", reason=reason, resets_at=(today+1)*86400)


def status():
    with Session() as db:
        return capacity(db)


def reserve(db, amount):
    """Called inside creation transaction; global lock serializes all owners/pairs."""
    db.execute(text("SELECT id FROM sandbox_control WHERE id=1 FOR UPDATE")).scalar_one()
    now = int(time.time())
    result = capacity(db, amount, lock_retention_state=True, now=now)
    if not result.available:
        detail = ("New runs are paused. Existing reports, example replay and documentation remain available."
                  if result.reason == "paused" else
                  "Public launch capacity reached. Existing runs, example replay and documentation remain available. "
                  "Daily quota resets at 00:00 UTC; retained capacity frees after deletion or cleanup.")
        raise HTTPException(503 if result.reason == "paused" else 429, detail,
                            headers={"Retry-After": str(max(1, result.resets_at-now))
                                     if result.reason == "daily_limit" else "60"})
    db.execute(text("""
        INSERT INTO sandbox_limits(key,bucket,count) VALUES (:key,:bucket,:amount)
        ON CONFLICT(key) DO UPDATE SET bucket=EXCLUDED.bucket,
          count=CASE WHEN sandbox_limits.bucket=EXCLUDED.bucket
                     THEN sandbox_limits.count+EXCLUDED.count ELSE EXCLUDED.count END
    """), {"key": DAILY_KEY, "bucket": now//86400, "amount": amount})


def reserve_auxiliary(db, kind):
    """Bound anonymous owner/registration storage too, not just run rows."""
    db.execute(text("SELECT id FROM sandbox_control WHERE id=1 FOR UPDATE")).scalar_one()
    if retention_intake_paused(db, lock=True):
        raise HTTPException(
            503,
            "New runs and agent actions are temporarily paused. Existing reports and deletion remain available.",
            headers={"Retry-After": "60"},
        )
    if kind == "owner":
        retained = db.execute(text("SELECT count(*) FROM sandbox_owners")).scalar_one()
        full = retained >= MAX_OWNER_RECORDS
    elif kind == "oauth":
        retained = db.execute(text("SELECT count(*) FROM sandbox_oauth")).scalar_one()
        full = retained >= MAX_OAUTH_RECORDS
    else:
        raise ValueError("Unsupported capacity class")
    if full:
        raise HTTPException(503, "Connection capacity reached. Retry after retention cleanup.",
                            headers={"Retry-After": "60"})
