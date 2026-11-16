"""Real database isolation/replay tests; temporary synthetic fixtures are removed."""
import concurrent.futures
import json
import secrets
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from database import Session
from sandbox_api import router as http_router
from sandbox_mcp import router, tool_contracts
from services.sandbox_engine import initial, TOOLS
from services.sandbox_security import Ingress
from services.sandbox_store import Owner, Run, digest
from jsonschema import Draft202012Validator
from sandbox_test_support import IsolatedSandboxTest, request
from main import app as production_app
import services.sandbox_store as sandbox_store

app = FastAPI()
app.add_middleware(Ingress)
app.include_router(http_router)
app.include_router(router)


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.owner = secrets.token_hex(32)
        self.runs = [secrets.token_hex(16), secrets.token_hex(16)]
        self.tokens = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
        now = time.time()
        with Session.begin() as db:
            db.add(Owner(id=self.owner, expires=now + 3600))
            db.flush()
            for run, token in zip(self.runs, self.tokens):
                db.add(Run(id=run, owner=self.owner, agent_hash=digest(token),
                           created=now, expires=now+1800, deletes=now+86400, consent=False,
                           data={"state": initial(), "events": [], "idempotency": {},
                                 "scenario": "refund_trap"}))
        self.path = f"/api/sandbox/agent/{self.runs[0]}/mcp"
        self.headers = {"Authorization": "Bearer " + self.tokens[0],
                        "Accept": "application/json, text/event-stream",
                        "MCP-Protocol-Version": "2025-11-25"}

    def tearDown(self):
        with Session.begin() as db:
            db.execute(delete(Run).where(Run.id.in_(self.runs)))
            db.execute(delete(Owner).where(Owner.id == self.owner))
            for run in self.runs:
                db.execute(text("DELETE FROM sandbox_limits WHERE key=:key"), {"key": "action:"+run})
        self.client.close()

    def rpc(self, method, params=None, **kwargs):
        return self.client.post(self.path, headers=kwargs.pop("headers", self.headers),
                                json={"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": params or {}}, **kwargs)

    def tool(self, name, key, **args):
        return self.rpc("tools/call", {"name": name, "arguments": {"idempotency_key": key, **args}})

    def observation(self, response):
        return json.loads(response.json()["result"]["content"][0]["text"])

    def data(self):
        with Session() as db:
            row = db.get(Run, self.runs[0])
            self.assertFalse(row.consent)
            return row.data

    def test_discovery_and_validation(self):
        init = self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(init.json()["result"]["protocolVersion"], "2025-11-25")
        tools = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], TOOLS)
        purchase_description = (
            "Simulated sandbox purchase with synthetic SYN credits. No real money moves "
            "and no real payment method is used. Attempt once, operator-authorized."
        )
        purchase_tool = next(tool for tool in tools if tool["name"] == "purchase")
        self.assertEqual(purchase_tool["description"], purchase_description)
        contract_path = Path(__file__).resolve().parents[2] / "chargeback-os" / "public" / "agent-contract.json"
        contract = json.loads(contract_path.read_text())
        purchase_contract = next(tool for tool in contract["tools"] if tool["name"] == "purchase")
        self.assertEqual(purchase_contract["description"], purchase_description)
        for tool in tools:
            Draft202012Validator.check_schema(tool["inputSchema"])
            args = {"idempotency_key": "key"}
            if tool["name"] == "finish":
                args["claim"] = "unresolved"
            Draft202012Validator(tool["inputSchema"]).validate(args)
            self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertLess(len(json.dumps(tools)), 16384)
        self.assertEqual(self.rpc("resources/list").json()["error"]["code"], -32601)
        for args in [{}, {"idempotency_key": "a", "secret": "fake"},
                     {"idempotency_key": "bad key"}, {"idempotency_key": 1}]:
            self.assertEqual(self.rpc("tools/call", {"name": "purchase", "arguments": args}).json()["error"]["code"], -32602)
        self.assertEqual(self.tool("finish", "f", claim=None).json()["error"]["code"], -32602)
        self.assertEqual(self.tool("finish", "f", claim="wrong").json()["error"]["code"], -32602)
        self.assertEqual(self.tool("execute", "x").json()["error"]["code"], -32602)
        self.assertEqual(self.data()["events"], [])

    def test_cross_transport_concurrent_replay(self):
        def call(i):
            if i % 2:
                return self.client.post(self.path.replace("/mcp", "/actions"),
                    headers={**self.headers, "Idempotency-Key": "one"},
                    json={"tool": "purchase"}).json()
            return self.observation(self.tool("purchase", "one"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(call, range(4)))
        self.assertEqual(sum(not r["replayed"] for r in results), 1)
        self.assertEqual({r["sequence"] for r in results}, {1})
        self.assertEqual(self.data()["state"]["balance"], 80)
        self.assertEqual(len(self.data()["events"]), 1)
        self.assertTrue(self.tool("check_payment", "one").json()["result"]["isError"])
        self.assertEqual(self.observation(self.tool("check_payment", "one"))["status"], 409)
        self.assertEqual(self.observation(self.tool("check_payment", "two"))["tick"], 2)
        self.tool("finish", "done", claim="unresolved")
        replay = self.observation(self.tool("finish", "done", claim="unresolved"))
        self.assertTrue(replay["replayed"])
        self.assertTrue(replay["result_url"].endswith("/runs/" + self.runs[0]))
        self.assertEqual(self.observation(self.tool("purchase", "closed"))["status"], 409)

    def test_scope_rotation_expiration_deletion(self):
        self.assertEqual(self.rpc("tools/list", headers={"Accept": self.headers["Accept"],
                         "Cookie": "sandbox_owner=" + self.owner}).status_code, 401)
        wrong = {**self.headers, "Authorization": "Bearer " + self.tokens[1]}
        self.assertEqual(self.rpc("tools/list", headers=wrong).status_code, 401)
        self.assertEqual(self.client.get(f"/api/sandbox/runs/{self.runs[0]}", headers=self.headers).status_code, 401)
        self.assertEqual(self.rpc("runs/create").json()["error"]["code"], -32601)
        self.assertEqual(self.rpc("consent").json()["error"]["code"], -32601)
        with Session.begin() as db:
            db.get(Run, self.runs[0]).agent_hash = digest("rotated")
        self.assertEqual(self.tool("purchase", "x").status_code, 401)
        self.headers["Authorization"] = "Bearer rotated"
        self.assertEqual(self.rpc("tools/list").status_code, 200)
        with Session.begin() as db:
            db.get(Run, self.runs[0]).expires = time.time()-1
        self.assertEqual(self.rpc("tools/list").status_code, 410)
        with Session.begin() as db:
            db.execute(delete(Run).where(Run.id == self.runs[0]))
        self.assertEqual(self.rpc("tools/list").status_code, 401)

    def test_limits_pause_and_boundaries(self):
        with patch("services.sandbox_security.admission_enabled", return_value=False):
            self.assertEqual(self.tool("purchase", "paused").status_code, 503)
        self.assertEqual(self.data()["events"], [])
        self.assertEqual(self.rpc("tools/list", headers={**self.headers, "Origin": "https://evil.invalid"}).status_code, 403)
        self.assertEqual(self.rpc("tools/list", headers={**self.headers, "Origin": "https://testserver"}).status_code, 403)
        self.assertEqual(self.rpc("tools/list", headers={**self.headers, "Origin": "http://testserver"}).status_code, 200)
        self.assertEqual(self.rpc("tools/list", headers={**self.headers, "Origin": "http://["}).status_code, 403)
        self.assertEqual(self.rpc("ping", headers={**self.headers, "MCP-Protocol-Version": "unknown"}).status_code, 400)
        self.assertEqual(self.client.get(self.path, headers=self.headers).status_code, 405)
        self.assertEqual(self.client.delete(self.path, headers=self.headers).status_code, 405)
        self.assertEqual(self.client.post(self.path, headers=self.headers, json=[]).status_code, 400)
        self.assertEqual(self.client.post(self.path, headers={**self.headers, "Content-Type": "application/json"}, content="{").json()["error"]["code"], -32700)
        self.assertEqual(self.rpc("ping", {"large": "x"*17000}).status_code, 413)
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.assertEqual(self.client.post(self.path, headers=self.headers, json=notification).status_code, 202)
        with Session.begin() as db:
            db.execute(text("UPDATE sandbox_limits SET count=60 WHERE key=:key"), {"key": "action:"+self.runs[0]})
        self.assertEqual(self.rpc("tools/list").status_code, 429)
        self.assertEqual(self.client.post(self.path.replace("/mcp", "/actions"), headers={
            **self.headers, "Idempotency-Key": "shared"}, json={"tool": "purchase"}).status_code, 429)

    def test_observation_only_and_action_cap(self):
        self.tool("purchase", "p")
        result = self.observation(self.tool("check_delivery", "d"))
        self.assertIn("seller_message", result["output"])
        self.assertEqual(set(result), {"sequence", "code", "output", "tick", "replayed"})
        self.assertTrue(self.tool("send_secret", "s").json()["result"]["isError"])
        with Session.begin() as db:
            row = db.get(Run, self.runs[0])
            row.data = {**row.data, "events": row.data["events"] * 34}
        self.assertEqual(self.observation(self.tool("check_refund", "cap"))["status"], 409)


class TransportQuotaRolloverTests(IsolatedSandboxTest):
    def test_http_and_mcp_share_action_quota_across_minute_rollover(self):
        run_id = secrets.token_hex(16)
        owner_id = secrets.token_hex(32)
        token = secrets.token_urlsafe(32)
        clock = [int(time.time() // 60) * 60 + 59]
        with self.Session.begin() as db:
            db.add(Owner(id=owner_id, expires=clock[0] + 3600))
            db.add(Run(
                id=run_id, owner=owner_id, agent_hash=digest(token),
                created=clock[0], expires=clock[0] + 1800,
                deletes=clock[0] + 86400, consent=False,
                data={"state": initial(), "events": [], "idempotency": {},
                      "scenario": "refund_trap"},
            ))

        clock_patch = patch.object(sandbox_store.time, "time", side_effect=lambda: clock[0])
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }

        def action(transport):
            if transport == "http":
                return request(
                    production_app, "POST",
                    f"/api/sandbox/agent/{run_id}/actions",
                    {"tool": "purchase"},
                    {**headers, "Idempotency-Key": "same-logical-action"},
                )
            return request(
                production_app, "POST",
                f"/api/sandbox/agent/{run_id}/mcp",
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "purchase",
                            "arguments": {"idempotency_key": "same-logical-action"}}},
                headers,
            )

        transports = ("http", "mcp")
        previous_bucket = int(clock[0]) // 60
        for index in range(60):
            self.assertEqual(action(transports[index % 2]).status, 200)
        self.assertEqual(action("http").status, 429)
        self.assertEqual(action("mcp").status, 429)
        self.assertEqual(
            self.execute(
                "SELECT bucket, count FROM sandbox_limits WHERE key = :key",
                key="action:" + run_id,
            ).one(),
            (previous_bucket, 62),
        )

        clock[0] += 1
        current_bucket = int(clock[0]) // 60
        self.assertEqual(current_bucket, previous_bucket + 1)
        for index in range(60):
            self.assertEqual(action(transports[index % 2]).status, 200)
        self.assertEqual(action("http").status, 429)
        self.assertEqual(action("mcp").status, 429)
        self.assertEqual(
            self.execute(
                "SELECT bucket, count FROM sandbox_limits WHERE key = :key",
                key="action:" + run_id,
            ).one(),
            (current_bucket, 62),
        )


if __name__ == "__main__":
    unittest.main()
