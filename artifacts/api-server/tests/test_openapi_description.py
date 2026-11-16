"""The public API description names the optional PayPal sandbox rail honestly."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


class OpenApiDescriptionTest(unittest.TestCase):
    def test_description_mentions_paypal_sandbox_rail_without_endorsement(self):
        text = TestClient(app).get("/api/openapi.json").json()["info"]["description"]
        self.assertIn("No real payments", text)
        self.assertIn("paypal_sandbox", text)
        self.assertIn("not affiliated with or endorsed by PayPal", text)
