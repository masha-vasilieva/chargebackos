"""Operator routes are not advertised in the public API schema but stay locked."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


class OperatorRoutesHiddenTest(unittest.TestCase):
    def test_operator_routes_absent_from_public_schema(self):
        client = TestClient(app)
        paths = client.get("/api/openapi.json").json()["paths"]
        self.assertFalse([p for p in paths if "/operator" in p])

    def test_operator_routes_still_require_token(self):
        client = TestClient(app)
        for method, path in (("GET", "/api/sandbox/operator/usage/daily"),
                             ("GET", "/api/sandbox/operator/health"),
                             ("DELETE", "/api/sandbox/operator/usage-history/expired")):
            self.assertIn(client.request(method, path).status_code, (401, 503), path)


if __name__ == "__main__":
    unittest.main()
