"""Shared ingress limits and periodic retention enforcement."""
import asyncio
import hashlib
import heapq
import hmac
import ipaddress
import logging
import math
import os
import random
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import Counter
from contextlib import asynccontextmanager
from sqlalchemy import text
from starlette.responses import JSONResponse
from fastapi import HTTPException
from database import Session
from services.sandbox_store import (
    limit, increment_limit, purge, admission_enabled, autovacuum_config,
)
from services.sandbox_usage_history import COUNTERS, operator_token, record_history_count, utc_today

log = logging.getLogger("sandbox")
rate_diagnostic_log = logging.getLogger("sandbox.public_rate_limit")
rate_diagnostic_log.setLevel(logging.INFO)
rate_diagnostic_log.propagate = False
if not rate_diagnostic_log.handlers:
    rate_diagnostic_handler = logging.StreamHandler()
    rate_diagnostic_handler.setLevel(logging.INFO)
    rate_diagnostic_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    ))
    rate_diagnostic_log.addHandler(rate_diagnostic_handler)
OUTCOMES = Counter()
OUTCOME_LOCK = threading.Lock()
OUTCOME_STATUS_CODES = frozenset((400, 401, 403, 404, 408, 409, 410, 413, 422, 429, 503))
OUTCOME_STATUSES = frozenset((*map(str, OUTCOME_STATUS_CODES), "other_error"))
USAGE_COUNTS = Counter()
USAGE_LOCK = threading.Lock()
USAGE_FLUSH_LOCK = threading.Lock()
USAGE_EVENTS = frozenset(COUNTERS)
BODY_DEADLINE_SECONDS = 10
GENERAL_CONCURRENCY = 24
CONTROL_CONCURRENCY = 8
RETENTION_FAILURE_THRESHOLD = 3
RETENTION_STEPS = {
    "transaction", "connection", "oauth_expiry", "run_deletion", "owner_expiry",
    "research_event_expiry", "limit_bucket", "usage_expiry", "commit",
}
TRUSTED_REPLIT_PROXY_IPS = os.getenv(
    "SANDBOX_TRUSTED_PROXY_IPS",
    "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
    "100.64.0.0/10,169.254.0.0/16,fc00::/7,fe80::/10,"
    "35.191.0.0/16,130.211.0.0/22",
)
TRUSTED_REPLIT_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(value.strip(), strict=False)
    for value in TRUSTED_REPLIT_PROXY_IPS.split(",")
    if value.strip()
)
GOOGLE_PROXY_NETWORKS = (
    ipaddress.ip_network("35.191.0.0/16"),
    ipaddress.ip_network("130.211.0.0/22"),
)


def parse_client_ip_hops(value):
    if value is None or not value.strip():
        return None
    try:
        hops = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("SANDBOX_CLIENT_IP_HOPS must be a positive integer") from exc
    if hops < 1:
        raise ValueError("SANDBOX_CLIENT_IP_HOPS must be a positive integer")
    return hops


def configured_client_ip_hops():
    return parse_client_ip_hops(os.getenv("SANDBOX_CLIENT_IP_HOPS"))


def parse_rate_diagnostic_sample(value):
    try:
        sample = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "SANDBOX_RATE_DIAGNOSTIC_SAMPLE must be between 0 and 1"
        ) from exc
    if not math.isfinite(sample) or not 0 <= sample <= 1:
        raise ValueError("SANDBOX_RATE_DIAGNOSTIC_SAMPLE must be between 0 and 1")
    return sample


def configured_rate_diagnostic_sample():
    return parse_rate_diagnostic_sample(
        os.getenv("SANDBOX_RATE_DIAGNOSTIC_SAMPLE", "1.0")
    )


