"""Keep public agent documentation aligned with the actual action interface."""
import json
import sys
import unittest
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox_api import Action
from services.sandbox_engine import TASK, TOOLS, SCENARIOS

PUBLIC = Path(__file__).resolve().parents[2] / "chargeback-os" / "public"


class AgentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (PUBLIC / "agent-contract.json").read_text()
        cls.contract = json.loads(cls.raw)
        cls.guide = (PUBLIC / "agent-guide.txt").read_text()

    def test_all_tools_match_server(self):
        self.assertEqual(
            [tool["name"] for tool in self.contract["tools"]], TOOLS
        )
        self.assertEqual(
            set(get_args(Action.model_fields["tool"].annotation)), set(TOOLS)
        )

    def test_task_matches_operator_authority(self):
        self.assertEqual(self.contract["task"], TASK)

    def test_documented_payloads_validate(self):
        alternatives = self.contract["request"]["body_schema"]["oneOf"]
        regular = alternatives[0]["properties"]["tool"]["enum"]
        self.assertEqual(set(regular), set(TOOLS) - {"finish"})
        for tool in regular:
            self.assertEqual(Action(tool=tool).tool, tool)
        claims = alternatives[1]["properties"]["claim"]["enum"]
        server_claims = get_args(get_args(Action.model_fields["claim"].annotation)[0])
        self.assertEqual(set(claims), set(server_claims))
        for claim in claims:
            self.assertEqual(Action(tool="finish", claim=claim).claim, claim)
        for schema in alternatives:
            self.assertFalse(schema["additionalProperties"])

    def test_documents_do_not_reveal_scenario_identifiers(self):
        for scenario in SCENARIOS:
            self.assertNotIn(scenario["id"], self.raw)
            self.assertNotIn(scenario["id"], self.guide)

    def test_documents_explain_time_and_retry_boundaries(self):
        self.assertIn("not simulation-time-neutral", self.contract["time_model"])
        self.assertIn("identical key", self.contract["retry_policy"]["unknown_outcome"])
        self.assertIn("MCP Streamable HTTP", self.contract["transport"])
        self.assertIn("hidden chain-of-thought", self.contract["data_policy"])
        self.assertLess(len(self.raw.encode()), 12_000)
