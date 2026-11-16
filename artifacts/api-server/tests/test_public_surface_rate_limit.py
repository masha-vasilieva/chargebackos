"""Pre-auth per-client limits for public MCP and OAuth surfaces."""
import hashlib
import hmac
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import text
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.sandbox_security import (
    ClientIpSelectionMiddleware,
    Ingress,
    PUBLIC_RATE_LIMITS,
    ProxyPeerObservationMiddleware,
    TRUSTED_REPLIT_PROXY_IPS,
    configured_client_ip_hops,
    configured_rate_diagnostic_sample,
    public_client_fingerprint,
    public_rate_group,
)
from sandbox_test_support import IsolatedSandboxTest


class AtomicCounter:
    """A small shared counter stand-in used by separate simulated app instances."""

    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        self.keys = []
        self.test_buckets = {}

    def __call__(self, key, seconds):
        with self.lock:
            bucket = self.test_buckets.setdefault(
                seconds, int(time.time()) // seconds
            )
            previous_bucket, previous_count = self.rows.get(key, (None, 0))
            count = previous_count + 1 if previous_bucket == bucket else 1
            self.rows[key] = (bucket, count)
            self.keys.append(key)
        return count, bucket


def limited_test_app(rate_counter=None, rate_limits=None):
    app = FastAPI()
    app.add_middleware(
        Ingress,
        rate_counter=rate_counter or AtomicCounter(),
        rate_limits=rate_limits,
        rate_hash_secret="test-rate-hash-secret",
    )
    calls = {"mcp": 0, "register": 0, "health": 0}

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"])
    async def mcp():
        calls["mcp"] += 1
        return JSONResponse({"detail": "OAuth authorization required."}, status_code=401)

    @app.post("/api/sandbox/oauth/register")
    async def register():
        calls["register"] += 1
        return JSONResponse({"client_id": "test-client"}, status_code=201)

    @app.get("/api/healthz")
    async def healthz():
        calls["health"] += 1
        return {"status": "ok"}

    return app, calls


def proxy_wrapped_app(app, hops_override=None):
    return ProxyPeerObservationMiddleware(
        ProxyHeadersMiddleware(
            ClientIpSelectionMiddleware(app, hops_override=hops_override),
            trusted_hosts=TRUSTED_REPLIT_PROXY_IPS,
        )
    )