SANDBOX_CLIENT_IP_HOPS = configured_client_ip_hops()
PUBLIC_RATE_DIAGNOSTIC_SAMPLE_RATE = configured_rate_diagnostic_sample()
PUBLIC_RATE_WINDOW_SECONDS = 60
PUBLIC_RATE_MAX_BUCKETS = 20_000
PUBLIC_RATE_WARNING_INTERVAL_SECONDS = 60
PUBLIC_RATE_WARNING_LOCK = threading.Lock()
PUBLIC_RATE_LAST_WARNING_AT = 0.0
PUBLIC_RATE_INSTANCE_ID = (
    os.getenv("REPLIT_DEPLOYMENT_INSTANCE_ID")
    or os.getenv("HOSTNAME")
    or f"{socket.gethostname()}-{os.getpid()}"
)
PUBLIC_RATE_LIMITS = {
    "mcp": 120,
    "oauth-register": 10,
    "oauth-authorize": 60,
    "oauth-token": 120,
    "oauth-revoke": 30,
    "oauth-metadata": 120,
    # Unauthenticated; each request costs an outbound PayPal verification call.
    "paypal-webhook": 60,
}


def public_rate_group(path):
    """Return the per-client budget for public auth surfaces, or None."""
    if path == "/mcp":
        return "mcp"
    oauth_paths = {
        "/api/sandbox/oauth/register": "oauth-register",
        "/api/sandbox/oauth/authorize": "oauth-authorize",
        "/api/sandbox/oauth/token": "oauth-token",
        "/api/sandbox/oauth/revoke": "oauth-revoke",
        "/.well-known/oauth-authorization-server": "oauth-metadata",
        "/.well-known/oauth-protected-resource": "oauth-metadata",
    }
    if path in oauth_paths:
        return oauth_paths[path]
    if path == "/api/sandbox/paypal/webhook":
        return "paypal-webhook"
    if (path.startswith("/.well-known/oauth-protected-resource/") or
            path.startswith("/api/sandbox/oauth/resource/")):
        return "oauth-metadata"
    return None


class PublicClientRateLimiter:
    """Bounded process-local fallback for shared public IP counters."""

    def __init__(self, limits=None, window_seconds=PUBLIC_RATE_WINDOW_SECONDS,
                 max_buckets=PUBLIC_RATE_MAX_BUCKETS, clock=None):
        self.limits = dict(limits or PUBLIC_RATE_LIMITS)
        self.window_seconds = window_seconds
        self.max_buckets = max_buckets
        self.clock = clock or time.monotonic
        self._buckets = {}
        self._expiry_heap = []
        self._generation = 0
        self._lock = threading.Lock()

    @staticmethod
    def client_key(client):
        host = client[0] if client else ""
        try:
            return ipaddress.ip_address(host).compressed
        except (ValueError, TypeError):
            # Test clients and missing peers share a deliberately conservative bucket.
            return "unknown"

    def check(self, path, client):
        group = public_rate_group(path)
        if group is None:
            return None
        maximum = self.limits[group]
        key = (group, self.client_key(client))
        now = self.clock()
        with self._lock:
            while self._expiry_heap and self._expiry_heap[0][0] <= now:
                expiry, generation, expired_key = heapq.heappop(self._expiry_heap)
                bucket = self._buckets.get(expired_key)
                if bucket and bucket[2] == generation:
                    del self._buckets[expired_key]

            bucket = self._buckets.get(key)
            if bucket:
                started, count, _generation = bucket
                count += 1
                self._buckets[key] = (started, count, _generation)
                return {
                    "group": group,
                    "limit": maximum,
                    "remaining": max(0, maximum - count),
                    "reset": max(1, math.ceil(started + self.window_seconds - now)),
                    "limited": count > maximum,
                }

            if len(self._buckets) >= self.max_buckets:
                retry_after = (
                    max(1, math.ceil(self._expiry_heap[0][0] - now))
                    if self._expiry_heap else self.window_seconds
                )
                return {
                    "group": group,
                    "limit": maximum,
                    "remaining": 0,
                    "reset": retry_after,
                    "limited": True,
                }

            self._generation += 1
            generation = self._generation
            expiry = now + self.window_seconds
            self._buckets[key] = (now, 1, generation)
            heapq.heappush(self._expiry_heap, (expiry, generation, key))
        return {
            "group": group,
            "limit": maximum,
            "remaining": max(0, maximum - 1),
            "reset": self.window_seconds,
            "limited": maximum < 1,
        }


