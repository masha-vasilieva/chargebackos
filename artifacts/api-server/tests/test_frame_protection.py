"""API pages must refuse framing, and every retention step must keep its label."""
import asyncio
import unittest
from unittest.mock import patch

import sandbox_test_support  # noqa: F401  (puts the API server on sys.path)
from services import sandbox_security


def app_with_headers(headers):
    async def respond(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})
    return respond


def raw_response_headers(app, path):
    """Return the raw header list, so duplicate headers stay visible."""
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345), "headers": [(b"host", b"testserver")],
    }
    asyncio.run(asyncio.wait_for(app(scope, receive, send), timeout=10))
    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["headers"]


def values(headers, name):
    return [value for key, value in headers if key.lower() == name]


class FrameProtectionTests(unittest.TestCase):
    def fetch(self, headers, path):
        ingress = sandbox_security.Ingress(app_with_headers(headers))
        with patch.object(sandbox_security, "limit"):
            return raw_response_headers(ingress, path)

    def test_api_pages_refuse_framing(self):
        headers = self.fetch([(b"content-type", b"text/html")], "/api/docs")
        self.assertEqual(values(headers, b"x-frame-options"), [b"DENY"])
        self.assertEqual(values(headers, b"content-security-policy"),
                         [b"frame-ancestors 'none'"])

    def test_route_policy_is_kept_without_duplicates(self):
        own_policy = b"default-src 'none'; frame-ancestors 'none'"
        headers = self.fetch([
            (b"content-type", b"text/html"),
            (b"Content-Security-Policy", own_policy),
            (b"X-Frame-Options", b"DENY"),
        ], "/api/page-with-own-policy")
        self.assertEqual(values(headers, b"content-security-policy"), [own_policy])
        self.assertEqual(values(headers, b"x-frame-options"), [b"DENY"])


class RetentionStepLabelTests(unittest.TestCase):
    def test_research_event_expiry_failures_keep_their_step_label(self):
        error = RuntimeError("private detail")
        error._retention_cleanup_step = "research_event_expiry"
        step, _error_type, _sqlstate = sandbox_security.retention_failure_fields(error)
        self.assertEqual(step, "research_event_expiry")


if __name__ == "__main__":
    unittest.main()
