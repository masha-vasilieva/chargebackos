"""OAuth security regression tests against disposable PostgreSQL records."""
import base64
import copy
import concurrent.futures
import hashlib
import os
import re
import secrets
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from database import Session
from sandbox_api import router as api
from sandbox_mcp import router as mcp
from sandbox_oauth import router, PREFIX
from services.sandbox_engine import initial
from services.sandbox_oauth_store import OAuthRecord, record
from services.sandbox_store import Owner, Run, digest

app = FastAPI()
for routes in (api, mcp, router):
    app.include_router(routes)
ROOT = "https://testserver"


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ROOT}),
                        patch("sandbox_oauth.limit"), patch("sandbox_api.limit"),
                        patch("sandbox_mcp.limit"),
                        # These protocol tests use disposable OAuth rows and
                        # do not exercise the separate retention intake gate.
                        patch("services.sandbox_oauth_store.reserve_auxiliary")]
        for p in self.patches:
            p.start()
        self.browser = TestClient(app, base_url=ROOT)
        self.agent = TestClient(app, base_url=ROOT)
        self.owner_secret = secrets.token_urlsafe(32)
        self.owner = digest(self.owner_secret)
        self.other_owner = secrets.token_hex(32)
        self.extra_owners = []
        self.run = secrets.token_hex(16)
        self.extra_runs = []
        self.extra_requests = []
        self.bearer = secrets.token_urlsafe(32)
        self.browser.cookies.set("sandbox_owner", self.owner_secret)
        self.headers = {"Origin": ROOT, "X-Sandbox-Client": "browser"}
        self.resource = ROOT + f"/api/sandbox/agent/{self.run}/mcp"
        self.stable_resource = ROOT + "/mcp"
        self.redirect = "http://127.0.0.1:34567/callback"
        self.verifier = secrets.token_urlsafe(48)
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")
        with Session.begin() as db:
            db.add(Owner(id=self.owner, expires=time.time()+3600))
            db.add(Owner(id=self.other_owner, expires=time.time()+3600))
            db.add(Run(id=self.run, owner=self.owner, agent_hash=digest(self.bearer),
                       created=time.time(), expires=time.time()+1800, deletes=time.time()+86400,
                       consent=False, data={"state": initial(), "events": [], "idempotency": {},
                                            "scenario": "non_delivery"}))
        self.client_id = self.agent.post(PREFIX+"/register", json={
            "redirect_uris": [self.redirect], "client_name": "<unverified>"}).json()["client_id"]
        self.client_ids = [self.client_id]
        self.params = {"client_id": self.client_id, "redirect_uri": self.redirect,
                       "response_type": "code", "state": "client-state", "scope": "sandbox:act",
                       "resource": self.resource, "code_challenge": self.challenge,
                       "code_challenge_method": "S256"}

    def tearDown(self):
        with Session.begin() as db:
            db.execute(delete(OAuthRecord).where(
                (OAuthRecord.data["run"].astext.in_([self.run, *self.extra_runs])) |
                (OAuthRecord.id.in_(["client:"+digest(item) for item in self.client_ids])) |
                (OAuthRecord.id.in_(["request:"+digest(item) for item in self.extra_requests]))))
            db.execute(delete(Run).where(Run.id.in_([self.run, *self.extra_runs])))
            db.execute(delete(Owner).where(Owner.id.in_([self.owner, self.other_owner,
                                                        *self.extra_owners])))
        self.browser.close()
        self.agent.close()
        for p in reversed(self.patches):
            p.stop()

    def pending(self, **overrides):
        response = self.agent.get(PREFIX+"/authorize", params={**self.params, **overrides})
        self.assertEqual(response.status_code, 200, response.text)
        request_id = re.search(r"/requests/([A-Za-z0-9_-]+)", response.text)[1]
        self.extra_requests.append(request_id)
        return request_id

    def code(self):
        pending = self.pending()
        result = self.browser.post(PREFIX+"/requests/"+pending, headers=self.headers,
                                   json={"approve": True})
        self.assertEqual(result.status_code, 200, result.text)
        query = parse_qs(urlsplit(result.json()["redirect"]).query)
        self.assertEqual(query["state"], ["client-state"])
        return query["code"][0]

    def exchange(self, code, **overrides):
        return self.agent.post(PREFIX+"/token", data={
            "grant_type": "authorization_code", "code": code,
            "code_verifier": self.verifier, "client_id": self.client_id,
            "redirect_uri": self.redirect, "resource": self.resource, **overrides})

    def refresh(self, refresh_token, **overrides):
        return self.agent.post(PREFIX+"/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": self.client_id, "resource": self.resource, **overrides})

    def enable_refresh_client(self):
        response = self.agent.post(PREFIX+"/register", json={
            "redirect_uris": [self.redirect], "client_name": "Refresh test client",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.client_id = response.json()["client_id"]
        self.client_ids.append(self.client_id)
        self.params["client_id"] = self.client_id

    def ping(self, token):
        return self.agent.post(self.resource, headers={
            "Authorization": "Bearer "+token, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    def stable_ping(self, token):
        return self.agent.post("/mcp", headers={
            "Authorization": "Bearer "+token, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})

    def test_stable_resource_metadata_and_auth_challenge_are_json(self):
        challenge = self.agent.post("/mcp", headers={
            "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(challenge.status_code, 401)
        self.assertTrue(challenge.headers["content-type"].startswith("application/json"))
        self.assertIn("resource_metadata", challenge.headers["www-authenticate"])
        self.assertIn("/.well-known/oauth-protected-resource/mcp",
                      challenge.headers["www-authenticate"])

        root_metadata = self.agent.get("/.well-known/oauth-protected-resource")
        self.assertEqual(root_metadata.status_code, 200, root_metadata.text)
        self.assertEqual(root_metadata.json()["resource"], ROOT)
        response = self.agent.get("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        metadata = response.json()
        self.assertEqual(metadata["resource"], self.stable_resource)
        self.assertEqual(metadata["authorization_servers"], [ROOT])
        unknown = self.agent.get(
            "/.well-known/oauth-protected-resource/api/sandbox/agent/"
            + "f" * 32 + "/mcp")
        self.assertEqual(unknown.status_code, 404)
        self.assertTrue(unknown.headers["content-type"].startswith("application/json"))

    def test_stable_mcp_run_creation_expiry_reauth_and_finish_flow(self):
        with Session.begin() as db:
            db.get(Run, self.run).expires = time.time() - 1
        self.enable_refresh_client()

        authorize_params = {**self.params, "resource": self.stable_resource}
        authorize_response = self.agent.get(PREFIX + "/authorize", params=authorize_params)
        self.assertEqual(authorize_response.status_code, 200, authorize_response.text)
        self.assertIn("const stableRequest=true", authorize_response.text)
        pending = re.search(r"/requests/([A-Za-z0-9_-]+)", authorize_response.text)[1]
        self.extra_requests.append(pending)
        details = self.browser.get(PREFIX + "/requests/" + pending)
        self.assertEqual(details.status_code, 200, details.text)
        self.assertTrue(details.json()["stable"])
        self.assertEqual(details.json()["runs"], [])

        # The approval-page run-creation path reuses regular launch policy but
        # deliberately never returns its otherwise one-time bearer credential.
        with (patch("sandbox_api.reserve"), patch("sandbox_api.record_persistent_usage"),
              patch("sandbox_api.record_usage_count")):
            created = self.browser.post(
                PREFIX + "/requests/" + pending + "/create-run",
                headers=self.headers, json={"scenario": "non_delivery"})
        self.assertEqual(created.status_code, 200, created.text)
        self.assertNotIn("agent_token", created.json())
        first_run = created.json()["run"]["id"]
        self.extra_runs.append(first_run)
        details = self.browser.get(PREFIX + "/requests/" + pending).json()
        self.assertEqual([run["run_id"] for run in details["runs"]], [first_run])
        decision = self.browser.post(
            PREFIX + "/requests/" + pending, headers=self.headers,
            json={"approve": True, "run_id": first_run})
        self.assertEqual(decision.status_code, 200, decision.text)
        code = parse_qs(urlsplit(decision.json()["redirect"]).query)["code"][0]
        token_response = self.exchange(code, resource=self.stable_resource)
        self.assertEqual(token_response.status_code, 200, token_response.text)
        token = token_response.json()["access_token"]
        refresh_token = token_response.json()["refresh_token"]
        self.assertLessEqual(token_response.json()["expires_in"], 600)
        wrong_audience = self.agent.post(
            f"/api/sandbox/agent/{first_run}/mcp", headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(wrong_audience.status_code, 401,
                         "A stable-resource token must not be accepted by a run-specific MCP URL.")
        self.assertEqual(self.stable_ping(self.bearer).status_code, 401,
                         "A direct run bearer token must not authenticate at the stable OAuth endpoint.")

        with Session.begin() as db:
            record(db, "token", token).expires = time.time() - 1
        self.assertEqual(self.stable_ping(token).status_code, 401)
        renewed = self.refresh(refresh_token, resource=self.stable_resource)
        self.assertEqual(renewed.status_code, 200, renewed.text)
        token = renewed.json()["access_token"]
        refresh_token = renewed.json()["refresh_token"]
        self.assertEqual(self.stable_ping(token).status_code, 200)

        tool_response = self.agent.post("/mcp", headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "inspect_offer",
                             "arguments": {"idempotency_key": "stable-first"}}})
        self.assertEqual(tool_response.status_code, 200, tool_response.text)
        self.assertIn("result", tool_response.json())

        with Session.begin() as db:
            db.get(Run, first_run).expires = time.time() - 1
        expired = self.stable_ping(token)
        self.assertEqual(expired.status_code, 401, expired.text)
        self.assertIn("/.well-known/oauth-protected-resource/mcp",
                      expired.headers["www-authenticate"])
        self.assertTrue(expired.headers["content-type"].startswith("application/json"))
        expired_refresh = self.refresh(refresh_token, resource=self.stable_resource)
        self.assertEqual(expired_refresh.status_code, 400)
        self.assertEqual(expired_refresh.json(), {"error": "invalid_grant"})

        replacement = secrets.token_hex(16)
        replacement_bearer = secrets.token_urlsafe(32)
        with Session.begin() as db:
            db.add(Run(id=replacement, owner=self.owner, agent_hash=digest(replacement_bearer),
                       created=time.time(), expires=time.time()+1800, deletes=time.time()+86400,
                       consent=False, data={"state": initial(), "events": [], "idempotency": {},
                                            "scenario": "late_delivery",
                                            "agent_label": "replacement test run"}))
        self.extra_runs.append(replacement)

        reauth = self.agent.get(PREFIX + "/authorize", params=authorize_params)
        self.assertEqual(reauth.status_code, 200, reauth.text)
        pending_again = re.search(r"/requests/([A-Za-z0-9_-]+)", reauth.text)[1]
        self.extra_requests.append(pending_again)
        choices = self.browser.get(PREFIX + "/requests/" + pending_again).json()["runs"]
        self.assertEqual([run["run_id"] for run in choices], [replacement])
        decision = self.browser.post(
            PREFIX + "/requests/" + pending_again, headers=self.headers,
            json={"approve": True, "run_id": replacement})
        self.assertEqual(decision.status_code, 200, decision.text)
        code = parse_qs(urlsplit(decision.json()["redirect"]).query)["code"][0]
        replacement_token_response = self.exchange(code, resource=self.stable_resource)
        self.assertEqual(replacement_token_response.status_code, 200,
                         replacement_token_response.text)
        replacement_token = replacement_token_response.json()["access_token"]
        replacement_refresh = replacement_token_response.json()["refresh_token"]
        self.assertEqual(self.stable_ping(replacement_token).status_code, 200)

        finish = self.agent.post("/mcp", headers={
            "Authorization": "Bearer " + replacement_token,
            "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "finish",
                             "arguments": {"idempotency_key": "stable-finish",
                                           "claim": "unresolved"}}})
        self.assertEqual(finish.status_code, 200, finish.text)
        closed = self.stable_ping(replacement_token)
        self.assertEqual(closed.status_code, 401, closed.text)
        self.assertIn("/.well-known/oauth-protected-resource/mcp",
                      closed.headers["www-authenticate"])
        closed_refresh = self.refresh(replacement_refresh, resource=self.stable_resource)
        self.assertEqual(closed_refresh.status_code, 400)
        self.assertEqual(closed_refresh.json(), {"error": "invalid_grant"})

    def test_no_owner_approval_can_create_run_and_approve_without_extending_request(self):
        authorize_response = self.agent.get(
            PREFIX + "/authorize",
            params={**self.params, "resource": self.stable_resource})
        self.assertEqual(authorize_response.status_code, 200, authorize_response.text)
        self.assertIn("Create a run", authorize_response.text)
        self.assertIn("open this link in the browser where you created your run", authorize_response.text)
        pending = re.search(r"/requests/([A-Za-z0-9_-]+)", authorize_response.text)[1]
        self.extra_requests.append(pending)
        no_owner_browser = TestClient(app, base_url=ROOT)
        try:
            unavailable = no_owner_browser.get(PREFIX + "/requests/" + pending)
            self.assertEqual(unavailable.status_code, 401)

            owner_response = no_owner_browser.post(
                "/api/sandbox/owner", headers=self.headers)
            self.assertEqual(owner_response.status_code, 200, owner_response.text)
            self.extra_owners.append(digest(no_owner_browser.cookies.get("sandbox_owner")))
            empty = no_owner_browser.get(PREFIX + "/requests/" + pending)
            self.assertEqual(empty.status_code, 200, empty.text)
            self.assertEqual(empty.json()["runs"], [])

            with Session() as db:
                pending_expiry = record(db, "request", pending).expires
            with (patch("sandbox_api.reserve"), patch("sandbox_api.record_persistent_usage"),
                  patch("sandbox_api.record_usage_count")):
                created = no_owner_browser.post(
                    PREFIX + "/requests/" + pending + "/create-run",
                    headers=self.headers, json={"scenario": "non_delivery"})
            self.assertEqual(created.status_code, 200, created.text)
            self.assertNotIn("agent_token", created.json())
            run_id = created.json()["run"]["id"]
            self.extra_runs.append(run_id)
            with Session() as db:
                self.assertEqual(record(db, "request", pending).expires, pending_expiry)

            details = no_owner_browser.get(PREFIX + "/requests/" + pending)
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual([run["run_id"] for run in details.json()["runs"]], [run_id])
            decision = no_owner_browser.post(
                PREFIX + "/requests/" + pending, headers=self.headers,
                json={"approve": True, "run_id": run_id})
            self.assertEqual(decision.status_code, 200, decision.text)
            code = parse_qs(urlsplit(decision.json()["redirect"]).query)["code"][0]
            token_response = self.exchange(code, resource=self.stable_resource)
            self.assertEqual(token_response.status_code, 200, token_response.text)
            self.assertEqual(
                self.stable_ping(token_response.json()["access_token"]).status_code, 200)
        finally:
            no_owner_browser.close()

    def test_expired_approval_request_returns_clear_json_error(self):
        authorize_response = self.agent.get(
            PREFIX + "/authorize",
            params={**self.params, "resource": self.stable_resource})
        pending = re.search(r"/requests/([A-Za-z0-9_-]+)", authorize_response.text)[1]
        self.extra_requests.append(pending)
        with Session.begin() as db:
            record(db, "request", pending).expires = time.time() - 1
        response = self.browser.get(PREFIX + "/requests/" + pending)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json(), {"error": "authorization_request_expired"})

    def test_complete_flow_and_owner_boundaries(self):
        pending = self.pending()
        self.assertEqual(self.agent.get(PREFIX+"/requests/"+pending).status_code, 401)
        self.assertEqual(self.browser.get(PREFIX+"/requests/"+pending).status_code, 200)
        self.assertEqual(self.browser.post(PREFIX+"/requests/"+pending,
                         json={"approve": True}).status_code, 403)
        response = self.exchange(self.code())
        self.assertEqual(response.status_code, 200, response.text)
        token = response.json()["access_token"]
        self.assertLessEqual(response.json()["expires_in"], 600)
        self.assertNotIn("refresh_token", response.json())
        self.assertEqual(self.ping(token).status_code, 200)
        self.assertEqual(self.ping(self.bearer).status_code, 200)
        self.assertEqual(self.agent.get("/api/sandbox/runs/"+self.run,
                         headers={"Authorization": "Bearer "+token}).status_code, 401)
        self.assertEqual(self.agent.patch("/api/sandbox/runs/"+self.run+"/consent",
                         headers={**self.headers, "Authorization": "Bearer "+token},
                         json={"consent": True}).status_code, 401)
        with Session() as db:
            self.assertFalse(db.get(Run, self.run).consent)
        self.assertFalse(self.agent.cookies)

    def test_fabricated_refresh_token_is_rejected_without_mutation(self):
        action = self.agent.post(
            f"/api/sandbox/agent/{self.run}/actions",
            headers={"Authorization": "Bearer " + self.bearer,
                     "Idempotency-Key": "before-refresh"},
            json={"tool": "purchase"})
        self.assertEqual(action.status_code, 200, action.text)

        access_token = self.exchange(self.code()).json()["access_token"]
        with Session.begin() as db:
            access_record = record(db, "token", access_token)
            self.assertIsNotNone(access_record)
            # Leave an active, short-lived token so an accidental renewal cannot
            # pass by merely returning the existing expiry unchanged.
            access_record.expires = time.time() + 120

        def snapshot():
            with Session() as db:
                run = db.get(Run, self.run)
                records = db.scalars(select(OAuthRecord).where(
                    OAuthRecord.data["run"].astext == self.run)).all()
                return {
                    "state": copy.deepcopy(run.data["state"]),
                    "events": copy.deepcopy(run.data["events"]),
                    "idempotency": copy.deepcopy(run.data["idempotency"]),
                    "consent": run.consent,
                    "lifetimes": (run.created, run.expires, run.deletes),
                    "oauth_records": {
                        item.id: (item.expires, copy.deepcopy(item.data))
                        for item in records
                    },
                }

        before = snapshot()
        self.assertTrue(before["events"])
        self.assertTrue(before["idempotency"])
        self.assertFalse(before["consent"])
        self.assertEqual(len(before["oauth_records"]), 1)
        fabricated_refresh = "fabricated-refresh-" + secrets.token_urlsafe(32)

        response = self.agent.post(PREFIX + "/token", data={
            "grant_type": "refresh_token",
            "refresh_token": fabricated_refresh,
            "client_id": self.client_id,
            "resource": self.resource,
        })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json().get("error"), "invalid_grant")
        self.assertNotIn("access_token", response.json())
        self.assertNotIn("refresh_token", response.json())
        self.assertEqual(snapshot(), before)

    def test_authorize_requires_exact_redirect_and_valid_parameters(self):
        for changes in ({"redirect_uri": self.redirect+"/other"},
                        {"code_challenge_method": "plain"}, {"scope": "owner"},
                        {"resource": "https://other.test/api/sandbox/agent/"+self.run+"/mcp"}):
            self.assertEqual(self.agent.get(PREFIX+"/authorize",
                             params={**self.params, **changes}).status_code, 400)

    def test_approval_page_offers_the_task_to_copy_before_approving(self):
        page = self.agent.get(PREFIX + "/authorize", params=self.params)
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="task-box"', page.text)
        self.assertIn('fetch("/api/sandbox/scenarios"', page.text)
        self.assertIn("Use the chargeback-os MCP tools for this task.", page.text)
        self.assertLess(page.text.index('id="task-box"'), page.text.index('id="approve"'))
        self.extra_requests.append(re.search(r"/requests/([A-Za-z0-9_-]+)", page.text)[1])

    def test_vscode_style_portless_loopback_completes_sign_in_on_any_port(self):
        response = self.agent.post(PREFIX + "/register", json={
            "client_name": "Visual Studio Code", "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "application_type": "native",
            "redirect_uris": ["https://insiders.vscode.dev/redirect", "https://vscode.dev/redirect",
                              "http://localhost", "http://127.0.0.1",
                              "http://localhost:33418", "http://127.0.0.1:33418"]})
        self.assertEqual(response.status_code, 201, response.text)
        self.client_id = response.json()["client_id"]
        self.client_ids.append(self.client_id)
        self.redirect = "http://127.0.0.1:51234/"
        self.params = {**self.params, "client_id": self.client_id, "redirect_uri": self.redirect}
        token = self.exchange(self.code())
        self.assertEqual(token.status_code, 200, token.text)
        self.assertEqual(self.ping(token.json()["access_token"]).status_code, 200)
        # A different redirect than the one approved still fails at token exchange.
        self.assertEqual(self.exchange(self.code(), redirect_uri="http://127.0.0.1:9999/").status_code, 400)
        # Non-loopback addresses still need an exact match.
        self.assertEqual(self.agent.get(PREFIX + "/authorize", params={
            **self.params, "redirect_uri": "https://vscode.dev/redirect/extra"}).status_code, 400)

    def test_sdk_registration_with_refresh_metadata_completes_pkce_refresh_rotation_and_mcp(self):
        response = self.agent.post(PREFIX + "/register", json={
            "redirect_uris": [self.redirect],
            "client_name": "Claude Code",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_uri": "https://client.example",
            "logo_uri": "https://client.example/logo.png",
            "scope": "sandbox:act",
            "software_id": "claude-code",
            "software_version": "test",
            "unknown_harmless_metadata": {"ignored": True},
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        registered = response.json()
        self.assertEqual(registered["grant_types"], ["authorization_code", "refresh_token"])
        for unsupported in ("client_uri", "logo_uri", "scope", "software_id",
                            "software_version", "unknown_harmless_metadata"):
            self.assertNotIn(unsupported, registered)

        self.client_id = registered["client_id"]
        self.client_ids.append(self.client_id)
        self.params["client_id"] = self.client_id
        authorize_response = self.agent.get(PREFIX + "/authorize", params=self.params)
        self.assertEqual(authorize_response.status_code, 200, authorize_response.text)
        self.assertIn("text/html", authorize_response.headers["content-type"])
        pending = re.search(r"/requests/([A-Za-z0-9_-]+)", authorize_response.text)[1]
        decision = self.browser.post(
            PREFIX + "/requests/" + pending,
            headers=self.headers,
            json={"approve": True},
        )
        self.assertEqual(decision.status_code, 200, decision.text)
        self.assertTrue(decision.headers["content-type"].startswith("application/json"))
        authorization_code = parse_qs(urlsplit(decision.json()["redirect"]).query)["code"][0]

        token_response = self.exchange(authorization_code)
        self.assertEqual(token_response.status_code, 200, token_response.text)
        self.assertTrue(token_response.headers["content-type"].startswith("application/json"))
        token_data = token_response.json()
        self.assertIn("refresh_token", token_data)
        mcp_response = self.ping(token_data["access_token"])
        self.assertEqual(mcp_response.status_code, 200, mcp_response.text)
        self.assertTrue(mcp_response.headers["content-type"].startswith("application/json"))
        self.assertEqual(mcp_response.json()["result"], {})

        first_refresh = token_data["refresh_token"]
        with Session.begin() as db:
            record(db, "token", token_data["access_token"]).expires = time.time() - 1
            self.assertLessEqual(
                record(db, "refresh", first_refresh).expires,
                db.get(Run, self.run).expires)
        self.assertEqual(self.ping(token_data["access_token"]).status_code, 401)
        wrong_resource = self.refresh(first_refresh, resource=self.stable_resource)
        self.assertEqual(wrong_resource.status_code, 400)
        self.assertEqual(wrong_resource.json(), {"error": "invalid_grant"})
        wrong_client = self.refresh(first_refresh, client_id="other-client")
        self.assertEqual(wrong_client.status_code, 400)
        with Session() as db:
            self.assertFalse(record(db, "refresh", first_refresh).data["used"])
        rotated = self.refresh(first_refresh)
        self.assertEqual(rotated.status_code, 200, rotated.text)
        rotated_data = rotated.json()
        self.assertNotEqual(rotated_data["refresh_token"], first_refresh)
        self.assertLessEqual(rotated_data["expires_in"], 600)
        self.assertEqual(self.ping(rotated_data["access_token"]).status_code, 200)

        second_rotation = self.refresh(rotated_data["refresh_token"])
        self.assertEqual(second_rotation.status_code, 200, second_rotation.text)
        replay = self.refresh(first_refresh)
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json(), {"error": "invalid_grant"})
        self.assertEqual(self.ping(second_rotation.json()["access_token"]).status_code, 401)
        self.assertEqual(
            self.refresh(second_rotation.json()["refresh_token"]).json(),
            {"error": "invalid_grant"})

    def test_dashboard_disconnect_and_credential_rotation_revoke_refresh_grants(self):
        self.enable_refresh_client()
        issued = self.exchange(self.code()).json()
        grant_url = f"/api/sandbox/runs/{self.run}/oauth-grants/{self.client_id}"
        disconnected = self.browser.delete(grant_url, headers=self.headers)
        self.assertEqual(disconnected.status_code, 200, disconnected.text)
        self.assertEqual(disconnected.json(), {"revoked": True})
        self.assertEqual(self.refresh(issued["refresh_token"]).json(), {"error": "invalid_grant"})
        self.assertEqual(self.ping(issued["access_token"]).status_code, 401)

        issued_again = self.exchange(self.code()).json()
        rotated = self.browser.post(
            f"/api/sandbox/runs/{self.run}/token", headers=self.headers)
        self.assertEqual(rotated.status_code, 200, rotated.text)
        self.assertEqual(
            self.refresh(issued_again["refresh_token"]).json(),
            {"error": "invalid_grant"})
        self.assertEqual(self.ping(issued_again["access_token"]).status_code, 401)

    def test_concurrent_refresh_reuse_revokes_the_rotated_grant(self):
        self.enable_refresh_client()
        issued = self.exchange(self.code()).json()

        def refresh_once():
            with TestClient(app, base_url=ROOT) as client:
                return client.post(PREFIX + "/token", data={
                    "grant_type": "refresh_token",
                    "refresh_token": issued["refresh_token"],
                    "client_id": self.client_id,
                    "resource": self.resource,
                })

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: refresh_once(), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 400])
        successful = next(response.json() for response in responses
                          if response.status_code == 200)
        self.assertEqual(
            self.refresh(successful["refresh_token"]).json(),
            {"error": "invalid_grant"})
        self.assertEqual(self.ping(successful["access_token"]).status_code, 401)

    def test_per_run_endpoint_rechallenges_after_finish_and_refresh_fails(self):
        self.enable_refresh_client()
        issued = self.exchange(self.code()).json()
        finished = self.agent.post(self.resource, headers={
            "Authorization": "Bearer " + issued["access_token"],
            "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "finish",
                             "arguments": {"idempotency_key": "finished-run",
                                           "claim": "unresolved"}}})
        self.assertEqual(finished.status_code, 200, finished.text)
        refresh = self.refresh(issued["refresh_token"])
        self.assertEqual(refresh.status_code, 400)
        self.assertEqual(refresh.json(), {"error": "invalid_grant"})
        challenge = self.ping(issued["access_token"])
        self.assertEqual(challenge.status_code, 401, challenge.text)
        self.assertIn(
            f"/api/sandbox/oauth/resource/{self.run}",
            challenge.headers["www-authenticate"])

    def test_cursor_registration_keeps_supported_callbacks_and_signs_in_via_app_link(self):
        cursor_link = "cursor://anysphere.cursor-mcp/oauth/callback"
        response = self.agent.post(PREFIX + "/register", json={
            "client_name": "Cursor", "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
            "redirect_uris": [cursor_link, "https://www.cursor.com/agents/mcp/oauth/callback",
                              "http://localhost:8787/callback", "cursor://other.app/steal",
                              "javascript:alert(1)"]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["redirect_uris"], [
            cursor_link, "https://www.cursor.com/agents/mcp/oauth/callback",
            "http://localhost:8787/callback"])
        self.client_id = response.json()["client_id"]
        self.client_ids.append(self.client_id)
        self.redirect = cursor_link
        self.params = {**self.params, "client_id": self.client_id, "redirect_uri": cursor_link}
        token = self.exchange(self.code())
        self.assertEqual(token.status_code, 200, token.text)
        self.assertEqual(self.ping(token.json()["access_token"]).status_code, 200)
        # A dropped callback can never be used at sign-in.
        self.assertEqual(self.agent.get(PREFIX + "/authorize", params={
            **self.params, "redirect_uri": "cursor://other.app/steal"}).status_code, 400)

    def test_registration_with_no_supported_callback_is_rejected(self):
        response = self.agent.post(PREFIX + "/register", json={
            "redirect_uris": ["cursor://other.app/cb", "http://example.org/cb"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "invalid_client_metadata"})

    def test_registration_rejects_refresh_only_or_unknown_grants(self):
        for grant_types in (["refresh_token"], ["authorization_code", "client_credentials"]):
            with self.subTest(grant_types=grant_types):
                response = self.agent.post(PREFIX + "/register", json={
                    "redirect_uris": [self.redirect],
                    "grant_types": grant_types,
                })
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"error": "invalid_client_metadata"})

    def test_owner_grant_list_and_selective_disconnect(self):
        started = time.time()
        first = self.exchange(self.code()).json()["access_token"]
        second = self.exchange(self.code()).json()["access_token"]
        original_client = self.client_id
        other_client = self.agent.post(PREFIX+"/register", json={
            "redirect_uris": [self.redirect], "client_name": "<unverified>"}).json()["client_id"]
        try:
            self.client_id = other_client
            self.params["client_id"] = other_client
            third = self.exchange(self.code()).json()["access_token"]
        finally:
            self.client_id = original_client
            self.params["client_id"] = original_client
            with Session.begin() as db:
                db.delete(record(db, "client", other_client))
        url = f"/api/sandbox/runs/{self.run}/oauth-grants"
        response = self.browser.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        grants = response.json()["grants"]
        self.assertEqual(len(grants), 2)
        for grant in grants:
            self.assertEqual(set(grant), {"id", "client_name", "scope", "approved_at", "expires_at"})
            self.assertEqual(grant["client_name"], "<unverified>")
            self.assertEqual(grant["scope"], "sandbox:act")
            self.assertGreaterEqual(grant["approved_at"], started)
            self.assertGreater(grant["expires_at"], grant["approved_at"])
        target = self.client_id
        for secret in (first, second, digest(first), digest(second), self.bearer, digest(self.bearer)):
            self.assertNotIn(secret, response.text)
        endpoint = url+"/"+target
        self.assertEqual(self.agent.get(url).status_code, 401)
        self.assertEqual(self.agent.delete(endpoint, headers=self.headers).status_code, 401)
        self.assertEqual(self.browser.delete(endpoint).status_code, 403)
        with patch("sandbox_oauth.owner_id", return_value=self.other_owner):
            self.assertEqual(self.browser.get(url).status_code, 404)
            self.assertEqual(self.browser.delete(endpoint, headers=self.headers).status_code, 404)
        self.assertEqual(self.browser.delete(endpoint, headers=self.headers).status_code, 200)
        self.assertEqual(self.ping(first).status_code, 401)
        self.assertEqual(self.ping(second).status_code, 401)
        self.assertEqual(self.ping(third).status_code, 200)
        self.assertEqual(self.ping(self.bearer).status_code, 200)
        self.assertEqual(len(self.browser.get(url).json()["grants"]), 1)
        self.assertEqual(self.browser.delete(endpoint, headers=self.headers).status_code, 404)
        self.assertEqual(self.browser.post(f"/api/sandbox/runs/{self.run}/token", headers=self.headers).status_code, 200)
        self.assertEqual(self.browser.get(url).json()["grants"], [])
        self.assertEqual(self.ping(second).status_code, 401)
        self.assertEqual(self.ping(third).status_code, 401)

    def test_grant_lists_are_isolated_between_runs_for_the_same_client(self):
        other_run = secrets.token_hex(16)
        self.extra_runs.append(other_run)
        other_bearer = secrets.token_urlsafe(32)
        other_resource = ROOT + f"/api/sandbox/agent/{other_run}/mcp"
        now = time.time()
        with Session.begin() as db:
            db.add(Run(id=other_run, owner=self.owner, agent_hash=digest(other_bearer),
                       created=now, expires=now+1800, deletes=now+86400,
                       consent=False, data={"state": initial(), "events": [], "idempotency": {},
                                            "scenario": "non_delivery"}))

        first_token = self.exchange(self.code()).json()["access_token"]
        original_resource = self.resource
        original_param_resource = self.params["resource"]
        try:
            self.resource = other_resource
            self.params["resource"] = other_resource
            second_token = self.exchange(self.code()).json()["access_token"]
        finally:
            self.resource = original_resource
            self.params["resource"] = original_param_resource

        first_approved = time.time()-80
        second_approved = time.time()-40
        first_expiry = time.time()+120
        second_expiry = time.time()+240
        with Session.begin() as db:
            first_grant = record(db, "token", first_token)
            first_grant.data = {**first_grant.data, "approved_at": first_approved}
            first_grant.expires = first_expiry
            second_grant = record(db, "token", second_token)
            second_grant.data = {**second_grant.data, "approved_at": second_approved}
            second_grant.expires = second_expiry

        first_response = self.browser.get(
            f"/api/sandbox/runs/{self.run}/oauth-grants")
        second_response = self.browser.get(
            f"/api/sandbox/runs/{other_run}/oauth-grants")
        self.assertEqual(first_response.status_code, 200, first_response.text)
        self.assertEqual(second_response.status_code, 200, second_response.text)

        first_grants = first_response.json()["grants"]
        second_grants = second_response.json()["grants"]
        self.assertEqual(len(first_grants), 1)
        self.assertEqual(len(second_grants), 1)
        self.assertEqual(first_grants[0]["id"], self.client_id)
        self.assertEqual(second_grants[0]["id"], self.client_id)
        self.assertEqual(first_grants[0]["approved_at"], first_approved)
        self.assertEqual(first_grants[0]["expires_at"], first_expiry)
        self.assertEqual(second_grants[0]["approved_at"], second_approved)
        self.assertEqual(second_grants[0]["expires_at"], second_expiry)

        for response in (first_response, second_response):
            self.assertEqual(set(response.json()), {"grants"})
            for grant in response.json()["grants"]:
                self.assertEqual(
                    set(grant),
                    {"id", "client_name", "scope", "approved_at", "expires_at"})
            for secret in (first_token, second_token, self.bearer, other_bearer,
                           digest(first_token), digest(second_token),
                           digest(self.bearer), digest(other_bearer),
                           self.run, other_run):
                self.assertNotIn(secret, response.text)

    def test_grants_exclude_expired_revoked_and_closed_runs(self):
        url = f"/api/sandbox/runs/{self.run}/oauth-grants"
        token = self.exchange(self.code()).json()["access_token"]
        with Session.begin() as db:
            record(db, "token", token).expires = time.time()-1
        self.assertEqual(self.browser.get(url).json()["grants"], [])
        token = self.exchange(self.code()).json()["access_token"]
        grant_id = self.browser.get(url).json()["grants"][0]["id"]
        self.agent.post(PREFIX+"/revoke", data={"token": token, "client_id": self.client_id})
        self.assertEqual(self.browser.get(url).json()["grants"], [])
        self.assertEqual(self.browser.delete(url+"/"+grant_id, headers=self.headers).status_code, 404)
        self.exchange(self.code())
        self.browser.post(f"/api/sandbox/runs/{self.run}/revoke", headers=self.headers)
        self.assertEqual(self.browser.get(url).json()["grants"], [])

    def test_revoke_noops_for_wrong_client_missing_client_or_unknown_token(self):
        token = self.exchange(self.code()).json()["access_token"]
        with Session() as db:
            original = record(db, "token", token)
            original_data = dict(original.data)
            original_expiry = original.expires

        attempts = (
            ("wrong client", {"token": token, "client_id": "wrong-client"}),
            ("omitted client", {"token": token}),
            ("unknown token", {"token": "unknown-token", "client_id": self.client_id}),
        )
        for attempt, params in attempts:
            with self.subTest(attempt=attempt):
                response = self.agent.post(PREFIX+"/revoke", data=params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {})
                with Session() as db:
                    unchanged = record(db, "token", token)
                    self.assertIsNotNone(unchanged)
                    self.assertEqual(unchanged.data, original_data)
                    self.assertEqual(unchanged.expires, original_expiry)
                self.assertEqual(self.ping(token).status_code, 200)

    def test_concurrent_old_token_revocations_preserve_new_approval(self):
        original_token = self.exchange(self.code()).json()["access_token"]
        replacement_token = self.exchange(self.code()).json()["access_token"]
        self.assertNotEqual(original_token, replacement_token)

        with Session() as db:
            original = record(db, "token", original_token)
            replacement = record(db, "token", replacement_token)
            self.assertIsNotNone(original)
            self.assertIsNotNone(replacement)
            original_id = original.id
            replacement_snapshot = (replacement.expires, copy.deepcopy(replacement.data))
        barrier = threading.Barrier(2)

        def revoke_original():
            with TestClient(app, base_url=ROOT) as client:
                barrier.wait()
                return client.post(PREFIX + "/revoke", data={
                    "token": original_token, "client_id": self.client_id,
                })

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: revoke_original(), range(2)))

        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {})
        with Session() as db:
            self.assertIsNone(db.get(OAuthRecord, original_id))
            replacement = record(db, "token", replacement_token)
            self.assertIsNotNone(replacement)
            self.assertEqual(
                (replacement.expires, replacement.data), replacement_snapshot)
        self.assertEqual(self.ping(original_token).status_code, 401)
        self.assertEqual(self.ping(replacement_token).status_code, 200)

    def test_ambiguous_or_unsupported_revocation_preserves_approved_access(self):
        token = self.exchange(self.code()).json()["access_token"]
        with Session() as db:
            original = record(db, "token", token)
            original_data = dict(original.data)
            original_expiry = original.expires

        content_type = {"Content-Type": "application/x-www-form-urlencoded"}
        attempts = (
            ("duplicate token", content_type,
             f"token={token}&token={token}&client_id={self.client_id}"),
            ("duplicate client_id", content_type,
             f"token={token}&client_id={self.client_id}&client_id={self.client_id}"),
            ("unsupported content type", {},
             {"token": token, "client_id": self.client_id}),
        )
        for attempt, headers, body in attempts:
            with self.subTest(attempt=attempt):
                if isinstance(body, dict):
                    response = self.agent.post(PREFIX + "/revoke", headers=headers, json=body)
                else:
                    response = self.agent.post(PREFIX + "/revoke", headers=headers, content=body)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json(), {"error": "invalid_request"})
                with Session() as db:
                    unchanged = record(db, "token", token)
                    self.assertIsNotNone(unchanged)
                    self.assertEqual(unchanged.data, original_data)
                    self.assertEqual(unchanged.expires, original_expiry)
                self.assertEqual(self.ping(token).status_code, 200)

    def test_fabricated_revocation_token_preserves_approved_access(self):
        token = self.exchange(self.code()).json()["access_token"]
        grants_url = f"/api/sandbox/runs/{self.run}/oauth-grants"
        grants_before = self.browser.get(grants_url).json()["grants"]
        self.assertEqual(len(grants_before), 1)
        original_grant = grants_before[0]
        with Session() as db:
            original = record(db, "token", token)
            self.assertIsNotNone(original)
            original_data = dict(original.data)
            original_expiry = original.expires

        fabricated_token = "oauth_" + secrets.token_urlsafe(32)
        response = self.agent.post(PREFIX + "/revoke", data={
            "token": fabricated_token, "client_id": self.client_id,
        })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {})
        with Session() as db:
            unchanged = record(db, "token", token)
            self.assertIsNotNone(unchanged)
            self.assertEqual(unchanged.data, original_data)
            self.assertEqual(unchanged.expires, original_expiry)
        self.assertEqual(self.ping(token).status_code, 200)
        self.assertEqual(self.browser.get(grants_url).json()["grants"], grants_before)
        self.assertEqual(original_grant["id"], self.client_id)
        self.assertEqual(original_grant["expires_at"], original_expiry)

        valid_revoke = self.agent.post(PREFIX + "/revoke", data={
            "token": token, "client_id": self.client_id,
        })
        self.assertEqual(valid_revoke.status_code, 200, valid_revoke.text)
        self.assertEqual(valid_revoke.json(), {})
        self.assertEqual(self.ping(token).status_code, 401)
        self.assertEqual(self.browser.get(grants_url).json()["grants"], [])

    def test_legacy_grant_gets_safe_stable_management_id(self):
        token = self.exchange(self.code()).json()["access_token"]
        with Session.begin() as db:
            grant = record(db, "token", token)
            grant.data = {k:v for k,v in grant.data.items()
                          if k not in ("grant_id", "client_name", "approved_at")}
        url = f"/api/sandbox/runs/{self.run}/oauth-grants"
        first = self.browser.get(url).json()["grants"][0]
        self.assertIsNone(first["approved_at"])
        self.assertEqual(first["client_name"], "<unverified>")
        self.assertEqual(self.browser.get(url).json()["grants"][0], first)
        self.assertEqual(self.browser.delete(url+"/"+first["id"], headers=self.headers).status_code, 200)
        self.assertEqual(self.ping(token).status_code, 401)

    def test_pkce_resource_client_redirect_binding_and_single_use(self):
        code = self.code()
        for changes in ({"code_verifier": "x"*43}, {"resource": self.resource+"other"},
                        {"client_id": "other"}, {"redirect_uri": self.redirect+"/other"}):
            self.assertEqual(self.exchange(code, **changes).status_code, 400)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.exchange(code).status_code, range(2)))
        self.assertEqual(sorted(statuses), [200, 400])

    def test_denial_and_repeated_approval(self):
        pending = self.pending()
        response = self.browser.post(PREFIX+"/requests/"+pending, headers=self.headers,
                                     json={"approve": False})
        self.assertIn("error=access_denied", response.json()["redirect"])
        self.assertEqual(self.browser.post(PREFIX+"/requests/"+pending, headers=self.headers,
                         json={"approve": True}).status_code, 410)

    def test_discovery_origin_and_fail_closed_configuration(self):
        response = self.agent.post(self.resource, json={})
        self.assertEqual(response.status_code, 401)
        self.assertIn(ROOT+"/api/sandbox/oauth/resource/"+self.run,
                      response.headers["www-authenticate"])
        metadata = self.agent.get("/.well-known/oauth-authorization-server",
                                  headers={"Host": "attacker.invalid"}).json()
        self.assertEqual(metadata["issuer"], ROOT)
        self.assertEqual(
            metadata["grant_types_supported"],
            ["authorization_code", "refresh_token"])
        with patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": ""}):
            self.assertEqual(self.agent.get("/.well-known/oauth-authorization-server").status_code, 503)
            self.assertEqual(self.ping(self.bearer).status_code, 200)
        with patch.dict(os.environ, {"SANDBOX_OAUTH_ORIGIN": "replit-development",
                                    "NODE_ENV": "production"}):
            self.assertEqual(self.agent.get("/.well-known/oauth-authorization-server").status_code, 503)
        oversized = self.agent.get(PREFIX+"/authorize", params={**self.params, "ignored": "x"*9000})
        self.assertEqual(oversized.status_code, 400)
        self.assertEqual(oversized.json()["error"], "invalid_request")
        duplicated = self.agent.get(PREFIX+"/authorize",
            params=list(self.params.items())+[("scope", "sandbox:act")])
        self.assertEqual(duplicated.status_code, 400)

    def test_rotation_revocation_expiry_deletion_and_cross_run(self):
        token = self.exchange(self.code()).json()["access_token"]
        self.browser.post("/api/sandbox/runs/"+self.run+"/token", headers=self.headers)
        self.assertEqual(self.ping(token).status_code, 401)
        code = self.code()
        self.browser.post("/api/sandbox/runs/"+self.run+"/token", headers=self.headers)
        self.assertEqual(self.exchange(code).status_code, 400)
        token = self.exchange(self.code()).json()["access_token"]
        response = self.agent.post(self.resource.replace(self.run, "a"*32),
            headers={"Authorization": "Bearer "+token, "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(response.status_code, 401)
        self.agent.post(PREFIX+"/revoke", data={"client_id": self.client_id, "token": token})
        self.assertEqual(self.ping(token).status_code, 401)
        token = self.exchange(self.code()).json()["access_token"]
        with Session.begin() as db:
            record(db, "token", token).expires = time.time()-1
        self.assertEqual(self.ping(token).status_code, 401)
        token = self.exchange(self.code()).json()["access_token"]
        with Session.begin() as db:
            db.get(Run, self.run).expires = time.time()-1
        expired = self.ping(token)
        self.assertEqual(expired.status_code, 401)
        self.assertIn(
            f"/api/sandbox/oauth/resource/{self.run}",
            expired.headers["www-authenticate"])
        with Session.begin() as db:
            db.get(Run, self.run).expires = time.time()+60
        self.browser.post("/api/sandbox/runs/"+self.run+"/revoke", headers=self.headers)
        self.assertEqual(self.ping(token).status_code, 401)
        self.browser.delete("/api/sandbox/runs/"+self.run, headers=self.headers)
        self.assertEqual(self.ping(token).status_code, 401)

    def test_expired_requests_codes_and_foreign_owner(self):
        pending = self.pending()
        with Session.begin() as db:
            record(db, "request", pending).expires = time.time()-1
        self.assertEqual(self.browser.post(PREFIX+"/requests/"+pending, headers=self.headers,
                         json={"approve": True}).status_code, 410)
        code = self.code()
        with Session.begin() as db:
            record(db, "code", code).expires = time.time()-1
        self.assertEqual(self.exchange(code).status_code, 400)
        pending = self.pending()
        with Session.begin() as db:
            db.get(Run, self.run).owner = self.other_owner
        self.assertEqual(self.browser.post(PREFIX+"/requests/"+pending, headers=self.headers,
                         json={"approve": True}).status_code, 404)

    def test_expired_owner_session_cannot_read_or_approve_pending_request(self):
        action = self.agent.post(
            f"/api/sandbox/agent/{self.run}/actions",
            headers={"Authorization": "Bearer " + self.bearer,
                     "Idempotency-Key": "before-expired-owner"},
            json={"tool": "purchase"})
        self.assertEqual(action.status_code, 200, action.text)
        token = self.exchange(self.code()).json()["access_token"]
        pending = self.pending()

        def snapshot():
            with Session() as db:
                run = db.get(Run, self.run)
                records = db.scalars(select(OAuthRecord).where(
                    OAuthRecord.data["run"].astext == self.run)).all()
                return {
                    "state": copy.deepcopy(run.data["state"]),
                    "events": copy.deepcopy(run.data["events"]),
                    "idempotency": copy.deepcopy(run.data["idempotency"]),
                    "consent": run.consent,
                    "oauth_records": {
                        item.id: (item.expires, copy.deepcopy(item.data))
                        for item in records
                    },
                }

        before = snapshot()
        self.assertTrue(before["events"])
        self.assertTrue(before["idempotency"])
        self.assertFalse(before["consent"])
        self.assertTrue(any(item_id.startswith("token:")
                            for item_id in before["oauth_records"]))
        self.assertTrue(any(item_id == "request:" + digest(pending)
                            for item_id in before["oauth_records"]))

        with Session.begin() as db:
            db.get(Owner, self.owner).expires = time.time() - 1

        details = self.browser.get(PREFIX + "/requests/" + pending)
        decision = self.browser.post(
            PREFIX + "/requests/" + pending,
            headers=self.headers, json={"approve": True})
        self.assertEqual(details.status_code, 401)
        self.assertEqual(decision.status_code, 401)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.ping(token).status_code, 200)


if __name__ == "__main__":
    unittest.main()