class PublicSurfaceRateLimitTests(unittest.TestCase):
    def test_all_public_oauth_and_metadata_paths_are_classified(self):
        cases = {
            "/api/sandbox/oauth/register": "oauth-register",
            "/api/sandbox/oauth/authorize": "oauth-authorize",
            "/api/sandbox/oauth/token": "oauth-token",
            "/api/sandbox/oauth/revoke": "oauth-revoke",
            "/.well-known/oauth-authorization-server": "oauth-metadata",
            "/.well-known/oauth-protected-resource": "oauth-metadata",
            "/.well-known/oauth-protected-resource/api/sandbox/agent/run/mcp": "oauth-metadata",
            "/api/sandbox/oauth/resource/run": "oauth-metadata",
            "/api/sandbox/paypal/webhook": "paypal-webhook",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(public_rate_group(path), expected)

    def test_diagnostic_sample_setting_is_configurable_and_defaults_to_full_sampling(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(configured_rate_diagnostic_sample(), 1.0)
        with patch.dict("os.environ", {"SANDBOX_RATE_DIAGNOSTIC_SAMPLE": "0.25"}):
            self.assertEqual(configured_rate_diagnostic_sample(), 0.25)

    def test_unauthenticated_mcp_is_limited_before_auth_for_all_methods(self):
        counter = AtomicCounter()
        app, calls = limited_test_app(rate_counter=counter)
        with TestClient(app, client=("198.51.100.23", 12345)) as client:
            methods = ("GET", "POST", "DELETE")
            for index in range(PUBLIC_RATE_LIMITS["mcp"]):
                response = client.request(methods[index % len(methods)], "/mcp")
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["ratelimit-limit"], "120")

            response = client.get("/mcp")

        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)
        self.assertEqual(response.headers["ratelimit-limit"], "120")
        self.assertEqual(response.headers["ratelimit-remaining"], "0")
        self.assertGreaterEqual(int(response.headers["ratelimit-reset"]), 1)
        self.assertEqual(response.json(), {"detail": "Rate limit exceeded. Retry shortly."})
        self.assertEqual(calls["mcp"], PUBLIC_RATE_LIMITS["mcp"])

    def test_registration_has_a_stricter_pre_auth_ip_limit(self):
        counter = AtomicCounter()
        app, calls = limited_test_app(rate_counter=counter)
        with TestClient(app, client=("198.51.100.23", 12345)) as client:
            for _ in range(PUBLIC_RATE_LIMITS["oauth-register"]):
                self.assertEqual(client.post("/api/sandbox/oauth/register").status_code, 201)
            response = client.post("/api/sandbox/oauth/register")

        self.assertEqual(response.status_code, 429)
        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)
        self.assertEqual(response.headers["ratelimit-limit"], "10")
        self.assertEqual(response.headers["ratelimit-remaining"], "0")
        self.assertEqual(calls["register"], PUBLIC_RATE_LIMITS["oauth-register"])
        self.assertEqual(len(counter.keys), PUBLIC_RATE_LIMITS["oauth-register"] + 1)

    def test_normal_mcp_call_volume_is_not_limited(self):
        app, calls = limited_test_app()
        with TestClient(app, client=("198.51.100.23", 12345)) as client:
            responses = [client.post("/mcp") for _ in range(40)]
        self.assertTrue(all(response.status_code == 401 for response in responses))
        self.assertEqual(calls["mcp"], 40)
        self.assertEqual(responses[-1].headers["ratelimit-remaining"], "80")

    def test_shared_counter_blocks_when_requests_are_spread_across_instances(self):
        counter = AtomicCounter()
        limits = {**PUBLIC_RATE_LIMITS, "mcp": 2}
        first_app, first_calls = limited_test_app(counter, limits)
        second_app, second_calls = limited_test_app(counter, limits)
        with (
            TestClient(first_app, client=("198.51.100.23", 10001)) as first,
            TestClient(second_app, client=("198.51.100.23", 10002)) as second,
        ):
            self.assertEqual(first.post("/mcp").status_code, 401)
            self.assertEqual(second.post("/mcp").status_code, 401)
            blocked = second.post("/mcp")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(first_calls["mcp"], 1)
        self.assertEqual(second_calls["mcp"], 1)
        self.assertEqual(len(set(counter.keys)), 1)

    def test_untrusted_client_cannot_spoof_x_forwarded_for_to_bypass_limit(self):
        counter = AtomicCounter()
        app, calls = limited_test_app(
            counter, {**PUBLIC_RATE_LIMITS, "mcp": 1}
        )
        proxy_wrapped = proxy_wrapped_app(app)
        with TestClient(proxy_wrapped, client=("198.51.100.23", 12345)) as client:
            first = client.post(
                "/mcp", headers={"X-Forwarded-For": "203.0.113.1"}
            )
            second = client.post(
                "/mcp", headers={"X-Forwarded-For": "203.0.113.2"}
            )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(calls["mcp"], 1)
        self.assertEqual(len(set(counter.keys)), 1)
        self.assertTrue(counter.keys[0].startswith("public-ip:v1:mcp:"))
        self.assertNotIn("198.51.100.23", counter.keys[0])
        self.assertNotIn("203.0.113.", counter.keys[0])

    def test_trusted_proxy_forwarding_selects_the_rightmost_untrusted_client_ip(self):
        counter = AtomicCounter()
        app, _ = limited_test_app(counter)
        proxy_wrapped = proxy_wrapped_app(app)
        with TestClient(proxy_wrapped, client=("10.2.3.4", 12345)) as client:
            response = client.post(
                "/mcp",
                headers={"X-Forwarded-For": "203.0.113.99, 198.51.100.23"},
            )
        expected_hash = hmac.new(
            b"test-rate-hash-secret",
            b"chargeback-os:public-ingress:v1:198.51.100.23",
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(counter.keys, [f"public-ip:v1:mcp:{expected_hash}"])

    def test_google_frontend_address_changes_keep_a_stable_client_key(self):
        counter = AtomicCounter()
        app, calls = limited_test_app(rate_counter=counter)
        proxy_wrapped = proxy_wrapped_app(app)
        with TestClient(proxy_wrapped, client=("10.2.3.4", 12345)) as client:
            first = client.post(
                "/mcp", headers={"X-Forwarded-For": "198.51.100.23, 35.191.0.10"}
            )
            second = client.post(
                "/mcp", headers={"X-Forwarded-For": "198.51.100.23, 130.211.0.8"}
            )
        expected_hash = public_client_fingerprint(
            ("198.51.100.23", 0), "test-rate-hash-secret"
        )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(calls["mcp"], 2)
        self.assertEqual(
            counter.keys,
            [
                f"public-ip:v1:mcp:{expected_hash}",
                f"public-ip:v1:mcp:{expected_hash}",
            ],
        )

    def test_spoofed_leading_forwarded_entry_does_not_change_default_key(self):
        counter = AtomicCounter()
        app, calls = limited_test_app(rate_counter=counter)
        proxy_wrapped = proxy_wrapped_app(app)
        with TestClient(proxy_wrapped, client=("10.2.3.4", 12345)) as client:
            first = client.post(
                "/mcp", headers={"X-Forwarded-For": "198.51.100.23, 35.191.0.10"}
            )
            second = client.post(
                "/mcp",
                headers={
                    "X-Forwarded-For":
                        "203.0.113.99, 198.51.100.23, 35.191.0.10"
                },
            )
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(calls["mcp"], 2)
        self.assertEqual(len(set(counter.keys)), 1)

    def test_client_ip_hops_environment_override_selects_nth_entry_from_right(self):
        counter = AtomicCounter()
        app, _ = limited_test_app(rate_counter=counter)
        with patch.dict("os.environ", {"SANDBOX_CLIENT_IP_HOPS": "2"}):
            hops_override = configured_client_ip_hops()
        proxy_wrapped = proxy_wrapped_app(app, hops_override=hops_override)
        with TestClient(proxy_wrapped, client=("10.2.3.4", 12345)) as client:
            response = client.post(
                "/mcp",
                headers={
                    "X-Forwarded-For":
                        "203.0.113.99, 198.51.100.23, 35.191.0.10"
                },
            )
        expected_hash = public_client_fingerprint(
            ("198.51.100.23", 0), "test-rate-hash-secret"
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(counter.keys, [f"public-ip:v1:mcp:{expected_hash}"])

    def test_database_failure_falls_back_to_local_limiter_and_does_not_leak_error(self):
        def unavailable(_key, _seconds):
            raise RuntimeError("database password=do-not-log")

        app, calls = limited_test_app(
            rate_counter=unavailable,
            rate_limits={**PUBLIC_RATE_LIMITS, "mcp": 1},
        )
        with self.assertLogs("sandbox", level="WARNING") as logs:
            with TestClient(app, client=("198.51.100.23", 12345)) as client:
                first = client.post("/mcp")
                second = client.post("/mcp")
        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(calls["mcp"], 1)
        self.assertNotIn("do-not-log", "\n".join(logs.output))

    def test_sampled_diagnostic_logs_only_hash_and_instance_not_ip_or_token(self):
        app, _ = limited_test_app()
        proxy_wrapped = proxy_wrapped_app(app)
        with patch("services.sandbox_security.random.random", return_value=0.0):
            with self.assertLogs("sandbox.public_rate_limit", level="INFO") as logs:
                with TestClient(
                    proxy_wrapped, client=("198.51.100.23", 12345)
                ) as client:
                    response = client.post(
                        "/mcp",
                        headers={
                            "Authorization": "Bearer never-log-this-token",
                            "X-Forwarded-For": "8.8.8.8",
                        },
                    )
        output = "\n".join(logs.output)
        self.assertIn("Public ingress rate sample", output)
        self.assertIn("client_key=", output)
        self.assertIn("instance=", output)
        self.assertIn("proxy_peer_trusted=no", output)
        self.assertIn("xff_entries=1", output)
        self.assertIn("xff_chain=right-1:other-public:", output)
        self.assertIn("client_key_position=peer", output)
        self.assertNotIn("client_key", response.text)
        self.assertNotIn("xff_chain", response.text)
        self.assertNotIn("client_key", "\n".join(
            f"{key}: {value}" for key, value in response.headers.items()
        ))
        self.assertNotIn("198.51.100.23", output)
        self.assertNotIn("never-log-this-token", output)

    def test_diagnostic_describes_every_forwarded_position_without_raw_addresses(self):
        app, _ = limited_test_app()
        proxy_wrapped = proxy_wrapped_app(app)
        with patch("services.sandbox_security.random.random", return_value=0.0):
            with self.assertLogs("sandbox.public_rate_limit", level="INFO") as logs:
                with TestClient(proxy_wrapped, client=("10.2.3.4", 12345)) as client:
                    client.post(
                        "/mcp",
                        headers={
                            "X-Forwarded-For":
                                "8.8.8.8, 35.191.2.3, 10.1.1.1"
                        },
                    )
        output = "\n".join(logs.output)
        self.assertIn("proxy_peer_trusted=yes", output)
        self.assertIn("xff_entries=3", output)
        self.assertIn("xff_positions_logged=3", output)
        self.assertIn("xff_truncated=no", output)
        self.assertIn("right-1:private:", output)
        self.assertIn("right-2:google-proxy:", output)
        self.assertIn("right-3:other-public:", output)
        self.assertIn("client_key_position=right-3", output)
        self.assertNotIn("8.8.8.8", output)
        self.assertNotIn("35.191.2.3", output)
        self.assertNotIn("10.1.1.1", output)

    def test_healthz_is_exempt_from_ip_and_database_rate_limits(self):
        def unavailable(_key, _seconds):
            raise AssertionError("healthz must not use the public limiter")

        app, calls = limited_test_app(rate_counter=unavailable)
        with TestClient(app, client=("198.51.100.23", 12345)) as client:
            responses = [client.get("/api/healthz") for _ in range(150)]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(all(response.json() == {"status": "ok"} for response in responses))
        self.assertEqual(calls["health"], 150)


class SharedDatabasePublicRateLimitTests(IsolatedSandboxTest):
    def test_postgres_counter_is_shared_across_independent_ingress_instances(self):
        limits = {**PUBLIC_RATE_LIMITS, "mcp": 2}
        fingerprint = public_client_fingerprint(
            ("198.51.100.23", 12345), "integration-rate-hash-secret"
        )
        counter_key = f"public-ip:v1:mcp:{fingerprint}"

        def app_with_new_ingress():
            app = FastAPI()
            app.add_middleware(
                Ingress,
                rate_limits=limits,
                rate_hash_secret="integration-rate-hash-secret",
            )

            @app.post("/mcp")
            async def mcp():
                return JSONResponse(
                    {"detail": "OAuth authorization required."}, status_code=401
                )

            return app

        first_app = app_with_new_ingress()
        second_app = app_with_new_ingress()
        try:
            with (
                TestClient(first_app, client=("198.51.100.23", 12345)) as first,
                TestClient(second_app, client=("198.51.100.23", 12346)) as second,
            ):
                self.assertEqual(first.post("/mcp").status_code, 401)
                self.assertEqual(second.post("/mcp").status_code, 401)
                blocked = second.post("/mcp")

            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.headers["ratelimit-limit"], "2")
            self.assertEqual(blocked.headers["ratelimit-remaining"], "0")
            with self.Session() as db:
                row = db.execute(
                    text("SELECT bucket, count FROM sandbox_limits WHERE key = :key"),
                    {"key": counter_key},
                ).one()
            self.assertEqual(row.count, 3)
            self.assertNotIn("198.51.100.23", counter_key)
        finally:
            with self.Session.begin() as db:
                db.execute(
                    text("DELETE FROM sandbox_limits WHERE key = :key"),
                    {"key": counter_key},
                )
