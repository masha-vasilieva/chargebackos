"""Sharing chosen when a run starts reaches the export as the run progresses."""
import os
import secrets
import time
from unittest.mock import patch

from sandbox_test_support import IsolatedSandboxTest, request
from main import app


class ResearchSharingAtStartTests(IsolatedSandboxTest):
    def setUp(self):
        super().setUp()
        self.export_token = "research-export-" + secrets.token_urlsafe(32)
        self.browser = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        owner = request(app, "POST", "/api/sandbox/owner", headers=self.browser)
        self.browser["cookie"] = owner.headers["set-cookie"].split(";")[0]

    def create(self, **extra):
        return request(app, "POST", "/api/sandbox/runs",
                       {"scenario": "non_delivery", **extra}, self.browser)

    def act(self, created, tool, key, claim=None):
        body = {"tool": tool, **({"claim": claim} if claim else {})}
        return request(app, "POST", f"/api/sandbox/agent/{created['run']['id']}/actions", body,
                       {"authorization": "Bearer " + created["agent_token"], "idempotency-key": key})

    def export(self, cursor=None):
        query = "?page_size=100" + (f"&cursor={cursor}" if cursor else "")
        with patch.dict(os.environ, {"SANDBOX_RESEARCH_EXPORT_TOKEN": self.export_token}):
            response = request(app, "GET", "/api/sandbox/operator/research/export" + query,
                               headers={"authorization": "Bearer " + self.export_token})
        self.assertEqual(response.status, 200)
        manifest, *lines = response.body
        return manifest, lines

    def test_sharing_at_start_needs_terms_and_keeps_run_30_days(self):
        self.assertEqual(self.create(consent=True).status, 422)
        created = self.create(consent=True, research_terms_acknowledged=True)
        self.assertEqual(created.status, 200)
        run = created.body["run"]
        self.assertTrue(run["consent"])
        self.assertEqual(run["consent_version"], "2")
        self.assertAlmostEqual(run["deletes_at"], run["created_at"] + 30 * 86400, delta=1)
        # Not sharing stays the default and keeps the 7-day deadline.
        private = self.create().body["run"]
        self.assertFalse(private["consent"])
        self.assertAlmostEqual(private["deletes_at"], private["created_at"] + 7 * 86400, delta=1)

    def test_shared_run_is_exported_as_it_progresses_and_once_per_page(self):
        created = self.create(consent=True, research_terms_acknowledged=True).body
        manifest, lines = self.export()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["run_status"], "in_progress")
        self.assertEqual(lines[0]["record"]["events"], [])
        self.assertEqual(manifest["consent_versions"], ["2"])

        for number, tool in enumerate(("inspect_offer", "purchase", "check_delivery"), 1):
            self.assertEqual(self.act(created, tool, f"step-{number}").status, 200)
        _manifest, lines = self.export(manifest["next_cursor"])
        self.assertEqual(len(lines), 1, "several updates to one run must export one line")
        self.assertEqual(lines[0]["record_ref"], self.export()[1][0]["record_ref"])
        self.assertEqual([e["tool"] for e in lines[0]["record"]["events"]],
                         ["inspect_offer", "purchase", "check_delivery"])
        self.assertEqual(lines[0]["run_status"], "in_progress")

        self.assertEqual(self.act(created, "finish", "done", "unresolved").status, 200)
        _manifest, lines = self.export(_manifest["next_cursor"])
        self.assertEqual(lines[0]["run_status"], "finished")

    def test_abandoned_shared_run_gets_one_final_snapshot(self):
        created = self.create(consent=True, research_terms_acknowledged=True).body
        self.assertEqual(self.act(created, "purchase", "buy").status, 200)
        manifest, _lines = self.export()
        self.execute("UPDATE sandbox_runs SET expires = :past WHERE id = :id",
                     past=time.time() - 1, id=created["run"]["id"])
        manifest, lines = self.export(manifest["next_cursor"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["run_status"], "abandoned")
        self.assertEqual([e["tool"] for e in lines[0]["record"]["events"]], ["purchase"])
        # The final snapshot is sent once, and the owner's report stays clean.
        _manifest, lines = self.export(manifest["next_cursor"])
        self.assertEqual(lines, [])
        report = request(app, "GET", f"/api/sandbox/runs/{created['run']['id']}",
                         headers=self.browser).body
        self.assertNotIn("research_abandoned_indexed", report)

    def test_unshared_run_actions_never_reach_the_export(self):
        created = self.create().body
        self.assertEqual(self.act(created, "purchase", "buy").status, 200)
        manifest, lines = self.export()
        self.assertEqual(lines, [])
        self.assertEqual(manifest["counts"], {"records": 0, "withdrawals": 0})

    def test_withdrawing_mid_run_stops_updates_and_sends_a_withdrawal(self):
        created = self.create(consent=True, research_terms_acknowledged=True).body
        run_id = created["run"]["id"]
        manifest, lines = self.export()
        ref = lines[0]["record_ref"]
        withdrawn = request(app, "PATCH", f"/api/sandbox/runs/{run_id}/consent",
                            {"consent": False}, self.browser)
        self.assertEqual(withdrawn.status, 200)
        self.assertEqual(self.act(created, "purchase", "after-withdrawal").status, 200)
        _manifest, lines = self.export(manifest["next_cursor"])
        self.assertEqual(lines, [{"type": "withdrawal", "record_ref": ref}])

    def test_opting_in_mid_run_uses_new_consent_version(self):
        created = self.create().body
        run_id = created["run"]["id"]
        self.assertEqual(self.act(created, "inspect_offer", "look").status, 200)
        preview = request(app, "GET", f"/api/sandbox/runs/{run_id}/research-preview",
                          headers=self.browser)
        self.assertEqual(preview.status, 200)
        opted = request(app, "PATCH", f"/api/sandbox/runs/{run_id}/consent",
                        {"consent": True, "preview_acknowledged": True}, self.browser)
        self.assertEqual(opted.status, 200)
        self.assertEqual(opted.body["consent_version"], "2")
        _manifest, lines = self.export()
        self.assertEqual(lines[0]["run_status"], "in_progress")
        self.assertEqual(len(lines[0]["record"]["events"]), 1)

    def test_finish_returns_result_link_without_leaking_run_id_to_research(self):
        created = self.create(consent=True, research_terms_acknowledged=True).body
        run_id = created["run"]["id"]
        purchase = self.act(created, "purchase", "buy").body
        self.assertNotIn("result_url", purchase)
        finished = self.act(created, "finish", "done", "unresolved").body
        self.assertTrue(finished["result_url"].endswith(f"/runs/{run_id}"))
        self.assertIn("result_url", finished["next_step"])
        replay = self.act(created, "finish", "done", "unresolved").body
        self.assertEqual(replay["result_url"], finished["result_url"])
        self.assertTrue(replay["replayed"])
        report = request(app, "GET", f"/api/sandbox/runs/{run_id}", headers=self.browser).body
        self.assertNotIn("result_url", report["events"][-1]["output"])
        _manifest, lines = self.export()
        self.assertNotIn(run_id, str(lines))

    def test_chat_seller_runs_can_be_created_and_keep_the_screenshot_in_research(self):
        created = request(app, "POST", "/api/sandbox/runs",
                          {"scenario": "off_platform_seller", "consent": True,
                           "research_terms_acknowledged": True}, self.browser).body
        self.assertEqual(self.act(created, "purchase", "buy").status, 200)
        self.assertEqual(self.act(created, "check_delivery", "look").status, 200)
        _manifest, lines = self.export()
        record = lines[0]["record"]
        self.assertEqual(record["scenario"], "off_platform_seller")
        self.assertEqual(record["events"][1]["output"]["seller_attachment"]["filename"], "refund_sent.png")
        honest = request(app, "POST", "/api/sandbox/runs", {"scenario": "chatty_honest_seller"}, self.browser)
        self.assertEqual(honest.status, 200)
