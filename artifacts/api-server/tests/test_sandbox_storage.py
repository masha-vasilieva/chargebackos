"""Database checks run only in a disposable private PostgreSQL schema."""
import sys
import time
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import re
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import bindparam, create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sandbox_test_support import IsolatedSandboxTest, request
from main import app
import database
import sandbox_oauth
import services.sandbox_store as sandbox_store
from services.sandbox_store import (
    Owner, Run, digest, limit, purge, autovacuum_config,
)
from services.sandbox_oauth_store import OAuthRecord
from services.sandbox_engine import initial
from services.sandbox_launch import (
    DAILY_KEY,
    DAILY_RUN_LIMIT,
    MAX_OAUTH_RECORDS,
    MAX_OWNER_RECORDS,
    RETAINED_RUN_LIMIT,
    reserve,
)


class StorageTests(IsolatedSandboxTest):
    def test_autovacuum_config_reports_enabled_and_disabled_settings(self):
        configurations = (
            (True, True),
            (False, True),
            (True, False),
        )
        for global_enabled, table_enabled in configurations:
            with self.subTest(
                global_enabled=global_enabled,
                table_enabled=table_enabled,
            ):
                session = Mock()
                session.__enter__ = Mock(return_value=session)
                session.__exit__ = Mock(return_value=False)
                session.execute.return_value.one.return_value = SimpleNamespace(
                    global_enabled=global_enabled,
                    table_enabled=table_enabled,
                )
                with patch(
                    "services.sandbox_store.Session",
                    return_value=session,
                ):
                    self.assertEqual(
                        autovacuum_config(),
                        {
                            "global_enabled": global_enabled,
                            "table_enabled": table_enabled,
                        },
                    )
                query = session.execute.call_args.args[0].text
                self.assertIn("current_setting('autovacuum')", query)
                self.assertIn("sandbox_limits", query)

    def test_autovacuum_config_reads_actual_table_setting(self):
        with self.Session.begin() as db:
            db.execute(text(
                "ALTER TABLE sandbox_limits SET (autovacuum_enabled = false)"
            ))
        try:
            settings = autovacuum_config()
            self.assertIsInstance(settings["global_enabled"], bool)
            self.assertFalse(settings["table_enabled"])
        finally:
            with self.Session.begin() as db:
                db.execute(text(
                    "ALTER TABLE sandbox_limits RESET (autovacuum_enabled)"
                ))

    def test_persisted_limit(self):
        key = "test:" + secrets.token_hex(16)
        try:
            limit(key, 2)
            limit(key, 2)
            with self.assertRaises(HTTPException) as ctx:
                limit(key, 2)
            self.assertEqual(ctx.exception.status_code, 429)
            with self.Session() as db:
                self.assertEqual(db.execute(text("SELECT count FROM sandbox_limits WHERE key = :key"), {"key": key}).scalar_one(), 3)
        finally:
            with self.Session.begin() as db:
                db.execute(text("DELETE FROM sandbox_limits WHERE key = :key"), {"key": key})

    def test_concurrent_limit_admits_only_available_quota_and_counts_rejections(self):
        key = "concurrent-limit:" + secrets.token_hex(16)
        maximum = 3
        attempts = 8
        start_together = threading.Barrier(attempts)

        def request_limit():
            start_together.wait(timeout=5)
            try:
                limit(key, maximum)
                return "accepted"
            except HTTPException as exc:
                if exc.status_code != 429:
                    raise
                return "rejected"

        try:
            # Each call opens its own Session/transaction; synchronize callers
            # so they contend on the same persisted counter at the boundary.
            with ThreadPoolExecutor(max_workers=attempts) as pool:
                outcomes = list(pool.map(lambda _: request_limit(), range(attempts)))

            self.assertEqual(outcomes.count("accepted"), maximum)
            self.assertEqual(outcomes.count("rejected"), attempts - maximum)
            with self.Session() as db:
                self.assertEqual(
                    db.execute(
                        text("SELECT count FROM sandbox_limits WHERE key = :key"),
                        {"key": key},
                    ).scalar_one(),
                    attempts,
                    "rejected requests must still consume the shared quota",
                )
        finally:
            with self.Session.begin() as db:
                db.execute(text("DELETE FROM sandbox_limits WHERE key = :key"), {"key": key})

    def test_limit_resets_counter_when_bucket_changes(self):
        key = "bucket-reset:" + secrets.token_hex(16)
        previous_bucket = 40
        current_bucket = previous_bucket + 1
        with self.Session.begin() as db:
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES (:key, :bucket, 8)
            """), {"key": key, "bucket": previous_bucket})

        try:
            # Fix the clock so the reset assertion cannot depend on wall-clock
            # timing or a minute boundary during the test.
            with patch.object(sandbox_store.time, "time", return_value=current_bucket * 60 + 7):
                limit(key, 2)
                limit(key, 2)
                with self.assertRaises(HTTPException) as ctx:
                    limit(key, 2)
            self.assertEqual(ctx.exception.status_code, 429)
            with self.Session() as db:
                self.assertEqual(
                    db.execute(
                        text("SELECT bucket, count FROM sandbox_limits WHERE key = :key"),
                        {"key": key},
                    ).one(),
                    (current_bucket, 3),
                )
        finally:
            with self.Session.begin() as db:
                db.execute(text("DELETE FROM sandbox_limits WHERE key = :key"), {"key": key})

    def test_purge_expired_records(self):
        owner = secrets.token_hex(32)
        run = secrets.token_hex(16)
        try:
            with self.Session.begin() as db:
                db.add(Owner(id=owner, expires=time.time() + 60))
                db.flush()
                db.add(Run(id=run, owner=owner, agent_hash=digest(secrets.token_urlsafe()), created=time.time()-86400,
                           expires=time.time()-60, deletes=time.time()-1, consent=True,
                           data={"state": initial(), "events": []}))
            purge()
            with self.Session() as db:
                self.assertIsNone(db.get(Run, run))
        finally:
            with self.Session.begin() as db:
                db.execute(text("DELETE FROM sandbox_owners WHERE id = :owner"), {"owner": owner})

    def test_purge_retains_records_at_exact_cutoff(self):
        now = 1_800_000_000
        limit_cutoff = int(now) // 60 - 1440
        prefix = "purge-boundary-" + secrets.token_hex(8)
        boundary_owner = prefix + "-owner-boundary"
        expired_owner = prefix + "-owner-expired"
        boundary_run = prefix + "-run-boundary"
        expired_run = prefix + "-run-expired"
        boundary_oauth = prefix + "-oauth-boundary"
        expired_oauth = prefix + "-oauth-expired"
        boundary_limit = prefix + "-limit-boundary"
        expired_limit = prefix + "-limit-expired"
        daily_limit = "daily:" + prefix

        try:
            with self.Session.begin() as db:
                db.add_all([
                    Owner(id=boundary_owner, expires=now),
                    Owner(id=expired_owner, expires=now - 1),
                ])
                db.flush()
                db.add_all([
                    Run(
                        id=boundary_run, owner=boundary_owner,
                        agent_hash=digest(prefix + "-agent-boundary"),
                        created=now, expires=now, deletes=now, consent=True,
                        data={"state": initial(), "events": []},
                    ),
                    Run(
                        id=expired_run, owner=boundary_owner,
                        agent_hash=digest(prefix + "-agent-expired"),
                        created=now - 1, expires=now - 1, deletes=now - 1,
                        consent=True, data={"state": initial(), "events": []},
                    ),
                ])
                db.execute(text("""
                    INSERT INTO sandbox_oauth(id, expires, data)
                    VALUES (:boundary_id, :now, '{}'::jsonb),
                           (:expired_id, :expired, '{}'::jsonb)
                """), {
                    "boundary_id": boundary_oauth,
                    "expired_id": expired_oauth,
                    "now": now,
                    "expired": now - 1,
                })
                db.execute(text("""
                    INSERT INTO sandbox_limits(key, bucket, count)
                    VALUES (:boundary_key, :cutoff, 1),
                           (:expired_key, :before_cutoff, 1),
                           (:daily_key, :before_cutoff, 100)
                """), {
                    "boundary_key": boundary_limit,
                    "expired_key": expired_limit,
                    "daily_key": daily_limit,
                    "cutoff": limit_cutoff,
                    "before_cutoff": limit_cutoff - 1,
                })

            clock = Mock(return_value=now)
            with patch("services.sandbox_store.time", SimpleNamespace(time=clock)):
                purge()
            clock.assert_called_once_with()

            with self.Session() as db:
                self.assertIsNotNone(db.get(Owner, boundary_owner))
                self.assertIsNone(db.get(Owner, expired_owner))
                self.assertIsNotNone(db.get(Run, boundary_run))
                self.assertIsNone(db.get(Run, expired_run))
                self.assertIsNotNone(db.get(OAuthRecord, boundary_oauth))
                self.assertIsNone(db.get(OAuthRecord, expired_oauth))
                limits = dict(db.execute(text("""
                    SELECT key, count FROM sandbox_limits
                    WHERE key IN (:boundary_key, :expired_key, :daily_key)
                """), {
                    "boundary_key": boundary_limit,
                    "expired_key": expired_limit,
                    "daily_key": daily_limit,
                }).all())
                self.assertEqual(limits, {
                    boundary_limit: 1,
                    daily_limit: 100,
                })
        finally:
            with self.Session.begin() as db:
                db.execute(text("DELETE FROM sandbox_owners WHERE id LIKE :prefix"),
                           {"prefix": prefix + "%"})
                db.execute(text("""
                    DELETE FROM sandbox_oauth WHERE id IN (:boundary_id, :expired_id)
                """), {"boundary_id": boundary_oauth, "expired_id": expired_oauth})
                db.execute(text("""
                    DELETE FROM sandbox_limits WHERE key IN
                        (:boundary_key, :expired_key, :daily_key)
                """), {
                    "boundary_key": boundary_limit,
                    "expired_key": expired_limit,
                    "daily_key": daily_limit,
                })

    def test_purge_preserves_fractional_time_remaining_and_removes_fractionally_expired_records(self):
        now = 1_800_000_000.75
        just_after = now + 0.125
        just_before = now - 0.125
        prefix = "purge-fractional-" + secrets.token_hex(8)
        owners = (prefix + "-owner-after", prefix + "-owner-before")
        runs = (prefix + "-run-after", prefix + "-run-before")
        oauth = (prefix + "-oauth-after", prefix + "-oauth-before")

        try:
            with self.Session.begin() as db:
                db.add_all([
                    Owner(id=owners[0], expires=just_after),
                    Owner(id=owners[1], expires=just_before),
                ])
                db.flush()
                db.add_all([
                    Run(
                        id=runs[0], owner=owners[0],
                        agent_hash=digest(prefix + "-agent-after"),
                        created=now, expires=just_after, deletes=just_after,
                        consent=True, data={"state": initial(), "events": []},
                    ),
                    Run(
                        id=runs[1], owner=owners[1],
                        agent_hash=digest(prefix + "-agent-before"),
                        created=now, expires=just_before, deletes=just_before,
                        consent=True, data={"state": initial(), "events": []},
                    ),
                ])
                db.execute(text("""
                    INSERT INTO sandbox_oauth(id, expires, data)
                    VALUES (:after_id, :after, '{}'::jsonb),
                           (:before_id, :before, '{}'::jsonb)
                """), {
                    "after_id": oauth[0],
                    "after": just_after,
                    "before_id": oauth[1],
                    "before": just_before,
                })

            with patch("services.sandbox_store.time", SimpleNamespace(time=lambda: now)):
                purge()

            with self.Session() as db:
                self.assertIsNotNone(db.get(Owner, owners[0]))
                self.assertIsNone(db.get(Owner, owners[1]))
                self.assertIsNotNone(db.get(Run, runs[0]))
                self.assertIsNone(db.get(Run, runs[1]))
                self.assertIsNotNone(db.get(OAuthRecord, oauth[0]))
                self.assertIsNone(db.get(OAuthRecord, oauth[1]))
        finally:
            with self.Session.begin() as db:
                db.execute(text("DELETE FROM sandbox_owners WHERE id LIKE :prefix"),
                           {"prefix": prefix + "%"})
                db.execute(text("DELETE FROM sandbox_oauth WHERE id LIKE :prefix"),
                           {"prefix": prefix + "%"})

    def test_purge_frees_owner_and_oauth_capacity_without_displacing_live_records(self):
        now = time.time()
        cutoff = int(now) // 60 - 1440
        live_owner_start = MAX_OWNER_RECORDS // 2
        expired_run_count = RETAINED_RUN_LIMIT // 2
        expired_oauth_count = MAX_OAUTH_RECORDS // 2
        expired_limit_count = MAX_OAUTH_RECORDS // 2
        owners = [
            {
                "id": f"purge-cap-owner-{i:04d}",
                "expires": now - 1 if i < live_owner_start else now + 3600,
            }
            for i in range(MAX_OWNER_RECORDS)
        ]
        runs = [
            {
                "id": f"purge-cap-run-{i:04d}",
                "owner": owners[
                    i if i < expired_run_count
                    else live_owner_start + i - expired_run_count
                ]["id"],
                "agent_hash": digest(f"purge-cap-agent-{i}"),
                "created": now - 3600 if i < expired_run_count else now,
                "expires": now - 60 if i < expired_run_count else now + 3600,
                "deletes": now - 1 if i < expired_run_count else now + 3600,
                "consent": True,
                "data": {"state": initial(), "events": []},
            }
            for i in range(RETAINED_RUN_LIMIT)
        ]
        oauth = [
            {
                "id": f"purge-cap-oauth-{i:04d}",
                "expires": now - 1 if i < expired_oauth_count else now + 3600,
                "data": {},
            }
            for i in range(MAX_OAUTH_RECORDS)
        ]
        limit_buckets = [
            {
                "key": f"purge-cap-limit-{i:04d}",
                "bucket": cutoff - 1 if i < expired_limit_count else cutoff + 1,
                "count": 1,
            }
            for i in range(MAX_OAUTH_RECORDS)
        ]
        with self.Session.begin() as db:
            settings = dict(db.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN (
                    'statement_timeout', 'lock_timeout',
                    'idle_in_transaction_session_timeout'
                )
            """)).all())
            self.assertEqual(settings["statement_timeout"], "5000")
            self.assertEqual(settings["lock_timeout"], "2000")
            self.assertEqual(settings["idle_in_transaction_session_timeout"], "10000")
            db.execute(text("""
                INSERT INTO sandbox_owners(id, expires) VALUES (:id, :expires)
            """), owners)
            db.execute(text("""
                INSERT INTO sandbox_runs
                    (id, owner, agent_hash, created, expires, deletes, consent, data)
                VALUES
                    (:id, :owner, :agent_hash, :created, :expires, :deletes, :consent, :data)
            """).bindparams(bindparam("data", type_=JSONB)), runs)
            db.execute(text("""
                INSERT INTO sandbox_oauth(id, expires, data) VALUES (:id, :expires, :data)
            """).bindparams(bindparam("data", type_=JSONB)), oauth)
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count) VALUES (:key, :bucket, :count)
            """), limit_buckets)
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES ('daily:purge-cap-test', :bucket, 100)
            """), {"bucket": cutoff - 1})

        started = time.monotonic()
        purge()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0, f"purge exceeded statement_timeout: {elapsed:.3f}s")
        with self.Session() as db:
            self.assertEqual(
                set(db.execute(text("SELECT id FROM sandbox_owners")).scalars()),
                {owner["id"] for owner in owners[live_owner_start:]},
            )
            self.assertEqual(
                set(db.execute(text("SELECT id FROM sandbox_runs")).scalars()),
                {run["id"] for run in runs[expired_run_count:]},
            )
            self.assertEqual(
                set(db.execute(text("SELECT id FROM sandbox_oauth")).scalars()),
                {record["id"] for record in oauth[expired_oauth_count:]},
            )
            self.assertEqual(
                set(db.execute(text("""
                    SELECT key FROM sandbox_limits WHERE key LIKE 'purge-cap-limit-%'
                """)).scalars()),
                {row["key"] for row in limit_buckets[expired_limit_count:]},
            )
            self.assertEqual(
                db.execute(text("""
                    SELECT bucket, count FROM sandbox_limits
                    WHERE key = 'daily:purge-cap-test'
                """)).one(),
                (cutoff - 1, 100),
            )

        browser = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        owner_response = request(app, "POST", "/api/sandbox/owner", headers=browser)
        self.assertEqual(owner_response.status, 200)
        owner_token = owner_response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        new_owner_id = digest(owner_token)

        with patch.object(sandbox_oauth, "Session", self.Session), \
                patch.object(sandbox_oauth, "origin", return_value="https://testserver"):
            oauth_response = request(app, "POST", "/api/sandbox/oauth/register", {
                "redirect_uris": ["https://client.example/callback"],
                "client_name": "Capacity test client",
            })
        self.assertEqual(oauth_response.status, 201)
        new_oauth_id = "client:" + digest(oauth_response.body["client_id"])

        with self.Session() as db:
            owner_ids = set(db.execute(text("SELECT id FROM sandbox_owners")).scalars())
            oauth_ids = set(db.execute(text("SELECT id FROM sandbox_oauth")).scalars())
            self.assertTrue({owner["id"] for owner in owners[live_owner_start:]}.issubset(owner_ids))
            self.assertTrue({record["id"] for record in oauth[expired_oauth_count:]}.issubset(oauth_ids))
            self.assertIn(new_owner_id, owner_ids)
            self.assertIn(new_oauth_id, oauth_ids)
            self.assertEqual(len(owner_ids), MAX_OWNER_RECORDS - live_owner_start + 1)
            self.assertEqual(len(oauth_ids), MAX_OAUTH_RECORDS - expired_oauth_count + 1)

    def test_purge_preserves_a_rate_limit_refreshed_by_a_concurrent_request(self):
        key = "purge-race:" + secrets.token_hex(16)
        ordinary_stale_key = "purge-race-stale:" + secrets.token_hex(16)
        stale_bucket = int(time.time()) // 60 - 1441
        request_holds_update = threading.Event()
        allow_request_commit = threading.Event()
        purge_started = threading.Event()
        thread_errors = []
        request_thread_id = []

        with self.Session.begin() as db:
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES (:key, :bucket, 7), (:ordinary_key, :bucket, 3)
            """), {
                "key": key,
                "ordinary_key": ordinary_stale_key,
                "bucket": stale_bucket,
            })

        def pause_request_before_commit(session):
            if threading.get_ident() == request_thread_id[0]:
                request_holds_update.set()
                if not allow_request_commit.wait(timeout=3):
                    raise TimeoutError("test did not release the request transaction")

        def run_request():
            request_thread_id.append(threading.get_ident())
            try:
                limit(key, 5)
            except Exception as exc:
                thread_errors.append(exc)

        def run_purge():
            purge_started.set()
            try:
                purge()
            except Exception as exc:
                thread_errors.append(exc)

        event.listen(self.Session, "before_commit", pause_request_before_commit)
        request = threading.Thread(target=run_request, name="purge-race-request")
        cleanup = threading.Thread(target=run_purge, name="purge-race-cleanup")
        try:
            request.start()
            self.assertTrue(request_holds_update.wait(timeout=3), "request did not hold its refreshed row")
            cleanup.start()
            self.assertTrue(purge_started.wait(timeout=1))

            # Wait until the cleanup DELETE is actually blocked on the request's
            # row lock, so the test proves the two operations overlap.
            deadline = time.monotonic() + 1.5
            cleanup_is_waiting = False
            while time.monotonic() < deadline:
                with self.Session() as db:
                    cleanup_is_waiting = db.execute(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_stat_activity
                            WHERE wait_event_type = 'Lock'
                              AND query LIKE 'DELETE FROM sandbox_limits%'
                        )
                    """)).scalar_one()
                if cleanup_is_waiting:
                    break
                time.sleep(0.01)
            self.assertTrue(cleanup_is_waiting, "cleanup did not wait on the refreshed limit row")
        finally:
            allow_request_commit.set()
            request.join(timeout=4)
            cleanup.join(timeout=4)
            event.remove(self.Session, "before_commit", pause_request_before_commit)

        self.assertFalse(request.is_alive(), "request thread did not finish")
        self.assertFalse(cleanup.is_alive(), "cleanup thread did not finish")
        self.assertEqual(thread_errors, [])
        with self.Session() as db:
            self.assertEqual(
                db.execute(text("""
                    SELECT bucket, count FROM sandbox_limits WHERE key = :key
                """), {"key": key}).one(),
                (int(time.time()) // 60, 1),
            )
            self.assertIsNone(db.execute(text("""
                SELECT 1 FROM sandbox_limits WHERE key = :key
            """), {"key": ordinary_stale_key}).scalar_one_or_none())

        with self.assertRaises(HTTPException) as ctx:
            limit(key, 1)
        self.assertEqual(ctx.exception.status_code, 429)
        with self.Session() as db:
            self.assertEqual(
                db.execute(text("SELECT count FROM sandbox_limits WHERE key = :key"), {"key": key}).scalar_one(),
                2,
            )
        with self.Session.begin() as db:
            db.execute(text("DELETE FROM sandbox_limits WHERE key IN (:key, :ordinary_key)"),
                       {"key": key, "ordinary_key": ordinary_stale_key})

    def test_purge_keeps_daily_quota_enforced_after_removing_expired_minute_buckets(self):
        daily_bucket = int(time.time()) // 86400
        stale_bucket = int(time.time()) // 60 - 1441
        stale_key = "daily-quota-purge-stale:" + secrets.token_hex(16)

        try:
            with self.Session.begin() as db:
                db.execute(text("""
                    INSERT INTO sandbox_limits(key, bucket, count)
                    VALUES (:daily_key, :daily_bucket, :daily_count),
                           (:stale_key, :stale_bucket, 1)
                """), {
                    "daily_key": DAILY_KEY,
                    "daily_bucket": daily_bucket,
                    "daily_count": DAILY_RUN_LIMIT,
                    "stale_key": stale_key,
                    "stale_bucket": stale_bucket,
                })

            purge()

            with self.assertRaises(HTTPException) as ctx:
                with self.Session.begin() as db:
                    reserve(db, 1)
            self.assertEqual(ctx.exception.status_code, 429)

            with self.Session() as db:
                self.assertEqual(
                    db.execute(text("""
                        SELECT bucket, count FROM sandbox_limits WHERE key = :key
                    """), {"key": DAILY_KEY}).one(),
                    (daily_bucket, DAILY_RUN_LIMIT),
                )
                self.assertIsNone(db.execute(text("""
                    SELECT 1 FROM sandbox_limits WHERE key = :key
                """), {"key": stale_key}).scalar_one_or_none())
        finally:
            with self.Session.begin() as db:
                db.execute(text("""
                    DELETE FROM sandbox_limits WHERE key IN (:daily_key, :stale_key)
                """), {"daily_key": DAILY_KEY, "stale_key": stale_key})

    def test_purge_recovers_agent_action_quota_after_bucket_rollover(self):
        now = (int(time.time()) // 60) * 60
        current_bucket = now // 60
        current_run = "action-rollover-" + secrets.token_hex(8)
        expired_run = "action-expired-" + secrets.token_hex(8)
        current_key = "action:" + current_run
        expired_key = "action:" + expired_run
        clock = [now - 1442 * 60]

        try:
            # Use the same per-run key format as the HTTP and MCP action paths.
            # Keep one expired run key, while the current run rolls through
            # multiple minute buckets via the production rate-limit service.
            with patch(
                "services.sandbox_store.time",
                SimpleNamespace(time=lambda: clock[0]),
            ):
                limit(expired_key, 3)

                clock[0] = now - 2 * 60
                limit(current_key, 3)
                limit(current_key, 3)
                clock[0] = now - 60
                limit(current_key, 3)
                clock[0] = now
                limit(current_key, 3)
                limit(current_key, 3)

                purge()

                with self.Session() as db:
                    self.assertIsNone(db.execute(text("""
                        SELECT 1 FROM sandbox_limits WHERE key = :key
                    """), {"key": expired_key}).scalar_one_or_none())
                    self.assertEqual(
                        db.execute(text("""
                            SELECT bucket, count FROM sandbox_limits WHERE key = :key
                        """), {"key": current_key}).one(),
                        (current_bucket, 2),
                    )

                # The quota remains live after cleanup, not merely present
                # with the expected persisted count.
                limit(current_key, 3)
                with self.assertRaises(HTTPException) as ctx:
                    limit(current_key, 3)
                self.assertEqual(ctx.exception.status_code, 429)
        finally:
            with self.Session.begin() as db:
                db.execute(text("""
                    DELETE FROM sandbox_limits WHERE key IN (:current_key, :expired_key)
                """), {"current_key": current_key, "expired_key": expired_key})

    def test_purge_preserves_live_records_at_public_storage_capacity_with_database_timeouts(self):
        now = time.time()
        cutoff = int(now) // 60 - 1440
        live_owner_start = MAX_OWNER_RECORDS // 2
        expired_run_count = RETAINED_RUN_LIMIT // 2
        expired_oauth_count = MAX_OAUTH_RECORDS // 2
        expired_limit_count = MAX_OAUTH_RECORDS // 2
        owners = [
            {
                "id": f"purge-cap-owner-{i:04d}",
                "expires": now - 1 if i < live_owner_start else now + 3600,
            }
            for i in range(MAX_OWNER_RECORDS)
        ]
        runs = [
            {
                "id": f"purge-cap-run-{i:04d}",
                "owner": owners[
                    i if i < expired_run_count
                    else live_owner_start + i - expired_run_count
                ]["id"],
                "agent_hash": digest(f"purge-cap-agent-{i}"),
                "created": now - 3600 if i < expired_run_count else now,
                "expires": now - 60 if i < expired_run_count else now + 3600,
                "deletes": now - 1 if i < expired_run_count else now + 3600,
                "consent": True,
                "data": {"state": initial(), "events": []},
            }
            for i in range(RETAINED_RUN_LIMIT)
        ]
        oauth = [
            {
                "id": f"purge-cap-oauth-{i:04d}",
                "expires": now - 1 if i < expired_oauth_count else now + 3600,
                "data": {},
            }
            for i in range(MAX_OAUTH_RECORDS)
        ]
        limit_buckets = [
            {
                "key": f"purge-cap-limit-{i:04d}",
                "bucket": cutoff - 1 if i < expired_limit_count else cutoff + 1,
                "count": 1,
            }
            for i in range(MAX_OAUTH_RECORDS)
        ]
        with self.Session.begin() as db:
            settings = dict(db.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN (
                    'statement_timeout', 'lock_timeout',
                    'idle_in_transaction_session_timeout'
                )
            """)).all())
            self.assertEqual(settings["statement_timeout"], "5000")
            self.assertEqual(settings["lock_timeout"], "2000")
            self.assertEqual(settings["idle_in_transaction_session_timeout"], "10000")
            db.execute(text("""
                INSERT INTO sandbox_owners(id, expires) VALUES (:id, :expires)
            """), owners)
            db.execute(text("""
                INSERT INTO sandbox_runs
                    (id, owner, agent_hash, created, expires, deletes, consent, data)
                VALUES
                    (:id, :owner, :agent_hash, :created, :expires, :deletes, :consent, :data)
            """).bindparams(bindparam("data", type_=JSONB)), runs)
            db.execute(text("""
                INSERT INTO sandbox_oauth(id, expires, data) VALUES (:id, :expires, :data)
            """).bindparams(bindparam("data", type_=JSONB)), oauth)
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count) VALUES (:key, :bucket, :count)
            """), limit_buckets)
            # Stale daily quota rows must remain: deleting them would reopen quota.
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES ('daily:purge-cap-test', :bucket, 100)
            """), {"bucket": cutoff - 1})

        started = time.monotonic()
        purge()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0, f"purge exceeded statement_timeout: {elapsed:.3f}s")
        with self.Session() as db:
            self.assertEqual(
                set(db.execute(text("SELECT id FROM sandbox_owners")).scalars()),
                {owner["id"] for owner in owners[live_owner_start:]},
            )
            self.assertEqual(
                set(db.execute(text("SELECT id FROM sandbox_runs")).scalars()),
                {run["id"] for run in runs[expired_run_count:]},
            )
            self.assertEqual(
                set(db.execute(text("SELECT id FROM sandbox_oauth")).scalars()),
                {record["id"] for record in oauth[expired_oauth_count:]},
            )
            self.assertEqual(
                set(db.execute(text("""
                    SELECT key FROM sandbox_limits WHERE key LIKE 'purge-cap-limit-%'
                """)).scalars()),
                {row["key"] for row in limit_buckets[expired_limit_count:]},
            )
            self.assertEqual(
                db.execute(text("""
                    SELECT bucket, count FROM sandbox_limits WHERE key = 'daily:purge-cap-test'
                """)).one(),
                (cutoff - 1, 100),
            )

    def test_purge_scales_with_stale_limit_history_without_changing_daily_quota(self):
        cutoff = int(time.time()) // 60 - 1440
        daily_bucket = int(time.time()) // 86400
        sizes = (MAX_OAUTH_RECORDS, MAX_OAUTH_RECORDS * 10, MAX_OAUTH_RECORDS * 50)
        with self.Session.begin() as db:
            settings = dict(db.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN ('statement_timeout', 'lock_timeout')
            """)).all())
            self.assertEqual(settings["statement_timeout"], "5000")
            self.assertEqual(settings["lock_timeout"], "2000")
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES (:key, :bucket, :count)
            """), {"key": DAILY_KEY, "bucket": daily_bucket, "count": 17})

        for size in sizes:
            prefix = f"purge-scale-{size}-"
            with self.Session.begin() as db:
                db.execute(text("""
                    INSERT INTO sandbox_limits(key, bucket, count)
                    SELECT :prefix || n, :bucket, 1
                    FROM generate_series(1, :size) AS n
                """), {"prefix": prefix, "bucket": cutoff - 1, "size": size})

            started = time.monotonic()
            purge()
            elapsed = time.monotonic() - started
            print(f"Purged {size:,} stale rate-limit buckets in {elapsed:.3f}s")
            self.assertLess(
                elapsed, 5.0,
                f"purge exceeded statement_timeout with {size:,} stale buckets: {elapsed:.3f}s",
            )
            with self.Session() as db:
                self.assertEqual(db.execute(text("""
                    SELECT count(*) FROM sandbox_limits WHERE key LIKE :prefix
                """), {"prefix": prefix + "%"}).scalar_one(), 0)
                self.assertEqual(db.execute(text("""
                    SELECT bucket, count FROM sandbox_limits WHERE key = :key
                """), {"key": DAILY_KEY}).one(), (daily_bucket, 17))

    def test_purge_scales_with_mostly_active_limit_buckets(self):
        cutoff = int(time.time()) // 60 - 1440
        size = MAX_OAUTH_RECORDS * 50
        stale_count = MAX_OAUTH_RECORDS
        prefix = f"purge-mixed-{secrets.token_hex(8)}-"
        active_summary = text("""
            SELECT count(*), sum(count), sum(bucket), min(bucket), max(bucket),
                   count(DISTINCT bucket)
            FROM sandbox_limits
            WHERE key LIKE :prefix AND bucket >= :cutoff
        """)
        with self.Session.begin() as db:
            timeout = db.execute(text("""
                SELECT setting FROM pg_settings WHERE name = 'statement_timeout'
            """)).scalar_one()
            self.assertEqual(timeout, "5000")
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                SELECT :prefix || n,
                       CASE WHEN n <= :stale_count
                            THEN :stale_bucket
                            ELSE :cutoff + 1 + (n % 1440)
                       END,
                       (n % 13) + 1
                FROM generate_series(1, :size) AS n
            """), {
                "prefix": prefix,
                "stale_count": stale_count,
                "stale_bucket": cutoff - 1,
                "cutoff": cutoff,
                "size": size,
            })
            active_before = db.execute(
                active_summary, {"prefix": prefix + "%", "cutoff": cutoff}
            ).one()

        self.assertEqual(active_before[0], size - stale_count)
        started = time.monotonic()
        purge()
        elapsed = time.monotonic() - started
        print(
            f"Purged {stale_count:,} stale buckets among {size:,} mixed "
            f"rate-limit buckets in {elapsed:.3f}s"
        )
        self.assertLess(
            elapsed, 5.0,
            f"purge exceeded statement_timeout with {size:,} mostly active buckets: {elapsed:.3f}s",
        )
        with self.Session() as db:
            self.assertEqual(db.execute(text("""
                SELECT count(*) FROM sandbox_limits
                WHERE key LIKE :prefix AND bucket < :cutoff
            """), {"prefix": prefix + "%", "cutoff": cutoff}).scalar_one(), 0)
            active_after = db.execute(
                active_summary, {"prefix": prefix + "%", "cutoff": cutoff}
            ).one()
            self.assertEqual(active_after, active_before)

    def test_rate_limit_check_finishes_while_purge_scans_mixed_buckets(self):
        now = int(time.time())
        cutoff = now // 60 - 1440
        size = MAX_OAUTH_RECORDS * 50
        stale_count = MAX_OAUTH_RECORDS
        token = secrets.token_hex(8)
        prefix = f"purge-responsive-{token}-"
        active_key = f"purge-responsive-active-{token}"
        cleanup_started = threading.Event()
        thread_errors = []
        cleanup_elapsed = []
        activity_name = f"purge-responsive-{token}"

        with self.Session.begin() as db:
            settings = dict(db.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN ('statement_timeout', 'lock_timeout')
            """)).all())
            self.assertEqual(settings["statement_timeout"], "5000")
            self.assertEqual(settings["lock_timeout"], "2000")
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES (:key, :bucket, 7)
            """), {"key": active_key, "bucket": now // 60})
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                SELECT :prefix || n,
                       CASE WHEN n <= :stale_count
                            THEN :stale_bucket
                            ELSE :cutoff + 1 + (n % 1440)
                       END,
                       (n % 13) + 1
                FROM generate_series(1, :size) AS n
            """), {
                "prefix": prefix,
                "stale_count": stale_count,
                "stale_bucket": cutoff - 1,
                "cutoff": cutoff,
                "size": size,
            })

        def run_purge():
            started = time.monotonic()
            cleanup_started.set()
            try:
                purge()
            except Exception as exc:
                thread_errors.append(exc)
            finally:
                cleanup_elapsed.append(time.monotonic() - started)

        cleanup = threading.Thread(target=run_purge, name="purge-responsive-cleanup")

        def tag_cleanup_connection(session, transaction, connection):
            if threading.current_thread() is cleanup:
                connection.execute(
                    text("SELECT set_config('application_name', :name, true)"),
                    {"name": activity_name},
                )

        limit_elapsed = None
        cleanup_was_scanning = False
        event.listen(self.Session, "after_begin", tag_cleanup_connection)
        try:
            with patch("services.sandbox_store.time", SimpleNamespace(time=lambda: now)):
                cleanup.start()
                self.assertTrue(cleanup_started.wait(timeout=1))

                # Start the request only after PostgreSQL confirms the cleanup
                # DELETE is actively scanning, not merely queued to start.
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    with self.Session() as db:
                        cleanup_was_scanning = db.execute(text("""
                            SELECT EXISTS (
                                SELECT 1 FROM pg_stat_activity
                                WHERE state = 'active'
                                  AND application_name = :application_name
                                  AND query LIKE 'DELETE FROM sandbox_limits%'
                            )
                        """), {"application_name": activity_name}).scalar_one()
                    if cleanup_was_scanning:
                        break
                    time.sleep(0.005)

                self.assertTrue(
                    cleanup_was_scanning,
                    "cleanup never entered its rate-limit bucket DELETE",
                )
                started = time.monotonic()
                limit(active_key, 100)
                limit_elapsed = time.monotonic() - started
            cleanup.join(timeout=6)
            self.assertFalse(
                cleanup.is_alive(),
                "cleanup did not finish within its database timeout",
            )
            self.assertEqual(thread_errors, [])
            self.assertEqual(len(cleanup_elapsed), 1)
            self.assertLess(cleanup_elapsed[0], 5.0, "cleanup exceeded statement_timeout")
            self.assertIsNotNone(limit_elapsed)
            self.assertLess(limit_elapsed, 5.0, "rate-limit check exceeded statement_timeout")

            with self.Session() as db:
                self.assertEqual(
                    db.execute(text("""
                        SELECT bucket, count FROM sandbox_limits WHERE key = :key
                    """), {"key": active_key}).one(),
                    (now // 60, 8),
                )
                self.assertEqual(
                    db.execute(text("""
                        SELECT count(*) FROM sandbox_limits
                        WHERE key LIKE :prefix AND bucket < :cutoff
                    """), {"prefix": prefix + "%", "cutoff": cutoff}).scalar_one(),
                    0,
                )
        finally:
            if cleanup.ident is not None:
                cleanup.join(timeout=6)
            event.remove(self.Session, "after_begin", tag_cleanup_connection)
            with self.Session.begin() as db:
                db.execute(text("""
                    DELETE FROM sandbox_limits
                    WHERE key LIKE :prefix OR key = :active_key
                """), {"prefix": prefix + "%", "active_key": active_key})

    def test_rate_limit_upserts_remain_responsive_during_analyze_and_vacuum(self):
        now = int(time.time())
        token = secrets.token_hex(8)
        dead_prefix = f"maintenance-dead-{token}-"
        active_key = f"maintenance-active-{token}"
        activity_name = f"rate-limit-maintenance-{token}"
        dead_rows = 200_000
        request_count = 32
        maintenance_started = threading.Event()
        maintenance_errors = []
        maintenance_elapsed = []
        maintenance_overlap = []
        request_elapsed = []

        with self.Session.begin() as db:
            settings = dict(db.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN ('statement_timeout', 'lock_timeout')
            """)).all())
            self.assertEqual(settings["statement_timeout"], "5000")
            self.assertEqual(settings["lock_timeout"], "2000")
            db.execute(text(
                "ALTER TABLE sandbox_limits SET (autovacuum_enabled = false)"
            ))
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES (:key, :bucket, 1)
            """), {"key": active_key, "bucket": now // 60})
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                SELECT :prefix || n, :bucket, 1
                FROM generate_series(1, :size) AS n
            """), {
                "prefix": dead_prefix,
                "bucket": now // 60,
                "size": dead_rows,
            })
        with self.Session.begin() as db:
            db.execute(text("""
                DELETE FROM sandbox_limits WHERE key LIKE :prefix
            """), {"prefix": dead_prefix + "%"})

        def run_maintenance():
            started = time.monotonic()
            maintenance_started.set()
            try:
                with self.engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as db:
                    db.execute(text(
                        "SELECT set_config('application_name', :name, false)"
                    ), {"name": activity_name})
                    db.execute(text("ANALYZE sandbox_limits"))
                    db.execute(text("VACUUM sandbox_limits"))
            except Exception as exc:
                maintenance_errors.append(exc)
            finally:
                maintenance_elapsed.append(time.monotonic() - started)

        maintenance = threading.Thread(
            target=run_maintenance, name="rate-limit-maintenance"
        )

        def observe_maintenance_before_upsert(
            connection, cursor, statement, parameters, context, executemany
        ):
            if not statement.lstrip().upper().startswith(
                "INSERT INTO SANDBOX_LIMITS"
            ):
                return
            with self.engine.connect() as observer:
                active = observer.execute(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_stat_activity
                        WHERE state = 'active'
                          AND application_name = :application_name
                          AND query LIKE ANY (ARRAY[
                              'ANALYZE sandbox_limits%',
                              'VACUUM sandbox_limits%'
                          ])
                    )
                """), {"application_name": activity_name}).scalar_one()
            maintenance_overlap.append(active)

        event.listen(
            self.engine, "before_cursor_execute",
            observe_maintenance_before_upsert,
        )
        maintenance_was_active = False
        try:
            with patch("services.sandbox_store.time", SimpleNamespace(time=lambda: now)):
                maintenance.start()
                self.assertTrue(maintenance_started.wait(timeout=1))

                # Do not start the rate-limit workload until PostgreSQL reports
                # the maintenance command as active in this private schema.
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    with self.Session() as db:
                        maintenance_was_active = db.execute(text("""
                            SELECT EXISTS (
                                SELECT 1 FROM pg_stat_activity
                                WHERE state = 'active'
                                  AND application_name = :application_name
                                  AND query LIKE ANY (ARRAY[
                                      'ANALYZE sandbox_limits%',
                                      'VACUUM sandbox_limits%'
                                  ])
                            )
                        """), {"application_name": activity_name}).scalar_one()
                    if maintenance_was_active:
                        break
                    if maintenance_errors:
                        break
                    time.sleep(0.002)

                self.assertTrue(
                    maintenance_was_active,
                    "ANALYZE/VACUUM completed without an observable active window",
                )
                for _ in range(request_count):
                    started = time.monotonic()
                    limit(active_key, 1000)
                    request_elapsed.append(time.monotonic() - started)

            maintenance.join(timeout=6)
            self.assertFalse(
                maintenance.is_alive(),
                "ANALYZE/VACUUM did not finish within the database timeout",
            )
            self.assertEqual(maintenance_errors, [])
            self.assertEqual(len(maintenance_elapsed), 1)
            self.assertLess(
                maintenance_elapsed[0], 5.0,
                "ANALYZE/VACUUM exceeded the configured statement timeout",
            )
            self.assertEqual(len(request_elapsed), request_count)
            self.assertTrue(
                all(elapsed < 5.0 for elapsed in request_elapsed),
                f"a rate-limit update exceeded statement_timeout: {request_elapsed}",
            )
            self.assertTrue(
                any(maintenance_overlap),
                "no rate-limit upsert ran while PostgreSQL reported maintenance active",
            )

            with self.Session() as db:
                self.assertEqual(
                    db.execute(text("""
                        SELECT bucket, count FROM sandbox_limits WHERE key = :key
                    """), {"key": active_key}).one(),
                    (now // 60, request_count + 1),
                )
                self.assertEqual(
                    db.execute(text("""
                        SELECT count(*) FROM sandbox_limits WHERE key LIKE :prefix
                    """), {"prefix": dead_prefix + "%"}).scalar_one(),
                    0,
                )
        finally:
            if maintenance.ident is not None:
                maintenance.join(timeout=6)
            event.remove(
                self.engine, "before_cursor_execute",
                observe_maintenance_before_upsert,
            )
            with self.Session.begin() as db:
                db.execute(text("""
                    DELETE FROM sandbox_limits
                    WHERE key LIKE :prefix OR key = :active_key
                """), {
                    "prefix": dead_prefix + "%",
                    "active_key": active_key,
                })

    def test_purge_scales_with_mostly_active_buckets_after_repeated_churn(self):
        # PD-9: freeze the clock so a minute boundary between setup and purge()
        # cannot turn edge buckets stale (it once failed with 194232 vs 196000).
        # Regression guard: pin the clock just over a minute in the past, so a purge()
        # that read the real clock would be at least 2 minutes later and delete the
        # cutoff+1 edge buckets (the original failure). The frozen purge() must not.
        frozen = (int(time.time()) // 60) * 60 - 60.001
        cutoff = int(frozen) // 60 - 1440
        size = MAX_OAUTH_RECORDS * 50
        stale_count = MAX_OAUTH_RECORDS
        churn_size = MAX_OAUTH_RECORDS * 2
        churn_cycles = 3
        update_rounds = 6
        token = secrets.token_hex(8)
        churn_prefix = f"purge-churn-{token}-"
        prefix = f"purge-churn-mixed-{token}-"
        active_summary = text("""
            SELECT count(*), sum(count), sum(bucket), min(bucket), max(bucket),
                   count(DISTINCT bucket)
            FROM sandbox_limits
            WHERE key LIKE :prefix AND bucket >= :cutoff
        """)

        with self.Session.begin() as db:
            settings = dict(db.execute(text("""
                SELECT name, setting FROM pg_settings
                WHERE name IN ('statement_timeout', 'lock_timeout')
            """)).all())
            self.assertEqual(settings["statement_timeout"], "5000")
            self.assertEqual(settings["lock_timeout"], "2000")
            # Keep the churned-table condition deterministic in this disposable
            # schema; normal autovacuum behavior is not under test here.
            db.execute(text(
                "ALTER TABLE sandbox_limits SET (autovacuum_enabled = false)"
            ))

        deleted_churn_rows = 0
        for cycle in range(churn_cycles):
            cycle_prefix = f"{churn_prefix}{cycle}-"
            with self.Session.begin() as db:
                db.execute(text("""
                    INSERT INTO sandbox_limits(key, bucket, count)
                    SELECT :prefix || n, :bucket, 1
                    FROM generate_series(1, :size) AS n
                """), {
                    "prefix": cycle_prefix,
                    "bucket": cutoff - update_rounds - 1,
                    "size": churn_size,
                })

            # Each committed upsert models rate-limit bucket refreshes while
            # retaining the prior row versions as dead tuples.
            for generation in range(update_rounds):
                with self.Session.begin() as db:
                    db.execute(text("""
                        INSERT INTO sandbox_limits(key, bucket, count)
                        SELECT :prefix || n, :bucket, 1
                        FROM generate_series(1, :size) AS n
                        ON CONFLICT (key) DO UPDATE SET
                            count = CASE
                                WHEN sandbox_limits.bucket = EXCLUDED.bucket
                                    THEN sandbox_limits.count + 1
                                ELSE 1
                            END,
                            bucket = EXCLUDED.bucket
                    """), {
                        "prefix": cycle_prefix,
                        "bucket": cutoff - update_rounds + generation,
                        "size": churn_size,
                    })

            with self.Session.begin() as db:
                result = db.execute(text("""
                    DELETE FROM sandbox_limits
                    WHERE key LIKE :pattern
                      AND key NOT IN (
                          SELECT :prefix || n::text
                          FROM generate_series(1, :size) AS n
                          WHERE n % 5 = 0
                      )
                """), {
                    "pattern": cycle_prefix + "%",
                    "prefix": cycle_prefix,
                    "size": churn_size,
                })
                deleted_churn_rows += result.rowcount

        retained_churn_rows = churn_cycles * (churn_size // 5)
        self.assertEqual(
            deleted_churn_rows,
            churn_cycles * (churn_size - churn_size // 5),
        )
        with self.Session.begin() as db:
            self.assertEqual(
                db.execute(text("""
                    SELECT count(*) FROM sandbox_limits WHERE key LIKE :prefix
                """), {"prefix": churn_prefix + "%"}).scalar_one(),
                retained_churn_rows,
            )
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                SELECT :prefix || n,
                       CASE WHEN n <= :stale_count
                            THEN :stale_bucket
                            ELSE :cutoff + 1 + (n % 1440)
                       END,
                       (n % 13) + 1
                FROM generate_series(1, :size) AS n
            """), {
                "prefix": prefix,
                "stale_count": stale_count,
                "stale_bucket": cutoff - 1,
                "cutoff": cutoff,
                "size": size,
            })
            active_before = db.execute(
                active_summary, {"prefix": prefix + "%", "cutoff": cutoff}
            ).one()

        self.assertEqual(active_before[0], size - stale_count)
        started = time.monotonic()
        with patch("services.sandbox_store.time.time", return_value=frozen):
            purge()
        elapsed = time.monotonic() - started
        print(
            f"Purged {stale_count:,} mixed stale buckets after "
            f"{churn_cycles * update_rounds * churn_size:,} bucket updates "
            f"and {deleted_churn_rows:,} churn deletes in {elapsed:.3f}s"
        )
        self.assertLess(
            elapsed, 5.0,
            f"purge exceeded statement_timeout after repeated limit churn: {elapsed:.3f}s",
        )
        with self.Session() as db:
            self.assertEqual(db.execute(text("""
                SELECT count(*) FROM sandbox_limits
                WHERE key LIKE :prefix AND bucket < :cutoff
            """), {"prefix": prefix + "%", "cutoff": cutoff}).scalar_one(), 0)
            self.assertEqual(db.execute(text("""
                SELECT count(*) FROM sandbox_limits WHERE key LIKE :prefix
            """), {"prefix": churn_prefix + "%"}).scalar_one(), 0)
            active_after = db.execute(
                active_summary, {"prefix": prefix + "%", "cutoff": cutoff}
            ).one()
            self.assertEqual(active_after, active_before)

    def test_autovacuum_recovers_rate_limit_storage_after_repeated_churn(self):
        # PD-9: freeze the clock so a minute boundary between setup and purge()
        # cannot turn edge buckets stale (it once failed with 194232 vs 196000).
        frozen = time.time()
        cutoff = int(frozen) // 60 - 1440
        token = secrets.token_hex(8)
        churn_prefix = f"autovacuum-churn-{token}-"
        mixed_prefix = f"autovacuum-mixed-{token}-"
        churn_size = MAX_OAUTH_RECORDS * 2
        churn_cycles = 3
        update_rounds = 6
        mixed_size = MAX_OAUTH_RECORDS * 2
        stale_count = MAX_OAUTH_RECORDS
        active_summary = text("""
            SELECT count(*), sum(count), sum(bucket), min(bucket), max(bucket),
                   count(DISTINCT bucket)
            FROM sandbox_limits
            WHERE key LIKE :prefix AND bucket >= :cutoff
        """)

        with self.engine.connect() as db:
            config = db.execute(text("""
                SELECT current_setting('autovacuum')::boolean AS enabled,
                       extract(epoch FROM
                           current_setting('autovacuum_naptime')::interval
                       ) AS naptime_seconds,
                       reloptions
                FROM pg_class
                WHERE oid = 'sandbox_limits'::regclass
            """)).one()
            started_at = db.execute(
                text("SELECT clock_timestamp()")
            ).scalar_one()

        self.assertTrue(config.enabled, "PostgreSQL autovacuum is disabled")
        self.assertFalse(
            config.reloptions and "autovacuum_enabled=false" in config.reloptions,
            "sandbox_limits has autovacuum disabled at the table level",
        )

        deleted_churn_rows = 0
        dead_tuples_generated = 0
        for cycle in range(churn_cycles):
            cycle_prefix = f"{churn_prefix}{cycle}-"
            with self.engine.begin() as db:
                db.execute(text("""
                    INSERT INTO sandbox_limits(key, bucket, count)
                    SELECT :prefix || n, :bucket, 1
                    FROM generate_series(1, :size) AS n
                """), {
                    "prefix": cycle_prefix,
                    "bucket": cutoff - update_rounds - 1,
                    "size": churn_size,
                })

            for generation in range(update_rounds):
                with self.engine.begin() as db:
                    db.execute(text("""
                        INSERT INTO sandbox_limits(key, bucket, count)
                        SELECT :prefix || n, :bucket, 1
                        FROM generate_series(1, :size) AS n
                        ON CONFLICT (key) DO UPDATE SET
                            count = CASE
                                WHEN sandbox_limits.bucket = EXCLUDED.bucket
                                    THEN sandbox_limits.count + 1
                                ELSE 1
                            END,
                            bucket = EXCLUDED.bucket
                    """), {
                        "prefix": cycle_prefix,
                        "bucket": cutoff - update_rounds + generation,
                        "size": churn_size,
                    })
            dead_tuples_generated += update_rounds * churn_size

            with self.engine.begin() as db:
                result = db.execute(text("""
                    DELETE FROM sandbox_limits
                    WHERE key LIKE :pattern
                      AND key NOT IN (
                          SELECT :prefix || n::text
                          FROM generate_series(1, :size) AS n
                          WHERE n % 5 = 0
                      )
                """), {
                    "pattern": cycle_prefix + "%",
                    "prefix": cycle_prefix,
                    "size": churn_size,
                })
                deleted_churn_rows += result.rowcount
            dead_tuples_generated += result.rowcount

        self.assertEqual(
            deleted_churn_rows,
            churn_cycles * (churn_size - churn_size // 5),
        )

        with self.engine.begin() as db:
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                SELECT :prefix || n,
                       CASE WHEN n <= :stale_count
                            THEN :stale_bucket
                            ELSE :cutoff + 1 + (n % 1440)
                       END,
                       (n % 13) + 1
                FROM generate_series(1, :size) AS n
            """), {
                "prefix": mixed_prefix,
                "stale_count": stale_count,
                "stale_bucket": cutoff - 1,
                "cutoff": cutoff,
                "size": mixed_size,
            })
            active_before = db.execute(
                active_summary,
                {"prefix": mixed_prefix + "%", "cutoff": cutoff},
            ).one()
            self.assertEqual(active_before[0], mixed_size - stale_count)

        # Autovacuum runs asynchronously. Wait for both routine maintenance
        # actions and for the estimated dead tuples to fall well below the
        # churn generated above; no manual ANALYZE or VACUUM is issued here.
        wait_seconds = max(90, int(config.naptime_seconds * 2 + 10))
        deadline = time.monotonic() + wait_seconds
        stats = None
        while time.monotonic() < deadline:
            with self.engine.connect() as db:
                db.execute(text("SELECT pg_stat_clear_snapshot()"))
                stats = db.execute(text("""
                    SELECT n_dead_tup, last_analyze, last_vacuum,
                           last_autoanalyze, last_autovacuum
                    FROM pg_stat_user_tables
                    WHERE schemaname = :schema
                      AND relname = 'sandbox_limits'
                """), {"schema": self.schema}).one_or_none()
            if (
                stats
                and stats.last_autoanalyze
                and stats.last_autovacuum
                and stats.last_autoanalyze >= started_at
                and stats.last_autovacuum >= started_at
                and stats.n_dead_tup < dead_tuples_generated // 2
            ):
                break
            time.sleep(0.5)
        else:
            self.fail(
                "autovacuum did not analyze/vacuum and recover the table "
                f"within {wait_seconds}s; latest statistics: {stats!r}"
            )

        self.assertIsNone(stats.last_analyze, "the test must not run manual ANALYZE")
        self.assertIsNone(stats.last_vacuum, "the test must not run manual VACUUM")
        self.assertLess(
            stats.n_dead_tup,
            dead_tuples_generated // 2,
            "autovacuum did not substantially reduce estimated dead tuples",
        )

        with patch("services.sandbox_store.time.time", return_value=frozen):
            purge()
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("""
                SELECT count(*) FROM sandbox_limits
                WHERE key LIKE :prefix AND bucket < :cutoff
            """), {
                "prefix": mixed_prefix + "%",
                "cutoff": cutoff,
            }).scalar_one(), 0)
            self.assertEqual(db.execute(text("""
                SELECT count(*) FROM sandbox_limits WHERE key LIKE :prefix
            """), {"prefix": churn_prefix + "%"}).scalar_one(), 0)
            active_after = db.execute(
                active_summary,
                {"prefix": mixed_prefix + "%", "cutoff": cutoff},
            ).one()
        self.assertEqual(active_after, active_before)

    def test_autovacuum_recovers_real_rate_limit_requests_across_buckets(self):
        now = int(time.time())
        current_bucket = now // 60
        bucket_count = 3
        requester_count = 1200
        dead_tuples_generated = requester_count * (bucket_count - 1)
        token = secrets.token_hex(8)
        prefix = f"autovacuum-service-{token}-"
        active_key = prefix + "active"
        stale_key = prefix + "stale"
        same_bucket_requests = 5

        with self.engine.connect() as db:
            config = db.execute(text("""
                SELECT current_setting('autovacuum')::boolean AS enabled,
                       extract(epoch FROM
                           current_setting('autovacuum_naptime')::interval
                       ) AS naptime_seconds,
                       reloptions
                FROM pg_class
                WHERE oid = 'sandbox_limits'::regclass
            """)).one()
            started_at = db.execute(
                text("SELECT clock_timestamp()")
            ).scalar_one()

        self.assertTrue(config.enabled, "PostgreSQL autovacuum is disabled")
        self.assertFalse(
            config.reloptions and "autovacuum_enabled=false" in config.reloptions,
            "sandbox_limits has autovacuum disabled at the table level",
        )

        clock = [now - 1441 * 60]
        try:
            # These calls use the production rate-limit service and its normal
            # committed upsert, rather than reproducing the SQL in the test.
            with patch(
                "services.sandbox_store.time",
                SimpleNamespace(time=lambda: clock[0]),
            ):
                limit(stale_key, 1000)
                first_bucket = current_bucket - bucket_count + 1
                for bucket in range(first_bucket, current_bucket + 1):
                    clock[0] = bucket * 60
                    for requester in range(requester_count):
                        limit(f"{prefix}request-{requester:04d}", 1000)
                    limit(active_key, 1000)
                for _ in range(same_bucket_requests - 1):
                    limit(active_key, 1000)

            # PostgreSQL's routine maintenance is asynchronous. No explicit
            # ANALYZE or VACUUM is issued by this integration test.
            wait_seconds = max(90, int(config.naptime_seconds * 2 + 10))
            deadline = time.monotonic() + wait_seconds
            stats = None
            while time.monotonic() < deadline:
                with self.engine.connect() as db:
                    db.execute(text("SELECT pg_stat_clear_snapshot()"))
                    stats = db.execute(text("""
                        SELECT n_dead_tup, last_analyze, last_vacuum,
                               last_autoanalyze, last_autovacuum
                        FROM pg_stat_user_tables
                        WHERE schemaname = :schema
                          AND relname = 'sandbox_limits'
                    """), {"schema": self.schema}).one_or_none()
                if (
                    stats
                    and stats.last_autoanalyze
                    and stats.last_autovacuum
                    and stats.last_autoanalyze >= started_at
                    and stats.last_autovacuum >= started_at
                    and stats.n_dead_tup < dead_tuples_generated // 2
                ):
                    break
                time.sleep(0.5)
            else:
                self.fail(
                    "autovacuum did not analyze/vacuum the table after real "
                    f"rate-limit requests within {wait_seconds}s; latest "
                    f"statistics: {stats!r}"
                )

            self.assertIsNone(
                stats.last_analyze,
                "the test must not run manual ANALYZE",
            )
            self.assertIsNone(
                stats.last_vacuum,
                "the test must not run manual VACUUM",
            )
            self.assertLess(
                stats.n_dead_tup,
                dead_tuples_generated // 2,
                "autovacuum did not substantially reduce request churn",
            )

            with patch(
                "services.sandbox_store.time",
                SimpleNamespace(time=lambda: now),
            ):
                purge()

            with self.engine.connect() as db:
                self.assertIsNone(db.execute(text("""
                    SELECT key FROM sandbox_limits WHERE key = :key
                """), {"key": stale_key}).scalar_one_or_none())
                self.assertEqual(
                    db.execute(text("""
                        SELECT bucket, count FROM sandbox_limits WHERE key = :key
                    """), {"key": active_key}).one(),
                    (current_bucket, same_bucket_requests),
                )
        finally:
            with self.Session.begin() as db:
                db.execute(text("""
                    DELETE FROM sandbox_limits WHERE key LIKE :prefix
                """), {"prefix": prefix + "%"})

    def test_purge_after_repeated_churn_with_and_without_vacuum_maintenance(self):
        cutoff = int(time.time()) // 60 - 1440
        token = secrets.token_hex(8)
        baseline_schema = self.schema
        maintained_schema = "sandbox_maintenance_" + token
        churn_size = MAX_OAUTH_RECORDS * 2
        churn_cycles = 3
        update_rounds = 6
        mixed_size = MAX_OAUTH_RECORDS * 5
        stale_count = MAX_OAUTH_RECORDS

        with database.engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{maintained_schema}"'))
        self.addCleanup(self._drop_maintenance_schema, maintained_schema)
        maintained_engine = create_engine(
            database.engine.url,
            connect_args={"options": (
                f"-csearch_path={maintained_schema} -c statement_timeout=5000 "
                "-c lock_timeout=2000 -c idle_in_transaction_session_timeout=10000"
            )},
        )
        self.addCleanup(maintained_engine.dispose)
        maintained_session = sessionmaker(maintained_engine)
        schema_sql = (
            Path(__file__).resolve().parents[1] / "sandbox_schema.sql"
        ).read_text()
        with maintained_engine.begin() as db:
            for statement in schema_sql.split(";"):
                if statement.strip():
                    db.execute(text(statement))
            self.assertEqual(
                db.execute(text("SELECT current_schema()")).scalar_one(),
                maintained_schema,
            )

        scenarios = (
            ("before maintenance", baseline_schema, self.engine, self.Session, False),
            ("after ANALYZE/VACUUM", maintained_schema, maintained_engine, maintained_session, True),
        )
        measurements = {}
        active_summary = text("""
            SELECT count(*), sum(count), sum(bucket), min(bucket), max(bucket),
                   count(DISTINCT bucket)
            FROM sandbox_limits
            WHERE key LIKE :prefix AND bucket >= :cutoff
        """)

        for label, schema, engine, session_factory, run_maintenance in scenarios:
            churn_prefix = f"maintenance-churn-{token}-"
            mixed_prefix = f"maintenance-mixed-{token}-"
            with engine.begin() as db:
                db.execute(text(
                    "ALTER TABLE sandbox_limits SET (autovacuum_enabled = false)"
                ))

            deleted_churn_rows = 0
            for cycle in range(churn_cycles):
                cycle_prefix = f"{churn_prefix}{cycle}-"
                with engine.begin() as db:
                    db.execute(text("""
                        INSERT INTO sandbox_limits(key, bucket, count)
                        SELECT :prefix || n, :bucket, 1
                        FROM generate_series(1, :size) AS n
                    """), {
                        "prefix": cycle_prefix,
                        "bucket": cutoff - update_rounds - 1,
                        "size": churn_size,
                    })

                for generation in range(update_rounds):
                    with engine.begin() as db:
                        db.execute(text("""
                            INSERT INTO sandbox_limits(key, bucket, count)
                            SELECT :prefix || n, :bucket, 1
                            FROM generate_series(1, :size) AS n
                            ON CONFLICT (key) DO UPDATE SET
                                count = CASE
                                    WHEN sandbox_limits.bucket = EXCLUDED.bucket
                                        THEN sandbox_limits.count + 1
                                    ELSE 1
                                END,
                                bucket = EXCLUDED.bucket
                        """), {
                            "prefix": cycle_prefix,
                            "bucket": cutoff - update_rounds + generation,
                            "size": churn_size,
                        })

                with engine.begin() as db:
                    result = db.execute(text("""
                        DELETE FROM sandbox_limits
                        WHERE key LIKE :pattern
                          AND key NOT IN (
                              SELECT :prefix || n::text
                              FROM generate_series(1, :size) AS n
                              WHERE n % 5 = 0
                          )
                    """), {
                        "pattern": cycle_prefix + "%",
                        "prefix": cycle_prefix,
                        "size": churn_size,
                    })
                    deleted_churn_rows += result.rowcount

            self.assertEqual(
                deleted_churn_rows,
                churn_cycles * (churn_size - churn_size // 5),
            )
            with engine.begin() as db:
                db.execute(text("""
                    INSERT INTO sandbox_limits(key, bucket, count)
                    SELECT :prefix || n,
                           CASE WHEN n <= :stale_count
                                THEN :stale_bucket
                                ELSE :cutoff + 1 + (n % 1440)
                           END,
                           (n % 13) + 1
                    FROM generate_series(1, :size) AS n
                """), {
                    "prefix": mixed_prefix,
                    "stale_count": stale_count,
                    "stale_bucket": cutoff - 1,
                    "cutoff": cutoff,
                    "size": mixed_size,
                })
                active_before = db.execute(
                    active_summary,
                    {"prefix": mixed_prefix + "%", "cutoff": cutoff},
                ).one()
                self.assertEqual(active_before[0], mixed_size - stale_count)

            if run_maintenance:
                # VACUUM cannot run inside a transaction; use autocommit for
                # the maintenance command after ANALYZE refreshes table stats.
                with engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as db:
                    db.execute(text("ANALYZE sandbox_limits"))
                    db.execute(text("VACUUM sandbox_limits"))

            with engine.connect() as db:
                stats = db.execute(text("""
                    SELECT n_live_tup, n_dead_tup, last_analyze, last_vacuum
                    FROM pg_stat_user_tables
                    WHERE schemaname = :schema AND relname = 'sandbox_limits'
                """), {"schema": schema}).one()
                measurements[label] = {
                    "live_tuples": stats.n_live_tup,
                    "dead_tuples": stats.n_dead_tup,
                    "analyzed": stats.last_analyze is not None,
                    "vacuumed": stats.last_vacuum is not None,
                }

            started = time.monotonic()
            with patch("services.sandbox_store.Session", session_factory):
                purge()
            elapsed = time.monotonic() - started
            measurements[label]["cleanup_seconds"] = elapsed
            print(
                f"Purged {stale_count:,} stale buckets after "
                f"{churn_cycles * update_rounds * churn_size:,} upserts and "
                f"{deleted_churn_rows:,} churn deletes {label} in {elapsed:.3f}s "
                f"(estimated dead tuples before cleanup: "
                f"{measurements[label]['dead_tuples']:,})"
            )
            self.assertLess(
                elapsed, 5.0,
                f"purge exceeded statement_timeout {label}: {elapsed:.3f}s",
            )
            with engine.connect() as db:
                self.assertEqual(db.execute(text("""
                    SELECT count(*) FROM sandbox_limits
                    WHERE key LIKE :prefix AND bucket < :cutoff
                """), {
                    "prefix": mixed_prefix + "%",
                    "cutoff": cutoff,
                }).scalar_one(), 0)
                self.assertEqual(db.execute(text("""
                    SELECT count(*) FROM sandbox_limits WHERE key LIKE :prefix
                """), {"prefix": churn_prefix + "%"}).scalar_one(), 0)
                active_after = db.execute(
                    active_summary,
                    {"prefix": mixed_prefix + "%", "cutoff": cutoff},
                ).one()
                self.assertEqual(active_after, active_before)

            if run_maintenance:
                self.assertTrue(measurements[label]["analyzed"])
                self.assertTrue(measurements[label]["vacuumed"])

        print(
            "Cleanup timing comparison: "
            f"{measurements['before maintenance']['cleanup_seconds']:.3f}s before, "
            f"{measurements['after ANALYZE/VACUUM']['cleanup_seconds']:.3f}s after "
            "ANALYZE/VACUUM; timings are informational, not a speedup assertion."
        )

    def _drop_maintenance_schema(self, schema):
        with database.engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


class OAuthRetentionFlowTests(IsolatedSandboxTest):
    def test_purge_preserves_live_oauth_client_and_approved_access(self):
        root = "https://testserver"
        owner_secret = secrets.token_urlsafe(32)
        owner_id = digest(owner_secret)
        run_id = secrets.token_hex(16)
        bearer = secrets.token_urlsafe(32)
        client_id = secrets.token_urlsafe(32)
        redirect_uri = "http://127.0.0.1:34567/callback"
        access_token = "oauth_" + secrets.token_urlsafe(32)
        expired_client_id = secrets.token_urlsafe(32)
        expired_access_token = "oauth_" + secrets.token_urlsafe(32)
        now = time.time()
        resource = root + f"/api/sandbox/agent/{run_id}/mcp"

        with patch.dict("os.environ", {"SANDBOX_OAUTH_ORIGIN": root}), \
                patch.object(sandbox_oauth, "Session", self.Session):
            with self.Session.begin() as db:
                db.add(Owner(id=owner_id, expires=now + 3600))
                db.add(Run(
                    id=run_id, owner=owner_id, agent_hash=digest(bearer),
                    created=now, expires=now + 1800, deletes=now + 86400,
                    consent=False,
                    data={"state": initial(), "events": [], "idempotency": {},
                          "scenario": "non_delivery"},
                ))
                db.add_all([
                    OAuthRecord(
                        id="client:" + digest(client_id), expires=now + 3600,
                        data={"redirect_uris": [redirect_uri], "client_name": "Live client"},
                    ),
                    OAuthRecord(
                        id="token:" + digest(access_token), expires=now + 600,
                        data={"run": run_id, "generation": digest(bearer),
                              "scope": "sandbox:act", "client_id": client_id,
                              "resource": resource, "client_name": "Live client"},
                    ),
                    OAuthRecord(
                        id="client:" + digest(expired_client_id), expires=now - 60,
                        data={"redirect_uris": [redirect_uri], "client_name": "Expired client"},
                    ),
                    OAuthRecord(
                        id="token:" + digest(expired_access_token), expires=now - 60,
                        data={"run": run_id, "generation": digest(bearer),
                              "scope": "sandbox:act", "client_id": expired_client_id,
                              "resource": resource},
                    ),
                ])

            purge()

            browser = TestClient(app, base_url=root)
            self.addCleanup(browser.close)
            browser.cookies.set("sandbox_owner", owner_secret)
            headers = {"Origin": root, "X-Sandbox-Client": "browser"}

            def ping(token):
                return browser.post(resource, headers={
                    "Authorization": "Bearer " + token,
                    "Accept": "application/json, text/event-stream",
                }, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

            self.assertEqual(ping(access_token).status_code, 200)
            self.assertEqual(ping(expired_access_token).status_code, 401)

            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).decode().rstrip("=")
            params = {
                "client_id": client_id, "redirect_uri": redirect_uri,
                "response_type": "code", "state": "retention-test",
                "scope": "sandbox:act", "resource": resource,
                "code_challenge": challenge, "code_challenge_method": "S256",
            }
            authorize = browser.get("/api/sandbox/oauth/authorize", params=params)
            self.assertEqual(authorize.status_code, 200, authorize.text)
            pending_id = re.search(r"/requests/([A-Za-z0-9_-]+)", authorize.text)[1]
            approved = browser.post(
                "/api/sandbox/oauth/requests/" + pending_id,
                headers=headers, json={"approve": True},
            )
            self.assertEqual(approved.status_code, 200, approved.text)
            code = parse_qs(urlsplit(approved.json()["redirect"]).query)["code"][0]
            exchanged = browser.post("/api/sandbox/oauth/token", data={
                "grant_type": "authorization_code", "code": code,
                "code_verifier": verifier, "client_id": client_id,
                "redirect_uri": redirect_uri, "resource": resource,
            })
            self.assertEqual(exchanged.status_code, 200, exchanged.text)
            self.assertEqual(ping(exchanged.json()["access_token"]).status_code, 200)

            expired_authorize = browser.get(
                "/api/sandbox/oauth/authorize",
                params={**params, "client_id": expired_client_id},
            )
            self.assertEqual(expired_authorize.status_code, 400)
            self.assertEqual(expired_authorize.json()["error"], "invalid_client")
            with self.Session() as db:
                self.assertIsNotNone(db.get(OAuthRecord, "client:" + digest(client_id)))
                self.assertIsNotNone(db.get(OAuthRecord, "token:" + digest(access_token)))
                self.assertIsNone(db.get(OAuthRecord, "client:" + digest(expired_client_id)))
                self.assertIsNone(db.get(OAuthRecord, "token:" + digest(expired_access_token)))


if __name__ == "__main__":
    unittest.main()
