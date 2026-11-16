import hashlib
import time
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import String, Float, Boolean, BigInteger, Integer, Date, CheckConstraint, Identity, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from fastapi import HTTPException
from database import Base, Session


class Owner(Base):
    __tablename__ = "sandbox_owners"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires: Mapped[float] = mapped_column(Float)


class Run(Base):
    __tablename__ = "sandbox_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    agent_hash: Mapped[str] = mapped_column(String(64))
    created: Mapped[float] = mapped_column(Float)
    expires: Mapped[float] = mapped_column(Float)
    deletes: Mapped[float] = mapped_column(Float)
    consent: Mapped[bool] = mapped_column(Boolean)
    research_ref: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    data: Mapped[dict] = mapped_column(JSONB)


class UsageDaily(Base):
    __tablename__ = "sandbox_usage_daily"
    __table_args__ = (
        CheckConstraint("run_created >= 0"),
        CheckConstraint("agent_action_accepted >= 0"),
        CheckConstraint("run_completed >= 0"),
    )

    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    run_created: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    agent_action_accepted: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    run_completed: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ResearchExportEvent(Base):
    __tablename__ = "sandbox_research_export_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('contribution_enabled', 'consent_withdrawn', 'legacy_current')"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    record_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[float] = mapped_column(Float, nullable=False)
    scenario: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class ResearchDailyCounter(Base):
    __tablename__ = "sandbox_research_daily"
    __table_args__ = (
        CheckConstraint("transition IN ('contribution_enabled', 'consent_withdrawn')"),
        CheckConstraint("count >= 0"),
    )

    activity_date: Mapped[date] = mapped_column(Date, primary_key=True)
    scenario: Mapped[str] = mapped_column(String(64), primary_key=True)
    transition: Mapped[str] = mapped_column(String(32), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ResearchExportAudit(Base):
    __tablename__ = "sandbox_research_export_audit"
    __table_args__ = (
        CheckConstraint("operation IN ('export', 'stats')"),
        CheckConstraint("records_count >= 0"),
        CheckConstraint("withdrawals_count >= 0"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    requested_at: Mapped[float] = mapped_column(Float, nullable=False)
    key_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    records_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    withdrawals_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor_from: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cursor_to: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    manifest_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def increment_limit(key, seconds=60):
    """Atomically increment a committed fixed-window counter shared by app instances."""
    with Session.begin() as db:
        count, bucket = db.execute(text("""
            INSERT INTO sandbox_limits(key, bucket, count) VALUES (:key, :bucket, 1)
            ON CONFLICT (key) DO UPDATE SET
              count = CASE WHEN sandbox_limits.bucket = EXCLUDED.bucket THEN sandbox_limits.count + 1 ELSE 1 END,
              bucket = EXCLUDED.bucket RETURNING count, bucket
        """), {"key": key, "bucket": int(time.time()) // seconds}).one()
    return int(count), int(bucket)


def limit(key, maximum, seconds=60):
    """Independent committed counter: rejected operations also consume quota."""
    count, _bucket = increment_limit(key, seconds)
    if count > maximum:
        raise HTTPException(429, "Rate limit reached. Try again later.", headers={"Retry-After": str(seconds)})
    return count


def purge():
    step = "transaction"
    try:
        with Session.begin() as db:
            step = "connection"
            db.connection()
            now = time.time()
            step = "oauth_expiry"
            db.execute(text("DELETE FROM sandbox_oauth WHERE expires < :now"), {"now": now})
            step = "run_deletion"
            db.execute(text("DELETE FROM sandbox_runs WHERE deletes < :now"), {"now": now})
            step = "research_event_expiry"
            db.execute(
                text("DELETE FROM sandbox_research_export_events WHERE expires_at < :now"),
                {"now": now},
            )
            step = "owner_expiry"
            db.execute(text("DELETE FROM sandbox_owners WHERE expires < :now"), {"now": now})
            # Daily admission is not a minute bucket. Deleting it would reopen today's quota.
            step = "limit_bucket"
            db.execute(
                text("DELETE FROM sandbox_limits WHERE bucket < :old AND key NOT LIKE 'daily:%'"),
                {"old": int(now) // 60 - 1440},
            )
            step = "usage_expiry"
            usage_cutoff = datetime.fromtimestamp(now, timezone.utc).date() - timedelta(days=89)
            db.execute(
                text("DELETE FROM sandbox_usage_daily WHERE activity_date < :cutoff"),
                {"cutoff": usage_cutoff},
            )
            step = "commit"
    except Exception as exc:
        # The worker logs only this fixed phase label, exception class and SQLSTATE.
        # Never attach SQL text, bound values, or any record data.
        try:
            exc._retention_cleanup_step = step
        except Exception:
            pass
        raise


def admission_enabled():
    with Session() as db:
        return db.execute(text("SELECT paused FROM sandbox_control WHERE id = 1")).scalar_one() is False


def autovacuum_config():
    """Read only the global and rate-limit-table autovacuum switches."""
    with Session() as db:
        state = db.execute(text("""
            SELECT current_setting('autovacuum')::boolean AS global_enabled,
                   CASE
                       WHEN relation.oid IS NULL THEN NULL
                       ELSE NOT EXISTS (
                           SELECT 1
                           FROM unnest(relation.reloptions) AS option
                           WHERE lower(option) = 'autovacuum_enabled=false'
                       )
                   END AS table_enabled
            FROM (SELECT to_regclass('sandbox_limits') AS oid) AS target
            LEFT JOIN pg_class AS relation ON relation.oid = target.oid
        """)).one()
    return {
        "global_enabled": state.global_enabled,
        "table_enabled": state.table_enabled,
    }
