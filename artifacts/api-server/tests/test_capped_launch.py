import asyncio
import concurrent.futures
import threading
import time
from unittest.mock import patch
from sqlalchemy import text
from fastapi import HTTPException
from sandbox_test_support import IsolatedSandboxTest, request
from main import app
from services import sandbox_launch, sandbox_security
from services.sandbox_store import digest, purge
from services.sandbox_security import Ingress
import sandbox_oauth


class LaunchTests(IsolatedSandboxTest):
    def setUp(self):
        super().setUp()
        self.browser = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        response = request(app, "POST", "/api/sandbox/owner", headers=self.browser)
        self.browser["cookie"] = response.headers["set-cookie"].split(";")[0]

    def create(self, **extra):
        return request(app, "POST", "/api/sandbox/runs",
                       {"scenario": "non_delivery", **extra}, self.browser)

    def set_daily(self, count):
        self.execute("INSERT INTO sandbox_limits(key,bucket,count) VALUES (:key,:day,:n)",
                     key=sandbox_launch.DAILY_KEY, day=int(time.time())//86400, n=count)

    def test_capacity_last_slot_concurrent_transactions(self):
        self.set_daily(99)
        def attempt():
            try:
                with self.Session.begin() as db:
                    sandbox_launch.reserve(db, 1)
                return 200
            except HTTPException as error:
                return error.status_code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: attempt(), range(2))), [200, 429])
        with self.Session() as db:
            self.assertEqual(db.execute(text("SELECT count FROM sandbox_limits WHERE key=:k"),
                                        {"k": sandbox_launch.DAILY_KEY}).scalar_one(), 100)

    def test_pair_is_atomic_at_daily_limit(self):
        self.set_daily(99)
        result = self.create(agent_label="a", compare_with="b")
        self.assertEqual(result.status, 429)
        self.assertEqual(request(app, "GET", "/api/sandbox/runs", headers=self.browser).body["runs"], [])
        self.assertEqual(self.create().status, 200)
        self.assertEqual(self.create().status, 429)

    def test_repeated_trial_series_uses_the_existing_two_active_run_slots(self):
        first = self.create(agent_label="a", compare_with="b", comparison_trial=1,
                            comparison_trials=2, authorize_repeated_trials=True)
        self.assertEqual(first.status, 200)
        members = first.body["comparison_runs"]
        series_id = members[0]["run"]["comparison"]["series_id"]
        self.assertTrue(members[0]["run"]["comparison"]["repeated_trials_authorized"])
        self.assertEqual(members[1]["run"]["comparison"]["series_id"], series_id)
        second = self.create(agent_label="a", compare_with="b", comparison_series_id=series_id,
                             comparison_trial=2, comparison_trials=2,
                             authorize_repeated_trials=True)
        self.assertEqual(second.status, 409)
        self.assertEqual(len(request(app, "GET", "/api/sandbox/runs", headers=self.browser).body["runs"]), 2)

    def test_failed_creation_rolls_back_reserved_capacity(self):
        self.assertEqual(self.create(agent_label="a", compare_with="b").status, 200)
        self.assertEqual(self.create().status, 409)
        with self.Session() as db:
            self.assertEqual(db.execute(text("SELECT count FROM sandbox_limits WHERE key=:k"),
                                        {"k": sandbox_launch.DAILY_KEY}).scalar_one(), 2)

    def test_daily_quota_survives_cleanup_and_deletion(self):
        result = self.create()
        run = result.body["run"]["id"]
        request(app, "DELETE", f"/api/sandbox/runs/{run}", headers=self.browser)
        purge()
        with self.Session() as db:
            self.assertEqual(db.execute(text("SELECT count FROM sandbox_limits WHERE key=:k"),
                                        {"k": sandbox_launch.DAILY_KEY}).scalar_one(), 1)

    def test_daily_quota_resets_at_utc_midnight(self):
        today = 20_000
        midnight = today * 86400
        self.execute(
            "INSERT INTO sandbox_limits(key, bucket, count) VALUES (:key, :bucket, :count)",
            key=sandbox_launch.DAILY_KEY, bucket=today - 1,
            count=sandbox_launch.DAILY_RUN_LIMIT,
        )

        with patch.object(sandbox_launch, "time") as clock:
            clock.time.return_value = midnight
            with self.Session.begin() as db:
                status = sandbox_launch.capacity(db)
                self.assertTrue(status.available)
                self.assertEqual(status.resets_at, midnight + 86400)

                sandbox_launch.reserve(db, 1)
                row = db.execute(
                    text("SELECT bucket, count FROM sandbox_limits WHERE key=:key"),
                    {"key": sandbox_launch.DAILY_KEY},
                ).one()
                self.assertEqual((row.bucket, row.count), (today, 1))

                sandbox_launch.reserve(db, sandbox_launch.DAILY_RUN_LIMIT - 1)
                with self.assertRaises(HTTPException) as error:
                    sandbox_launch.reserve(db, 1)
                self.assertEqual(error.exception.status_code, 429)

        with self.Session() as db:
            row = db.execute(
                text("SELECT bucket, count FROM sandbox_limits WHERE key=:key"),
                {"key": sandbox_launch.DAILY_KEY},
            ).one()
            self.assertEqual((row.bucket, row.count), (today, sandbox_launch.DAILY_RUN_LIMIT))

    def test_daily_limit_retry_after_tracks_utc_reset_as_midnight_approaches(self):
        today = 20_000
        midnight = today * 86400
        self.execute(
            "INSERT INTO sandbox_limits(key, bucket, count) VALUES (:key, :bucket, :count)",
            key=sandbox_launch.DAILY_KEY, bucket=today - 1,
            count=sandbox_launch.DAILY_RUN_LIMIT,
        )

        with patch.object(sandbox_launch, "time") as clock:
            clock.time.return_value = midnight - 12
            status = sandbox_launch.status()
            self.assertFalse(status.available)
            self.assertEqual(status.reason, "daily_limit")
            self.assertEqual(status.resets_at, midnight)

            # The quota is still exhausted, but midnight is now only seconds away.
            clock.time.return_value = midnight - 3
            with self.Session.begin() as db:
                with self.assertRaises(HTTPException) as error:
                    sandbox_launch.reserve(db, 1)

            retry_after = int(error.exception.headers["Retry-After"])
            self.assertEqual(retry_after, 3)
            self.assertEqual(clock.time.return_value + retry_after, status.resets_at)

    def test_reservation_keeps_its_day_if_clock_crosses_midnight(self):
        today = 20_000
        midnight = today * 86400
        yesterday = today - 1
        self.execute(
            "INSERT INTO sandbox_limits(key, bucket, count) VALUES (:key, :bucket, :count)",
            key=sandbox_launch.DAILY_KEY, bucket=yesterday,
            count=sandbox_launch.DAILY_RUN_LIMIT - 1,
        )

        with patch.object(sandbox_launch, "time") as clock:
            clock.time.side_effect = [midnight - 1, midnight]
            with self.Session.begin() as db:
                sandbox_launch.reserve(db, 1)

            self.assertEqual(clock.time.call_count, 1)

        with self.Session() as db:
            rows = db.execute(
                text("SELECT bucket, count FROM sandbox_limits WHERE key=:key ORDER BY bucket"),
                {"key": sandbox_launch.DAILY_KEY},
            ).all()
        self.assertEqual([(row.bucket, row.count) for row in rows], [
            (yesterday, sandbox_launch.DAILY_RUN_LIMIT),
        ])

    def test_launch_status_reports_daily_limit_after_cleanup(self):
        self.set_daily(sandbox_launch.DAILY_RUN_LIMIT)
        stale_key = "expired-minute-bucket"
        with self.Session.begin() as db:
            db.execute(text("""
                INSERT INTO sandbox_limits(key, bucket, count)
                VALUES (:key, :bucket, 1)
            """), {"key": stale_key, "bucket": int(time.time()) // 60 - 1441})

        purge()

        status = request(app, "GET", "/api/sandbox/launch")
        self.assertFalse(status.body["available"])
        self.assertEqual(status.body["reason"], "daily_limit")
        with self.Session() as db:
            self.assertEqual(
                db.execute(text("SELECT count FROM sandbox_limits WHERE key=:key"),
                           {"key": sandbox_launch.DAILY_KEY}).scalar_one(),
                sandbox_launch.DAILY_RUN_LIMIT,
            )
            self.assertIsNone(db.execute(text("SELECT 1 FROM sandbox_limits WHERE key=:key"),
                                         {"key": stale_key}).scalar_one_or_none())

    def test_retained_limit_preserves_example_docs_and_controls(self):
        run = self.create().body["run"]["id"]
        with patch.object(sandbox_launch, "RETAINED_RUN_LIMIT", 1):
            self.assertEqual(self.create().status, 429)
            status = request(app, "GET", "/api/sandbox/launch")
            self.assertFalse(status.body["available"])
            self.assertEqual(status.body["reason"], "retained_limit")
            self.assertEqual(request(app, "GET", "/api/sandbox/example").status, 200)
            self.assertEqual(request(app, "POST", f"/api/sandbox/runs/{run}/revoke",
                                     headers=self.browser).status, 200)
            self.assertEqual(request(app, "DELETE", f"/api/sandbox/runs/{run}",
                                     headers=self.browser).status, 200)
            self.assertTrue(request(app, "GET", "/api/sandbox/launch").body["available"])

    def test_context_and_provenance_private_to_owner(self):
        created = self.create(context={"client": "claude_code", "coaching": "task_only"})
        self.assertEqual(created.status, 200)
        run = created.body["run"]
        self.assertFalse(run["consent"])
        self.assertEqual(run["context"]["client"], "claude_code")
        self.assertIn("task_version", run)
        headers = {"authorization": "Bearer " + created.body["agent_token"], "idempotency-key": "one"}
        response = request(app, "POST", f"/api/sandbox/agent/{run['id']}/actions",
                           {"tool": "inspect_offer"}, headers)
        self.assertNotIn("context", response.body)
        self.assertNotIn("claude_code", str(response.body))
        self.assertEqual(self.create(context={"client": "https://secret.example"}).status, 422)

    def test_record_size_limit_rolls_back_action(self):
        created = self.create().body
        run = created["run"]["id"]
        with patch("sandbox_api.MAX_RUN_BYTES", 1):
            response = request(app, "POST", f"/api/sandbox/agent/{run}/actions",
                               {"tool": "purchase"},
                               {"authorization": "Bearer "+created["agent_token"], "idempotency-key": "large"})
        self.assertEqual(response.status, 413)
        report = request(app, "GET", f"/api/sandbox/runs/{run}", headers=self.browser).body
        self.assertEqual(report["events"], [])
        self.assertEqual(report["state"]["balance"], 100)

    def test_legacy_routes_disabled_before_storage(self):
        with patch.object(sandbox_security, "limit", side_effect=AssertionError("no database")):
            for path in ("/api/office", "/api/office/actions", "/api/evidence/test"):
                self.assertEqual(request(app, "GET", path).status, 404)

    def test_auxiliary_capacity_fails_closed(self):
        with patch.object(sandbox_launch, "MAX_OWNER_RECORDS", 1):
            self.assertEqual(request(app, "POST", "/api/sandbox/owner",
                                     headers={"origin": "http://testserver", "x-sandbox-client": "browser"}).status, 503)
            # Already-owned sessions do not need to allocate another record.
            self.assertEqual(request(app, "POST", "/api/sandbox/owner", headers=self.browser).status, 200)
        with patch.object(sandbox_launch, "MAX_OAUTH_RECORDS", 0):
            with self.Session.begin() as db:
                with self.assertRaises(HTTPException):
                    sandbox_launch.reserve_auxiliary(db, "oauth")

    def test_concurrent_owner_creation_admits_only_last_available_slot(self):
        with self.Session() as db:
            existing_ids = set(db.execute(text("SELECT id FROM sandbox_owners")).scalars())
        now = time.time()
        seeded = [
            {"id": f"capacity-live-owner-{index}", "expires": now + 3600}
            for index in range(sandbox_launch.MAX_OWNER_RECORDS - len(existing_ids) - 1)
        ]
        if seeded:
            with self.Session.begin() as db:
                db.execute(text("""
                    INSERT INTO sandbox_owners(id, expires) VALUES (:id, :expires)
                """), seeded)
        with self.Session() as db:
            existing_ids.update(db.execute(text("SELECT id FROM sandbox_owners")).scalars())
            self.assertEqual(len(existing_ids), sandbox_launch.MAX_OWNER_RECORDS - 1)

        barrier = threading.Barrier(2)
        headers = {"origin": "http://testserver", "x-sandbox-client": "browser"}

        def attempt():
            barrier.wait(timeout=3)
            return request(app, "POST", "/api/sandbox/owner", headers=headers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: attempt(), range(2)))

        admitted = [response for response in responses if response.status == 200]
        rejected = [response for response in responses if response.status == 503]
        self.assertEqual((len(admitted), len(rejected)), (1, 1))
        owner_token = admitted[0].headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        admitted_id = digest(owner_token)
        with self.Session() as db:
            final_ids = set(db.execute(text("SELECT id FROM sandbox_owners")).scalars())
        self.assertEqual(len(final_ids), sandbox_launch.MAX_OWNER_RECORDS)
        self.assertTrue(existing_ids.issubset(final_ids))
        self.assertIn(admitted_id, final_ids)

    def test_concurrent_oauth_registration_admits_only_last_available_slot(self):
        now = time.time()
        seeded = [
            {"id": f"client:capacity-live-{index}", "expires": now + 3600}
            for index in range(sandbox_launch.MAX_OAUTH_RECORDS - 1)
        ]
        with self.Session.begin() as db:
            db.execute(text("""
                INSERT INTO sandbox_oauth(id, expires, data)
                VALUES (:id, :expires, '{}'::jsonb)
            """), seeded)
        existing_ids = {row["id"] for row in seeded}
        with self.Session() as db:
            self.assertEqual(
                db.execute(text("SELECT count(*) FROM sandbox_oauth")).scalar_one(),
                sandbox_launch.MAX_OAUTH_RECORDS - 1,
            )

        barrier = threading.Barrier(2)
        body = {
            "redirect_uris": ["https://client.example/callback"],
            "client_name": "Capacity race client",
        }

        def attempt():
            barrier.wait(timeout=3)
            return request(app, "POST", "/api/sandbox/oauth/register", body)

        with patch.object(sandbox_oauth, "Session", self.Session), \
                patch.object(sandbox_oauth, "origin", return_value="https://testserver"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: attempt(), range(2)))

        admitted = [response for response in responses if response.status == 201]
        rejected = [response for response in responses if response.status == 503]
        self.assertEqual((len(admitted), len(rejected)), (1, 1))
        admitted_id = "client:" + digest(admitted[0].body["client_id"])
        with self.Session() as db:
            final_ids = set(db.execute(text("SELECT id FROM sandbox_oauth")).scalars())
        self.assertEqual(len(final_ids), sandbox_launch.MAX_OAUTH_RECORDS)
        self.assertTrue(existing_ids.issubset(final_ids))
        self.assertIn(admitted_id, final_ids)


