"""API safety regressions using real controls in disposable PostgreSQL schemas.

Run: uv run --no-sync python -m unittest discover -s artifacts/api-server/tests -p test_sandbox_security.py
The running development server and its pause/counter state are never changed.
"""
import copy
import asyncio
import json
import os
import re
import secrets
import threading
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database
from sandbox_test_support import IsolatedSandboxTest, request
from main import app
from services import sandbox_store
from services import sandbox_security
from services import sandbox_usage_history
from services.sandbox_store import Owner, Run, UsageDaily, digest, purge


def usage_summary_totals(lines):
    totals = {"run_created": 0, "agent_action_accepted": 0, "run_completed": 0}
    pattern = re.compile(
        r"^Sandbox aggregate usage counts: run_created=(\d+) "
        r"agent_action_accepted=(\d+) run_completed=(\d+)$"
    )
    for line in lines:
        match = pattern.fullmatch(line)
        if match is None:
            raise AssertionError(f"Unexpected usage summary: {line}")
        for name, value in zip(totals, match.groups()):
            totals[name] += int(value)
    return totals


def outcome_summary_totals(summaries):
    totals = {}
    allowed = sandbox_security.OUTCOME_STATUSES
    for summary in summaries:
        if not set(summary).issubset(allowed):
            raise AssertionError(f"Unexpected outcome categories: {summary}")
        for category, count in summary.items():
            totals[category] = totals.get(category, 0) + count
    return totals


