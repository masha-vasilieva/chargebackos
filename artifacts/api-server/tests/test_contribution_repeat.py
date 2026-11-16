import json
import time
from sqlalchemy import text
from sandbox_test_support import IsolatedSandboxTest, request
from main import app
from export_research import summarize
from services.sandbox_store import Run


class ContributionRepeatTests(IsolatedSandboxTest):
    def setUp(self):
        super().setUp()
        self.browser = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        owner = request(app, "POST", "/api/sandbox/owner", headers=self.browser)
        self.browser["cookie"] = owner.headers["set-cookie"].split(";")[0]
        created = self.create(consent=False, agent_label="PRIVATE-LABEL")
        self.assertEqual(created.status, 200)
        self.run = created.body["run"]["id"]
        self.token = created.body["agent_token"]

    def call(self, method, suffix, body=None, headers=None):
        return request(app, method, "/api/sandbox"+suffix, body,
                       self.browser if headers is None else headers)

    def create(self, **extra):
        return self.call("POST", "/runs", {"scenario": "non_delivery", **extra})

    def finish(self):
        return self.call("POST", f"/agent/{self.run}/actions",
                         {"tool": "finish", "claim": "unresolved"},
                         {"authorization": "Bearer "+self.token, "idempotency-key": "finish"})

    def finish_member(self, member, key):
        return self.call("POST", f"/agent/{member['run']['id']}/actions",
                         {"tool": "finish", "claim": "unresolved"},
                         {"authorization": "Bearer "+member["agent_token"], "idempotency-key": key})

    def opt_in(self, run_id):
        self.assertEqual(self.call("GET", f"/runs/{run_id}/research-preview").status, 200)
        return self.call("PATCH", f"/runs/{run_id}/consent",
                         {"consent": True, "preview_acknowledged": True})

    def research(self):
        with self.Session() as db:
            return summarize(list(db.query(Run).all()))

    def test_preview_read_only_allowlist_and_owner_only(self):
        self.finish()
        before = self.call("GET", f"/runs/{self.run}").body
        preview = self.call("GET", f"/runs/{self.run}/research-preview")
        self.assertEqual(preview.status, 200)
        rendered = json.dumps(preview.body["record"])
        for excluded in (self.run, self.token, "PRIVATE-LABEL", "idempotency", "consent_updated_at"):
            self.assertNotIn(excluded, rendered)
        record = preview.body["record"]
        self.assertIn("elapsed_seconds", record["events"][0])
        self.assertNotIn("timestamp", record["events"][0])
        after = self.call("GET", f"/runs/{self.run}").body
        self.assertEqual(before, after)
        self.assertEqual(self.research()["consented_runs"], 0)
        agent = {"authorization": "Bearer "+self.token}
        self.assertEqual(self.call("GET", f"/runs/{self.run}/research-preview", headers=agent).status, 401)
        stranger = {"origin": "http://testserver", "x-sandbox-client": "browser"}
        cookie = self.call("POST", "/owner", headers=stranger).headers["set-cookie"].split(";")[0]
        stranger["cookie"] = cookie
        self.assertEqual(self.call("GET", f"/runs/{self.run}/research-preview", headers=stranger).status, 404)

    def test_opt_in_requires_acknowledgment_at_start_or_during_a_run(self):
        # Sharing at the start needs the research terms confirmed.
        self.assertEqual(self.create(consent=True).status, 422)
        self.assertFalse(self.call("GET", f"/runs/{self.run}").body["consent"])
        # During a run the current record can be previewed, and opting in needs that review.
        self.assertEqual(self.call("GET", f"/runs/{self.run}/research-preview").status, 200)
        self.assertEqual(self.call("PATCH", f"/runs/{self.run}/consent",
                                   {"consent": True}).status, 422)
        self.assertTrue(self.opt_in(self.run).body["consent"])

    def test_not_now_persists_without_research_or_retention_extension(self):
        self.finish()
        before = self.call("GET", f"/runs/{self.run}").body
        response = self.call("PATCH", f"/runs/{self.run}/consent", {"consent": False})
        self.assertEqual(response.status, 200)
        reloaded = self.call("GET", f"/runs/{self.run}").body
        self.assertTrue(reloaded["research_prompt_dismissed"])
        self.assertFalse(reloaded["consent"])
        self.assertEqual(reloaded["deletes_at"], before["deletes_at"])
        self.assertEqual(self.research()["consented_runs"], 0)

    def test_opt_in_then_withdraw_removes_research_and_keeps_private_result(self):
        self.finish()
        updated = self.opt_in(self.run).body
        self.assertEqual(updated["deletes_at"], updated["created_at"]+30*86400)
        self.assertEqual(self.research()["consented_runs"], 1)
        withdrawn = self.call("PATCH", f"/runs/{self.run}/consent", {"consent": False}).body
        self.assertEqual(withdrawn["deletes_at"], withdrawn["created_at"]+7*86400)
        self.assertTrue(withdrawn["research_prompt_dismissed"])
        self.assertEqual(self.research()["consented_runs"], 0)
        self.assertEqual(self.call("GET", f"/runs/{self.run}").status, 200)

    def test_withdrawing_one_comparison_member_keeps_only_current_contribution(self):
        self.finish()
        created = self.create(consent=False, agent_label="PRIVATE-ARM-A", compare_with="PRIVATE-ARM-B")
        members = created.body["comparison_runs"]
        first, second = members
        comparison_id = first["run"]["comparison"]["id"]
        self.assertEqual(second["run"]["comparison"]["id"], comparison_id)

        self.finish_member(first, "finish-first")
        self.finish_member(second, "finish-second")
        self.opt_in(first["run"]["id"])
        self.opt_in(second["run"]["id"])
        withdrawn = self.call(
            "PATCH", f"/runs/{first['run']['id']}/consent", {"consent": False}
        )
        self.assertEqual(withdrawn.status, 200)
        summary = self.research()

        self.assertEqual(summary["consented_runs"], 1)
        self.assertEqual(summary["participating_owners"], 1)
        self.assertTrue(all(row["count"] == 1 for row in summary["counts"]))
        self.assertTrue(all(row["count"] == 1 for row in summary["behavior_counts"]))
        self.assertEqual(
            {(row["simulator_version"], row["scorer_version"]) for row in summary["counts"]},
            {(second["run"]["version"], second["run"]["scorer_version"])},
        )
        serialized = json.dumps(summary)
        for private_value in (
            first["run"]["id"], second["run"]["id"], comparison_id,
            first["agent_token"], second["agent_token"], "PRIVATE-ARM-A", "PRIVATE-ARM-B",
        ):
            self.assertNotIn(private_value, serialized)

    def test_old_withdrawal_makes_run_unavailable_instead_of_renewing(self):
        self.finish()
        self.opt_in(self.run)
        self.execute("UPDATE sandbox_runs SET created=:old WHERE id=:id",
                     old=time.time()-8*86400, id=self.run)
        self.call("PATCH", f"/runs/{self.run}/consent", {"consent": False})
        self.assertEqual(self.call("GET", f"/runs/{self.run}/research-preview").status, 404)
        self.assertEqual(self.research()["consented_runs"], 0)

    def test_repeat_new_run_independent_and_source_unchanged(self):
        self.finish()
        self.opt_in(self.run)
        before = self.call("GET", f"/runs/{self.run}").body
        created = self.create(repeat_of=self.run)
        self.assertEqual(created.status, 200)
        repeat = created.body["run"]
        self.assertNotEqual(created.body["agent_token"], self.token)
        self.assertFalse(repeat["consent"])
        self.assertFalse(repeat.get("research_prompt_dismissed", False))
        self.assertEqual(repeat["repeat_of"], self.run)
        self.assertEqual(repeat["events"], [])
        self.assertEqual(repeat["state"]["balance"], 100)
        self.assertEqual(repeat["context"]["coaching"], "unknown")
        self.assertEqual(self.call("GET", f"/runs/{self.run}").body, before)
        self.assertEqual(self.call("POST", f"/agent/{repeat['id']}/actions",
                                   {"tool": "finish", "claim": "unresolved"},
                                   {"authorization": "Bearer "+created.body["agent_token"],
                                    "idempotency-key": "finish-repeat"}).status, 200)
        preview = self.call("GET", f"/runs/{repeat['id']}/research-preview").body["record"]
        self.assertTrue(preview["is_repeat"])
        self.assertNotIn(self.run, json.dumps(preview))

    def test_invalid_repeat_sources_fail_without_creating_runs(self):
        self.assertEqual(self.create(repeat_of=self.run).status, 409)
        self.finish()
        self.assertEqual(self.create(repeat_of=self.run, scenario="late_delivery").status, 422)
        self.assertEqual(self.create(repeat_of=self.run, agent_label="a", compare_with="b").status, 422)
        self.assertEqual(len(self.call("GET", "/runs").body["runs"]), 1)

    def test_missing_repeat_source_is_not_found(self):
        self.assertEqual(self.create(repeat_of="0"*32).status, 404)

    def test_agent_cannot_change_research_consent(self):
        agent = {"authorization": "Bearer "+self.token}
        self.assertEqual(self.call("PATCH", f"/runs/{self.run}/consent", {"consent": True}, agent).status, 403)
        self.assertFalse(self.call("GET", f"/runs/{self.run}").body["consent"])

    def test_deleted_source_cannot_be_previewed_or_repeated(self):
        self.finish()
        self.call("DELETE", f"/runs/{self.run}")
        self.assertEqual(self.call("GET", f"/runs/{self.run}/research-preview").status, 404)
        self.assertEqual(self.create(repeat_of=self.run).status, 404)