class RequestBoundaryTests(IsolatedSandboxTest):
    def test_total_deadline_not_reset_by_chunks(self):
        async def exercise():
            sent = []
            async def never_called(*args):
                raise AssertionError("No handler before full body")
            async def receive():
                await asyncio.sleep(.015)
                return {"type": "http.request", "body": b"x", "more_body": True}
            async def send(message):
                sent.append(message)
            with patch.object(sandbox_security, "BODY_DEADLINE_SECONDS", .04):
                await Ingress(never_called)(
                    {"type": "http", "method": "GET", "path": "/api/sandbox/scenarios", "headers": []},
                    receive, send)
            self.assertEqual(sent[0]["status"], 408)
        asyncio.run(exercise())

    def test_general_overload_has_separate_control_lane(self):
        async def exercise():
            calls, sent = [], []
            async def handler(scope, receive, send):
                calls.append(scope["method"])
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"{}"})
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                sent.append(message)
            gate = Ingress(handler)
            gate.inflight["general"] = sandbox_security.GENERAL_CONCURRENCY
            await gate({"type": "http", "method": "GET", "path": "/api/sandbox/scenarios"}, receive, send)
            self.assertEqual(sent[0]["status"], 503)
            await gate({"type": "http", "method": "DELETE", "path": "/api/sandbox/runs/test"}, receive, send)
            self.assertEqual(calls, ["DELETE"])
            self.assertEqual(gate.inflight["control"], 0)
        asyncio.run(exercise())
