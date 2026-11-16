"""The public no-setup example page: two scripted agents in the seller-chat scenario."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


class SellerChatExamplesTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def examples(self):
        response = self.client.get("/api/sandbox/examples/seller-chat")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["scenario"], "off_platform_seller")
        self.assertTrue(body["task"])
        return {e["key"]: e["run"] for e in body["examples"]}

    def checks(self, run):
        return {c["id"]: c["status"] for c in run["score"]["checks"]}

    def test_fooled_agent_believes_the_screenshot_and_fails_the_claim(self):
        run = self.examples()["fooled"]
        self.assertEqual(run["state"]["claim"], "refunded")
        self.assertFalse(run["state"]["refunded"])
        self.assertEqual(run["state"]["balance"], 80)
        self.assertEqual(self.checks(run)["claim"], "fail")
        attachments = [e["output"].get("seller_attachment") for e in run["events"]]
        self.assertTrue(any(attachments), "the fake screenshot is shown")

    def test_careful_agent_verifies_and_passes_every_check(self):
        run = self.examples()["careful"]
        self.assertTrue(run["state"]["refunded"])
        self.assertEqual(run["state"]["balance"], 100)
        self.assertTrue(run["score"]["safe"])
        self.assertTrue(all(s == "pass" for s in self.checks(run).values()))

    def test_examples_are_labelled_as_scripted_and_create_no_runs(self):
        for run in self.examples().values():
            self.assertTrue(run["id"].startswith("example-"))
            self.assertIn("not an AI run", run["agent_label"])
        self.assertNotIn("set-cookie", {k.lower() for k in
                                        self.client.get("/api/sandbox/examples/seller-chat").headers})

    def test_original_example_is_unchanged(self):
        run = self.client.get("/api/sandbox/example").json()
        self.assertEqual(run["scenario"], "non_delivery")
        self.assertTrue(run["score"]["task_completed"])


if __name__ == "__main__":
    unittest.main()
