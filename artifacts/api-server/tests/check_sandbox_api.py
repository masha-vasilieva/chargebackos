"""Integration checks against running development API, using synthetic cases only."""
import concurrent.futures
import http.cookiejar
import json
import os
import urllib.request
import urllib.error
import uuid

BASE = os.environ.get("SANDBOX_TEST_URL", "http://127.0.0.1:8080")


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def call(browser, path, method="GET", body=None, token=None, key=None, browser_headers=True):
    headers = {"Content-Type": "application/json"}
    if browser_headers:
        headers.update({"Origin": BASE, "X-Sandbox-Client": "browser"})
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(BASE + "/api/sandbox" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with browser.open(req) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def main():
    a, b, agent = client(), client(), client()
    created = []
    try:
        assert call(a, "/owner", "POST", browser_headers=False)[0] == 403
        assert call(a, "/owner", "POST")[0] == 200
        assert call(b, "/owner", "POST")[0] == 200
        status, payload = call(a, "/runs", "POST", {"scenario": "non_delivery"})
        assert status == 200, payload
        run = payload["run"]["id"]
        created.append(run)
        token = payload["agent_token"]
        resume_path = f"/agent/{run}/resume"
        assert call(agent, resume_path, token=token)[1]["remaining_actions"] == 100
        assert call(a, resume_path)[0] == 401  # Owner cookie is not agent authorization.
        for query in ["after=-1", "after=101", "after=x", "page_size=0", "page_size=51"]:
            assert call(agent, resume_path + "?" + query, token=token)[0] == 422
        assert payload["run"]["consent"] is False
        assert call(b, f"/runs/{run}")[0] == 404
        assert call(agent, f"/runs/{run}", token=token)[0] == 401
        assert call(agent, f"/runs/{run}/consent", "PATCH", {"consent": True}, token=token)[0] == 401
        def action(tool, key=None, claim=None):
            return call(agent, f"/agent/{run}/actions", "POST", {"tool": tool, **({"claim": claim} if claim else {})},
                        token=token, key=key or uuid.uuid4().hex, browser_headers=False)
        assert action("purchase", "purchase-once")[0] == 200
        assert action("purchase", "purchase-once")[1]["replayed"] is True
        assert action("check_payment", "purchase-once")[0] == 409
        assert call(a, f"/runs/{run}")[1]["state"]["balance"] == 80
        assert call(agent, f"/agent/{run}/actions", "POST", {"tool": "purchase", "secret": "synthetic-only"}, token=token, key="unknown")[0] == 422
        assert call(agent, f"/agent/{run}/actions", "POST", {"tool": "finish"}, token=token, key="finish-bad")[0] == 422
        assert call(agent, f"/agent/{run}/actions", "POST", {"tool": "purchase", "extra": "x"*17000}, token=token, key="oversize")[0] == 413
        for tool in ["check_payment", "check_delivery", "request_refund", "check_refund", "check_refund"]:
            assert action(tool)[0] == 200
        assert action("finish", claim="refunded")[0] == 200
        report = call(a, f"/runs/{run}")[1]
        assert report["score"]["task_completed"] and report["score"]["safe"], report
        assert report["state"]["balance"] == 100
        assert "idempotency" not in report and "agent_hash" not in report
        assert call(a, f"/runs/{run}/research-preview")[0] == 200
        assert call(a, f"/runs/{run}/consent", "PATCH", {"consent": True, "preview_acknowledged": True})[1]["consent"]
        assert not call(a, f"/runs/{run}/consent", "PATCH", {"consent": False})[1]["consent"]
        assert call(a, f"/runs/{run}/export")[0] == 200
        status, payload = call(a, "/runs", "POST", {"scenario": "refund_trap"})
        assert status == 200, payload
        second = payload["run"]["id"]
        created.append(second)
        second_token = payload["agent_token"]
        assert call(agent, f"/agent/{second}/resume", token=token)[0] == 401
        assert call(agent, resume_path, token=second_token)[0] == 401
        # Parallel retries must result in one event and one debit.
        def purchase(_):
            return call(client(), f"/agent/{second}/actions", "POST", {"tool": "purchase"}, token=second_token, key="parallel-purchase", browser_headers=False)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(purchase, range(4)))
        assert all(r[0] == 200 for r in results), results
        second_report = call(a, f"/runs/{second}")[1]
        assert second_report["state"]["balance"] == 80 and len(second_report["events"]) == 1
        new_token = call(a, f"/runs/{second}/token", "POST")[1]["agent_token"]
        assert call(agent, f"/agent/{second}/resume", token=second_token)[0] == 401
        assert call(agent, f"/agent/{second}/resume", token=new_token)[0] == 200
        assert call(agent, f"/agent/{second}/actions", "POST", {"tool": "check_payment"}, token=second_token, key="old")[0] == 401
        assert call(agent, f"/agent/{second}/actions", "POST", {"tool": "send_secret"}, token=new_token, key="trap")[1]["code"] == "unauthorized_action_blocked"
        assert call(a, f"/runs/{second}/revoke", "POST")[1]["state"]["status"] == "revoked"
        assert call(agent, f"/agent/{second}/resume", token=new_token)[0] == 401
        assert call(agent, f"/agent/{second}/actions", "POST", {"tool": "check_payment"}, token=new_token, key="revoked")[0] == 401
        assert call(a, "/example")[1]["score"]["task_completed"]
        print("PASS: owner/agent isolation, CSRF, strict payloads, size cap, idempotency, concurrency, recovery, consent, export, rotation and revocation.")
    finally:
        for run in created:
            assert call(a, f"/runs/{run}", "DELETE")[0] == 200
            assert call(a, f"/runs/{run}")[0] == 404
        print("PASS: test runs deleted and inaccessible.")


if __name__ == "__main__":
    main()
