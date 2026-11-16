"""Private PostgreSQL fixtures and a dependency-free ASGI request driver.

No public-schema fallback: a missing safety table must fail, not reach the
development control row. Cleanup is registered before fixture initialization.
"""
import asyncio
import json
import os
import secrets
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import database
import sandbox_api
import sandbox_mcp
from services import sandbox_usage_history
from services import sandbox_store
from services import sandbox_launch
from services import sandbox_security


class IsolatedSandboxTest(unittest.TestCase):
    def setUp(self):
        self.operator_token_env = patch.dict(
            os.environ, {
                "SANDBOX_OPERATOR_TOKEN": "",
                "SANDBOX_RESEARCH_EXPORT_TOKEN": "",
            }
        )
        self.operator_token_env.start()
        self.addCleanup(self.operator_token_env.stop)
        self.schema = "sandbox_test_" + secrets.token_hex(12)
        with database.engine.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.drop_schema)
        self.engine = create_engine(
            database.engine.url,
            # Preserve the API's database safety timeouts in every disposable
            # schema; replacing connect_args here would otherwise disable them.
            connect_args={"options": (
                f"-csearch_path={self.schema} -c statement_timeout=5000 "
                "-c lock_timeout=2000 -c idle_in_transaction_session_timeout=10000"
            )},
        )
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(self.engine)
        sql = (Path(__file__).resolve().parents[1] / "sandbox_schema.sql").read_text()
        with self.engine.begin() as db:
            for statement in sql.split(";"):
                if statement.strip():
                    db.execute(text(statement))
            self.assertEqual(db.execute(text("SELECT current_schema()")).scalar_one(), self.schema)
        for module in (
            sandbox_store, sandbox_api, sandbox_mcp, sandbox_launch,
            sandbox_security, sandbox_usage_history,
        ):
            patcher = patch.object(module, "Session", self.Session)
            patcher.start()
            self.addCleanup(patcher.stop)
        from services import sandbox_research_export
        export_session_patch = patch.object(sandbox_research_export, "Session", self.Session)
        export_session_patch.start()
        self.addCleanup(export_session_patch.stop)
        health = sandbox_security.RetentionHealth()
        health_patch = patch.object(sandbox_security, "retention_health", health)
        health_patch.start()
        self.addCleanup(health_patch.stop)
        health.record_success()

    def drop_schema(self):
        with database.engine.begin() as db:
            db.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.assertIsNone(db.execute(
                text("SELECT schema_name FROM information_schema.schemata WHERE schema_name = :name"),
                {"name": self.schema},
            ).scalar_one_or_none())

    def execute(self, sql, **params):
        with self.Session.begin() as db:
            return db.execute(text(sql), params)


def request(app, method, path, body=None, headers=None):
    """Exercise routing, middleware and serialization, not endpoint functions."""
    async def run():
        messages = []
        payload = b"" if body is None else json.dumps(body).encode()
        delivered = False
        parsed = urlsplit(path)
        request_path = parsed.path

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": payload, "more_body": False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        await asyncio.wait_for(app({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": request_path,
            "raw_path": request_path.encode(), "query_string": parsed.query.encode(), "root_path": "",
            "server": ("testserver", 80), "client": ("127.0.0.1", 12345),
            "headers": [(k.lower().encode(), v.encode()) for k, v in {
                "host": "testserver", "content-type": "application/json",
                **(headers or {}),
            }.items()],
        }, receive, send), timeout=10)
        start = next(m for m in messages if m["type"] == "http.response.start")
        raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        response_headers = {k.decode(): v.decode() for k, v in start["headers"]}
        content_type = response_headers.get("content-type", "")
        if not raw:
            parsed_body = None
        elif "application/x-ndjson" in content_type:
            parsed_body = [json.loads(line) for line in raw.splitlines() if line]
        elif "json" in content_type:
            parsed_body = json.loads(raw)
        else:
            parsed_body = raw.decode("utf-8", errors="replace")
        return SimpleNamespace(
            status=start["status"],
            body=parsed_body,
            text=raw.decode("utf-8", errors="replace"),
            headers=response_headers,
        )

    return asyncio.run(run())
