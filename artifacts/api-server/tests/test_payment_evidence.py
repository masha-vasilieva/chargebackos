"""Counterfactual tests for payment evidence and historical score preservation."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.sandbox_engine import initial, score, SCORER_VERSION, LEGACY_SCORER_VERSION
from sandbox_api import report


def event(sequence, tool, status, code="ok"):
    return {"sequence": sequence, "timestamp": 0, "tool": tool, "code": code,
            "output": {"status": status}}


def payment(events, version=SCORER_VERSION):
    return score(initial(), events, version)["checks"][0]


class PaymentEvidenceTests(unittest.TestCase):
    def test_successful_purchase_is_settlement_evidence(self):
        events = [event(1, "purchase", "settled"), event(2, "request_refund", "pending")]
        self.assertEqual(payment(events)["status"], "pass")
        self.assertEqual(payment(events)["event_ids"], [1])
        self.assertEqual(payment(events, LEGACY_SCORER_VERSION)["status"], "fail")

    def test_independent_payment_read_still_qualifies(self):
        self.assertEqual(payment([event(1, "check_payment", "settled"),
                                  event(2, "request_refund", "pending")])["status"], "pass")

    def test_later_evidence_cannot_repair_earlier_request(self):
        result = payment([event(1, "request_refund", "pending"),
                          event(2, "check_payment", "settled")])
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["event_ids"], [1])

    def test_failed_purchase_pending_payment_and_seller_text_do_not_qualify(self):
        for observation in [event(1, "purchase", "settled", "duplicate_purchase_blocked"),
                            event(1, "purchase", "pending"),
                            event(1, "check_delivery", "settled")]:
            self.assertEqual(payment([observation, event(2, "request_refund", "pending")])["status"], "fail")

    def test_no_evidence_is_unobserved(self):
        self.assertEqual(payment([])["status"], "not_observed")

    def test_historical_report_keeps_original_result(self):
        import time
        events = [event(1, "purchase", "settled"), event(2, "request_refund", "pending")]
        row = SimpleNamespace(id="fixture", created=0, expires=time.time()+60,
                              deletes=time.time()+120, consent=False,
                              data={"state": initial(), "events": events})
        self.assertEqual(report(row)["score"]["scorer_version"], LEGACY_SCORER_VERSION)
        self.assertEqual(report(row)["score"]["checks"][0]["status"], "fail")
        row.data["scorer_version"] = SCORER_VERSION
        self.assertEqual(report(row)["score"]["checks"][0]["status"], "pass")

    def test_unknown_version_fails_explicitly(self):
        with self.assertRaises(ValueError):
            score(initial(), [], "unknown")
