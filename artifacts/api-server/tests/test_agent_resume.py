"""Resume security and read-only checks against disposable development rows."""
import copy
import secrets
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import text
from database import Session
from sandbox_api import Action, agent_action, agent_resume
from services.sandbox_engine import initial
from services.sandbox_store import Owner, Run, digest


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.owner = secrets.token_hex(32)
        self.ids = [secrets.token_hex(16), secrets.token_hex(16)]
        self.tokens = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
        with Session.begin() as db:
            db.add(Owner(id=self.owner, expires=time.time() + 60))
            db.flush()
            for run, token in zip(self.ids, self.tokens):
                db.add(Run(id=run, owner=self.owner, agent_hash=digest(token),
                           created=time.time(), expires=time.time() + 60,
                           deletes=time.time() + 120, consent=False,
                           data={"scenario": "refund_trap", "state": initial(),
                                 "events": [], "idempotency": {}, "score": "OWNER_ONLY"}))

    def tearDown(self):
        with Session.begin() as db:
            for run in self.ids:
                db.delete(db.get(Run, run))
                db.execute(text("DELETE FROM sandbox_limits WHERE key = :key"),
                           {"key": "action:" + run})
            db.delete(db.get(Owner, self.owner))

    def request(self, token=None, key="test"):
        return Request({"type": "http", "headers": [
            (b"authorization", ("Bearer " + (token or self.tokens[0])).encode()),
            (b"idempotency-key", key.encode())]})

    def resume(self, after=0, page_size=20, token=None, run=None):
        return agent_resume(run or self.ids[0], self.request(token), after, page_size)

    def action(self, tool, key, claim=None):
        return agent_action(self.ids[0], Action(tool=tool, claim=claim), self.request(key=key))

    def data(self):
        with Session() as db:
            return copy.deepcopy(db.get(Run, self.ids[0]).data)

    def assert_error(self, status, callback):
        with self.assertRaises(HTTPException) as ctx:
            callback()
        self.assertEqual(ctx.exception.status_code, status)

    def test_pagination_projection_and_state_unchanged(self):
        self.assertEqual(self.resume(), {"results": [], "next_after": None,
                                        "last_sequence": 0, "remaining_actions": 100, "closed": False})
        responses = [self.action(tool, str(i)) for i, tool in enumerate(
            ["purchase", "check_delivery", "request_refund", "check_refund"])]
        self.action("purchase", "0")  # A transport retry is not another event.
        before = self.data()
        page = self.resume(page_size=2)
        second = self.resume(after=page["next_after"], page_size=2)
        self.assertIsNone(second["next_after"])
        self.assertEqual(page["remaining_actions"], 96)
        results = page["results"] + second["results"]
        self.assertEqual([r["sequence"] for r in results], [1, 2, 3, 4])
        for result, response in zip(results, responses):
            self.assertEqual(set(result), {"sequence", "tool", "claim", "code", "output", "tick"})
            self.assertEqual({k: result[k] for k in ("sequence", "code", "output", "tick")},
                             {k: response[k] for k in ("sequence", "code", "output", "tick")})
        self.assertEqual(set(page), {"results", "next_after", "last_sequence", "remaining_actions", "closed"})
        self.assertEqual(self.resume(after=4)["results"], [])
        self.assert_error(400, lambda: self.resume(after=5))
        self.assertEqual(self.data(), before)

    def test_cross_run_missing_and_malformed_credentials(self):
        self.assert_error(401, lambda: self.resume(run=self.ids[1]))
        self.assert_error(401, lambda: self.resume(token=self.tokens[1]))
        self.assert_error(401, lambda: self.resume(run="absent"))
        for headers in ([], [(b"authorization", b"Basic invalid")],
                        [(b"authorization", b"Bearer " + b"x" * 161)]):
            self.assert_error(401, lambda: agent_resume(self.ids[0],
                              Request({"type": "http", "headers": headers}), 0, 20))

    def test_rotation_revocation_and_expiry(self):
        new_token = secrets.token_urlsafe(32)
        with Session.begin() as db:
            db.get(Run, self.ids[0]).agent_hash = digest(new_token)
        self.assert_error(401, self.resume)
        self.assertEqual(self.resume(token=new_token)["results"], [])
        with Session.begin() as db:
            row = db.get(Run, self.ids[0])
            data = copy.deepcopy(row.data)
            data["state"]["status"] = "revoked"
            row.data = data
        self.assert_error(401, lambda: self.resume(token=new_token))
        with Session.begin() as db:
            db.get(Run, self.ids[0]).expires = time.time() - 1
        self.assert_error(410, lambda: self.resume(token=new_token))
        with Session.begin() as db:
            row = db.get(Run, self.ids[0])
            row.expires = time.time() + 60
            row.deletes = time.time() - 1
        self.assert_error(410, lambda: self.resume(token=new_token))

    def test_closed_history_and_shared_quota(self):
        self.action("finish", "done", "unresolved")
        before = self.data()
        result = self.resume()
        self.assertTrue(result["closed"])
        self.assertEqual(result["remaining_actions"], 99)
        self.assertEqual(result["results"][0]["claim"], "unresolved")
        self.assertEqual(self.data(), before)
        with Session.begin() as db:
            db.execute(text("UPDATE sandbox_limits SET count = 60, bucket = :bucket WHERE key = :key"),
                       {"bucket": int(time.time()) // 60, "key": "action:" + self.ids[0]})
        self.assert_error(429, self.resume)
        self.assert_error(429, lambda: self.action("finish", "done", "unresolved"))

    def test_full_budget_is_bounded(self):
        with Session.begin() as db:
            row = db.get(Run, self.ids[0])
            data = copy.deepcopy(row.data)
            data["events"] = [{"sequence": i, "tool": "check_payment", "claim": None,
                               "code": "ok", "output": {}, "tick": i,
                               "balance_delta": "OWNER_ONLY"} for i in range(1, 101)]
            data["state"]["status"] = "limit_reached"
            row.data = data
        result = self.resume(page_size=50)
        self.assertEqual(len(result["results"]), 50)
        self.assertEqual(result["remaining_actions"], 0)
        self.assertTrue(result["closed"])
        self.assertIsNone(self.resume(after=50, page_size=50)["next_after"])
        self.assertEqual(self.resume(after=100)["results"], [])
