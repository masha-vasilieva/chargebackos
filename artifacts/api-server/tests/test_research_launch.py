import time
import json
import unittest
from types import SimpleNamespace
from export_research import summarize
from services.sandbox_engine import initial, SCORER_VERSION, LEGACY_SCORER_VERSION


class ResearchLaunchTests(unittest.TestCase):
    def row(self, **extra):
        return SimpleNamespace(consent=True, deletes=time.time()+60, created=time.time(), owner="private-owner",
                               data={"scenario": "non_delivery", "mode": "practice", "state": initial(),
                                     "events": [], **extra})

    def test_simulator_and_scorer_versions_never_pool(self):
        result = summarize([
            self.row(version="simulator-a", scorer_version=LEGACY_SCORER_VERSION),
            self.row(version="simulator-b", scorer_version=LEGACY_SCORER_VERSION),
            self.row(version="simulator-a", scorer_version=SCORER_VERSION),
        ])
        expected = {
            ("simulator-a", LEGACY_SCORER_VERSION),
            ("simulator-b", LEGACY_SCORER_VERSION),
            ("simulator-a", SCORER_VERSION),
        }
        self.assertEqual(
            {(row["simulator_version"], row["scorer_version"]) for row in result["counts"]},
            expected,
        )
        self.assertTrue(all(row["count"] == 1 for row in result["counts"]))
        self.assertEqual(
            {(row["simulator_version"], row["scorer_version"]) for row in result["behavior_counts"]},
            expected,
        )

    def test_summary_omits_identity_trace_and_rankings(self):
        row = self.row(
            version="simulator-a",
            agent_label="PRIVATE-LABEL",
            agent_token="PRIVATE-TOKEN",
            private_trace="PRIVATE-TRACE-BODY",
            model_rankings=["model-a", "model-b"],
            context={"client": "claude_code", "model_family": "gpt"},
        )
        result = summarize([row])
        serialized = json.dumps(result)
        for private_value in (
            "private-owner", "PRIVATE-LABEL", "PRIVATE-TOKEN", "PRIVATE-TRACE-BODY",
            "model_rankings", "model-a", "model-b",
        ):
            self.assertNotIn(private_value, serialized)
        self.assertEqual(result["behavior_counts"][0]["client_self_reported"], "claude_code")

    def test_withdrawal_and_expiry_exclude_context_and_counts(self):
        withdrawn = self.row(context={"client": "claude_code"})
        withdrawn.consent = False
        expired = self.row()
        expired.deletes = time.time()-1
        result = summarize([withdrawn, expired])
        self.assertEqual(result["consented_runs"], 0)
        self.assertEqual(result["behavior_counts"], [])
        self.assertEqual(result["counts"], [])