def public_client_fingerprint(client, secret):
    if not secret:
        raise RuntimeError("rate-limit fingerprint key is not configured")
    ip = PublicClientRateLimiter.client_key(client)
    fingerprint = hmac.new(
        secret.encode("utf-8"),
        b"chargeback-os:public-ingress:v1:" + ip.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return fingerprint


def forwarded_for_entries(scope):
    entries = []
    for name, value in scope.get("headers", ()):
        if name.lower() == b"x-forwarded-for":
            entries.extend(
                part.decode("ascii", errors="replace").strip()
                for part in value.split(b",")
                if part.strip()
            )
    return entries


def is_trusted_proxy_peer(peer):
    try:
        address = ipaddress.ip_address(peer)
    except (ValueError, TypeError):
        return False
    return any(address in network for network in TRUSTED_REPLIT_PROXY_NETWORKS)


class ProxyPeerObservationMiddleware:
    """Record safe proxy metadata before Uvicorn rewrites scope.client."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and public_rate_group(scope.get("path", "")):
            state = scope.setdefault("state", {})
            state["immediate_peer"] = scope.get("client")
            state["trusted_proxy_peer"] = is_trusted_proxy_peer(
                (scope.get("client") or ("", 0))[0]
            )
        return await self.app(scope, receive, send)


class ClientIpSelectionMiddleware:
    """Apply an explicit trusted XFF hop override and record the selected position."""

    def __init__(self, app, hops_override=SANDBOX_CLIENT_IP_HOPS):
        self.app = app
        self.hops_override = hops_override

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and public_rate_group(scope.get("path", "")):
            state = scope.setdefault("state", {})
            entries = forwarded_for_entries(scope)
            trusted_peer = bool(state.get("trusted_proxy_peer", False))
            if self.hops_override is not None:
                selected = None
                if trusted_peer and self.hops_override <= len(entries):
                    try:
                        selected = ipaddress.ip_address(
                            entries[-self.hops_override]
                        ).compressed
                    except ValueError:
                        selected = None
                if selected:
                    current = scope.get("client") or ("", 0)
                    scope["client"] = (selected, current[1])
                    state["client_ip_position"] = f"right-{self.hops_override}"
                else:
                    peer = state.get("immediate_peer")
                    if peer is not None:
                        scope["client"] = peer
                    state["client_ip_position"] = "peer-fallback"
            else:
                selected = PublicClientRateLimiter.client_key(scope.get("client"))
                position = None
                if trusted_peer and selected != "unknown":
                    for index, raw_entry in enumerate(reversed(entries), start=1):
                        try:
                            entry_ip = ipaddress.ip_address(raw_entry).compressed
                        except ValueError:
                            continue
                        if entry_ip == selected:
                            position = index
                            break
                state["client_ip_position"] = (
                    f"right-{position}" if position is not None else "peer"
                )
        return await self.app(scope, receive, send)


def public_rate_headers(decision):
    return [
        (b"ratelimit-limit", str(decision["limit"]).encode("ascii")),
        (b"ratelimit-remaining", str(decision["remaining"]).encode("ascii")),
        (b"ratelimit-reset", str(decision["reset"]).encode("ascii")),
    ]


MAX_XFF_DIAGNOSTIC_ENTRIES = 32


def _xff_diagnostics(scope, secret):
    entries = forwarded_for_entries(scope)
    descriptions = []
    for position, raw_entry in enumerate(
        reversed(entries[-MAX_XFF_DIAGNOSTIC_ENTRIES:]), start=1
    ):
        try:
            address = ipaddress.ip_address(raw_entry)
            normalized = address.compressed
            category = (
                "google-proxy"
                if any(address in network for network in GOOGLE_PROXY_NETWORKS)
                else "other-public" if address.is_global else "private"
            )
            digest_source = normalized.encode("ascii")
        except ValueError:
            category = "other-public"
            digest_source = raw_entry.encode("utf-8", errors="replace")[:256]
        entry_hash = hmac.new(
            secret.encode("utf-8"),
            b"chargeback-os:xff-entry:v1:" + digest_source,
            hashlib.sha256,
        ).hexdigest()[:12]
        descriptions.append(f"right-{position}:{category}:{entry_hash}")
    return (
        len(entries),
        len(descriptions),
        len(entries) > MAX_XFF_DIAGNOSTIC_ENTRIES,
        ",".join(descriptions) if descriptions else "none",
    )


def _log_public_rate_sample(group, fingerprint, scope, secret):
    if not fingerprint or random.random() >= PUBLIC_RATE_DIAGNOSTIC_SAMPLE_RATE:
        return
    entry_count, logged_count, truncated, chain = _xff_diagnostics(scope, secret)
    state = scope.get("state") or {}
    peer_trusted = "yes" if state.get("trusted_proxy_peer") else "no"
    rate_diagnostic_log.info(
        "Public ingress rate sample bucket=%s client_key=%s instance=%s "
        "proxy_peer_trusted=%s xff_entries=%s xff_positions_logged=%s "
        "xff_truncated=%s xff_chain=%s client_key_position=%s",
        group,
        fingerprint[:16],
        PUBLIC_RATE_INSTANCE_ID,
        peer_trusted,
        entry_count,
        logged_count,
        "yes" if truncated else "no",
        chain,
        state.get("client_ip_position", "not-recorded"),
    )


def _log_public_rate_database_failure(error):
    global PUBLIC_RATE_LAST_WARNING_AT
    now = time.monotonic()
    with PUBLIC_RATE_WARNING_LOCK:
        if now - PUBLIC_RATE_LAST_WARNING_AT < PUBLIC_RATE_WARNING_INTERVAL_SECONDS:
            return
        PUBLIC_RATE_LAST_WARNING_AT = now
    error_type = type(error).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type):
        error_type = "DatabaseError"
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if not (isinstance(sqlstate, str) and re.fullmatch(r"[A-Za-z0-9]{5}", sqlstate)):
        sqlstate = "unknown"
    log.warning(
        "Shared public IP rate limiter unavailable; using process-local fallback "
        "error_type=%s sqlstate=%s",
        error_type,
        sqlstate,
    )


class RetentionHealth:
    """Track only consecutive cleanup failures; never retain exception details."""
    def __init__(self, threshold=RETENTION_FAILURE_THRESHOLD):
        self.threshold = threshold
        self._coordination_unavailable = False
        self._lock = threading.Lock()

    def begin_startup(self):
        # Do not accept new work after a restart until at least one cleanup succeeds.
        try:
            with Session.begin() as db:
                self._require_state_row(db.execute(
                    text("UPDATE sandbox_retention_health SET startup_unverified = true WHERE id = 1")
                ))
        except Exception:
            self._mark_coordination_unavailable()

    def record_failure(self):
        try:
            with Session.begin() as db:
                self._require_state_row(db.execute(text("""
                    UPDATE sandbox_retention_health
                    SET consecutive_failures = LEAST(consecutive_failures + 1, :threshold)
                    WHERE id = 1
                """), {"threshold": self.threshold}))
        except Exception:
            self._mark_coordination_unavailable()
            raise

    def record_success(self):
        try:
            with Session.begin() as db:
                self._require_state_row(db.execute(text("""
                    UPDATE sandbox_retention_health
                    SET consecutive_failures = 0, startup_unverified = false
                    WHERE id = 1
                """)))
        except Exception:
            self._mark_coordination_unavailable()
            raise
        with self._lock:
            self._coordination_unavailable = False

    def intake_paused(self, db=None, lock=False):
        with self._lock:
            if self._coordination_unavailable:
                return True
        try:
            def read_state(session):
                query = """
                    SELECT consecutive_failures, startup_unverified
                    FROM sandbox_retention_health WHERE id = 1
                """
                if lock:
                    query += " FOR SHARE"
                return session.execute(text(query)).one()
            if db is not None:
                state = read_state(db)
            else:
                with Session() as session:
                    state = read_state(session)
            return state.startup_unverified or state.consecutive_failures >= self.threshold
        except Exception:
            self._mark_coordination_unavailable()
            return True

    @property
    def consecutive_failures(self):
        with Session() as db:
            return db.execute(text("""
                SELECT consecutive_failures FROM sandbox_retention_health WHERE id = 1
            """)).scalar_one()

    def _mark_coordination_unavailable(self):
        with self._lock:
            self._coordination_unavailable = True

    @staticmethod
    def _require_state_row(result):
        if result.rowcount != 1:
            raise RuntimeError("Retention health state is unavailable")

    def snapshot(self):
        """Return bounded operator health without retaining or exposing failure details."""
        try:
            with Session() as db:
                state = db.execute(text("""
                    SELECT consecutive_failures, startup_unverified
                    FROM sandbox_retention_health WHERE id = 1
                """)).one()
        except Exception:
            self._mark_coordination_unavailable()
            return {"status": "failing", "intake_paused": True}
        with self._lock:
            if self._coordination_unavailable:
                return {"status": "failing", "intake_paused": True}
            consecutive_failures = state.consecutive_failures
            startup_unverified = state.startup_unverified
            intake_paused = (
                startup_unverified
                or consecutive_failures >= self.threshold
            )
        if startup_unverified and not consecutive_failures:
            status = "starting"
        elif consecutive_failures >= self.threshold:
            status = "failing"
        elif consecutive_failures:
            status = "degraded"
        else:
            status = "healthy"
        return {"status": status, "intake_paused": intake_paused}


retention_health = RetentionHealth()


def retention_health_snapshot():
    return retention_health.snapshot()


def autovacuum_health_snapshot():
    """Return safe operator guidance without exposing database error details."""
    try:
        config = autovacuum_config()
    except Exception:
        return {
            "status": "unknown",
            "global_enabled": None,
            "rate_limit_table_enabled": None,
            "warning": "PostgreSQL autovacuum status could not be verified.",
            "remediation": (
                "Check database connectivity and confirm the sandbox_limits "
                "table exists."
            ),
        }

    global_enabled = config["global_enabled"]
    table_enabled = config["table_enabled"]
    if global_enabled is False or table_enabled is False:
        status = "disabled"
        warning = (
            "PostgreSQL autovacuum is disabled globally or for the "
            "rate-limit table."
        )
        remediation = (
            "Enable autovacuum globally and for sandbox_limits; coordinate "
            "with the database administrator."
        )
    elif global_enabled is True and table_enabled is True:
        status = "enabled"
        warning = None
        remediation = None
    else:
        status = "unknown"
        warning = "PostgreSQL autovacuum status could not be verified."
        remediation = (
            "Confirm the sandbox_limits table exists and check database "
            "configuration."
        )

    return {
        "status": status,
        "global_enabled": global_enabled,
        "rate_limit_table_enabled": table_enabled,
        "warning": warning,
        "remediation": remediation,
    }


def retention_intake_paused(db=None, lock=False):
    return retention_health.intake_paused(db=db, lock=lock)


def retention_failure_fields(error):
    """Return bounded diagnostics; never expose DB messages or query parameters."""
    step = getattr(error, "_retention_cleanup_step", "unknown")
    if step not in RETENTION_STEPS:
        step = "unknown"
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if not (isinstance(sqlstate, str) and len(sqlstate) == 5
            and sqlstate.isascii() and sqlstate.isalnum()):
        sqlstate = "unknown"
    error_type = type(error).__name__
    if not (isinstance(error_type, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", error_type)):
        error_type = "DatabaseError"
    return step, error_type, sqlstate


def flush_outcomes():
    """Operational counts only; bounded keys, no request attributes or identifiers."""
    with OUTCOME_LOCK:
        summary = dict(OUTCOMES)
        OUTCOMES.clear()
    if summary:
        try:
            log.warning("Sandbox operational response counts: %s", summary)
        except Exception:
            # Logging failed before the batch was acknowledged; restore it
            # without overwriting outcomes recorded while the logger ran.
            with OUTCOME_LOCK:
                OUTCOMES.update(summary)
            raise


def record_outcome(status):
    """Record a response using only a bounded status category."""
    category = str(status) if status in OUTCOME_STATUS_CODES else "other_error"
    with OUTCOME_LOCK:
        OUTCOMES[category] += 1


def record_usage_count(name, amount=1):
    """Record fixed-schema operational counts without run or request attributes."""
    if name not in USAGE_EVENTS or not isinstance(amount, int) or amount <= 0:
        return
    with USAGE_LOCK:
        USAGE_COUNTS[name] += amount


def record_persistent_usage(db, name, amount=1, event_time=None):
    """Increment one fixed daily counter inside the caller's successful transaction."""
    if (name not in USAGE_EVENTS or not isinstance(amount, int)
            or isinstance(amount, bool) or amount <= 0):
        raise ValueError("Unsupported operational usage counter")
    if operator_token() is None:
        return
    activity_date = (
        datetime.fromtimestamp(event_time, timezone.utc).date()
        if event_time is not None else datetime.now(timezone.utc).date()
    )
    record_history_count(db, activity_date, name, amount)


def flush_usage_counts():
    """Emit bounded in-memory interval totals without request attributes."""
    with USAGE_FLUSH_LOCK:
        with USAGE_LOCK:
            summary = {name: USAGE_COUNTS.pop(name, 0) for name in COUNTERS}
    if any(summary.values()):
        try:
            log.info(
                "Sandbox aggregate usage counts: run_created=%d agent_action_accepted=%d run_completed=%d",
                summary["run_created"], summary["agent_action_accepted"], summary["run_completed"],
            )
        except Exception:
            # Restore only the summary batch; newly recorded interval counts
            # remain additive. Durable history committed with events is separate.
            with USAGE_LOCK:
                USAGE_COUNTS.update(summary)
            raise


def _report_summary_flush_failure(message):
    """Report a summary failure without letting a broken handler stop cleanup."""
    try:
        log.error(message)
    except Exception:
        # Logging can itself be the failing operation; cleanup must still run.
        pass


@asynccontextmanager
async def lifespan(app):
    retention_health.begin_startup()

    async def cleanup():
        while True:
            try:
                await asyncio.to_thread(purge)
            except Exception as exc:
                try:
                    retention_health.record_failure()
                except Exception:
                    # Fail closed locally without exposing database or record details.
                    log.error("Retention health state unavailable; intake remains paused")
                step, error_type, sqlstate = retention_failure_fields(exc)
                log.error(
                    "Retention cleanup failed; operator action required "
                    "(step=%s error_type=%s sqlstate=%s)",
                    step, error_type, sqlstate,
                )
            else:
                try:
                    retention_health.record_success()
                except Exception:
                    # A successful purge is not enough to resume until shared state clears.
                    log.error("Retention health state unavailable; intake remains paused")
            try:
                flush_outcomes()
            except Exception:
                _report_summary_flush_failure("Sandbox response summary flush failed")
            try:
                await asyncio.to_thread(flush_usage_counts)
            except Exception:
                _report_summary_flush_failure("Sandbox usage summary flush failed")
            await asyncio.sleep(60)
    task = asyncio.create_task(cleanup())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class Ingress:
    def __init__(self, app, public_limiter=None, rate_counter=None, rate_limits=None,
                 rate_hash_secret=None):
        self.app = app
        self.inflight = {"general": 0, "control": 0}
        limits = PUBLIC_RATE_LIMITS if rate_limits is None else rate_limits
        self.public_limiter = public_limiter or PublicClientRateLimiter(limits=limits)
        self.public_limits = dict(limits)
        self.rate_counter = rate_counter or increment_limit
        self.rate_hash_secret = (
            os.getenv("SESSION_SECRET") if rate_hash_secret is None else rate_hash_secret
        )

    async def _check_public_rate(self, path, scope):
        group = public_rate_group(path)
        if group is None:
            return None
        client = scope.get("client")
        maximum = self.public_limits[group]
        fingerprint = None
        try:
            fingerprint = public_client_fingerprint(client, self.rate_hash_secret)
            counter_key = f"public-ip:v1:{group}:{fingerprint}"
            count, bucket = await asyncio.to_thread(
                self.rate_counter, counter_key, PUBLIC_RATE_WINDOW_SECONDS
            )
            reset = max(
                1,
                min(
                    PUBLIC_RATE_WINDOW_SECONDS,
                    (int(bucket) + 1) * PUBLIC_RATE_WINDOW_SECONDS - int(time.time()),
                ),
            )
            decision = {
                "group": group,
                "limit": maximum,
                "remaining": max(0, maximum - int(count)),
                "reset": reset,
                "limited": int(count) > maximum,
            }
        except Exception as exc:
            _log_public_rate_database_failure(exc)
            # Keep the service available if the shared counter store is down.
            # The process-local limiter remains a bounded best-effort fallback.
            decision = self.public_limiter.check(path, client)
            if decision is None:
                return None
        _log_public_rate_sample(group, fingerprint, scope, self.rate_hash_secret)
        return decision

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Health probes must remain available even when request or DB limits are saturated.
        if path == "/api/healthz":
            return await self.app(scope, receive, send)
        rate_decision = await self._check_public_rate(path, scope)
        if rate_decision is not None and rate_decision["limited"]:
            record_outcome(429)
            response_headers = {
                key.decode("ascii"): value.decode("ascii")
                for key, value in public_rate_headers(rate_decision)
            }
            response_headers["Retry-After"] = str(rate_decision["reset"])
            return await JSONResponse(
                {"detail": "Rate limit exceeded. Retry shortly."},
                status_code=429,
                headers=response_headers,
            )(scope, receive, send)
        if rate_decision is not None:
            rate_headers = public_rate_headers(rate_decision)
            downstream_send = send

            async def rate_limited_send(message):
                if message["type"] == "http.response.start":
                    message = dict(message)
                    existing = [
                        (key, value) for key, value in message.get("headers", [])
                        if key.lower() not in {
                            b"ratelimit-limit",
                            b"ratelimit-remaining",
                            b"ratelimit-reset",
                        }
                    ]
                    message["headers"] = existing + rate_headers
                await downstream_send(message)

            send = rate_limited_send
        if not path.startswith(("/api", "/webhooks", "/.well-known/oauth")):
            return await self.app(scope, receive, send)
        async def measured_send(message):
            if message["type"] == "http.response.start":
                status = message["status"]
                # Operational health only: no run/client/IP/path/body is logged.
                if status >= 400:
                    record_outcome(status)
                headers = list(message.get("headers", []))
                present = {key.lower() for key, _value in headers}
                headers += [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer")]
                # Never allow API pages (docs, OAuth approval) to be framed.
                # Keep a route's own stricter policy instead of adding a second one.
                if b"x-frame-options" not in present:
                    headers.append((b"x-frame-options", b"DENY"))
                if b"content-security-policy" not in present:
                    headers.append((b"content-security-policy", b"frame-ancestors 'none'"))
                message["headers"] = headers
            await send(message)
        if path.startswith(("/api/office", "/api/evidence")):
            return await JSONResponse({"detail": "Legacy demo endpoints are disabled."}, 404)(scope, receive, measured_send)
        control = (scope["method"] in ("DELETE", "PATCH") or path.endswith("/revoke"))
        group, maximum = ("control", CONTROL_CONCURRENCY) if control else ("general", GENERAL_CONCURRENCY)
        if self.inflight[group] >= maximum:
            return await JSONResponse({"detail": "Service busy. Retry shortly."}, 503,
                                      headers={"Retry-After": "5"})(scope, receive, measured_send)
        self.inflight[group] += 1
        try:
            return await self._handle(scope, receive, measured_send)
        finally:
            self.inflight[group] -= 1

    async def _handle(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if not path.startswith(("/api", "/webhooks", "/.well-known/oauth")):
            return await self.app(scope, receive, send)
        try:
            # Shared PostgreSQL counter, including legacy demo and failed auth.
            await asyncio.to_thread(limit, "all-api-global", 1200)
            if scope["method"] == "POST" and (path in ("/api/sandbox/runs", "/api/sandbox/owner") or path.startswith("/api/sandbox/agent/")):
                if (not await asyncio.to_thread(admission_enabled)
                        or retention_intake_paused()):
                    return await JSONResponse({"detail": "New runs and agent actions are temporarily paused. Existing reports and deletion remain available."}, 503)(scope, receive, send)
        except HTTPException:
            return await JSONResponse({"detail": "Service request limit reached. Retry shortly."}, 429, headers={"Retry-After": "60"})(scope, receive, send)
        except Exception:
            return await JSONResponse({"detail": "Safety controls unavailable."}, 503)(scope, receive, send)
        chunks, total = [], 0
        deadline = asyncio.get_running_loop().time() + BODY_DEADLINE_SECONDS
        while True:
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except asyncio.TimeoutError:
                return await JSONResponse({"detail": "Request body timeout."}, 408)(scope, receive, send)
            if message["type"] == "http.disconnect":
                return
            total += len(message.get("body", b""))
            if total > 16384:
                return await JSONResponse({"detail": "Request body exceeds 16 KiB."}, 413)(scope, receive, send)
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()
        await self.app(scope, bounded_receive, send)
