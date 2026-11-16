"""Private, read-only operator export of consented sandbox research records."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select, text

from database import Session
from services.sandbox_research import research_record
from services.sandbox_store import (
    ResearchExportAudit,
    ResearchExportEvent,
    Run,
    digest,
    limit as rate_limit,
)

TOKEN_ENV = "SANDBOX_RESEARCH_EXPORT_TOKEN"
KEY_IDENTITY = "research-export-primary"
MAX_PAGE_SIZE = 100
PAGE_RATE_PER_MINUTE = 10
GLOBAL_RATE_PER_MINUTE = 60
# Version 2: sharing can start when the run starts and covers unfinished runs.
CONSENT_VERSION = "2"
# "Export this record's current state again." The stored name predates in-run
# sharing; it is reused so no database constraint change is needed.
SNAPSHOT_EVENT = "legacy_current"

router = APIRouter(prefix="/api/sandbox/operator/research", tags=["Private research export"])


def _configured_token():
    token = os.environ.get(TOKEN_ENV, "")
    if not 32 <= len(token.encode("utf-8")) <= 512:
        raise HTTPException(503, "Research export is not configured.")
    operator_token = os.environ.get("SANDBOX_OPERATOR_TOKEN", "")
    if operator_token and hmac.compare_digest(
        token.encode("utf-8"), operator_token.encode("utf-8")
    ):
        raise HTTPException(503, "Research export requires a dedicated secret.")
    return token


def _authenticate(request: Request):
    expected = _configured_token()
    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    candidate = supplied if separator and scheme.casefold() == "bearer" else ""
    if len(candidate.encode("utf-8")) > 512:
        candidate = ""
    if not hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            401,
            "Research-export operator authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return expected


def _rate_limit_global():
    rate_limit("research-export:global", GLOBAL_RATE_PER_MINUTE)


def _rate_limit_token(token: str):
    # The digest prevents the bearer value from entering the rate-limit table.
    rate_limit("research-export:" + digest(token), PAGE_RATE_PER_MINUTE)


def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


def _cursor_payload(sequence: int) -> bytes:
    return json.dumps({"v": 1, "seq": sequence}, separators=(",", ":"), sort_keys=True).encode()


def _encode_cursor(sequence: int, token: str) -> str:
    payload = _cursor_payload(sequence)
    signature = hmac.new(
        token.encode("utf-8"), b"chargeback-research-cursor-v1:" + payload, hashlib.sha256
    ).digest()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return encoded + "." + signed


def _decode_cursor(cursor: str | None, token: str) -> int:
    if cursor is None or cursor == "":
        return 0
    try:
        encoded, signed = cursor.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        signature = base64.urlsafe_b64decode(signed + "=" * (-len(signed) % 4))
        expected = hmac.new(
            token.encode("utf-8"), b"chargeback-research-cursor-v1:" + payload, hashlib.sha256
        ).digest()
        data = json.loads(payload)
        sequence = data["seq"]
        if (
            not hmac.compare_digest(signature, expected)
            or data.get("v") != 1
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 0
        ):
            raise ValueError("invalid cursor")
        return sequence
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(400, "Invalid research export cursor.")


def _append_event(db, row: Run, event_type: str, occurred_at: float):
    # Serialize event-ID allocation and commits so a later cursor cannot skip
    # an earlier event that commits after it.
    db.execute(text("SELECT pg_advisory_xact_lock(145901, 77821)"))
    db.add(ResearchExportEvent(
        record_ref=row.research_ref,
        event_type=event_type,
        occurred_at=occurred_at,
        scenario=row.data["scenario"],
        consent_version=str(row.data.get("consent_version", "1")),
        expires_at=row.created + 30 * 86400,
    ))


def record_research_snapshot(db, row: Run, occurred_at: float):
    """Queue a shared run for re-export after its record changed."""
    if row.consent and row.research_ref is not None:
        _append_event(db, row, SNAPSHOT_EVENT, occurred_at)


def run_status(run: Run, now: float) -> str:
    status = run.data["state"]["status"]
    if status == "active":
        return "abandoned" if run.expires <= now else "in_progress"
    return "finished" if status == "completed" else status


def _index_abandoned_runs(db, now: float):
    """Export one final snapshot for shared runs that ended without finishing."""
    rows = db.scalars(
        select(Run)
        .where(
            Run.consent.is_(True),
            Run.deletes > now,
            Run.expires <= now,
            Run.research_ref.is_not(None),
            Run.data["state"]["status"].astext == "active",
        )
        .with_for_update()
    ).all()
    for row in rows:
        if row.data.get("research_abandoned_indexed"):
            continue
        row.data = {**row.data, "research_abandoned_indexed": True}
        _append_event(db, row, SNAPSHOT_EVENT, now)


def record_consent_transition(db, row: Run, event_type: str, occurred_at: float):
    """Record a true consent state transition and its identifier-free daily count."""
    if event_type not in ("contribution_enabled", "consent_withdrawn"):
        raise ValueError("Unsupported research consent transition.")
    if row.research_ref is None:
        row.research_ref = uuid.uuid4().hex
    _append_event(db, row, event_type, occurred_at)
    day = datetime.fromtimestamp(occurred_at, timezone.utc).date()
    db.execute(text("""
        INSERT INTO sandbox_research_daily(activity_date, scenario, transition, count)
        VALUES (:day, :scenario, :transition, 1)
        ON CONFLICT (activity_date, scenario, transition)
        DO UPDATE SET count = sandbox_research_daily.count + 1
    """), {
        "day": day,
        "scenario": row.data["scenario"],
        "transition": event_type,
    })


def _ensure_legacy_events(db, now: float):
    """Index existing v1 opt-ins once without changing consent or record content."""
    rows = db.scalars(
        select(Run)
        .where(
            Run.consent.is_(True),
            Run.deletes > now,
            Run.research_ref.is_(None),
        )
        .order_by(Run.created, Run.id)
        .with_for_update()
    ).all()
    for row in rows:
        row.research_ref = uuid.uuid4().hex
        _append_event(db, row, "legacy_current", now)


def _audit(
    *,
    operation: str,
    key_identity: str,
    outcome: str,
    records_count: int = 0,
    withdrawals_count: int = 0,
    cursor_from: int | None = None,
    cursor_to: int | None = None,
    manifest_checksum: str | None = None,
):
    # Deliberately excludes auth headers, record references, and record bodies.
    with Session.begin() as db:
        db.add(ResearchExportAudit(
            requested_at=time.time(),
            key_identity=key_identity,
            operation=operation,
            outcome=outcome,
            records_count=records_count,
            withdrawals_count=withdrawals_count,
            cursor_from=cursor_from,
            cursor_to=cursor_to,
            manifest_checksum=manifest_checksum,
        ))


def _response(operation: str, request: Request, work):
    audit_data = {
        "operation": operation,
        "key_identity": "unverified",
        "outcome": "internal_error",
        "records_count": 0,
        "withdrawals_count": 0,
        "cursor_from": None,
        "cursor_to": None,
        "manifest_checksum": None,
    }
    try:
        _rate_limit_global()
        token = _authenticate(request)
        audit_data["key_identity"] = KEY_IDENTITY
        _rate_limit_token(token)
        result = work(token, audit_data)
        audit_data["outcome"] = "success"
    except HTTPException as exc:
        audit_data["outcome"] = {
            401: "unauthorized",
            403: "forbidden",
            400: "invalid_request",
            429: "rate_limited",
            503: "disabled",
        }.get(exc.status_code, "http_error")
        if exc.status_code == 503:
            audit_data["key_identity"] = "disabled"
        _audit(**audit_data)
        raise
    except Exception:
        _audit(**audit_data)
        raise
    _audit(**audit_data)
    return result


@router.get("/export", include_in_schema=False)
def export_research(
    request: Request,
    cursor: str | None = Query(default=None, max_length=2048),
    page_size: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
):
    def build(token: str, audit_data: dict):
        start_sequence = _decode_cursor(cursor, token)
        audit_data["cursor_from"] = start_sequence
        now = time.time()
        with Session.begin() as db:
            _ensure_legacy_events(db, now)
            _index_abandoned_runs(db, now)
            rows = db.scalars(
                select(ResearchExportEvent)
                .where(ResearchExportEvent.id > start_sequence)
                .order_by(ResearchExportEvent.id)
                .limit(page_size + 1)
            ).all()
            has_more = len(rows) > page_size
            page = rows[:page_size]
            end_sequence = page[-1].id if page else start_sequence
            audit_data["cursor_to"] = end_sequence

            lines = []
            versions = set()
            for event in page:
                versions.add(str(event.consent_version))
                if event.expires_at < now:
                    continue
                if event.event_type == "consent_withdrawn":
                    lines.append({"type": "withdrawal", "record_ref": event.record_ref})
                    continue
                run = db.scalar(
                    select(Run)
                    .where(
                        Run.research_ref == event.record_ref,
                        Run.consent.is_(True),
                        Run.deletes > now,
                    )
                    .with_for_update()
                )
                if not run:
                    continue
                version = str(run.data.get("consent_version", "1"))
                versions.add(version)
                lines.append({
                    "type": "record",
                    "record_ref": event.record_ref,
                    "consent_version": version,
                    "expires_at": _utc_iso(run.created + 30 * 86400),
                    "run_status": run_status(run, now),
                    "record": research_record(run),
                })
            # A shared run can have several update events on one page; keep only
            # its latest line so each record appears once, in final order.
            latest = {line["record_ref"]: index for index, line in enumerate(lines)}
            lines = [line for index, line in enumerate(lines) if latest[line["record_ref"]] == index]

        next_cursor = _encode_cursor(end_sequence, token)
        record_count = sum(line["type"] == "record" for line in lines)
        withdrawal_count = len(lines) - record_count
        manifest = {
            "type": "manifest",
            "export_schema_version": "2",
            "record_schema_version": "1",
            "generated_at": _utc_iso(now),
            "consent_versions": sorted(versions),
            "counts": {"records": record_count, "withdrawals": withdrawal_count},
            "cursor_from": cursor,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }
        manifest_line = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        checksum = hashlib.sha256(manifest_line.encode("utf-8")).hexdigest()
        audit_data["records_count"] = record_count
        audit_data["withdrawals_count"] = withdrawal_count
        audit_data["manifest_checksum"] = checksum
        body = "\n".join(
            [manifest_line] + [
                json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                for line in lines
            ]
        ) + "\n"
        return Response(
            content=body,
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-store",
                "X-Research-Manifest-SHA256": checksum,
            },
        )

    return _response("export", request, build)


@router.get("/stats", include_in_schema=False)
def research_stats(request: Request):
    def build(_token: str, audit_data: dict):
        with Session() as db:
            rows = db.execute(text("""
                SELECT activity_date, scenario, transition, count
                FROM sandbox_research_daily
                ORDER BY activity_date, scenario, transition
            """)).mappings().all()
        result = {
            "counts": [
                {
                    "date": row["activity_date"].isoformat(),
                    "scenario": row["scenario"],
                    "transition": row["transition"],
                    "count": row["count"],
                }
                for row in rows
            ]
        }
        return Response(
            content=json.dumps(result, separators=(",", ":")),
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    return _response("stats", request, build)
