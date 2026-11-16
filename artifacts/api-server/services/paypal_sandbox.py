"""PayPal sandbox payment rail (optional).

When a run uses the PayPal sandbox rail, the agent's purchase and the real
(engine-decided) refund also move sandbox money on PayPal, and the agent's
payment/refund checks read the live status from PayPal. The engine and scorer
are unchanged: PayPal is evidence next to the synthetic state, never input to it.

Sandbox only: the base URL is fixed to PayPal's sandbox and cannot be changed by
configuration. Keys come from the environment (Replit secrets) and are never
logged or returned.
"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
# Per call. Calls run outside the database transaction (see sandbox_api.execute_action).
TIMEOUT = 6
PRICE = "20.00"
CURRENCY = "USD"
MAX_WEBHOOKS_PER_RUN = 20

_token = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()


class PayPalError(Exception):
    """A PayPal call failed. The message is safe to show (no secrets)."""


def _env(name):
    return os.environ.get(name, "").strip()


def enabled():
    """True when sandbox API keys are configured."""
    return bool(_env("PAYPAL_CLIENT_ID") and _env("PAYPAL_CLIENT_SECRET"))


def status():
    return {"available": enabled(), "environment": "sandbox",
            "webhooks": bool(_env("PAYPAL_WEBHOOK_ID"))}


def _http(method, path, body=None, headers=None, form=None):
    url = SANDBOX_BASE + path
    data = None
    hdrs = {"Accept": "application/json", **(headers or {})}
    if form is not None:
        data = form.encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read() or b"{}").get("name", "")
        except (ValueError, AttributeError):
            pass
        raise PayPalError(f"paypal_http_{exc.code}" + (f"_{detail}" if detail else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise PayPalError("paypal_unreachable") from None


def _access_token():
    with _token_lock:
        if _token["value"] and _token["expires"] > time.time() + 60:
            return _token["value"]
        basic = base64.b64encode(f"{_env('PAYPAL_CLIENT_ID')}:{_env('PAYPAL_CLIENT_SECRET')}".encode()).decode()
        result = _http("POST", "/v1/oauth2/token", headers={"Authorization": "Basic " + basic},
                       form="grant_type=client_credentials")
        if not result.get("access_token"):
            raise PayPalError("paypal_auth_failed")
        _token["value"] = result["access_token"]
        _token["expires"] = time.time() + int(result.get("expires_in", 300))
        return _token["value"]


def _api(method, path, body=None, request_id=None):
    headers = {"Authorization": "Bearer " + _access_token()}
    if request_id:
        # PayPal's own idempotency: a retried call never pays or refunds twice.
        headers["PayPal-Request-Id"] = request_id
    return _http(method, path, body=body, headers=headers)


def _card():
    number, expiry = _env("PAYPAL_SANDBOX_CARD_NUMBER"), _env("PAYPAL_SANDBOX_CARD_EXPIRY")
    if not (number and expiry):
        raise PayPalError("paypal_test_card_not_configured")
    return {"number": number, "expiry": expiry, "name": "Sandbox Agent"}


def capture_purchase(run_id, request_id):
    """Create and capture a sandbox order paid with a PayPal sandbox test card."""
    order = _api("POST", "/v2/checkout/orders", request_id=request_id, body={
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": "research-dataset-v1", "custom_id": run_id,
                            "description": "Chargeback OS sandbox: research-dataset-v1",
                            "amount": {"currency_code": CURRENCY, "value": PRICE}}],
        "payment_source": {"card": _card()},
    })
    captures = (order.get("purchase_units") or [{}])[0].get("payments", {}).get("captures", [])
    if not captures:
        raise PayPalError("paypal_capture_missing")
    return {"order_id": order.get("id"), "capture_id": captures[0].get("id"),
            "status": captures[0].get("status", order.get("status"))}


def refund_capture(capture_id, request_id, run_id):
    # custom_id lets the refund webhook find its run, like the order's custom_id does.
    refund = _api("POST", f"/v2/payments/captures/{capture_id}/refund", body={"custom_id": run_id}, request_id=request_id)
    return {"refund_id": refund.get("id"), "status": refund.get("status")}


def capture_status(capture_id):
    capture = _api("GET", f"/v2/payments/captures/{capture_id}")
    return {"capture_id": capture_id, "status": capture.get("status")}


def verify_webhook(headers, event):
    """Ask PayPal whether a webhook is genuine. False on any doubt."""
    webhook_id = _env("PAYPAL_WEBHOOK_ID")
    if not (enabled() and webhook_id):
        return False
    names = ("paypal-auth-algo", "paypal-cert-url", "paypal-transmission-id",
             "paypal-transmission-sig", "paypal-transmission-time")
    values = {name: headers.get(name, "") for name in names}
    if not all(values.values()):
        return False
    try:
        result = _api("POST", "/v1/notifications/verify-webhook-signature", body={
            "auth_algo": values["paypal-auth-algo"], "cert_url": values["paypal-cert-url"],
            "transmission_id": values["paypal-transmission-id"],
            "transmission_sig": values["paypal-transmission-sig"],
            "transmission_time": values["paypal-transmission-time"],
            "webhook_id": webhook_id, "webhook_event": event,
        })
    except PayPalError:
        return False
    return result.get("verification_status") == "SUCCESS"


def _log(paypal, sequence, action, outcome):
    paypal.setdefault("log", []).append({"sequence": sequence, "action": action, **outcome})


def after_step(run_id, paypal, before, state, tool, sequence):
    """Mirror the engine's money movements on PayPal and read live status.

    Mutates `paypal` (the run's PayPal record) and returns evidence for the
    agent's response, or None. Never raises: failures become evidence.
    """
    evidence = {}
    try:
        # Keyed on state, not the transition: a failed capture is retried on the next step (same PayPal-Request-Id, so never twice).
        if state["paid"] and not paypal.get("capture_id"):
            purchase = capture_purchase(run_id, f"cbos-{run_id}-purchase")
            paypal.update(order_id=purchase["order_id"], capture_id=purchase["capture_id"])
            _log(paypal, sequence, "capture", purchase)
            evidence["purchase"] = purchase
        if state["refunded"] and paypal.get("capture_id") and not paypal.get("refund_id"):
            refund = refund_capture(paypal["capture_id"], f"cbos-{run_id}-refund", run_id)
            paypal["refund_id"] = refund["refund_id"]
            _log(paypal, sequence, "refund", refund)
            evidence["refund"] = refund
        if tool in ("check_payment", "check_refund") and paypal.get("capture_id"):
            live = capture_status(paypal["capture_id"])
            evidence["live_status"] = live["status"]
    except PayPalError as exc:
        _log(paypal, sequence, "error", {"status": "unavailable", "error": str(exc)})
        evidence["status"] = "unavailable"
        evidence["error"] = str(exc)
    if not evidence:
        return None
    evidence.update(environment="sandbox", order_id=paypal.get("order_id"),
                    capture_id=paypal.get("capture_id"), refund_id=paypal.get("refund_id"))
    return evidence


def merge(fresh, base, updated):
    """Apply what after_step changed (updated vs. base) onto the run's current record."""
    for key in ("order_id", "capture_id", "refund_id"):
        if updated.get(key) and not fresh.get(key):
            fresh[key] = updated[key]
    new_log = updated.get("log", [])[len(base.get("log", [])):]
    if new_log:
        fresh.setdefault("log", []).extend(new_log)
        del fresh["log"][:-50]
    return fresh


def record_webhook(paypal, event):
    """Store a verified webhook's essentials on the run (no payer details)."""
    resource = event.get("resource") or {}
    hooks = paypal.setdefault("webhooks", [])
    if any(h["id"] == event.get("id") for h in hooks):
        return False
    hooks.append({"id": event.get("id"), "event_type": event.get("event_type"),
                  "resource_id": resource.get("id"), "status": resource.get("status"),
                  "received_at": time.time()})
    del hooks[:-MAX_WEBHOOKS_PER_RUN]
    return True


def run_id_from_webhook(event):
    """Our run id travels as the purchase unit's custom_id."""
    resource = event.get("resource") or {}
    custom = resource.get("custom_id")
    if not custom:
        custom = ((resource.get("purchase_units") or [{}])[0]).get("custom_id")
    if isinstance(custom, str) and len(custom) == 32 and all(c in "0123456789abcdef" for c in custom):
        return custom
    return None
