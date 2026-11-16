import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from unittest.mock import patch

from sandbox_test_support import IsolatedSandboxTest, request
from main import app
import sandbox_mcp
from services.sandbox_engine import TOOLS
from services.sandbox_store import ResearchExportAudit


class SandboxResearchExportTests(IsolatedSandboxTest):
    def setUp(self):
        super().setUp()
        self.token = "research-export-" + secrets.token_urlsafe(32)
        self.browser = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        owner = request(app, "POST", "/api/sandbox/owner", headers=self.browser)
        self.browser["cookie"] = owner.headers["set-cookie"].split(";")[0]
        created = request(
            app, "POST", "/api/sandbox/runs", {"scenario": "non_delivery"},
            self.browser,
        )
        self.assertEqual(created.status, 200)
        self.run_id = created.body["run"]["id"]
        self.created_at = created.body["run"]["created_at"]
        self.agent_token = created.body["agent_token"]
        finish = request(
            app,
            "POST",
            f"/api/sandbox/agent/{self.run_id}/actions",
            {"tool": "finish", "claim": "unresolved"},
            {"authorization": "Bearer " + self.agent_token, "idempotency-key": "finish"},
        )
        self.assertEqual(finish.status, 200)

    def consent(self, enabled):
        return request(
            app,
            "PATCH",
            f"/api/sandbox/runs/{self.run_id}/consent",
            {"consent": enabled, "preview_acknowledged": enabled},
            self.browser,
        )

    def export(self, cursor=None, page_size=100, headers=None):
        query = f"?page_size={page_size}"
        if cursor is not None:
            query += "&cursor=" + cursor
        return request(
            app,
            "GET",
            "/api/sandbox/operator/research/export" + query,
            headers=(
                headers if headers is not None
                else {"authorization": "Bearer " + self.token}
            ),
        )

    def authorized(self, callback):
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            return callback()

    def test_export_auth_requires_dedicated_bearer_and_fails_closed(self):
        self.assertEqual(self.export(headers=self.browser).status, 503)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            self.assertEqual(self.export(headers={}).status, 401)
            self.assertEqual(
                self.export(headers={"authorization": "Bearer wrong-token"}).status,
                401,
            )
            self.assertEqual(self.export(headers=self.browser).status, 401)
            self.assertEqual(
                self.export(headers={"authorization": "Bearer " + self.agent_token}).status,
                401,
            )
            self.assertEqual(
                self.export(headers={"authorization": "Bearer oauth_access_test_token"}).status,
                401,
            )
            with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": "operator-only-token"}):
                self.assertEqual(
                    self.export(headers={
                        "authorization": "Bearer operator-only-token",
                    }).status,
                    401,
                )
            with patch.dict(os.environ, {"SANDBOX_OPERATOR_TOKEN": self.token}):
                self.assertEqual(self.export().status, 503)

    def test_unset_short_or_reused_operator_secret_never_enables_export(self):
        self.assertEqual(self.export().status, 503)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": "short"}):
            self.assertEqual(self.export().status, 503)
        with patch.dict(os.environ, {
            "SANDBOX_RESEARCH_EXPORT_TOKEN": self.token,
            "SANDBOX_OPERATOR_TOKEN": self.token,
        }):
            self.assertEqual(self.export().status, 503)

    def test_preview_matches_export_record_and_expiry_is_creation_plus_30_days(self):
        preview = request(
            app, "GET", f"/api/sandbox/runs/{self.run_id}/research-preview",
            headers=self.browser,
        ).body["record"]
        self.assertEqual(self.consent(True).status, 200)
        before_export = request(
            app, "GET", f"/api/sandbox/runs/{self.run_id}", headers=self.browser
        ).body
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            response = self.export()
        self.assertEqual(response.status, 200)
        manifest, record_line = response.body
        self.assertEqual(manifest["type"], "manifest")
        self.assertEqual(manifest["export_schema_version"], "2")
        self.assertEqual(manifest["record_schema_version"], "1")
        self.assertEqual(manifest["consent_versions"], ["2"])
        self.assertEqual(record_line["type"], "record")
        self.assertEqual(record_line["record"], preview)
        self.assertEqual(record_line["consent_version"], "2")
        self.assertEqual(record_line["run_status"], "finished")
        self.assertEqual(
            record_line["expires_at"],
            datetime.fromtimestamp(
                self.created_at + 30 * 86400, timezone.utc
            ).isoformat().replace("+00:00", "Z"),
        )
        self.assertEqual(len(record_line["record_ref"]), 32)
        self.assertNotIn(self.run_id, json.dumps(record_line))
        self.assertNotIn(self.agent_token, json.dumps(record_line))
        self.assertEqual(manifest["counts"], {"records": 1, "withdrawals": 0})
        after = request(
            app, "GET", f"/api/sandbox/runs/{self.run_id}", headers=self.browser
        ).body
        self.assertEqual(after["consent"], before_export["consent"])
        self.assertEqual(after["deletes_at"], before_export["deletes_at"])

    def test_cursor_pages_events_without_skipping_and_with_stable_withdrawal_ref(self):
        self.assertEqual(self.consent(True).status, 200)
        self.assertEqual(self.consent(False).status, 200)
        self.assertEqual(self.consent(True).status, 200)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            first = self.export(page_size=1)
            self.assertEqual(first.status, 200)
            first_manifest, first_record = first.body
            self.assertTrue(first_manifest["has_more"])
            ref = first_record["record_ref"]

            second = self.export(cursor=first_manifest["next_cursor"], page_size=1)
            self.assertEqual(second.status, 200)
            second_manifest, tombstone = second.body
            self.assertTrue(second_manifest["has_more"])
            self.assertEqual(tombstone, {"type": "withdrawal", "record_ref": ref})

            repeated = self.export(cursor=first_manifest["next_cursor"], page_size=1)
            self.assertEqual(repeated.body[1:], second.body[1:])
            self.assertEqual(
                repeated.body[0]["next_cursor"],
                second_manifest["next_cursor"],
            )
            third = self.export(cursor=second_manifest["next_cursor"], page_size=1)
            self.assertEqual(third.status, 200)
            self.assertFalse(third.body[0]["has_more"])
            self.assertEqual(third.body[1]["type"], "record")
            self.assertEqual(third.body[1]["record_ref"], ref)
            empty = self.export(cursor=third.body[0]["next_cursor"], page_size=1)
            self.assertEqual(empty.body[0]["counts"], {"records": 0, "withdrawals": 0})
            self.assertEqual(empty.body[0]["has_more"], False)

            original_cursor = first_manifest["next_cursor"]
            tampered = ("A" if original_cursor[0] != "A" else "B") + original_cursor[1:]
            self.assertEqual(self.export(cursor=tampered).status, 400)

    def test_expired_or_withdrawn_runs_never_export_record_bodies(self):
        self.assertEqual(self.consent(True).status, 200)
        self.execute(
            "UPDATE sandbox_runs SET deletes=:deadline WHERE id=:run_id",
            deadline=time.time() - 1,
            run_id=self.run_id,
        )
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            response = self.export()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body[0]["counts"], {"records": 0, "withdrawals": 0})
        self.assertEqual(len(response.body), 1)

    def test_withdrawn_record_is_replaced_by_tombstone_only(self):
        self.assertEqual(self.consent(True).status, 200)
        self.assertEqual(self.consent(False).status, 200)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            response = self.export()
        self.assertEqual(response.status, 200)
        self.assertEqual(
            response.body[0]["counts"],
            {"records": 0, "withdrawals": 1},
        )
        self.assertEqual(len(response.body), 2)
        self.assertEqual(response.body[1]["type"], "withdrawal")
        self.assertEqual(set(response.body[1]), {"type", "record_ref"})

    def test_deleting_a_contributed_run_emits_withdrawal(self):
        self.assertEqual(self.consent(True).status, 200)
        removed = request(
            app,
            "DELETE",
            f"/api/sandbox/runs/{self.run_id}",
            headers=self.browser,
        )
        self.assertEqual(removed.status, 200)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            response = self.export()
        self.assertEqual(response.body[0]["counts"], {"records": 0, "withdrawals": 1})
        self.assertEqual(response.body[1]["type"], "withdrawal")

    def test_consent_counters_are_durable_and_retries_do_not_double_count(self):
        self.assertEqual(self.consent(True).status, 200)
        self.assertEqual(self.consent(True).status, 200)
        self.assertEqual(self.consent(False).status, 200)
        self.assertEqual(self.consent(False).status, 200)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            response = request(
                app,
                "GET",
                "/api/sandbox/operator/research/stats",
                headers={"authorization": "Bearer " + self.token},
            )
        self.assertEqual(response.status, 200)
        counts = {
            row["transition"]: row["count"]
            for row in response.body["counts"]
        }
        self.assertEqual(counts, {"consent_withdrawn": 1, "contribution_enabled": 1})
        serialized = json.dumps(response.body)
        self.assertNotIn(self.run_id, serialized)
        self.assertNotIn(self.agent_token, serialized)
        self.assertNotIn("record_ref", serialized)

    def test_export_audit_has_manifest_checksum_and_never_stores_token_or_record(self):
        self.assertEqual(self.consent(True).status, 200)
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            response = self.export()
        manifest_line = response.text.splitlines()[0]
        checksum = hashlib.sha256(manifest_line.encode("utf-8")).hexdigest()
        self.assertEqual(response.headers["x-research-manifest-sha256"], checksum)
        with self.Session() as db:
            audits = db.query(ResearchExportAudit).all()
            audit_rows = [
                {
                    "key_identity": row.key_identity,
                    "outcome": row.outcome,
                    "records_count": row.records_count,
                    "withdrawals_count": row.withdrawals_count,
                    "cursor_from": row.cursor_from,
                    "cursor_to": row.cursor_to,
                    "manifest_checksum": row.manifest_checksum,
                }
                for row in audits
            ]
        serialized = json.dumps(audit_rows)
        self.assertIn("research-export-primary", serialized)
        self.assertIn(checksum, serialized)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn(self.agent_token, serialized)
        self.assertNotIn(self.run_id, serialized)
        self.assertNotIn("unresolved", serialized)
        self.assertEqual(audit_rows[-1]["records_count"], 1)

    def test_page_rate_limit_and_no_mcp_or_openapi_exposure(self):
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.token}):
            for _ in range(10):
                self.assertEqual(self.export().status, 200)
            self.assertEqual(self.export().status, 429)
            stats = request(
                app, "GET", "/api/sandbox/operator/research/stats",
                headers={"authorization": "Bearer " + self.token},
            )
            self.assertEqual(stats.status, 429)
        self.assertNotIn("research_export", TOOLS)
        self.assertFalse(any(
            getattr(route, "path", "").startswith("/api/sandbox/operator/research")
            for route in sandbox_mcp.router.routes
        ))
        self.assertNotIn(
            "/api/sandbox/operator/research/export",
            app.openapi()["paths"],
        )