class SafetyAPITests(IsolatedSandboxTest):
    def setUp(self):
        super().setUp()
        with sandbox_security.USAGE_LOCK:
            sandbox_security.USAGE_COUNTS.clear()
        self.now = time.time()
        # Freeze bucket/retention time without changing the API's own clock.
        clock = patch.object(sandbox_store, "time", SimpleNamespace(time=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)
        self.browser = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        owner = self.call("POST", "/owner")
        self.assertEqual(owner.status, 200, owner.body)
        self.browser["cookie"] = owner.headers["set-cookie"].split(";")[0]
        created = self.call("POST", "/runs", {"scenario": "non_delivery"})
        self.assertEqual(created.status, 200, created.body)
        self.run = created.body["run"]["id"]
        self.token = created.body["agent_token"]
        self.agent = {"authorization": "Bearer " + self.token, "idempotency-key": "purchase-once"}
        self.mcp = {**self.agent, "accept": "application/json, text/event-stream"}
        self.owner = digest(self.browser["cookie"].split("=", 1)[1])

    def call(self, method, path, body=None, headers=None):
        return request(app, method, "/api/sandbox" + path, body,
                       self.browser if headers is None else headers)

    async def drive_asgi(self, method, path, receive, headers=None):
        messages = []

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
            "headers": [(key.lower().encode(), value.encode()) for key, value in {
                "host": "testserver", "content-type": "application/json",
                **(headers or {}),
            }.items()],
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=2)
        start = next((message for message in messages
                      if message["type"] == "http.response.start"), None)
        if start is None:
            return None, b""
        body = b"".join(
            message.get("body", b"") for message in messages
            if message["type"] == "http.response.body"
        )
        return start, body

    def run_asgi(self, method, path, messages, headers=None):
        async def receive():
            if messages:
                return messages.pop(0)
            await asyncio.Event().wait()

        start, body = asyncio.run(self.drive_asgi(method, path, receive, headers))
        parsed_body = json.loads(body) if body else None
        return start, body, parsed_body

    def test_ingress_accepts_exact_16_kib_request_body(self):
        payload = b'{"scenario":"non_delivery"}'
        payload += b" " * (16 * 1024 - len(payload))
        self.assertEqual(len(payload), 16 * 1024)
        before = self.snapshot()
        messages = [
            {"type": "http.request", "body": payload[:4096], "more_body": True},
            {"type": "http.request", "body": payload[4096:12000], "more_body": True},
            {"type": "http.request", "body": payload[12000:], "more_body": False},
        ]

        start, raw_body, body = self.run_asgi(
            "POST", "/api/sandbox/runs", messages, self.browser,
        )

        self.assertEqual(start["status"], 200, raw_body)
        after = self.snapshot()
        self.assertEqual(len(after["runs"]), len(before["runs"]) + 1)
        self.assertIn(body["run"]["id"], {run["id"] for run in after["runs"]})

    def test_ingress_rejects_multichunk_overflow_without_mutating_runs(self):
        marker = "private-overflow-request-marker"
        chunks = [
            b"x" * (8 * 1024),
            b"x" * (8 * 1024),
            marker.encode(),
        ]
        messages = [
            {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
            for index, chunk in enumerate(chunks)
        ]
        before = self.snapshot()

        start, raw_body, body = self.run_asgi(
            "POST", f"/api/sandbox/agent/{self.run}/actions", messages, self.agent,
        )

        self.assertEqual(start["status"], 413)
        self.assertEqual(body, {"detail": "Request body exceeds 16 KiB."})
        self.assertNotIn(marker.encode(), raw_body)
        self.assertNotIn(b"RuntimeError", raw_body)
        self.assertEqual(self.snapshot(), before)

    def test_ingress_receive_timeout_returns_sanitized_error_without_mutation(self):
        marker = b"private-timeout-request-marker"
        sent_first_chunk = False
        before = self.snapshot()

        async def receive():
            nonlocal sent_first_chunk
            if not sent_first_chunk:
                sent_first_chunk = True
                return {"type": "http.request", "body": marker, "more_body": True}
            await asyncio.Event().wait()

        with patch.object(sandbox_security, "BODY_DEADLINE_SECONDS", 0.01):
            start, raw_body = asyncio.run(self.drive_asgi(
                "POST", f"/api/sandbox/agent/{self.run}/actions", receive, self.agent,
            ))

        self.assertEqual(start["status"], 408)
        self.assertEqual(json.loads(raw_body), {"detail": "Request body timeout."})
        self.assertNotIn(marker, raw_body)
        self.assertNotIn(b"TimeoutError", raw_body)
        self.assertEqual(self.snapshot(), before)

    def test_ingress_client_disconnect_does_not_reach_sandbox_action(self):
        marker = b"private-disconnect-request-marker"
        messages = [
            {"type": "http.request", "body": marker, "more_body": True},
            {"type": "http.disconnect"},
        ]
        before = self.snapshot()

        start, raw_body, _ = self.run_asgi(
            "POST", f"/api/sandbox/agent/{self.run}/actions", messages, self.agent,
        )

        self.assertIsNone(start)
        self.assertEqual(raw_body, b"")
        self.assertEqual(self.snapshot(), before)

    def test_ingress_replays_complete_body_to_sandbox_action(self):
        payload = json.dumps({"tool": "purchase"}).encode()
        split = len(payload) // 2
        messages = [
            {"type": "http.request", "body": payload[:split], "more_body": True},
            {"type": "http.request", "body": payload[split:], "more_body": False},
        ]
        before = self.snapshot()

        start, raw_body, body = self.run_asgi(
            "POST", f"/api/sandbox/agent/{self.run}/actions", messages, self.agent,
        )

        self.assertEqual(start["status"], 200, raw_body)
        self.assertEqual(body["sequence"], 1)
        self.assertFalse(body["replayed"])
        after = self.snapshot()
        self.assertNotEqual(after, before)
        report = self.call("GET", f"/runs/{self.run}")
        self.assertEqual(report.status, 200, report.body)
        self.assertEqual(report.body["state"]["balance"], 80)

    def test_concurrent_api_errors_are_counted_once_by_bounded_status(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()

        count = 10
        barrier = threading.Barrier(count * 2)

        def make_error(index):
            barrier.wait()
            if index % 2:
                return request(app, "GET", "/api/sandbox/runs")
            return self.call("GET", f"/runs/missing-{index}")

        with ThreadPoolExecutor(max_workers=count * 2) as pool:
            responses = list(pool.map(make_error, range(count * 2)))

        self.assertEqual([response.status for response in responses].count(401), count)
        self.assertEqual([response.status for response in responses].count(404), count)
        summaries = []
        rendered_summaries = []

        def capture_summary(message, summary):
            summaries.append(dict(summary))
            rendered_summaries.append(message % summary)

        with patch.object(
            sandbox_security.log, "warning", side_effect=capture_summary,
        ):
            sandbox_security.flush_outcomes()

        self.assertEqual(outcome_summary_totals(summaries), {"401": count, "404": count})
        self.assertEqual(len(rendered_summaries), 1)
        for private_value in ("/api/sandbox/runs", *(f"/runs/missing-{i * 2}" for i in range(count))):
            self.assertNotIn(private_value, rendered_summaries[0])
        self.assertEqual(set(sandbox_security.OUTCOMES), set())

    def test_failed_outcome_warning_can_be_retried_without_private_details(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()
        sandbox_security.record_outcome(401)
        sandbox_security.record_outcome(404)
        sandbox_security.record_outcome(404)

        with patch.object(
            sandbox_security.log, "warning", side_effect=RuntimeError("private logger detail"),
        ) as warning:
            with self.assertRaisesRegex(RuntimeError, "private logger detail"):
                sandbox_security.flush_outcomes()
            with sandbox_security.OUTCOME_LOCK:
                self.assertEqual(dict(sandbox_security.OUTCOMES), {"401": 1, "404": 2})

            rendered_summaries = []

            def capture_summary(message, summary):
                rendered_summaries.append(message % summary)

            warning.side_effect = capture_summary
            sandbox_security.flush_outcomes()
            sandbox_security.flush_outcomes()

        self.assertEqual(
            rendered_summaries,
            ["Sandbox operational response counts: {'401': 1, '404': 2}"],
        )
        self.assertNotIn("private logger detail", rendered_summaries[0])
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_failed_outcome_warning_preserves_new_counts_recorded_during_logging(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()
        sandbox_security.record_outcome(401)
        sandbox_security.record_outcome(404)
        sandbox_security.record_outcome(404)

        private_path = "/api/private-run-identifier/private-client-identifier"
        private_detail = "private response body detail"

        async def response_with_private_details(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": ('{"detail":"' + private_detail + '"}').encode(),
                "more_body": False,
            })

        ingress = sandbox_security.Ingress(response_with_private_details)
        failed_summaries = []
        successful_summaries = []
        rendered_summaries = []

        def record_new_outcome_then_fail(message, summary):
            failed_summaries.append(dict(summary))
            response = request(ingress, "GET", private_path)
            if response.status != 503:
                raise AssertionError(f"Expected 503 response, got {response.status}")
            raise RuntimeError("private logger detail")

        with patch.object(
            sandbox_security.log, "warning", side_effect=record_new_outcome_then_fail,
        ) as warning:
            with self.assertRaisesRegex(RuntimeError, "private logger detail"):
                sandbox_security.flush_outcomes()

            with sandbox_security.OUTCOME_LOCK:
                self.assertEqual(
                    dict(sandbox_security.OUTCOMES),
                    {"401": 1, "404": 2, "503": 1},
                )

            def capture_summary(message, summary):
                successful_summaries.append(dict(summary))
                rendered_summaries.append(message % summary)

            warning.side_effect = capture_summary
            sandbox_security.flush_outcomes()
            sandbox_security.flush_outcomes()

        self.assertEqual(failed_summaries, [{"401": 1, "404": 2}])
        self.assertEqual(successful_summaries, [{"503": 1, "401": 1, "404": 2}])
        self.assertEqual(len(rendered_summaries), 1)
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(
            rendered_summaries[0],
            f"Sandbox operational response counts: {successful_summaries[0]}",
        )
        for private_value in (
            "private logger detail",
            private_path,
            private_detail,
            "private-run-identifier",
            "private-client-identifier",
        ):
            self.assertNotIn(private_value, rendered_summaries[0])
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_oauth_and_webhook_errors_are_counted_once_without_request_details(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()

        client_marker = "private-oauth-client-identifier"
        response_marker = "private OAuth origin response detail"

        with patch("sandbox_oauth.origin", side_effect=HTTPException(503, response_marker)):
            discovery = request(app, "GET", "/.well-known/oauth-authorization-server")
        self.assertEqual(discovery.status, 503)
        self.assertEqual(discovery.body, {"detail": response_marker})

        token = request(app, "POST", "/api/sandbox/oauth/token", {
            "client_id": client_marker,
        })
        self.assertEqual(token.status, 400)
        self.assertEqual(token.body, {"error": "invalid_request"})

        webhook_responses = [
            request(app, "POST", path)
            for path in ("/webhooks/stripe", "/api/webhooks/stripe")
        ]
        self.assertEqual([response.status for response in webhook_responses], [503, 503])
        self.assertTrue(all(
            response.body == {
                "detail": "Stripe is not connected. Live event ingestion is disabled."
            }
            for response in webhook_responses
        ))

        summaries = []
        rendered_summaries = []

        def capture_summary(message, summary):
            summaries.append(dict(summary))
            rendered_summaries.append(message % summary)

        with patch.object(sandbox_security.log, "warning", side_effect=capture_summary):
            sandbox_security.flush_outcomes()

        self.assertEqual(outcome_summary_totals(summaries), {"400": 1, "503": 3})
        self.assertEqual(len(rendered_summaries), 1)
        summary = rendered_summaries[0]
        for private_value in (
            "/.well-known/oauth-authorization-server",
            "/api/sandbox/oauth/token",
            "/webhooks/stripe",
            "/api/webhooks/stripe",
            client_marker,
            response_marker,
            "invalid_request",
            "Stripe is not connected",
        ):
            self.assertNotIn(private_value, summary)
        self.assertEqual(set(sandbox_security.OUTCOMES), set())

    def test_operator_usage_history_rejects_owner_and_agent_credentials(self):
        path = "/api/sandbox/operator/usage/daily"
        token = "operator-secret-for-tests-" + ("x" * 32)
        self.assertIsNone(sandbox_usage_history.operator_token())
        with self.Session() as db:
            self.assertEqual(
                db.execute(text("SELECT count(*) FROM sandbox_usage_daily")).scalar_one(),
                0,
            )
        with patch.dict("os.environ", {"SANDBOX_OPERATOR_TOKEN": token}):
            owner = request(app, "GET", path, headers=self.browser)
            agent = request(app, "GET", path, headers=self.agent)
            wrong = request(app, "GET", path, headers={"authorization": "Bearer " + "z" * 64})
            self.assertEqual(owner.status, 401)
            self.assertEqual(agent.status, 401)
            self.assertEqual(wrong.status, 401)

            authorized = request(app, "GET", path, headers={"authorization": "Bearer " + token})
            self.assertEqual(authorized.status, 200, authorized.body)
            self.assertEqual(len(authorized.body["days"]), 90)
            self.assertEqual(
                set(authorized.body["days"][0]),
                {"date", "run_created", "agent_action_accepted", "run_completed"},
            )
            self.assertEqual(
                len({day["date"] for day in authorized.body["days"]}), 90
            )
            self.assertEqual(authorized.headers["cache-control"], "no-store")

        with patch.dict("os.environ", {"SANDBOX_OPERATOR_TOKEN": ""}):
            unavailable = request(app, "GET", path, headers={})
        self.assertEqual(unavailable.status, 503)

    def test_operator_health_reports_autovacuum_warning_privately(self):
        path = "/api/sandbox/operator/health"
        token = "operator-health-token-" + ("x" * 40)
        with patch.dict("os.environ", {"SANDBOX_OPERATOR_TOKEN": token}):
            unauthorized = request(app, "GET", path, headers=self.browser)
            self.assertEqual(unauthorized.status, 401)

            for config in (
                {"global_enabled": False, "table_enabled": True},
                {"global_enabled": True, "table_enabled": False},
            ):
                with self.subTest(config=config):
                    with patch.object(
                        sandbox_security,
                        "autovacuum_config",
                        return_value=config,
                    ):
                        disabled = request(
                            app, "GET", path,
                            headers={"authorization": "Bearer " + token},
                        )
                    self.assertEqual(disabled.status, 200, disabled.body)
                    self.assertEqual(disabled.headers["cache-control"], "no-store")
                    self.assertEqual(
                        disabled.body["autovacuum"]["status"],
                        "disabled",
                    )
                    self.assertEqual(
                        disabled.body["autovacuum"]["global_enabled"],
                        config["global_enabled"],
                    )
                    self.assertEqual(
                        disabled.body["autovacuum"]["rate_limit_table_enabled"],
                        config["table_enabled"],
                    )
                    self.assertIn(
                        "Enable autovacuum globally and for sandbox_limits",
                        disabled.body["autovacuum"]["remediation"],
                    )
                    self.assertNotIn("records", str(disabled.body).lower())
                    self.assertNotIn("credentials", str(disabled.body).lower())

            with patch.object(
                sandbox_security,
                "autovacuum_config",
                return_value={"global_enabled": True, "table_enabled": True},
            ):
                enabled = request(
                    app, "GET", path,
                    headers={"authorization": "Bearer " + token},
                )
            self.assertEqual(
                enabled.body["autovacuum"],
                {
                    "status": "enabled",
                    "global_enabled": True,
                    "rate_limit_table_enabled": True,
                    "warning": None,
                    "remediation": None,
                },
            )

        with patch.dict("os.environ", {"SANDBOX_OPERATOR_TOKEN": ""}):
            unavailable = request(app, "GET", path, headers={})
        self.assertEqual(unavailable.status, 503)

    def test_operator_health_returns_private_unknown_when_autovacuum_cannot_be_checked(self):
        path = "/api/sandbox/operator/health"
        token = "operator-health-failure-token-" + ("x" * 40)
        headers = {"authorization": "Bearer " + token}
        with patch.dict("os.environ", {"SANDBOX_OPERATOR_TOKEN": token}):
            # The ingress limiter also uses sandbox_limits; bypass only that
            # unrelated check so this request reaches the health endpoint.
            with patch.object(sandbox_security, "limit", return_value=None):
                unauthorized = request(app, "GET", path, headers={})
                self.assertEqual(unauthorized.status, 401)

                self.execute("DROP TABLE sandbox_limits")
                missing_table = request(app, "GET", path, headers=headers)
                self.assertEqual(missing_table.status, 200, missing_table.body)
                self.assertEqual(missing_table.body.keys(), {"autovacuum"})
                self.assertEqual(
                    missing_table.body["autovacuum"]["status"], "unknown"
                )
                self.assertIs(
                    missing_table.body["autovacuum"]["global_enabled"], True
                )
                self.assertIsNone(
                    missing_table.body["autovacuum"]["rate_limit_table_enabled"]
                )
                self.assertNotIn("relation", str(missing_table.body).lower())

                exception_details = (
                    'relation "sandbox_limits" does not exist; '
                    "host=private-db-host.example password=do-not-return"
                )
                with patch.object(
                    sandbox_security,
                    "autovacuum_config",
                    side_effect=RuntimeError(exception_details),
                ):
                    failed_query = request(app, "GET", path, headers=headers)

        self.assertEqual(failed_query.status, 200, failed_query.body)
        response_text = str(failed_query.body)
        self.assertEqual(failed_query.body.keys(), {"autovacuum"})
        self.assertEqual(failed_query.body["autovacuum"]["status"], "unknown")
        self.assertIsNone(failed_query.body["autovacuum"]["global_enabled"])
        self.assertIsNone(
            failed_query.body["autovacuum"]["rate_limit_table_enabled"]
        )
        self.assertNotIn("private-db-host.example", response_text)
        self.assertNotIn("do-not-return", response_text)
        self.assertNotIn('relation "sandbox_limits" does not exist', response_text)

    def test_success_and_redirect_responses_are_excluded_from_outcome_summaries(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()

        statuses = (200, 201, 204, 301, 302, 304)

        async def response_app(scope, receive, send):
            status = int(scope["path"].rsplit("/", 1)[-1])
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [],
            })
            await send({
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            })

        ingress = sandbox_security.Ingress(response_app)
        with patch.object(sandbox_security, "limit"):
            responses = [
                request(ingress, "GET", f"/api/outcome-test/{status}")
                for status in statuses
            ]

        self.assertEqual([response.status for response in responses], list(statuses))
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

        with patch.object(sandbox_security.log, "warning") as warning:
            sandbox_security.flush_outcomes()

        warning.assert_not_called()
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_unlisted_failure_statuses_share_one_private_summary_category(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()

        private_path = "/api/private-request-marker"
        private_detail = "private response detail marker"
        statuses = (418, 599, 700)

        async def unusual_response(scope, receive, send):
            status = int(scope["path"].rsplit("/", 1)[-1])
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": ('{"detail":"' + private_detail + '"}').encode(),
                "more_body": False,
            })

        ingress = sandbox_security.Ingress(unusual_response)
        with patch.object(sandbox_security, "limit"):
            responses = [
                request(ingress, "GET", f"{private_path}/{status}")
                for status in statuses
            ]

        self.assertEqual([response.status for response in responses], list(statuses))
        summaries = []
        rendered_summaries = []

        def capture_summary(message, summary):
            summaries.append(dict(summary))
            rendered_summaries.append(message % summary)

        with patch.object(sandbox_security.log, "warning", side_effect=capture_summary):
            sandbox_security.flush_outcomes()

        self.assertEqual(outcome_summary_totals(summaries), {"other_error": len(statuses)})
        self.assertEqual(summaries, [{"other_error": len(statuses)}])
        for private_value in (private_path, private_detail, *(str(status) for status in statuses)):
            self.assertNotIn(private_value, rendered_summaries[0])
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_approved_failure_statuses_keep_individual_summary_categories(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()

        private_path = "/api/private-approved-status-marker"
        private_detail = "private approved response detail"
        statuses = (400, 401, 403, 404, 408, 409, 410, 413, 422, 429, 503)
        self.assertEqual(set(statuses), sandbox_security.OUTCOME_STATUS_CODES)

        async def approved_response(scope, receive, send):
            status = int(scope["path"].rsplit("/", 1)[-1])
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": ('{"detail":"' + private_detail + '"}').encode(),
                "more_body": False,
            })

        ingress = sandbox_security.Ingress(approved_response)
        with patch.object(sandbox_security, "limit"):
            responses = [
                request(ingress, "GET", f"{private_path}/{status}")
                for status in statuses
            ]

        self.assertEqual(tuple(response.status for response in responses), statuses)
        summaries = []
        rendered_summaries = []

        def capture_summary(message, summary):
            summaries.append(dict(summary))
            rendered_summaries.append(message % summary)

        with patch.object(sandbox_security.log, "warning", side_effect=capture_summary):
            sandbox_security.flush_outcomes()

        expected = {str(status): 1 for status in statuses}
        self.assertEqual(summaries, [expected])
        self.assertEqual(outcome_summary_totals(summaries), expected)
        self.assertEqual(set(summaries[0]), set(map(str, statuses)))
        for private_value in (private_path, private_detail):
            self.assertNotIn(private_value, rendered_summaries[0])
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_errors_arriving_during_summary_flush_are_emitted_once(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()
        self.assertEqual(self.call("GET", "/runs/missing-before-flush").status, 404)

        snapshot_taken = threading.Event()
        resume_flush = threading.Event()
        summaries = []
        rendered_summaries = []

        def capture_and_pause(message, summary):
            summaries.append(dict(summary))
            rendered_summaries.append(message % summary)
            snapshot_taken.set()
            self.assertTrue(resume_flush.wait(timeout=5), "summary flush was not released")

        def capture_summary(message, summary):
            summaries.append(dict(summary))
            rendered_summaries.append(message % summary)

        with patch.object(sandbox_security.log, "warning", side_effect=capture_and_pause):
            flusher = threading.Thread(target=sandbox_security.flush_outcomes)
            flusher.start()
            try:
                self.assertTrue(snapshot_taken.wait(timeout=5), "summary was not captured")
                count = 8
                barrier = threading.Barrier(count * 2)

                def make_error(index):
                    barrier.wait()
                    if index % 2:
                        return request(app, "GET", "/api/sandbox/runs")
                    return self.call("GET", f"/runs/missing-during-flush-{index}")

                with ThreadPoolExecutor(max_workers=count * 2) as pool:
                    responses = list(pool.map(make_error, range(count * 2)))
                self.assertEqual([response.status for response in responses].count(401), count)
                self.assertEqual([response.status for response in responses].count(404), count)
            finally:
                resume_flush.set()
                flusher.join(timeout=5)
                self.assertFalse(flusher.is_alive(), "outcome flusher did not stop")

            sandbox_security.log.warning.side_effect = capture_summary
            sandbox_security.flush_outcomes()

        self.assertEqual(
            outcome_summary_totals(summaries),
            {"401": count, "404": 1 + count},
        )
        combined_summary = "\n".join(rendered_summaries)
        for private_value in (
            "/runs/missing-before-flush",
            "/api/sandbox/runs",
            *(f"/runs/missing-during-flush-{i * 2}" for i in range(count)),
        ):
            self.assertNotIn(private_value, combined_summary)
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_overlapping_summary_flushes_do_not_duplicate_error_totals(self):
        with sandbox_security.OUTCOME_LOCK:
            sandbox_security.OUTCOMES.clear()
        sandbox_security.record_outcome(404)
        sandbox_security.record_outcome(404)

        first_warning_started = threading.Event()
        release_first_warning = threading.Event()
        second_warning_finished = threading.Event()
        summaries = []
        first_flush_errors = []

        def pause_then_fail_first_warning(message, summary):
            first_warning_started.set()
            self.assertTrue(
                release_first_warning.wait(timeout=5),
                "first summary warning was not released",
            )
            raise RuntimeError("warning logger unavailable")

        def capture_second_warning(message, summary):
            summaries.append(dict(summary))
            second_warning_finished.set()

        def run_first_flush():
            try:
                sandbox_security.flush_outcomes()
            except Exception as exc:
                first_flush_errors.append(exc)

        with patch.object(
            sandbox_security.log,
            "warning",
            side_effect=pause_then_fail_first_warning,
        ) as warning:
            first_flusher = threading.Thread(target=run_first_flush)
            first_flusher.start()
            try:
                self.assertTrue(
                    first_warning_started.wait(timeout=5),
                    "first summary warning did not start",
                )

                sandbox_security.record_outcome(401)
                sandbox_security.record_outcome(404)
                warning.side_effect = capture_second_warning
                second_flusher = threading.Thread(
                    target=sandbox_security.flush_outcomes,
                )
                second_flusher.start()
                self.assertTrue(
                    second_warning_finished.wait(timeout=5),
                    "second summary warning did not finish",
                )
                second_flusher.join(timeout=5)
                self.assertFalse(second_flusher.is_alive(), "second flusher did not stop")
            finally:
                release_first_warning.set()
                first_flusher.join(timeout=5)
                self.assertFalse(first_flusher.is_alive(), "first flusher did not stop")

        self.assertEqual(len(first_flush_errors), 1)
        self.assertIsInstance(first_flush_errors[0], RuntimeError)
        self.assertEqual(summaries, [{"401": 1, "404": 1}])
        self.assertEqual(outcome_summary_totals(summaries), {"401": 1, "404": 1})
        with sandbox_security.OUTCOME_LOCK:
            retained = dict(sandbox_security.OUTCOMES)
        self.assertTrue(set(retained).issubset(sandbox_security.OUTCOME_STATUSES))
        self.assertEqual(
            {
                category: outcome_summary_totals(summaries).get(category, 0)
                + retained.get(category, 0)
                for category in set(summaries[0]) | set(retained)
            },
            {"401": 1, "404": 3},
        )

        def capture_retry(message, summary):
            summaries.append(dict(summary))

        with patch.object(sandbox_security.log, "warning", side_effect=capture_retry):
            sandbox_security.flush_outcomes()

        self.assertEqual(outcome_summary_totals(summaries), {"401": 1, "404": 3})
        self.assertTrue(all(
            set(summary).issubset(sandbox_security.OUTCOME_STATUSES)
            for summary in summaries
        ))
        with sandbox_security.OUTCOME_LOCK:
            self.assertEqual(dict(sandbox_security.OUTCOMES), {})

    def test_usage_cleanup_retains_90_utc_dates_and_expires_older_buckets(self):
        today = datetime.now(timezone.utc).date()
        expired = today - timedelta(days=90)
        oldest_live = today - timedelta(days=89)
        self.execute("""
            INSERT INTO sandbox_usage_daily
                (activity_date, run_created, agent_action_accepted, run_completed)
            VALUES (:expired, 1, 0, 0), (:live, 2, 0, 0)
        """, expired=expired, live=oldest_live)

        purge()

        with self.Session() as db:
            days = set(db.execute(text("""
                SELECT activity_date FROM sandbox_usage_daily
                WHERE activity_date IN (:expired, :live)
            """), {"expired": expired, "live": oldest_live}).scalars())
        self.assertEqual(days, {oldest_live})

    def test_health_reports_retention_pause_and_recovery_without_sensitive_details(self):
        health = sandbox_security.RetentionHealth()
        with patch.object(sandbox_security, "retention_health", health):
            result = request(app, "GET", "/api/healthz")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.body, {
                "status": "ok",
                "retention": {"status": "healthy", "intake_paused": False},
            })

            unknown = request(app, "GET", "/api/unknown-launch-check")
            self.assertEqual(unknown.status, 404)
            self.assertEqual(unknown.body, {"detail": "Not Found"})

            for _ in range(sandbox_security.RETENTION_FAILURE_THRESHOLD):
                health.record_failure()
            result = request(app, "GET", "/api/healthz")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.body, {
                "status": "ok",
                "retention": {"status": "failing", "intake_paused": True},
            })
            self.assertNotIn("consecutive_failures", result.body["retention"])
            self.assertNotIn("exception", result.body)
            self.assertNotIn("sql", result.body)
            self.assertNotIn("run_id", result.body)

            health.record_success()
            result = request(app, "GET", "/api/healthz")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.body, {
                "status": "ok",
                "retention": {"status": "healthy", "intake_paused": False},
            })

    def routes(self):
        r = self.run
        return [
            ("POST", "/owner", None, self.browser),
            ("POST", "/runs", {"scenario": "non_delivery"}, self.browser),
            ("POST", f"/agent/{r}/actions", {"tool": "purchase"}, self.agent),
            ("GET", f"/agent/{r}/resume", None, self.agent),
            ("GET", "/runs", None, self.browser),
            ("GET", f"/runs/{r}", None, self.browser),
            ("GET", f"/runs/{r}/export", None, self.browser),
            ("GET", "/scenarios", None, {}),
            ("GET", "/example", None, {}),
            ("PATCH", f"/runs/{r}/consent", {"consent": True}, self.browser),
            ("POST", f"/runs/{r}/token", None, self.browser),
            ("POST", f"/runs/{r}/revoke", None, self.browser),
            ("DELETE", f"/runs/{r}", None, self.browser),
            # Global protection must precede authentication/validation too.
            ("POST", f"/agent/{r}/actions", {"unknown": "field"}, {}),
            ("GET", "/runs", None, {}),
        ] + self.mcp_routes()

    def mcp_routes(self):
        path = f"/agent/{self.run}/mcp"
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "isolated-safety-test", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "purchase", "arguments": {"idempotency_key": "mcp-purchase"}}},
        ]
        return [("POST", path, body, self.mcp) for body in messages] + [
            ("GET", path, None, self.mcp),
            ("DELETE", path, None, self.mcp),
            ("POST", path, messages[2], {}),
        ]

    def admission_routes(self):
        return self.routes()[:3] + [route for route in self.mcp_routes() if route[0] == "POST"]

    def snapshot(self):
        with self.Session() as db:
            return {
                "owners": [(o.id, o.expires) for o in db.query(Owner).order_by(Owner.id)],
                "runs": [copy.deepcopy({c.name: getattr(r, c.name) for c in Run.__table__.columns})
                         for r in db.query(Run).order_by(Run.id)],
            }

    def assert_blocked(self, status, detail):
        before = self.snapshot()
        for method, path, body, headers in self.routes():
            with self.subTest(method=method, path=path, authenticated=bool(headers)):
                result = self.call(method, path, body, headers)
                self.assertEqual(result.status, status, result.body)
                self.assertEqual(result.body, {"detail": detail})
                self.assertNotIn("set-cookie", result.headers)
                if status == 429:
                    self.assertEqual(result.headers["retry-after"], "60")
        self.assertEqual(self.snapshot(), before)

    def test_global_saturation_blocks_every_route_and_recovers_next_bucket(self):
        self.execute("UPDATE sandbox_limits SET count = 1199 WHERE key = 'all-api-global'")
        self.assertEqual(self.call("GET", "/scenarios").status, 200)  # 1200 accepted
        self.assert_blocked(429, "Service request limit reached. Retry shortly.")
        count = self.execute("SELECT count FROM sandbox_limits WHERE key = 'all-api-global'").scalar_one()
        self.assertEqual(count, 1200 + len(self.routes()))
        self.now += 60
        self.assertEqual(self.call("GET", "/scenarios").status, 200)
        self.assertEqual(self.execute(
            "SELECT count FROM sandbox_limits WHERE key = 'all-api-global'").scalar_one(), 1)

    def test_missing_counter_storage_fails_closed_for_every_route(self):
        self.execute("DROP TABLE sandbox_limits")
        self.assert_blocked(503, "Safety controls unavailable.")

    def test_unavailable_session_fails_closed_for_every_route(self):
        with patch.object(sandbox_store, "Session", side_effect=RuntimeError("private connection detail")):
            self.assert_blocked(503, "Safety controls unavailable.")

    def test_admission_storage_missing_or_empty_blocks_writes_but_not_reads(self):
        for sql in ("DELETE FROM sandbox_control", "DROP TABLE sandbox_control"):
            with self.subTest(failure=sql):
                self.execute(sql)
                before = self.snapshot()
                for method, path, body, headers in self.admission_routes():
                    result = self.call(method, path, body, headers)
                    self.assertEqual(result.status, 503)
                    self.assertEqual(result.body, {"detail": "Safety controls unavailable."})
                for method, path, body, headers in self.routes()[3:9]:
                    self.assertEqual(self.call(method, path, body, headers).status, 200)
                self.assertEqual(self.snapshot(), before)

    def test_pause_blocks_admission_but_preserves_reports_and_owner_controls(self):
        self.execute("UPDATE sandbox_control SET paused = true WHERE id = 1")
        before = self.snapshot()
        for method, path, body, headers in self.admission_routes():
            result = self.call(method, path, body, headers)
            self.assertEqual(result.status, 503)
            self.assertEqual(result.body, {"detail": "New runs and agent actions are temporarily paused. Existing reports and deletion remain available."})
        for method, path, body, headers in self.routes()[3:9]:
            self.assertEqual(self.call(method, path, body, headers).status, 200)
        # Stateless MCP does not support GET/DELETE, even during a pause.
        for method, path, body, headers in self.mcp_routes()[3:5]:
            self.assertEqual(self.call(method, path, body, headers).status, 405)
        self.assertEqual(self.snapshot(), before)

        # Intake pauses do not block owners from changing research sharing.
        opt_in = self.call(
            "PATCH",
            f"/runs/{self.run}/consent",
            {"consent": True, "preview_acknowledged": True},
            self.browser,
        )
        self.assertEqual(opt_in.status, 200)
        self.assertTrue(self.call("GET", f"/runs/{self.run}").body["consent"])

        # Owners can still keep or withdraw consent and use other controls.
        withdrawn = self.call("PATCH", f"/runs/{self.run}/consent", {"consent": False}, self.browser)
        self.assertEqual(withdrawn.status, 200)
        for method, path, body, headers in self.routes()[10:13]:
            self.assertEqual(self.call(method, path, body, headers).status, 200)
        self.assertEqual(self.call("GET", f"/runs/{self.run}").status, 404)
        self.execute("UPDATE sandbox_control SET paused = false WHERE id = 1")
        created = self.call("POST", "/runs", {"scenario": "non_delivery"})
        self.assertEqual(created.status, 200)
        result = self.call("POST", f"/agent/{created.body['run']['id']}/actions",
                           {"tool": "purchase"}, {"authorization": "Bearer " + created.body["agent_token"],
                                                "idempotency-key": "after-pause"})
        self.assertEqual(result.status, 200)

    def test_repeated_retention_failures_pause_intake_without_blocking_existing_controls(self):
        health = sandbox_security.RetentionHealth()
        health.record_success()
        with patch.object(sandbox_security, "retention_health", health):
            health.record_failure()
            health.record_failure()
            self.assertEqual(health.consecutive_failures, 2)
            self.assertFalse(health.intake_paused())
            health.record_success()
            self.assertEqual(health.consecutive_failures, 0)

            for _ in range(sandbox_security.RETENTION_FAILURE_THRESHOLD):
                health.record_failure()
            self.assertTrue(health.intake_paused())
            launch = self.call("GET", "/launch")
            self.assertEqual(launch.status, 200)
            self.assertEqual(launch.body["reason"], "paused")

            for method, path, body, headers in self.admission_routes():
                result = self.call(method, path, body, headers)
                self.assertEqual(result.status, 503, result.body)

            self.assertEqual(self.call("GET", f"/runs/{self.run}").status, 200)
            self.assertEqual(self.call("GET", f"/runs/{self.run}/export").status, 200)
            self.assertEqual(self.call("POST", f"/runs/{self.run}/revoke").status, 200)
            self.assertEqual(self.call("DELETE", f"/runs/{self.run}").status, 200)

            health.record_success()
            self.assertFalse(health.intake_paused())
            self.assertEqual(self.call("GET", "/launch").body["reason"], "open")
            self.assertEqual(self.call("POST", "/runs", {"scenario": "non_delivery"}).status, 200)

    def test_independent_api_instances_share_retention_pause_and_recovery(self):
        threshold = sandbox_security.RETENTION_FAILURE_THRESHOLD
        worker_count = threshold
        health_instances = [
            sandbox_security.RetentionHealth() for _ in range(worker_count)
        ]
        workers_ready = threading.Barrier(worker_count)

        def fail_cleanup(health):
            workers_ready.wait(timeout=5)
            health.record_failure()

        # Each independent worker updates the same database row at once. The
        # first wave must reach the threshold without lost increments.
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            list(pool.map(fail_cleanup, health_instances))

        self.assertEqual(health_instances[0].consecutive_failures, threshold)
        for health in health_instances:
            self.assertEqual(health.consecutive_failures, threshold)
            self.assertTrue(health.intake_paused())

        # Further simultaneous failures remain capped rather than overflowing.
        overflow_count = worker_count * 4
        overflow_health = [
            sandbox_security.RetentionHealth() for _ in range(overflow_count)
        ]
        overflow_barrier = threading.Barrier(overflow_count)

        def fail_again(health):
            overflow_barrier.wait(timeout=5)
            health.record_failure()

        with ThreadPoolExecutor(max_workers=overflow_count) as pool:
            list(pool.map(fail_again, overflow_health))
        self.assertEqual(health_instances[0].consecutive_failures, threshold)

        from services.sandbox_launch import capacity, reserve
        with self.Session() as db:
            self.assertEqual(capacity(db).reason, "paused")
        with self.assertRaises(HTTPException) as rejected:
            with self.Session.begin() as db:
                reserve(db, 1)
        self.assertEqual(rejected.exception.status_code, 503)

        instances = [
            (health_instances[index], sandbox_security.Ingress(app))
            for index in range(worker_count)
        ]
        for health, instance_app in instances:
            with patch.object(sandbox_security, "retention_health", health):
                result = request(
                    instance_app,
                    "POST",
                    "/api/sandbox/runs",
                    {"scenario": "non_delivery"},
                    self.browser,
                )
            self.assertEqual(result.status, 503)

        # A successful cleanup on any worker resets the shared row observed by
        # every instance, allowing each request-serving instance to admit work.
        health_instances[-1].record_success()
        for health, _ in instances:
            self.assertFalse(health.intake_paused())
        for health, instance_app in instances:
            with patch.object(sandbox_security, "retention_health", health):
                result = request(
                    instance_app,
                    "POST",
                    "/api/sandbox/owner",
                    headers=self.browser,
                )
            self.assertEqual(result.status, 200, result.body)

    def test_retention_health_state_contains_no_cleanup_or_record_details(self):
        health = sandbox_security.RetentionHealth()
        health.record_success()
        health.record_failure()
        row = self.execute(
            "SELECT consecutive_failures, startup_unverified "
            "FROM sandbox_retention_health WHERE id = 1"
        ).one()
        self.assertEqual(row.consecutive_failures, 1)
        self.assertFalse(row.startup_unverified)
        columns = set(self.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'sandbox_retention_health'
        """).scalars())
        self.assertEqual(columns, {"id", "consecutive_failures", "startup_unverified"})

    def test_retention_expiry_cleanup_and_cascades_are_visible_through_api(self):
        # Prove MCP reaches the same private run before expiry is introduced.
        for method, path, body, headers in self.mcp_routes()[:3]:
            result = self.call(method, path, body, headers)
            self.assertEqual(result.status, 200, result.body)
            self.assertIn("result", result.body)
        # An expired action window is not yet the report deletion deadline.
        self.execute("UPDATE sandbox_runs SET expires = :past WHERE id = :id",
                     past=self.now - 1, id=self.run)
        purge()
        self.assertEqual(self.call("GET", f"/runs/{self.run}").status, 200)
        self.assertEqual(self.call("GET", f"/runs/{self.run}/export").status, 200)
        self.assertEqual(self.call("GET", f"/agent/{self.run}/resume", headers=self.agent).status, 410)
        self.assertEqual(self.call("POST", f"/agent/{self.run}/actions",
                                  {"tool": "purchase"}, self.agent).status, 410)
        for method, path, body, headers in self.mcp_routes()[:5]:
            self.assertEqual(self.call(method, path, body, headers).status, 410)
        retained = self.call("POST", "/runs", {"scenario": "non_delivery"}).body["run"]["id"]
        self.execute("UPDATE sandbox_runs SET deletes = :past WHERE id = :id",
                     past=self.now - 1, id=self.run)
        orphan_owner, orphan_run = secrets.token_hex(32), secrets.token_hex(16)
        with self.Session.begin() as db:
            db.add(Owner(id=orphan_owner, expires=self.now - 1))
            db.flush()
            original = db.get(Run, retained)
            db.add(Run(id=orphan_run, owner=orphan_owner, agent_hash=digest("synthetic"),
                       created=self.now, expires=self.now + 60, deletes=self.now + 600,
                       consent=True, data=copy.deepcopy(original.data)))
        cutoff = int(self.now) // 60 - 1440
        for key, bucket in (("stale-test", cutoff - 1), ("boundary-test", cutoff), ("fresh-test", cutoff + 1)):
            self.execute("INSERT INTO sandbox_limits VALUES (:key, :bucket, 1)", key=key, bucket=bucket)
        # Logical access is denied even before the periodic deletion runs.
        self.assertEqual(self.call("GET", f"/runs/{self.run}").status, 404)
        purge()
        purge()  # Cleanup is idempotent.
        with self.Session() as db:
            self.assertIsNone(db.get(Run, self.run))
            self.assertIsNone(db.get(Owner, orphan_owner))
            self.assertIsNone(db.get(Run, orphan_run))
            self.assertIsNotNone(db.get(Run, retained))
            self.assertIsNotNone(db.get(Owner, self.owner))
        keys = set(self.execute("SELECT key FROM sandbox_limits").scalars())
        self.assertNotIn("stale-test", keys)
        self.assertTrue({"boundary-test", "fresh-test"} <= keys)
        self.assertEqual([r["id"] for r in self.call("GET", "/runs").body["runs"]], [retained])
        for path in (f"/runs/{self.run}", f"/runs/{self.run}/export"):
            self.assertEqual(self.call("GET", path).status, 404)
        self.assertEqual(self.call("GET", f"/agent/{self.run}/resume", headers=self.agent).status, 401)
        self.assertEqual(self.call("POST", f"/agent/{self.run}/actions",
                                  {"tool": "purchase"}, self.agent).status, 401)
        for method, path, body, headers in self.mcp_routes()[:5]:
            self.assertEqual(self.call(method, path, body, headers).status, 401)

    def test_cleanup_failure_rolls_back_partial_deletion(self):
        self.execute("UPDATE sandbox_runs SET deletes = :past WHERE id = :id",
                     past=self.now - 1, id=self.run)
        self.execute("UPDATE sandbox_owners SET expires = :past WHERE id = :id",
                     past=self.now - 1, id=self.owner)
        self.execute("""
            INSERT INTO sandbox_oauth(id, expires, data)
            VALUES ('purge-rollback-oauth', :past, '{}'::jsonb)
        """, past=self.now - 1)
        cutoff = int(self.now) // 60 - 1440
        self.execute("""
            INSERT INTO sandbox_limits(key, bucket, count)
            VALUES ('purge-rollback-limit', :bucket, 1)
        """, bucket=cutoff - 1)
        expired_day = datetime.now(timezone.utc).date() - timedelta(days=90)
        self.execute("""
            INSERT INTO sandbox_usage_daily
                (activity_date, run_created, agent_action_accepted, run_completed)
            VALUES (:day, 1, 0, 0)
        """, day=expired_day)
        self.execute("""
            CREATE FUNCTION retention_test_failure() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'injected retention failure'
                    USING ERRCODE = 'P0001';
            END;
            $$
        """)

        phases = (
            ("sandbox_oauth", "oauth_expiry"),
            ("sandbox_runs", "run_deletion"),
            ("sandbox_owners", "owner_expiry"),
            ("sandbox_limits", "limit_bucket"),
            ("sandbox_usage_daily", "usage_expiry"),
        )
        from sqlalchemy.exc import DBAPIError
        for table, expected_step in phases:
            with self.subTest(step=expected_step):
                self.execute(f"""
                    CREATE TRIGGER retention_test_failure
                    BEFORE DELETE ON {table}
                    FOR EACH STATEMENT EXECUTE FUNCTION retention_test_failure()
                """)
                with self.assertRaises(DBAPIError) as caught:
                    purge()
                self.assertEqual(caught.exception._retention_cleanup_step, expected_step)
                self.assertEqual(
                    sandbox_security.retention_failure_fields(caught.exception)[2], "P0001"
                )
                with self.Session() as db:
                    self.assertIsNotNone(db.get(Run, self.run))
                    self.assertIsNotNone(db.get(Owner, self.owner))
                    self.assertEqual(db.execute(text("""
                        SELECT count(*) FROM sandbox_oauth
                        WHERE id = 'purge-rollback-oauth'
                    """)).scalar_one(), 1)
                    self.assertEqual(db.execute(text("""
                        SELECT count(*) FROM sandbox_limits
                        WHERE key = 'purge-rollback-limit'
                    """)).scalar_one(), 1)
                    self.assertEqual(db.execute(text("""
                        SELECT count(*) FROM sandbox_usage_daily
                        WHERE activity_date = :day
                    """), {"day": expired_day}).scalar_one(), 1)
                self.execute(f"DROP TRIGGER retention_test_failure ON {table}")
        self.execute("DROP FUNCTION retention_test_failure()")

    def test_aggregate_usage_counts_only_committed_actions_and_completion(self):
        with sandbox_security.USAGE_LOCK:
            sandbox_security.USAGE_COUNTS.clear()
        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": "t" * 64}):
            created = self.call("POST", "/runs", {
                "scenario": "non_delivery",
                "agent_label": "private usage test label",
            })
            self.assertEqual(created.status, 200, created.body)
            run_id = created.body["run"]["id"]
            agent = {"authorization": "Bearer " + created.body["agent_token"],
                     "idempotency-key": "counted-purchase"}

            first = self.call("POST", f"/agent/{run_id}/actions", {"tool": "purchase"}, agent)
            replay = self.call("POST", f"/agent/{run_id}/actions", {"tool": "purchase"}, agent)
            self.assertEqual(first.status, 200, first.body)
            self.assertFalse(first.body["replayed"])
            self.assertEqual(replay.status, 200, replay.body)
            self.assertTrue(replay.body["replayed"])

            finish = self.call("POST", f"/agent/{run_id}/actions", {"tool": "finish", "claim": "unresolved"},
                               {**agent, "idempotency-key": "counted-finish"})
            self.assertEqual(finish.status, 200, finish.body)

            with sandbox_security.USAGE_LOCK:
                self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {
                    "run_created": 1,
                    "agent_action_accepted": 2,
                    "run_completed": 1,
                })
            with self.Session() as db:
                totals = db.execute(text("""
                    SELECT run_created, agent_action_accepted, run_completed
                    FROM sandbox_usage_daily
                    WHERE activity_date = :day
                """), {"day": datetime.now(timezone.utc).date()}).one()
            self.assertEqual(
                (totals.run_created, totals.agent_action_accepted, totals.run_completed),
                (1, 2, 1),
            )
        with self.assertLogs("sandbox", level="INFO") as logs:
            sandbox_security.flush_usage_counts()
        self.assertIn("run_created=1 agent_action_accepted=2 run_completed=1", logs.output[0])
        self.assertNotIn(run_id, logs.output[0])
        self.assertNotIn(created.body["agent_token"], logs.output[0])
        self.assertNotIn("private usage test label", logs.output[0])
        self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {})

    def test_transaction_failure_reports_only_safe_sqlstate(self):
        from sqlalchemy.exc import OperationalError

        class DatabaseError:
            sqlstate = "08006"

            def __str__(self):
                return "private connection and query details"

        class FailedTransaction:
            def __enter__(self):
                raise OperationalError("BEGIN", {"private": "parameter"}, DatabaseError())

            def __exit__(self, *args):
                return False

        with patch.object(sandbox_store, "Session") as session:
            session.begin.return_value = FailedTransaction()
            with self.assertRaises(OperationalError) as failure:
                purge()
        self.assertEqual(failure.exception._retention_cleanup_step, "transaction")
        fields = sandbox_security.retention_failure_fields(failure.exception)
        self.assertEqual((fields[0], fields[2]), ("transaction", "08006"))
        self.assertNotIn("private", " ".join(fields))
        unsafe = RuntimeError("private database detail")
        unsafe._retention_cleanup_step = "private table name"
        unsafe.orig = SimpleNamespace(sqlstate="P0001\nprivate database detail")
        self.assertEqual(
            sandbox_security.retention_failure_fields(unsafe),
            ("unknown", "RuntimeError", "unknown"),
        )

    def test_usage_summary_failure_preserves_aggregate_counts_for_single_retry(self):
        with sandbox_security.USAGE_LOCK:
            sandbox_security.USAGE_COUNTS.clear()
        sandbox_security.record_usage_count("run_created", 2)
        sandbox_security.record_usage_count("agent_action_accepted", 3)
        sandbox_security.record_usage_count("run_completed")

        with patch.object(sandbox_security.log, "info", side_effect=OSError("logger unavailable")):
            with self.assertRaisesRegex(OSError, "logger unavailable"):
                sandbox_security.flush_usage_counts()

        with sandbox_security.USAGE_LOCK:
            self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {
                "run_created": 2,
                "agent_action_accepted": 3,
                "run_completed": 1,
            })

        # Counts added after the failure must be retained along with the failed batch.
        sandbox_security.record_usage_count("run_created")
        lines = []

        def capture_summary(message, *args):
            lines.append(message % args)

        with patch.object(sandbox_security.log, "info", side_effect=capture_summary):
            sandbox_security.flush_usage_counts()
            sandbox_security.flush_usage_counts()

        self.assertEqual(usage_summary_totals(lines), {
            "run_created": 3,
            "agent_action_accepted": 3,
            "run_completed": 1,
        })
        self.assertEqual(lines, [
            "Sandbox aggregate usage counts: run_created=3 "
            "agent_action_accepted=3 run_completed=1"
        ])
        with sandbox_security.USAGE_LOCK:
            self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {})

    def test_overlapping_failed_and_successful_usage_flushes_preserve_totals_once(self):
        with sandbox_security.USAGE_LOCK:
            sandbox_security.USAGE_COUNTS.clear()
        sandbox_security.record_usage_count("run_created", 2)

        failed_flush_started = threading.Event()
        release_failed_flush = threading.Event()
        emitted_lines = []

        def fail_first_summary(message, *args):
            if not failed_flush_started.is_set():
                failed_flush_started.set()
                self.assertTrue(
                    release_failed_flush.wait(timeout=5),
                    "failed summary logger was not released",
                )
                raise OSError("logger unavailable")
            emitted_lines.append(message % args)

        with patch.object(sandbox_security.log, "info", side_effect=fail_first_summary):
            with ThreadPoolExecutor(max_workers=1) as pool:
                failed_flush = pool.submit(sandbox_security.flush_usage_counts)
                self.assertTrue(
                    failed_flush_started.wait(timeout=5),
                    "failed summary was not captured",
                )

                sandbox_security.record_usage_count("agent_action_accepted", 3)
                sandbox_security.flush_usage_counts()
                release_failed_flush.set()
                with self.assertRaisesRegex(OSError, "logger unavailable"):
                    failed_flush.result(timeout=5)

                # The retry emits only the failed batch; the successful batch is
                # not restored or repeated.
                sandbox_security.flush_usage_counts()
                sandbox_security.flush_usage_counts()

        self.assertEqual(len(emitted_lines), 2)
        self.assertEqual(usage_summary_totals(emitted_lines), {
            "run_created": 2,
            "agent_action_accepted": 3,
            "run_completed": 0,
        })
        with sandbox_security.USAGE_LOCK:
            self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {})

    def test_concurrent_run_creation_and_actions_are_counted_once_while_flushing(self):
        with sandbox_security.USAGE_LOCK:
            sandbox_security.USAGE_COUNTS.clear()

        count = 8
        browser_headers = {key: value for key, value in self.browser.items() if key != "cookie"}
        owners = []
        for _ in range(count):
            owner = self.call("POST", "/owner", headers=browser_headers)
            self.assertEqual(owner.status, 200, owner.body)
            cookie = owner.headers["set-cookie"].split(";", 1)[0]
            owners.append({**browser_headers, "cookie": cookie})

        lines = []
        lines_lock = threading.Lock()

        def capture_summary(message, *args):
            with lines_lock:
                lines.append(message % args)

        stop_flushing = threading.Event()

        def flush_repeatedly():
            while not stop_flushing.is_set():
                sandbox_security.flush_usage_counts()
                stop_flushing.wait(0.001)

        with patch.object(sandbox_security.log, "info", side_effect=capture_summary):
            flusher = threading.Thread(target=flush_repeatedly)
            flusher.start()
            try:
                create_barrier = threading.Barrier(count)

                def create_run(index):
                    create_barrier.wait()
                    return self.call(
                        "POST", "/runs",
                        {
                            "scenario": "non_delivery",
                            "agent_label": f"private concurrent marker {index}",
                        },
                        owners[index],
                    )

                with ThreadPoolExecutor(max_workers=count) as pool:
                    created = list(pool.map(create_run, range(count)))

                for response in created:
                    self.assertEqual(response.status, 200, response.body)
                run_ids = [response.body["run"]["id"] for response in created]
                tokens = [response.body["agent_token"] for response in created]

                action_barrier = threading.Barrier(count)

                def accept_action(index):
                    action_barrier.wait()
                    return self.call(
                        "POST", f"/agent/{run_ids[0]}/actions", {"tool": "purchase"},
                        {
                            "authorization": "Bearer " + tokens[0],
                            "idempotency-key": f"concurrent-purchase-{index}",
                        },
                    )

                with ThreadPoolExecutor(max_workers=count) as pool:
                    actions = list(pool.map(accept_action, range(count)))
                for response in actions:
                    self.assertEqual(response.status, 200, response.body)
                    self.assertFalse(response.body["replayed"])
            finally:
                stop_flushing.set()
                flusher.join(timeout=5)
                self.assertFalse(flusher.is_alive(), "usage flusher did not stop")
                sandbox_security.flush_usage_counts()

        self.assertEqual(usage_summary_totals(lines), {
            "run_created": count,
            "agent_action_accepted": count,
            "run_completed": 0,
        })
        summaries = "\n".join(lines)
        event_report = self.call("GET", f"/runs/{run_ids[0]}", headers=owners[0])
        self.assertEqual(event_report.status, 200, event_report.body)
        event_content = [
            value
            for event in event_report.body["events"]
            for value in event.values()
            if isinstance(value, str)
        ]
        for sensitive_value in (
            *run_ids, *tokens,
            *event_content,
            *(f"private concurrent marker {index}" for index in range(count)),
            *(f"concurrent-purchase-{index}" for index in range(count)),
        ):
            self.assertNotIn(sensitive_value, summaries)
        with sandbox_security.USAGE_LOCK:
            self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {})

    def test_operator_history_access_requires_a_configured_bearer_token(self):
        # Operator routes are not advertised in the public schema; access is enforced below.
        self.assertFalse([p for p in app.openapi()["paths"] if "/operator" in p])

        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": ""}):
            for method, path in (
                ("GET", "/api/sandbox/operator/usage/daily"),
                ("DELETE", "/api/sandbox/operator/usage-history/expired"),
            ):
                for headers in ({}, self.browser):
                    with self.subTest(method=method, headers=bool(headers)):
                        result = request(app, method, path, headers=headers)
                        self.assertEqual(result.status, 503, result.body)

        token = "operator-test-token-" + ("x" * 40)
        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": token}):
            for method, path in (
                ("GET", "/api/sandbox/operator/usage/daily"),
                ("DELETE", "/api/sandbox/operator/usage-history/expired"),
            ):
                for headers in ({}, self.browser,
                                {"authorization": "Bearer incorrect-token"}):
                    with self.subTest(method=method, headers=bool(headers)):
                        result = request(app, method, path, headers=headers)
                        self.assertEqual(result.status, 401, result.body)

    def test_daily_history_exact_schema_operator_access_and_ninety_day_retention(self):
        token = "operator-test-token-" + ("x" * 40)
        today = sandbox_usage_history.date(2026, 10, 26)
        cutoff = sandbox_usage_history.history_cutoff(today)

        columns = self.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'sandbox_usage_daily'
            ORDER BY ordinal_position
        """).all()
        self.assertEqual(columns, [
            ("activity_date", "date", "NO"),
            ("run_created", "bigint", "NO"),
            ("agent_action_accepted", "bigint", "NO"),
            ("run_completed", "bigint", "NO"),
        ])
        primary_key_columns = self.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = current_schema()
              AND tc.table_name = 'sandbox_usage_daily'
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position
        """).scalars().all()
        self.assertEqual(primary_key_columns, ["activity_date"])

        self.execute("""
            INSERT INTO sandbox_usage_daily
                (activity_date, run_created, agent_action_accepted, run_completed)
            VALUES (:old, 9, 8, 7), (:boundary, 1, 2, 3), (:newer, 4, 5, 6)
        """, old=cutoff - sandbox_usage_history.timedelta(days=1),
             boundary=cutoff, newer=today - sandbox_usage_history.timedelta(days=1))

        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": token}), \
             patch.object(sandbox_usage_history, "utc_today", return_value=today):
            headers = {"authorization": "Bearer " + token}
            history = request(app, "GET", "/api/sandbox/operator/usage/daily", headers=headers)
            self.assertEqual(history.status, 200, history.body)
            self.assertEqual(len(history.body["days"]), 90)
            self.assertEqual(
                history.body["days"][0],
                {"date": cutoff.isoformat(),
                 "run_created": 1, "agent_action_accepted": 2, "run_completed": 3},
            )
            self.assertEqual(
                history.body["days"][-2],
                {"date": (today - sandbox_usage_history.timedelta(days=1)).isoformat(),
                 "run_created": 4, "agent_action_accepted": 5, "run_completed": 6},
            )
            self.assertEqual(
                history.body["days"][-1],
                {"date": today.isoformat(),
                 "run_created": 0, "agent_action_accepted": 0, "run_completed": 0},
            )

            deleted = request(app, "DELETE", "/api/sandbox/operator/usage-history/expired",
                              headers=headers)
            self.assertEqual(deleted.status, 200, deleted.body)
            self.assertEqual(deleted.body, {"deleted_buckets": 1})
            remaining = self.execute(
                "SELECT activity_date FROM sandbox_usage_daily ORDER BY activity_date"
            ).scalars().all()
            self.assertEqual(remaining, [cutoff, today - sandbox_usage_history.timedelta(days=1)])

    def test_persistent_usage_writes_only_fixed_counters_to_event_utc_date(self):
        token = "operator-test-token-" + ("x" * 40)
        first_time = datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc).timestamp()
        second_time = datetime(2026, 10, 26, 0, 0, 1, tzinfo=timezone.utc).timestamp()
        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": token}):
            with self.Session.begin() as db:
                sandbox_security.record_persistent_usage(db, "run_created", 2, first_time)
                sandbox_security.record_persistent_usage(
                    db, "agent_action_accepted", 3, second_time,
                )
                sandbox_security.record_persistent_usage(db, "run_completed", 1, second_time)

        rows = self.execute("""
            SELECT activity_date, run_created, agent_action_accepted, run_completed
            FROM sandbox_usage_daily ORDER BY activity_date
        """).all()
        self.assertEqual(rows, [
            (datetime(2026, 10, 25, tzinfo=timezone.utc).date(), 2, 0, 0),
            (datetime(2026, 10, 26, tzinfo=timezone.utc).date(), 0, 3, 1),
        ])

    def test_persistent_usage_rolls_back_with_its_event_transaction(self):
        token = "operator-test-token-" + ("x" * 40)
        event_time = datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp()
        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": token}):
            with self.assertRaisesRegex(RuntimeError, "rollback test"):
                with self.Session.begin() as db:
                    sandbox_security.record_persistent_usage(
                        db, "agent_action_accepted", event_time=event_time,
                    )
                    raise RuntimeError("rollback test")
        self.assertEqual(
            self.execute("SELECT count(*) FROM sandbox_usage_daily").scalar_one(),
            0,
        )

    def test_expired_history_is_pruned_even_after_operator_access_is_disabled(self):
        today = datetime.now(timezone.utc).date()
        cutoff = sandbox_usage_history.history_cutoff(today)
        self.execute("""
            INSERT INTO sandbox_usage_daily
                (activity_date, run_created, agent_action_accepted, run_completed)
            VALUES (:old, 1, 0, 0), (:new, 2, 0, 0)
        """, old=cutoff - sandbox_usage_history.timedelta(days=1), new=cutoff)

        with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": ""}):
            purge()

        rows = self.execute(
            "SELECT activity_date FROM sandbox_usage_daily ORDER BY activity_date"
        ).scalars().all()
        self.assertEqual(rows, [cutoff])

    def test_product_disclosure_and_operator_setup_describe_the_gate_and_retention(self):
        root = Path(__file__).resolve().parents[3]
        disclosure = (root / "docs/contribution-and-repeat.md").read_text()
        usage_notice = (root / "artifacts/chargeback-os/src/components/usage-analytics-notice.tsx").read_text()
        operator_guide = (root / "docs/sandbox-operations.md").read_text()
        for text_value in (disclosure, usage_notice, operator_guide):
            with self.subTest(document="retention"):
                self.assertIn("90 days", text_value)
        self.assertIn("daily UTC", disclosure)
        self.assertIn("service-operator", disclosure)
        self.assertIn("daily UTC", usage_notice)
        self.assertIn("authenticated service operators", usage_notice)
        self.assertIn("successful run", usage_notice)
        self.assertIn("newly accepted agent actions", usage_notice)
        self.assertIn("completed runs", usage_notice)
        self.assertIn("SANDBOX_OPERATOR_TOKEN", operator_guide)
        self.assertIn("UTC date buckets", operator_guide)
        self.assertIn("backup-retention", disclosure)

    def test_simultaneous_counter_updates_and_flushes_preserve_every_count(self):
        with sandbox_security.USAGE_LOCK:
            sandbox_security.USAGE_COUNTS.clear()

        producer_count = 6
        rounds = 80
        events = tuple(sandbox_security.USAGE_EVENTS)
        barrier = threading.Barrier(producer_count + 1)
        lines = []
        lines_lock = threading.Lock()

        def capture_summary(message, *args):
            with lines_lock:
                lines.append(message % args)

        def produce(event):
            for _ in range(rounds):
                barrier.wait()
                sandbox_security.record_usage_count(event)
                barrier.wait()

        def flush_each_round():
            for _ in range(rounds):
                barrier.wait()
                sandbox_security.flush_usage_counts()
                barrier.wait()

        with patch.object(sandbox_security.log, "info", side_effect=capture_summary):
            with ThreadPoolExecutor(max_workers=producer_count + 1) as pool:
                futures = [
                    pool.submit(produce, events[index % len(events)])
                    for index in range(producer_count)
                ]
                futures.append(pool.submit(flush_each_round))
                for future in futures:
                    future.result(timeout=20)
                sandbox_security.flush_usage_counts()

        expected = {
            event: rounds * sum(index % len(events) == event_index
                                for index in range(producer_count))
            for event_index, event in enumerate(events)
        }
        self.assertEqual(usage_summary_totals(lines), expected)
        with sandbox_security.USAGE_LOCK:
            self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {})


class RetentionWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "sandbox_worker_test_" + secrets.token_hex(12)
        with database.engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(
            database.engine.url,
            connect_args={"options": f"-csearch_path={self.schema}"},
        )
        self.Session = sessionmaker(self.engine)
        sql = (Path(__file__).resolve().parents[1] / "sandbox_schema.sql").read_text()
        with self.engine.begin() as db:
            for statement in sql.split(";"):
                if statement.strip():
                    db.execute(text(statement))
        self.session_patch = patch.object(sandbox_security, "Session", self.Session)
        self.session_patch.start()
        self.addAsyncCleanup(self._cleanup_schema)

    async def _cleanup_schema(self):
        self.session_patch.stop()
        self.engine.dispose()
        with database.engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    async def test_cleanup_threshold_pauses_intake_until_cleanup_succeeds(self):
        waiting = asyncio.Event()
        retries = [asyncio.Event() for _ in range(sandbox_security.RETENTION_FAILURE_THRESHOLD)]
        calls = 0

        async def interval(seconds):
            nonlocal calls
            self.assertEqual(seconds, 60)
            calls += 1
            waiting.set()
            if calls <= sandbox_security.RETENTION_FAILURE_THRESHOLD:
                await retries[calls - 1].wait()
            else:
                await asyncio.Event().wait()

        # Replace only the worker's clock; real event-loop scheduling is intact.
        worker_asyncio = SimpleNamespace(
            create_task=asyncio.create_task, to_thread=asyncio.to_thread,
            sleep=interval, CancelledError=asyncio.CancelledError,
        )
        failure = RuntimeError("private storage detail")
        failure._retention_cleanup_step = "limit_bucket"
        failure.orig = SimpleNamespace(sqlstate="42P01")
        health = sandbox_security.RetentionHealth()
        health.record_success()
        with patch.object(sandbox_security, "asyncio", worker_asyncio), \
             patch.object(sandbox_security, "retention_health", health), \
             patch.object(sandbox_security, "purge",
                          side_effect=[failure, failure, failure, None]) as cleanup, \
             self.assertLogs("sandbox", level="ERROR") as logs:
            async with sandbox_security.lifespan(app):
                self.assertTrue(health.intake_paused())
                for attempt in range(1, sandbox_security.RETENTION_FAILURE_THRESHOLD + 2):
                    await asyncio.wait_for(waiting.wait(), 2)
                    waiting.clear()
                    self.assertEqual(cleanup.call_count, attempt)
                    if attempt <= sandbox_security.RETENTION_FAILURE_THRESHOLD:
                        self.assertEqual(health.consecutive_failures, attempt)
                        # Startup remains quarantined until the first successful purge;
                        # the explicit API test covers the three-failure threshold.
                        self.assertTrue(health.intake_paused())
                        retries[attempt - 1].set()
                    else:
                        self.assertFalse(health.intake_paused())
                        self.assertEqual(health.consecutive_failures, 0)
            self.assertEqual(cleanup.call_count, sandbox_security.RETENTION_FAILURE_THRESHOLD + 1)
            self.assertFalse([task for task in asyncio.all_tasks()
                              if task is not asyncio.current_task() and not task.done()])
        self.assertEqual(len(logs.output), sandbox_security.RETENTION_FAILURE_THRESHOLD)
        for line in logs.output:
            self.assertIn("Retention cleanup failed; operator action required", line)
            self.assertIn("step=limit_bucket error_type=RuntimeError sqlstate=42P01", line)
            self.assertNotIn("private storage detail", line)
            self.assertNotIn("DELETE FROM", line)

    async def test_cleanup_failure_is_logged_retried_and_worker_stops_on_shutdown(self):
        waiting = asyncio.Event()
        retry = asyncio.Event()
        calls = 0

        async def interval(seconds):
            nonlocal calls
            self.assertEqual(seconds, 60)
            calls += 1
            waiting.set()
            if calls == 1:
                await retry.wait()
            else:
                await asyncio.Event().wait()

        # Replace only the worker's clock; real event-loop scheduling is intact.
        worker_asyncio = SimpleNamespace(
            create_task=asyncio.create_task, to_thread=asyncio.to_thread,
            sleep=interval, CancelledError=asyncio.CancelledError,
        )
        failure = RuntimeError("private storage detail")
        failure._retention_cleanup_step = "run_deletion"
        failure.orig = SimpleNamespace(sqlstate="42P01")
        with patch.object(sandbox_security, "asyncio", worker_asyncio), \
             patch.object(sandbox_security, "purge",
                          side_effect=[failure, None]) as cleanup, \
             self.assertLogs("sandbox", level="ERROR") as logs:
            async with sandbox_security.lifespan(app):
                await asyncio.wait_for(waiting.wait(), 2)
                self.assertEqual(cleanup.call_count, 1)
                waiting.clear()
                retry.set()
                await asyncio.wait_for(waiting.wait(), 2)
                self.assertEqual(cleanup.call_count, 2)
            self.assertEqual(cleanup.call_count, 2)
            self.assertFalse([task for task in asyncio.all_tasks()
                              if task is not asyncio.current_task() and not task.done()])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Retention cleanup failed; operator action required", logs.output[0])
        self.assertIn("step=run_deletion error_type=RuntimeError sqlstate=42P01", logs.output[0])
        self.assertNotIn("private storage detail", logs.output[0])

    async def test_summary_logger_failures_do_not_stop_cleanup_or_duplicate_totals(self):
        iterations = [asyncio.Event(), asyncio.Event()]
        retries = [asyncio.Event(), asyncio.Event()]
        interval_calls = 0

        async def interval(seconds):
            nonlocal interval_calls
            self.assertEqual(seconds, 60)
            index = interval_calls
            interval_calls += 1
            if index < len(iterations):
                iterations[index].set()
                await retries[index].wait()
            else:
                await asyncio.Event().wait()

        worker_asyncio = SimpleNamespace(
            create_task=asyncio.create_task, to_thread=asyncio.to_thread,
            sleep=interval, CancelledError=asyncio.CancelledError,
        )
        health = SimpleNamespace(
            begin_startup=lambda: None,
            record_success=lambda: None,
        )
        summaries = []
        usage_lines = []
        warning_attempts = 0
        info_attempts = 0
        errors = []

        def warning(message, summary):
            nonlocal warning_attempts
            warning_attempts += 1
            if warning_attempts == 1:
                raise OSError("warning handler unavailable")
            summaries.append(dict(summary))

        def info(message, *args):
            nonlocal info_attempts
            info_attempts += 1
            if info_attempts == 1:
                raise OSError("info handler unavailable")
            usage_lines.append(message % args)

        sandbox_security.record_outcome(401)
        sandbox_security.record_usage_count("run_created", 2)
        with patch.object(sandbox_security, "asyncio", worker_asyncio), \
             patch.object(sandbox_security, "retention_health", health), \
             patch.object(sandbox_security, "purge", return_value=None) as cleanup, \
             patch.object(sandbox_security.log, "warning", side_effect=warning), \
             patch.object(sandbox_security.log, "info", side_effect=info), \
             patch.object(sandbox_security.log, "error",
                          side_effect=lambda message: errors.append(message)):
            async with sandbox_security.lifespan(app):
                await asyncio.wait_for(iterations[0].wait(), 2)
                self.assertEqual(cleanup.call_count, 1)
                with sandbox_security.OUTCOME_LOCK:
                    self.assertEqual(dict(sandbox_security.OUTCOMES), {"401": 1})
                with sandbox_security.USAGE_LOCK:
                    self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {
                        "run_created": 2,
                        "agent_action_accepted": 0,
                        "run_completed": 0,
                    })

                retries[0].set()
                await asyncio.wait_for(iterations[1].wait(), 2)
                self.assertEqual(cleanup.call_count, 2)
                with sandbox_security.OUTCOME_LOCK:
                    self.assertEqual(dict(sandbox_security.OUTCOMES), {})
                with sandbox_security.USAGE_LOCK:
                    self.assertEqual(dict(sandbox_security.USAGE_COUNTS), {})

        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(summaries, [{"401": 1}])
        self.assertEqual(usage_summary_totals(usage_lines), {
            "run_created": 2,
            "agent_action_accepted": 0,
            "run_completed": 0,
        })
        self.assertEqual(errors, [
            "Sandbox response summary flush failed",
            "Sandbox usage summary flush failed",
        ])
        self.assertFalse([task for task in asyncio.all_tasks()
                          if task is not asyncio.current_task() and not task.done()])
