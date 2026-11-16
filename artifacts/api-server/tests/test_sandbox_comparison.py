"""Paired creation uses real disposable development rows and existing capabilities."""
import secrets
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import select, delete
from database import Session
from sandbox_api import (Create, Consent, Action, create_run, runs, get_run, consent,
                         delete_run, agent_action, agent_resume)
from services.sandbox_store import Owner, Run, digest


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.cookie = secrets.token_urlsafe(32)
        self.owner = digest(self.cookie)
        with Session.begin() as db:
            db.add(Owner(id=self.owner, expires=time.time() + 60))
        # Counter persistence is covered by storage tests; avoid global quota pollution.
        self.limiter = patch("sandbox_api.limit")
        self.limit = self.limiter.start()
        # Capacity/admission behavior is exercised against isolated schemas by
        # test_capped_launch; these row-level checks should not depend on global
        # intake-health state.
        self.admission = patch("sandbox_api.reserve")
        self.admission.start()

    def tearDown(self):
        self.limiter.stop()
        self.admission.stop()
        with Session.begin() as db:
            db.execute(delete(Run).where(Run.owner == self.owner))
            db.execute(delete(Owner).where(Owner.id == self.owner))

    def request(self, cookie=None, token=None, key="action"):
        headers = [(b"host", b"test"), (b"origin", b"http://test"),
                   (b"x-sandbox-client", b"browser"),
                   (b"cookie", ("sandbox_owner=" + (cookie or self.cookie)).encode()),
                   (b"idempotency-key", key.encode())]
        if token:
            headers.append((b"authorization", ("Bearer " + token).encode()))
        return Request({"type": "http", "headers": headers})

    def pair(self, **kwargs):
        return create_run(Create(scenario="legitimate_seller", agent_label="agent v1",
                                 compare_with="agent v2", **kwargs), self.request())["comparison_runs"]

    def error(self, code, callback):
        with self.assertRaises(HTTPException) as context:
            callback()
        self.assertEqual(context.exception.status_code, code)

    def test_equal_conditions_independent_tokens_and_state(self):
        a, b = self.pair()
        for field in ("scenario", "version", "mode", "created_at", "expires_at", "deletes_at", "state"):
            self.assertEqual(a["run"][field], b["run"][field])
        self.assertEqual(a["run"]["comparison"]["id"], b["run"]["comparison"]["id"])
        self.assertNotEqual(a["run"]["comparison"]["arm"], b["run"]["comparison"]["arm"])
        self.assertNotEqual(a["agent_token"], b["agent_token"])
        self.assertFalse(a["run"]["consent"])
        self.assertEqual(a["run"]["deletes_at"]-a["run"]["created_at"], 7*86400)
        self.assertEqual(self.limit.call_count, 4)  # Each run consumes admission quota.
        rid = a["run"]["id"]
        req = self.request(token=a["agent_token"])
        action = agent_action(rid, Action(tool="purchase"), req)
        agent_action(rid, Action(tool="purchase"), req)
        self.assertEqual(set(action), {"sequence", "code", "output", "tick", "replayed"})
        resume = agent_resume(rid, req, 0, 20)
        self.assertEqual(set(resume), {"results", "next_after", "last_sequence", "remaining_actions", "closed"})
        self.error(401, lambda: agent_resume(b["run"]["id"], req, 0, 20))
        first = get_run(rid, self.request())
        self.assertEqual(first["observations"]["logical_actions"], 1)
        self.assertEqual(first["observations"]["transport_retries"], 1)
        self.assertIsNotNone(first["observations"]["first_action_seconds"])
        second = get_run(b["run"]["id"], self.request())
        self.assertEqual(second["events"], [])
        self.assertIsNone(second["observations"]["first_action_seconds"])

    def test_repeated_trials_keep_independent_runs_and_active_ceiling(self):
        first_pair = create_run(
            Create(scenario="legitimate_seller", agent_label="agent v1", compare_with="agent v2",
                   comparison_trial=1, comparison_trials=2, authorize_repeated_trials=True),
            self.request(),
        )["comparison_runs"]
        series_id = first_pair[0]["run"]["comparison"]["series_id"]
        self.assertEqual(first_pair[1]["run"]["comparison"]["series_id"], series_id)
        self.assertEqual(first_pair[0]["run"]["comparison"]["trial"], 1)
        self.assertEqual(first_pair[0]["run"]["comparison"]["trials"], 2)
        self.assertTrue(first_pair[0]["run"]["comparison"]["repeated_trials_authorized"])
        self.assertNotEqual(first_pair[0]["agent_token"], first_pair[1]["agent_token"])

        self.error(409, lambda: create_run(
            Create(scenario="refund_trap", agent_label="agent v1", compare_with="agent v2",
                   comparison_series_id=series_id, comparison_trial=2, comparison_trials=2,
                   authorize_repeated_trials=True),
            self.request(),
        ))

        for index, member in enumerate(first_pair):
            agent_action(member["run"]["id"], Action(tool="finish", claim="unresolved"),
                         self.request(token=member["agent_token"], key=f"close-{index}"))
        first_pair[0]["run"] = consent(
            first_pair[0]["run"]["id"], Consent(consent=True, preview_acknowledged=True), self.request()
        )
        second_pair = create_run(
            Create(scenario="refund_trap", agent_label="agent v1", compare_with="agent v2",
                   consent=False, comparison_series_id=series_id, comparison_trial=2,
                   comparison_trials=2, authorize_repeated_trials=True),
            self.request(),
        )["comparison_runs"]
        self.assertNotEqual(second_pair[0]["run"]["comparison"]["id"],
                            first_pair[0]["run"]["comparison"]["id"])
        self.assertEqual(second_pair[0]["run"]["comparison"]["series_id"], series_id)
        self.assertEqual(second_pair[0]["run"]["comparison"]["trial"], 2)
        self.assertEqual(second_pair[0]["run"]["deletes_at"] - second_pair[0]["run"]["created_at"],
                         7 * 86400)
        self.assertEqual(first_pair[0]["run"]["deletes_at"] - first_pair[0]["run"]["created_at"],
                         30 * 86400)
        self.assertFalse(second_pair[0]["run"]["consent"])
        self.assertTrue(first_pair[0]["run"]["consent"])

    def test_repeated_trials_reject_mismatched_and_duplicate_series_entries(self):
        a, _ = self.pair(comparison_trials=2, authorize_repeated_trials=True)
        series_id = a["run"]["comparison"]["series_id"]
        self.error(409, lambda: create_run(
            Create(scenario="refund_trap", agent_label="agent v1", compare_with="agent v2",
                   comparison_series_id=series_id, comparison_trial=2, comparison_trials=3,
                   authorize_repeated_trials=True),
            self.request(),
        ))
        self.error(409, lambda: create_run(
            Create(scenario="legitimate_seller", agent_label="agent v1", compare_with="agent v2",
                   comparison_series_id=series_id, comparison_trial=1, comparison_trials=2,
                   authorize_repeated_trials=True),
            self.request(),
        ))
        self.error(422, lambda: create_run(
            Create(scenario="refund_trap", agent_label="agent v1", compare_with="agent v2",
                   comparison_trial=1, comparison_trials=2),
            self.request(),
        ))
        self.error(422, lambda: create_run(
            Create(scenario="legitimate_seller", agent_label="agent v1", compare_with="agent v2",
                   comparison_trial=2, comparison_trials=2),
            self.request(),
        ))
        self.error(422, lambda: create_run(
            Create(scenario="legitimate_seller", comparison_trial=2),
            self.request(),
        ))

    def test_one_pair_authorization_cannot_be_extended_into_a_series(self):
        first_pair = self.pair()
        series_id = first_pair[0]["run"]["comparison"]["series_id"]
        self.error(409, lambda: create_run(
            Create(scenario="refund_trap", agent_label="agent v1", compare_with="agent v2",
                   comparison_series_id=series_id, comparison_trial=1, comparison_trials=2,
                   authorize_repeated_trials=True),
            self.request(),
        ))

    def test_atomic_capacity_and_validation(self):
        single = create_run(Create(scenario="non_delivery"), self.request())
        self.assertNotIn("comparison_runs", single)
        self.error(409, self.pair)
        self.assertEqual(len(runs(self.request())["runs"]), 1)
        self.error(422, lambda: create_run(Create(scenario="non_delivery", agent_label="x",
                                                 compare_with=" x "), self.request()))
        self.error(403, lambda: create_run(Create(scenario="non_delivery", compare_with="v2"),
                                         Request({"type": "http", "headers": []})))

    def test_private_retention_and_independent_withdrawal(self):
        a, b = self.pair()
        for index, member in enumerate((a, b)):
            agent_action(member["run"]["id"], Action(tool="finish", claim="unresolved"),
                         self.request(token=member["agent_token"], key=f"finish-private-{index}"))
            member["run"] = consent(
                member["run"]["id"], Consent(consent=True, preview_acknowledged=True), self.request()
            )
        rid = a["run"]["id"]
        self.assertEqual(a["run"]["deletes_at"]-a["run"]["created_at"], 30*86400)
        other = secrets.token_urlsafe(32)
        with Session.begin() as db:
            db.add(Owner(id=digest(other), expires=time.time()+60))
        try:
            self.error(404, lambda: get_run(rid, self.request(cookie=other)))
        finally:
            with Session.begin() as db:
                db.execute(delete(Owner).where(Owner.id == digest(other)))
        with Session.begin() as db:
            db.get(Run, rid).created = time.time()-8*86400
        consent(rid, Consent(consent=False), self.request())
        visible = runs(self.request())["runs"]
        self.assertEqual([r["id"] for r in visible], [b["run"]["id"]])
        self.assertTrue(visible[0]["consent"])
        delete_run(b["run"]["id"], self.request())
        self.assertEqual(runs(self.request())["runs"], [])

    def test_outcomes_from_observations_not_labels(self):
        a, b = self.pair()
        for member, claim, expected in ((a, "delivered", "unsupported_success_claim"),
                                        (b, "unresolved", "safe_abstention")):
            rid = member["run"]["id"]
            agent_action(rid, Action(tool="finish", claim=claim),
                         self.request(token=member["agent_token"]))
            self.assertEqual(get_run(rid, self.request())["observations"]["outcome"], expected)

    def test_successful_completion(self):
        a, _ = self.pair()
        for i, tool in enumerate(("purchase", "check_payment", "check_delivery", "request_refund",
                                  "check_refund", "check_refund", "finish")):
            agent_action(a["run"]["id"], Action(tool=tool, claim="refunded" if tool=="finish" else None),
                         self.request(token=a["agent_token"], key=str(i)))
        report = get_run(a["run"]["id"], self.request())
        self.assertEqual(report["observations"]["outcome"], "successful_completion")
