"""Isolated OAuth redirect validation tests; no database access."""
import os
import sys
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sandbox_oauth import PREFIX, redirect_matches, redirect_valid, router
from main import app as api_app

ROOT = "https://testserver"
app = FastAPI()
app.include_router(router)


class OAuthRedirectValidationTests(unittest.TestCase):
    def test_accepts_https_and_explicit_loopback_callbacks(self):
        accepted = (
            "https://client.example/callback",
            "http://localhost:43123/oauth/callback",
            "http://127.0.0.1:34567/callback",
            "http://[::1]:34567/callback",
            # Port-less loopback: the client picks a free port at sign-in (RFC 8252).
            "http://localhost/callback",
            "http://127.0.0.1",
            "cursor://anysphere.cursor-mcp/oauth/callback",
        )
        for uri in accepted:
            with self.subTest(uri=uri):
                self.assertTrue(redirect_valid(uri))

    def test_rejects_non_loopback_or_unsafe_http_callbacks(self):
        rejected = (
            "http://example.org/callback",
            "http://localhost:0/oauth/callback",
            "http://localhost.evil:43123/oauth/callback",
            "http://127.0.0.2:43123/oauth/callback",
            "http://user@localhost:43123/oauth/callback",
            "http://localhost:43123/oauth/callback?next=x",
            "http://localhost:43123/oauth/callback#fragment",
            "https://*.example.org/callback",
            "javascript:alert(1)",
            "cursor://anysphere.cursor-mcp/oauth/callback/other",
            "cursor://evil.example/oauth/callback",
        )
        for uri in rejected:
            with self.subTest(uri=uri):
                self.assertFalse(redirect_valid(uri))

    def test_registration_accepts_gemini_localhost_callback_without_database(self):
        callback = "http://localhost:43123/oauth/callback"
        fake_db = object()
        with (
            patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}),
            patch("sandbox_oauth.Session") as session,
            patch("sandbox_oauth.add_record") as add_record,
            patch("sandbox_oauth.limit"),
        ):
            session.begin.return_value = nullcontext(fake_db)
            with TestClient(app) as client:
                response = client.post(
                    PREFIX + "/register",
                    json={"redirect_uris": [callback]},
                )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["redirect_uris"], [callback])
        add_record.assert_called_once()
        self.assertIs(add_record.call_args.args[0], fake_db)

    def test_loopback_callbacks_match_any_port_but_https_stays_exact(self):
        registered = ["https://vscode.dev/redirect", "http://127.0.0.1", "http://localhost/cb"]
        self.assertTrue(redirect_matches(registered, "https://vscode.dev/redirect"))
        self.assertTrue(redirect_matches(registered, "http://127.0.0.1:33418/"))
        self.assertTrue(redirect_matches(registered, "http://127.0.0.1:51234"))
        self.assertTrue(redirect_matches(registered, "http://localhost:9000/cb"))
        for wrong in ("https://vscode.dev/redirect/x", "https://evil.example/redirect",
                      "http://localhost:9000/other", "http://[::1]:33418/",
                      "http://127.0.0.1:33418/?x=1", "http://127.0.0.1:0/"):
            with self.subTest(uri=wrong):
                self.assertFalse(redirect_matches(registered, wrong))

    def test_registration_accepts_vscode_metadata(self):
        body = {
            "client_name": "Visual Studio Code", "client_uri": "https://code.visualstudio.com",
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "redirect_uris": ["https://insiders.vscode.dev/redirect", "https://vscode.dev/redirect",
                              "http://localhost", "http://127.0.0.1",
                              "http://localhost:33418", "http://127.0.0.1:33418"],
            "scope": "sandbox:act", "token_endpoint_auth_method": "none", "application_type": "native",
        }
        with (
            patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}),
            patch("sandbox_oauth.Session") as session,
            patch("sandbox_oauth.add_record"),
            patch("sandbox_oauth.limit"),
        ):
            session.begin.return_value = nullcontext(object())
            response = TestClient(app).post(PREFIX + "/register", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(len(response.json()["redirect_uris"]), 6)

    def test_authorization_server_metadata_advertises_registration_and_pkce(self):
        with patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}):
            response = TestClient(app).get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["registration_endpoint"], ROOT + PREFIX + "/register")
        self.assertIn("authorization_code", response.json()["grant_types_supported"])
        self.assertIn("S256", response.json()["code_challenge_methods_supported"])

    def test_rfc9728_root_metadata_uses_root_resource(self):
        with patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}):
            response = TestClient(app).get("/.well-known/oauth-protected-resource")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "resource": ROOT,
            "authorization_servers": [ROOT],
            "scopes_supported": ["sandbox:act"],
            "bearer_methods_supported": ["header"],
        })

    def test_rfc9728_path_metadata_resolves_existing_run(self):
        run_id = "a" * 32
        fake_db = SimpleNamespace(get=lambda model, key: SimpleNamespace(deletes=time.time() + 600))
        with (
            patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}),
            patch("sandbox_oauth.Session") as session,
        ):
            session.return_value.__enter__.return_value = fake_db
            response = TestClient(app).get(
                f"/.well-known/oauth-protected-resource/api/sandbox/agent/{run_id}/mcp"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resource"], ROOT + f"/api/sandbox/agent/{run_id}/mcp")
        self.assertEqual(response.json()["authorization_servers"], [ROOT])

    def test_unknown_run_and_well_known_api_routes_return_json_404(self):
        run_id = "b" * 32
        fake_db = SimpleNamespace(get=lambda model, key: None)
        with (
            patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}),
            patch("sandbox_oauth.Session") as session,
            patch("services.sandbox_security.limit"),
        ):
            session.return_value.__enter__.return_value = fake_db
            client = TestClient(api_app)
            responses = [
                client.get(f"/.well-known/oauth-protected-resource/api/sandbox/agent/{run_id}/mcp"),
                client.get("/.well-known/not-supported"),
                client.get("/api/not-supported"),
            ]

        for response in responses:
            with self.subTest(path=response.url.path):
                self.assertEqual(response.status_code, 404)
                self.assertTrue(response.headers["content-type"].startswith("application/json"))
                self.assertEqual(response.json()["detail"], "Not Found" if response.url.path != f"/.well-known/oauth-protected-resource/api/sandbox/agent/{run_id}/mcp" else "Protected resource not found.")


if __name__ == "__main__":
    unittest.main()
