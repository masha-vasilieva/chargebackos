"""PayPal sandbox rail and Claude explanations (both optional).

Unit tests use a fake PayPal (no network). API tests run through the production
app in a private schema (sandbox_test_support) and check that the synthetic
rail, scores and research records are unchanged by the PayPal rail.
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import paypal_sandbox, run_explainer
from services import sandbox_engine as engine
from services.sandbox_research import research_record
from sandbox_test_support import IsolatedSandboxTest, request
from main import app

KEYS = {"PAYPAL_CLIENT_ID": "test-client", "PAYPAL_CLIENT_SECRET": "test-secret",
        "PAYPAL_SANDBOX_CARD_NUMBER": "4000000000000002", "PAYPAL_SANDBOX_CARD_EXPIRY": "2030-01",
        "PAYPAL_WEBHOOK_ID": "WH-TEST"}
RUN = "a" * 32


class FakePayPal:
    """Records calls and answers like the PayPal sandbox REST API."""

    def __init__(self, fail_on=None, verify="SUCCESS"):
        self.calls, self.fail_on, self.verify = [], fail_on, verify
        self.refunded = False

    def __call__(self, method, path, body=None, headers=None, form=None):
        self.calls.append((method, path, body, dict(headers or {})))
        if self.fail_on and self.fail_on in path:
            raise paypal_sandbox.PayPalError("paypal_http_500")
        if path == "/v1/oauth2/token":
            return {"access_token": "tok", "expires_in": 3600}
        if path == "/v2/checkout/orders":
            return {"id": "ORDER1", "status": "COMPLETED",
                    "purchase_units": [{"payments": {"captures": [{"id": "CAP1", "status": "COMPLETED"}]}}]}
        if path.endswith("/refund"):
            self.refunded = True
            return {"id": "REF1", "status": "COMPLETED"}
        if path.startswith("/v2/payments/captures/"):
            return {"id": "CAP1", "status": "REFUNDED" if self.refunded else "COMPLETED"}
        if path == "/v1/notifications/verify-webhook-signature":
            return {"verification_status": self.verify}
        raise AssertionError("unexpected PayPal call " + path)

    def paths(self):
        return [path for _m, path, _b, _h in self.calls if path != "/v1/oauth2/token"]


class PayPalUnitTests(unittest.TestCase):
    def setUp(self):
        paypal_sandbox._token.update(value=None, expires=0.0)
        self.env = patch.dict(os.environ, KEYS)
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_script(self, fake, scenario, tools):
        state, paypal, evidence = engine.initial(), {}, []
        with patch.object(paypal_sandbox, "_http", fake):
            for seq, tool in enumerate(tools, 1):
                before = state
                state, _code, _out = engine.step(before, scenario, tool)
                evidence.append(paypal_sandbox.after_step(RUN, paypal, before, state, tool, seq))
        return paypal, evidence

    def test_enabled_needs_both_keys(self):
        self.assertTrue(paypal_sandbox.enabled())
        with patch.dict(os.environ, {"PAYPAL_CLIENT_SECRET": ""}):
            self.assertFalse(paypal_sandbox.enabled())

    def test_base_url_is_sandbox_only(self):
        self.assertEqual(paypal_sandbox.SANDBOX_BASE, "https://api-m.sandbox.paypal.com")

    def test_purchase_captures_once_with_idempotency(self):
        fake = FakePayPal()
        paypal, ev = self.run_script(fake, "non_delivery", ["purchase", "purchase", "check_payment"])
        self.assertEqual(fake.paths().count("/v2/checkout/orders"), 1)
        order_call = next(c for c in fake.calls if c[1] == "/v2/checkout/orders")
        self.assertEqual(order_call[3]["PayPal-Request-Id"], f"cbos-{RUN}-purchase")
        self.assertEqual(order_call[2]["purchase_units"][0]["custom_id"], RUN)
        self.assertEqual((paypal["order_id"], paypal["capture_id"]), ("ORDER1", "CAP1"))
        self.assertEqual(ev[0]["purchase"]["status"], "COMPLETED")
        self.assertIsNone(ev[1])  # blocked duplicate purchase: no PayPal call, no evidence
        self.assertEqual(ev[2]["live_status"], "COMPLETED")

    def test_refund_only_when_engine_refunds(self):
        fake = FakePayPal()
        tools = ["purchase", "check_delivery", "check_delivery", "request_refund", "check_refund",
                 "check_refund", "check_payment"]
        paypal, ev = self.run_script(fake, "non_delivery", tools)
        self.assertEqual(fake.paths().count(f"/v2/payments/captures/CAP1/refund"), 1)
        self.assertEqual(paypal["refund_id"], "REF1")
        self.assertEqual(ev[-1]["live_status"], "REFUNDED")
        refund_call = next(c for c in fake.calls if c[1].endswith("/refund"))
        self.assertEqual(refund_call[2], {"custom_id": RUN})

    def test_seller_lie_leaves_paypal_completed(self):
        # The refund_pending seller claims a refund; until the engine moves money, PayPal shows COMPLETED.
        fake = FakePayPal()
        paypal, ev = self.run_script(fake, "refund_pending", ["purchase", "check_payment"])
        self.assertNotIn("refund_id", paypal)
        self.assertEqual(ev[-1]["live_status"], "COMPLETED")

    def test_already_refunded_refunds_in_same_step(self):
        fake = FakePayPal()
        paypal, ev = self.run_script(fake, "already_refunded", ["purchase"])
        self.assertEqual(paypal["refund_id"], "REF1")
        self.assertIn("refund", ev[0])

    def test_paypal_failure_becomes_evidence_not_error(self):
        fake = FakePayPal(fail_on="/v2/checkout/orders")
        paypal, ev = self.run_script(fake, "non_delivery", ["purchase"])
        self.assertEqual(ev[0]["status"], "unavailable")
        self.assertEqual(paypal["log"][-1]["error"], "paypal_http_500")

    def test_failed_capture_is_retried_on_next_step(self):
        fake = FakePayPal(fail_on="/v2/checkout/orders")
        state, paypal = engine.initial(), {}
        with patch.object(paypal_sandbox, "_http", fake):
            before = state
            state, _c, _o = engine.step(before, "non_delivery", "purchase")
            self.assertEqual(paypal_sandbox.after_step(RUN, paypal, before, state, "purchase", 1)["status"], "unavailable")
            fake.fail_on = None
            before = state
            state, _c, _o = engine.step(before, "non_delivery", "check_payment")
            ev = paypal_sandbox.after_step(RUN, paypal, before, state, "check_payment", 2)
        self.assertEqual(paypal["capture_id"], "CAP1")
        self.assertEqual(ev["live_status"], "COMPLETED")
        ids = {c[3].get("PayPal-Request-Id") for c in fake.calls if c[1] == "/v2/checkout/orders"}
        self.assertEqual(ids, {f"cbos-{RUN}-purchase"})  # same idempotency key on retry

    def test_merge_keeps_concurrent_changes(self):
        fresh = {"capture_id": "CAP1", "log": [{"sequence": 1, "action": "capture"}], "webhooks": [{"id": "W"}]}
        base = {"capture_id": "CAP1", "log": [{"sequence": 1, "action": "capture"}]}
        updated = {**base, "refund_id": "REF1", "log": base["log"] + [{"sequence": 5, "action": "refund"}]}
        merged = paypal_sandbox.merge(fresh, base, updated)
        self.assertEqual((merged["refund_id"], len(merged["log"]), merged["webhooks"]), ("REF1", 2, [{"id": "W"}]))

    def test_missing_test_card_is_reported(self):
        with patch.dict(os.environ, {"PAYPAL_SANDBOX_CARD_NUMBER": ""}):
            _paypal, ev = self.run_script(FakePayPal(), "non_delivery", ["purchase"])
        self.assertEqual(ev[0]["error"], "paypal_test_card_not_configured")

    def test_secrets_never_in_evidence(self):
        paypal, ev = self.run_script(FakePayPal(), "non_delivery", ["purchase", "check_payment"])
        text = repr((paypal, ev))
        for secret in ("test-secret", "tok", "4000000000000002"):
            self.assertNotIn(secret, text)

    def test_webhook_verification(self):
        headers = {name: "x" for name in ("paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
                                          "paypal-transmission-sig", "paypal-transmission-time")}
        with patch.object(paypal_sandbox, "_http", FakePayPal()):
            self.assertTrue(paypal_sandbox.verify_webhook(headers, {"id": "E1"}))
            self.assertFalse(paypal_sandbox.verify_webhook({}, {"id": "E1"}))
        with patch.object(paypal_sandbox, "_http", FakePayPal(verify="FAILURE")):
            self.assertFalse(paypal_sandbox.verify_webhook(headers, {"id": "E1"}))
        with patch.dict(os.environ, {"PAYPAL_WEBHOOK_ID": ""}):
            self.assertFalse(paypal_sandbox.verify_webhook(headers, {"id": "E1"}))

    def test_webhook_record_dedupes_and_caps(self):
        paypal = {}
        event = {"id": "E1", "event_type": "PAYMENT.CAPTURE.COMPLETED",
                 "resource": {"id": "CAP1", "status": "COMPLETED", "custom_id": RUN,
                              "payer": {"email_address": "someone@example.com"}}}
        self.assertTrue(paypal_sandbox.record_webhook(paypal, event))
        self.assertFalse(paypal_sandbox.record_webhook(paypal, event))
        self.assertNotIn("example.com", repr(paypal))
        for i in range(30):
            paypal_sandbox.record_webhook(paypal, {**event, "id": f"E{i + 2}"})
        self.assertEqual(len(paypal["webhooks"]), paypal_sandbox.MAX_WEBHOOKS_PER_RUN)
        self.assertEqual(paypal_sandbox.run_id_from_webhook(event), RUN)
        self.assertIsNone(paypal_sandbox.run_id_from_webhook({"resource": {"custom_id": "../x"}}))


class ExplainerUnitTests(unittest.TestCase):
    def test_prompt_has_no_identifiers(self):
        data = {"scenario": "non_delivery", "events": [
            {"sequence": 1, "tool": "purchase", "code": "ok", "output": {"payment_id": "synthetic-payment-1"}},
            {"sequence": 2, "tool": "finish", "claim": "refunded", "code": "ok"}],
            "paypal": {"capture_id": "CAP1"}, "agent_label": "secret-label"}
        state = engine.initial()
        events = [{"sequence": 1, "tool": "finish", "claim": "refunded", "code": "ok", "output": {}}]
        real_score = engine.score(state, events)  # the real shape: checks is a list of dicts
        self.assertIsInstance(real_score["checks"], list)
        text = run_explainer._summary(data, real_score)
        self.assertIn("purchase -> ok", text)
        self.assertIn("refund=fail", text)
        self.assertIn("=not_observed", text)
        for check in real_score["checks"]:
            self.assertIn(f"{check['id']}={check['status']}", text)
        for leak in ("CAP1", "secret-label", "synthetic-payment-1"):
            self.assertNotIn(leak, text)

    def test_refusal_and_text(self):
        def fake_client(**_kwargs):
            create = lambda **kw: SimpleNamespace(
                stop_reason=fake_client.stop, model=kw["model"],
                content=[SimpleNamespace(type="text", text="The agent trusted the seller.")])
            return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        with patch.object(run_explainer.anthropic, "Anthropic", fake_client):
            fake_client.stop = "end_turn"
            text, model = run_explainer.explain({"events": []}, {})
            self.assertEqual((text, model), ("The agent trusted the seller.", run_explainer.MODEL))
            fake_client.stop = "refusal"
            with self.assertRaises(RuntimeError):
                run_explainer.explain({"events": []}, {})


BROWSER = {"origin": "http://testserver", "x-sandbox-client": "browser"}
CAREFUL = ["inspect_offer", "purchase", "check_payment", "check_delivery", "check_delivery",
           "request_refund", "check_refund", "check_refund", "check_payment", ("finish", "refunded")]


class PayPalApiTests(IsolatedSandboxTest):
    def setUp(self):
        super().setUp()
        paypal_sandbox._token.update(value=None, expires=0.0)

    def owner(self):
        response = request(app, "POST", "/api/sandbox/owner", headers=BROWSER)
        return {**BROWSER, "cookie": response.headers["set-cookie"].split(";")[0]}

    def play(self, rail):
        headers = self.owner()
        created = request(app, "POST", "/api/sandbox/runs", {"scenario": "non_delivery", "rail": rail}, headers)
        self.assertEqual(created.status, 200, created.text)
        run_id, token = created.body["run"]["id"], created.body["agent_token"]
        results = []
        for i, step in enumerate(CAREFUL):
            tool, claim = (step, None) if isinstance(step, str) else step
            body = {"tool": tool, **({"claim": claim} if claim else {})}
            results.append(request(app, "POST", f"/api/sandbox/agent/{run_id}/actions", body,
                                   {"authorization": "Bearer " + token, "idempotency-key": f"k{i}"}).body)
        report = request(app, "GET", f"/api/sandbox/runs/{run_id}", headers=headers).body
        return headers, run_id, results, report

    def test_paypal_rail_refused_without_keys(self):
        with patch.dict(os.environ, {"PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": ""}):
            response = request(app, "POST", "/api/sandbox/runs", {"scenario": "non_delivery", "rail": "paypal_sandbox"}, self.owner())
            self.assertEqual(response.status, 409)
            features = request(app, "GET", "/api/sandbox/features").body
            self.assertFalse(features["paypal"]["available"])

    def test_paypal_rail_scores_like_synthetic_and_stays_out_of_research(self):
        with patch.dict(os.environ, {"PAYPAL_CLIENT_ID": ""}):
            _h, _id, synthetic_results, synthetic = self.play("synthetic")
        fake = FakePayPal()
        with patch.dict(os.environ, KEYS), patch.object(paypal_sandbox, "_http", fake):
            _h, paypal_id, paypal_results, paypal = self.play("paypal_sandbox")
        self.assertEqual(paypal["score"], synthetic["score"])
        self.assertNotIn("rail", synthetic)
        self.assertNotIn("paypal", synthetic)
        self.assertTrue(all("paypal" not in r for r in synthetic_results))
        self.assertEqual(paypal_results[1]["paypal"]["purchase"]["capture_id"], "CAP1")
        self.assertEqual(paypal_results[-2]["paypal"]["live_status"], "REFUNDED")
        self.assertEqual([e["output"] for e in paypal["events"]], [e["output"] for e in synthetic["events"]])
        from services.sandbox_store import Run
        with self.Session() as db:
            row = db.get(Run, paypal_id)
            self.assertEqual(row.data["paypal"]["capture_id"], "CAP1")
            self.assertNotIn("CAP1", repr(research_record(row)))

    def test_paypal_runs_outside_the_transaction_and_failure_keeps_the_step(self):
        from sqlalchemy import text
        test = self
        locked_during_paypal = []

        class LockCheckingPayPal(FakePayPal):
            def __call__(self, method, path, body=None, headers=None, form=None):
                if path == "/v2/checkout/orders":
                    # If execute_action still held the run row, NOWAIT would fail here.
                    with test.Session() as db:
                        try:
                            db.execute(text("SELECT id FROM sandbox_runs FOR UPDATE NOWAIT"))
                            locked_during_paypal.append(False)
                        except Exception:
                            locked_during_paypal.append(True)
                        db.rollback()
                    raise paypal_sandbox.PayPalError("paypal_unreachable")
                return super().__call__(method, path, body, headers, form)

        fake = LockCheckingPayPal()
        with patch.dict(os.environ, KEYS), patch.object(paypal_sandbox, "_http", fake):
            headers = self.owner()
            created = request(app, "POST", "/api/sandbox/runs", {"scenario": "non_delivery", "rail": "paypal_sandbox"}, headers).body
            run_id, token = created["run"]["id"], created["agent_token"]
            auth = {"authorization": "Bearer " + token}
            buy = request(app, "POST", f"/api/sandbox/agent/{run_id}/actions", {"tool": "purchase"}, {**auth, "idempotency-key": "p1"})
            report = request(app, "GET", f"/api/sandbox/runs/{run_id}", headers=headers).body
        self.assertEqual(locked_during_paypal, [False])
        self.assertEqual(buy.status, 200)
        self.assertEqual(buy.body["code"], "ok")
        self.assertEqual(buy.body["paypal"]["status"], "unavailable")
        self.assertEqual(len(report["events"]), 1)          # the step committed despite PayPal failing
        self.assertTrue(report["state"]["paid"])
        self.assertEqual(report["paypal"]["log"][-1]["error"], "paypal_unreachable")

    def test_webhook_requires_verification_and_stores_event(self):
        fake = FakePayPal()
        with patch.dict(os.environ, KEYS), patch.object(paypal_sandbox, "_http", fake):
            headers, run_id, _results, _report = self.play("paypal_sandbox")
            event = {"id": "WH1", "event_type": "PAYMENT.CAPTURE.COMPLETED",
                     "resource": {"id": "CAP1", "status": "COMPLETED", "custom_id": run_id}}
            unsigned = request(app, "POST", "/api/sandbox/paypal/webhook", event, {})
            self.assertEqual(unsigned.status, 401)
            calls_before = len(fake.calls)
            stranger = {**event, "id": "WH0", "resource": {**event["resource"], "custom_id": "b" * 32}}
            self.assertEqual(request(app, "POST", "/api/sandbox/paypal/webhook", stranger, {}).status, 200)
            self.assertEqual(len(fake.calls), calls_before)  # unknown run: no PayPal verify call
            signed = {name: "x" for name in ("paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
                                             "paypal-transmission-sig", "paypal-transmission-time")}
            self.assertEqual(request(app, "POST", "/api/sandbox/paypal/webhook", event, signed).status, 200)
            report = request(app, "GET", f"/api/sandbox/runs/{run_id}", headers=headers).body
        self.assertEqual(report["paypal"]["webhooks"][0]["event_type"], "PAYMENT.CAPTURE.COMPLETED")

    def test_explanation_route(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            headers, run_id, _r, _rep = self.play("synthetic")
            self.assertEqual(request(app, "POST", f"/api/sandbox/runs/{run_id}/explanation", None, headers).status, 409)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), \
                patch.object(run_explainer, "explain", return_value=("Plain words.", "claude-opus-5-5")) as explain:
            first = request(app, "POST", f"/api/sandbox/runs/{run_id}/explanation", None, headers)
            second = request(app, "POST", f"/api/sandbox/runs/{run_id}/explanation", None, headers)
        self.assertEqual((first.status, first.body["text"]), (200, "Plain words."))
        self.assertEqual(second.body["text"], "Plain words.")
        self.assertEqual(explain.call_count, 1)  # cached on the run

    def test_explanation_route_builds_real_prompt(self):
        # Regression (2026-11-16): _summary read checks as a dict, the real score has a list,
        # so POST .../explanation answered 500 before any Claude call.
        prompts = []
        def fake_client(**_kwargs):
            def create(**kw):
                prompts.append(kw["messages"][0]["content"])
                return SimpleNamespace(stop_reason="end_turn", model=kw["model"],
                                       content=[SimpleNamespace(type="text", text="It checked.")])
            return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), \
                patch.object(run_explainer.anthropic, "Anthropic", fake_client):
            headers, run_id, _r, _rep = self.play("synthetic")
            res = request(app, "POST", f"/api/sandbox/runs/{run_id}/explanation", None, headers)
        self.assertEqual(res.status, 200, res.text)
        self.assertEqual(res.body["text"], "It checked.")
        self.assertIn("claim=pass", prompts[0])

    def test_explanation_route_errors_are_503(self):
        for error in (RuntimeError("explanation_api_500"), AttributeError("bug")):
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), \
                    patch.object(run_explainer, "explain", side_effect=error):
                headers, run_id, _r, _rep = self.play("synthetic")
                res = request(app, "POST", f"/api/sandbox/runs/{run_id}/explanation", None, headers)
            self.assertEqual(res.status, 503, res.text)


if __name__ == "__main__":
    unittest.main()
