"""Real HTTP official MCP OAuth-only client, with simulated explicit operator UI.

Use SANDBOX_TEST_URL=https://<configured OAuth origin>. Never logs credentials.
Browser click verification is separate; this exercises the unmodified SDK OAuth
client, not a hand-written token/header client.
"""
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthTokenError
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from check_sandbox_api import BASE


class MemoryStorage:
    def __init__(self):
        self.tokens = None
        self.client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


class ApprovalFailure(Exception):
    """Sanitized operator-side error propagated to the SDK caller."""


class AbandonedLifecycleFailure(Exception):
    """Controlled early exit used to verify disposable OAuth cleanup."""


class LostApprovalResponse(httpx.ReadError):
    """A committed approval whose redirect never reaches the operator."""


class DropApprovalResponseTransport(httpx.AsyncBaseTransport):
    """Drop one successful operator POST, never exposing its code to the SDK."""

    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.approval_requests = 0
        self.dropped = False
        # In-memory negative-probe material only; never printed or persisted.
        self.redirect: str | None = None
        self.request_id: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        is_approval = (request.method == "POST" and
                       request.url.path.startswith("/api/sandbox/oauth/requests/"))
        if is_approval:
            self.approval_requests += 1
        response = await self.inner.handle_async_request(request)
        if is_approval and response.status_code == 200 and not self.dropped:
            try:
                await response.aread()
                self.redirect = response.json()["redirect"]
                self.request_id = request.url.path.rsplit("/", 1)[1]
            finally:
                await response.aclose()
            self.dropped = True
            raise LostApprovalResponse("approval response lost", request=request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class LostRegistrationResponse(httpx.ReadError):
    """A committed registration whose response never reaches the provider."""


class DropRegistrationResponseTransport(httpx.AsyncBaseTransport):
    """Drop one committed registration; retain only its hashed cleanup key."""

    def __init__(self, cleanup_ids: set[str]):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.cleanup_ids = cleanup_ids
        self.registration_id: str | None = None
        self.registration_requests = 0
        self.token_requests = 0
        self.dropped = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        is_registration = (request.method == "POST" and
                           request.url.path == "/api/sandbox/oauth/register")
        if is_registration:
            self.registration_requests += 1
        if request.method == "POST" and request.url.path == "/api/sandbox/oauth/token":
            self.token_requests += 1
        response = await self.inner.handle_async_request(request)
        if is_registration and response.status_code == 201 and not self.dropped:
            try:
                await response.aread()
                # Do not persist or print the response or plaintext client ID.
                self.registration_id = "client:" + hashlib.sha256(
                    response.json()["client_id"].encode()).hexdigest()
                self.cleanup_ids.add(self.registration_id)
            finally:
                await response.aclose()
            self.dropped = True
            raise LostRegistrationResponse("registration response lost", request=request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class LostTokenResponse(httpx.ReadError):
    """A committed exchange whose response never reaches the OAuth provider."""


class LostDenialResponse(httpx.ReadError):
    """A committed denial whose response never reaches the simulated UI."""


class DropDenialResponseTransport(httpx.AsyncBaseTransport):
    """Arm once to lose a real denial response after the server consumes it."""

    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.armed = False
        self.dropped = False
        self.decisions = 0

    async def handle_async_request(self, request):
        is_decision = (request.method == "POST" and
                       request.url.path.startswith("/api/sandbox/oauth/requests/"))
        is_denial = False
        if is_decision:
            self.decisions += 1
            is_denial = json.loads(await request.aread()) == {"approve": False}
        response = await self.inner.handle_async_request(request)
        if self.armed and is_denial and response.status_code == 200 and not self.dropped:
            try:
                await response.aread()
                values = parse_qs(urlsplit(response.json()["redirect"]).query)
                assert values.get("error") == ["access_denied"]
                assert values.get("state") and "code" not in values
            finally:
                await response.aclose()
            self.dropped = True
            self.armed = False
            raise LostDenialResponse("access-denial response lost", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


class DropTokenResponseTransport(httpx.AsyncBaseTransport):
    """Drop exactly one successful token response, without retrying the request."""

    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.exchange_url = None
        self.exchange_body = None
        self.token_requests = 0
        self.dropped = False

    async def handle_async_request(self, request):
        is_exchange = (request.method == "POST" and
                       request.url.path == "/api/sandbox/oauth/token")
        if is_exchange:
            self.token_requests += 1
        response = await self.inner.handle_async_request(request)
        if is_exchange and response.status_code == 200 and not self.dropped:
            try:
                # Read the real response to ensure the server finished redemption,
                # but never give its body (or token) to the SDK or the test log.
                await response.aread()
            finally:
                await response.aclose()
            self.exchange_url = str(request.url)
            self.exchange_body = await request.aread()
            self.dropped = True
            raise LostTokenResponse("token response lost", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


class LostRevocationResponse(httpx.ReadError):
    """A committed revocation whose successful response is lost."""


class DropRevocationResponseTransport(httpx.AsyncBaseTransport):
    """Drop one completed revocation response; never retry automatically."""

    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0)
        self.revocation_requests = 0
        self.dropped = False
        self.body = None

    async def handle_async_request(self, request):
        is_revocation = (request.method == "POST" and
                         request.url.path == "/api/sandbox/oauth/revoke")
        if is_revocation:
            self.revocation_requests += 1
        response = await self.inner.handle_async_request(request)
        if is_revocation and response.status_code == 200 and not self.dropped:
            try:
                # Consume and close the response only after the real server has
                # committed. Never expose the body or credentials in test logs.
                await response.aread()
            finally:
                await response.aclose()
            self.body = await request.aread()
            self.dropped = True
            raise LostRevocationResponse("revocation response lost", request=request)
        return response

    async def aclose(self):
        await self.inner.aclose()


def leaves(error):
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in leaves(child)]
    return [error]


async def expect_failure(operation, message, http_status=None, error_type=ApprovalFailure):
    try:
        await operation()
    except Exception as error:
        assert all(
            (isinstance(leaf, httpx.HTTPStatusError) and
             leaf.response.status_code == http_status) if http_status else
            (isinstance(leaf, error_type) and str(leaf) == message)
            for leaf in leaves(error)), "Unexpected SDK reconnect failure"
    else:
        raise AssertionError("Reconnect unexpectedly succeeded")
    print("PASS: SDK caller received " + message)


async def main():
    lifecycle = "--smoke-only" not in sys.argv
    if lifecycle:
        # Never mutate expiry on an arbitrary remote target or production DB.
        assert os.environ.get("NODE_ENV") != "production"
        assert os.environ.get("REPLIT_DEPLOYMENT") != "1"
        assert BASE == "https://" + os.environ.get("REPLIT_DEV_DOMAIN", ""), (
            "Lifecycle checks require SANDBOX_TEST_URL=https://$REPLIT_DEV_DOMAIN")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from database import Session
        from sqlalchemy import delete
        from services.sandbox_store import Owner, Run, digest
        from services.sandbox_oauth_store import OAuthRecord
        # Fail before creating fixtures if the development migration is missing.
        with Session() as db:
            db.query(OAuthRecord).limit(1).all()

    denial_drop = DropDenialResponseTransport()
    async with httpx.AsyncClient(base_url=BASE, transport=denial_drop, headers={
            "Origin": BASE, "X-Sandbox-Client": "browser"}) as owner:
        response = await owner.post("/api/sandbox/owner")
        response.raise_for_status()
        disposable_owner = digest(owner.cookies["sandbox_owner"]) if lifecycle else None
        created = await owner.post("/api/sandbox/runs", json={"scenario": "non_delivery"})
        created.raise_for_status()
        run = created.json()["run"]["id"]
        created_runs = [run]
        resource = BASE+f"/api/sandbox/agent/{run}/mcp"
        approval_run = run
        callback = None
        storage = MemoryStorage()
        approvals = 0
        redirects = 0
        callbacks = 0
        allow_approval = True
        deny_approval = False
        pending_requests = []
        clients = set()
        orphan_registration_ids: set[str] = set()
        responses = []
        token_resources = []
        rejected_cached_tokens = []

        def cleanup_lifecycle_records(run_ids, owner_ids, *, include_clients):
            record_filter = OAuthRecord.data["run"].astext.in_(run_ids)
            registration_ids = (
                ["client:" + digest(client) for client in clients] +
                list(orphan_registration_ids)
                if include_clients else []
            )
            if registration_ids:
                record_filter = record_filter | OAuthRecord.id.in_(registration_ids)
            with Session.begin() as db:
                if run_ids or registration_ids:
                    db.execute(delete(OAuthRecord).where(record_filter))
                if run_ids:
                    db.execute(delete(Run).where(Run.id.in_(run_ids)))
                if owner_ids:
                    db.execute(delete(Owner).where(Owner.id.in_(owner_ids)))

        try:
            async def approve(url, *, approval_client=None, page_owner=None):
                nonlocal callback, approvals, redirects
                redirects += 1
                callback = None
                request_owner = owner if page_owner is None else page_owner
                assert parse_qs(urlsplit(url).query).get("resource") == [
                    BASE+f"/api/sandbox/agent/{approval_run}/mcp"
                ], "SDK authorization must target the requested run resource"
                page = await request_owner.get(url)
                if page.status_code == 400:
                    raise ApprovalFailure("authorization: " + page.json()["error"])
                page.raise_for_status()
                pending = re.search(r"/requests/([A-Za-z0-9_-]+)", page.text)[1]
                pending_requests.append(pending)
                details = await request_owner.get("/api/sandbox/oauth/requests/"+pending)
                if details.status_code == 400:
                    raise ApprovalFailure("run unavailable: " + details.json()["error"])
                details.raise_for_status()
                assert details.json()["run_id"] == approval_run
                assert details.json()["research_consent"] is False
                if not allow_approval:
                    raise ApprovalFailure("fresh operator approval required")
                operator = request_owner if approval_client is None else approval_client
                approved = await operator.post("/api/sandbox/oauth/requests/"+pending,
                                               json={"approve": not deny_approval})
                approved.raise_for_status()
                if deny_approval:
                    raise ApprovalFailure("authorization: access_denied")
                approvals += 1
                values = parse_qs(urlsplit(approved.json()["redirect"]).query)
                callback = (values["code"][0], values["state"][0])

            async def receive_callback():
                nonlocal callbacks
                callbacks += 1
                assert callback is not None
                return callback

            async def observe(response, client_storage):
                # Store no headers, query strings, bodies or credential values.
                responses.append((response.request.url.path, response.status_code))
                if response.status_code == 401 and client_storage.tokens:
                    rejected_cached_tokens.append((
                        response.request.url.path,
                        response.request.headers.get("Authorization") ==
                        "Bearer " + client_storage.tokens.access_token))
                if response.request.url.path == "/api/sandbox/oauth/token":
                    # Inspect only the non-secret resource field, never retain the form.
                    token_resources.append(parse_qs(
                        response.request.content.decode()).get("resource"))

            async def connect(call=False, *, client_storage=None, transport=None, target_run=run,
                              approval_client=None, stale_code=None,
                              redirect_handler=None):
                nonlocal approval_run
                approval_run = target_run
                target_resource = BASE+f"/api/sandbox/agent/{target_run}/mcp"
                client_storage = storage if client_storage is None else client_storage

                async def redirect(url):
                    if redirect_handler is None:
                        await approve(url, approval_client=approval_client)
                    else:
                        await redirect_handler(url)

                async def observe_client(response):
                    await observe(response, client_storage)

                async def receive():
                    code, state = await receive_callback()
                    # A delayed old code paired with the current state must still
                    # fail PKCE at the server; do not patch the SDK itself.
                    return (stale_code if stale_code is not None else code), state

                auth = OAuthClientProvider(server_url=target_resource, storage=client_storage,
                    client_metadata=OAuthClientMetadata(
                        client_name="Official MCP SDK OAuth lifecycle test",
                        redirect_uris=["http://127.0.0.1:35421/callback"],
                        grant_types=["authorization_code"], response_types=["code"],
                        token_endpoint_auth_method="none", scope="sandbox:act"),
                    redirect_handler=redirect, callback_handler=receive)
                try:
                    async with asyncio.timeout(30):
                        async with httpx.AsyncClient(auth=auth, transport=transport,
                                                     event_hooks={"response": [observe_client]}) as agent:
                            async with streamable_http_client(target_resource, http_client=agent) as (read, write, _):
                                async with ClientSession(read, write) as session:
                                    await session.initialize()
                                    assert len((await session.list_tools()).tools) == 9
                                    if call:
                                        result = await session.call_tool("inspect_offer", {"idempotency_key": "oauth-smoke"})
                                        assert not result.isError
                                        assert json.loads(result.content[0].text)["sequence"] == 1
                            assert not agent.cookies
                finally:
                    if client_storage.client_info:
                        clients.add(client_storage.client_info.client_id)

            await connect(call=True)
            report = (await owner.get("/api/sandbox/runs/"+run)).json()
            assert report["consent"] is False and len(report["events"]) == 1
            assert storage.tokens and storage.tokens.scope == "sandbox:act"
            assert storage.tokens.refresh_token is None
            print("PASS: official MCP OAuth-only SDK discovery, registration, S256 PKCE, explicit approval, token exchange, initialize, tools/list, call; no owner cookies or consent changes.")
            if not lifecycle:
                return

            with Session() as db:
                row = db.get(Run, run)
                assert row is not None, "Target API must use this development database"
                original_expiry = row.expires
                original_lifetimes = (row.created, row.deletes)
                original_data = row.data

            def unchanged(expiry=original_expiry, status="active"):
                with Session() as db:
                    row = db.get(Run, run)
                    assert row.expires == expiry and row.consent is False
                    assert (row.created, row.deletes) == original_lifetimes
                    expected = {**original_data, "state": {**original_data["state"], "status": status}}
                    assert row.data == expected, "Reconnect changed run state or action ledger"

            def run_oauth_records():
                with Session() as db:
                    return {row.id: (row.expires, row.data) for row in
                            db.query(OAuthRecord).filter(
                                OAuthRecord.data["run"].astext == run).all()}

            def oauth_records_for(target_run):
                with Session() as db:
                    return {row.id: (row.expires, row.data) for row in
                            db.query(OAuthRecord).filter(
                                OAuthRecord.data["run"].astext == target_run).all()}

            # Force exits at both points where lifecycle setup can leave
            # run-scoped OAuth state behind: before approval, and after the
            # operator has minted a code but before the SDK exchanges it.
            probe_storage = MemoryStorage()
            async with httpx.AsyncClient(base_url=BASE, headers={
                    "Origin": BASE, "X-Sandbox-Client": "browser"}) as probe_owner:
                probe_owner_response = await probe_owner.post("/api/sandbox/owner")
                probe_owner_response.raise_for_status()
                probe_owner_id = digest(probe_owner.cookies["sandbox_owner"])
                probe_created = await probe_owner.post(
                    "/api/sandbox/runs", json={"scenario": "non_delivery"})
                probe_created.raise_for_status()
                probe_run = probe_created.json()["run"]["id"]
                probe_resource = BASE+f"/api/sandbox/agent/{probe_run}/mcp"
                pending_record_id = None
                code_record_id = None
                probe_registration_id = None
                probe_registration_snapshot = None
                probe_request_ids = []

                async def fail_with_pending_request(url):
                    assert parse_qs(urlsplit(url).query).get("resource") == [
                        probe_resource], "Early-exit probe targeted the wrong run"
                    page = await probe_owner.get(url)
                    page.raise_for_status()
                    request_id = re.search(
                        r"/requests/([A-Za-z0-9_-]+)", page.text)[1]
                    probe_request_ids.append(request_id)
                    details = await probe_owner.get(
                        "/api/sandbox/oauth/requests/"+request_id)
                    details.raise_for_status()
                    assert details.json()["run_id"] == probe_run
                    raise AbandonedLifecycleFailure("exit with pending request")

                try:
                    responses.clear()
                    await expect_failure(
                        lambda: connect(
                            client_storage=probe_storage,
                            target_run=probe_run,
                            redirect_handler=fail_with_pending_request),
                        "exit with pending request",
                        error_type=AbandonedLifecycleFailure)
                    pending_record_id = "request:" + digest(probe_request_ids[-1])
                    pending_snapshot = oauth_records_for(probe_run)
                    assert set(pending_snapshot) == {pending_record_id}, (
                        "Controlled early exit did not leave exactly one pending request")
                    assert probe_storage.client_info is not None
                    assert probe_storage.tokens is None
                    probe_registration_id = (
                        "client:" + digest(probe_storage.client_info.client_id))
                    with Session() as db:
                        registration = db.get(OAuthRecord, probe_registration_id)
                        assert registration is not None, (
                            "Early-exit probe registration was not committed")
                        probe_registration_snapshot = (
                            registration.expires, registration.data)

                    async def fail_with_unexchanged_code(url):
                        await approve(
                            url, approval_client=probe_owner, page_owner=probe_owner)
                        assert callback is not None
                        raise AbandonedLifecycleFailure("exit with unexchanged code")

                    responses.clear()
                    await expect_failure(
                        lambda: connect(
                            client_storage=probe_storage,
                            target_run=probe_run,
                            redirect_handler=fail_with_unexchanged_code),
                        "exit with unexchanged code",
                        error_type=AbandonedLifecycleFailure)
                    assert probe_storage.tokens is None
                    assert callback is not None
                    code_record_id = "code:" + digest(callback[0])
                    code_snapshot = oauth_records_for(probe_run)
                    assert set(code_snapshot) == {pending_record_id, code_record_id}, (
                        "Early exit did not leave the pending request and unexchanged code")
                    assert code_snapshot[code_record_id][1]["client_id"] == (
                        probe_storage.client_info.client_id)
                    assert not any(path == "/api/sandbox/oauth/token"
                                   for path, _ in responses), (
                        "SDK exchanged the code after the simulated callback failure")
                    with Session() as db:
                        registration = db.get(OAuthRecord, probe_registration_id)
                        assert registration is not None
                        assert (registration.expires, registration.data) == (
                            probe_registration_snapshot)
                finally:
                    try:
                        (await probe_owner.delete(
                            "/api/sandbox/runs/"+probe_run)).raise_for_status()
                    finally:
                        cleanup_lifecycle_records(
                            [probe_run], [probe_owner_id], include_clients=False)

                with Session() as db:
                    assert db.get(Run, probe_run) is None, (
                        "Early-exit probe run cleanup failed")
                    assert db.get(Owner, probe_owner_id) is None, (
                        "Early-exit probe owner cleanup failed")
                    assert not db.query(OAuthRecord).filter(
                        OAuthRecord.data["run"].astext == probe_run).count(), (
                        "Early-exit cleanup left run-scoped OAuth records")
                    registration = db.get(OAuthRecord, probe_registration_id)
                    assert registration is not None and (
                        registration.expires, registration.data) == (
                            probe_registration_snapshot), (
                        "Run cleanup removed or changed the shared cached registration")

            # Same denial semantics as test_denial_and_repeated_approval, but
            # lose the committed HTTP response before the simulated UI sees it.
            denied_storage = MemoryStorage()
            before_denial = run_oauth_records()
            prior_approvals, prior_redirects = approvals, redirects
            prior_decisions = denial_drop.decisions
            deny_approval = True
            denial_drop.armed = True
            responses.clear()

            async def connect_denied():
                # If denial accidentally authorizes, this would also act.
                await connect(call=True, client_storage=denied_storage)

            await expect_failure(connect_denied, "access-denial response lost",
                                 error_type=LostDenialResponse)
            denied_pending = pending_requests[-1]
            denied_id = "request:" + digest(denied_pending)
            assert denial_drop.dropped and denial_drop.decisions == prior_decisions + 1, (
                "SDK retried the committed denial")
            assert approvals == prior_approvals and redirects == prior_redirects + 1
            assert callback is None and denied_storage.tokens is None
            assert denied_storage.client_info is not None
            denied_client = denied_storage.client_info.client_id
            assert not any(path == "/api/sandbox/oauth/token" for path, _ in responses)
            assert run_oauth_records() == before_denial, (
                "Denial left a request, minted a code/token, or changed existing grants")
            with Session() as db:
                assert db.get(OAuthRecord, denied_id) is None, "Denial did not consume request"
                registered = db.get(OAuthRecord, "client:" + digest(denied_client))
                denied_registration = (registered.expires, registered.data)
            unchanged()

            # Replaying either decision cannot turn the consumed denial into
            # approval, even though its original response was never delivered.
            for decision in (False, True):
                replay = await owner.post("/api/sandbox/oauth/requests/"+denied_pending,
                                          json={"approve": decision})
                assert replay.status_code == 400 and replay.json() == {"error": "invalid_request"}
                assert run_oauth_records() == before_denial
                unchanged()

            # A caller retry uses the cached registration, not cached consent.
            deny_approval = False
            allow_approval = False
            prior_decisions = denial_drop.decisions
            responses.clear()
            await expect_failure(connect_denied, "fresh operator approval required")
            retry_pending = pending_requests[-1]
            assert retry_pending != denied_pending
            assert denial_drop.decisions == prior_decisions
            assert approvals == prior_approvals and redirects == prior_redirects + 2
            assert callback is None and denied_storage.tokens is None
            assert denied_storage.client_info.client_id == denied_client
            assert not any(path in ("/api/sandbox/oauth/register", "/api/sandbox/oauth/token")
                           for path, _ in responses)
            pending_records = run_oauth_records()
            assert {key: pending_records[key] for key in before_denial} == before_denial
            assert set(pending_records) - set(before_denial) == {"request:" + digest(retry_pending)}
            assert pending_records["request:" + digest(retry_pending)][1]["client_id"] == denied_client
            unchanged()

            # Only a new explicit approval may grant access. Do not perform
            # actions during recovery, so the original ledger must stay exact.
            allow_approval = True
            responses.clear()
            await connect(client_storage=denied_storage)
            assert pending_requests[-1] not in (denied_pending, retry_pending)
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 3
            assert denial_drop.decisions == prior_decisions + 1
            assert denied_storage.client_info.client_id == denied_client
            assert denied_storage.tokens and denied_storage.tokens.refresh_token is None
            assert responses.count(("/api/sandbox/oauth/token", 200)) == 1
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            recovered_records = run_oauth_records()
            assert {key: recovered_records[key] for key in pending_records} == pending_records
            recovered_id = "token:" + digest(denied_storage.tokens.access_token)
            assert set(recovered_records) - set(pending_records) == {recovered_id}
            expiry, grant = recovered_records[recovered_id]
            assert grant["client_id"] == denied_client
            assert time.time() < expiry <= min(original_expiry, time.time() + 600)
            with Session() as db:
                assert db.get(OAuthRecord, denied_id) is None
                registered = db.get(OAuthRecord, "client:" + digest(denied_client))
                assert (registered.expires, registered.data) == denied_registration
            unchanged()
            await connect(client_storage=denied_storage)
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 3
            assert run_oauth_records() == recovered_records, "Cached retry extended grant lifetimes"
            unchanged()
            print("PASS: lost denial response fails at SDK caller; consumed denial cannot be approved; retry needs a fresh explicit decision; no actions, consent or lifetime changes")

            # Registration is not authorization. Lose its committed HTTP response
            # before the SDK can cache a client ID, redirect, or request a token.
            registration_storage = MemoryStorage()
            registration_drop = DropRegistrationResponseTransport(orphan_registration_ids)
            prior_approvals, prior_redirects = approvals, redirects
            before_registration = run_oauth_records()

            async def lose_registration():
                await connect(client_storage=registration_storage, transport=registration_drop)

            await expect_failure(lose_registration, "registration response lost",
                                 error_type=LostRegistrationResponse)
            assert registration_drop.dropped and registration_drop.registration_requests == 1, (
                "SDK retried a committed registration")
            assert registration_drop.token_requests == 0
            assert registration_storage.client_info is None and registration_storage.tokens is None
            assert (approvals, redirects) == (prior_approvals, prior_redirects)
            assert run_oauth_records() == before_registration, (
                "Registration alone created or changed approval, code, or token records")
            unchanged()
            with Session() as db:
                orphan = db.get(OAuthRecord, registration_drop.registration_id)
                assert orphan is not None, "Dropped response must leave a committed registration"
                orphan_snapshot = (orphan.expires, orphan.data)

            # Caller-initiated retry registers afresh, but cannot silently approve.
            allow_approval = False
            responses.clear()

            async def recover_registration():
                await connect(client_storage=registration_storage)

            await expect_failure(recover_registration, "fresh operator approval required")
            assert responses.count(("/api/sandbox/oauth/register", 201)) == 1
            assert not any(path == "/api/sandbox/oauth/token" for path, _ in responses)
            assert approvals == prior_approvals and redirects == prior_redirects + 1
            assert registration_storage.client_info is not None and registration_storage.tokens is None
            recovery_client = registration_storage.client_info.client_id
            recovery_id = "client:" + digest(recovery_client)
            assert recovery_id != registration_drop.registration_id, "Retry reused the orphan"
            with Session() as db:
                recovered_registration = db.get(OAuthRecord, recovery_id)
                assert recovered_registration is not None
                registration_snapshot = (recovered_registration.expires, recovered_registration.data)
            pending_records = run_oauth_records()
            assert {key: pending_records[key] for key in before_registration} == before_registration
            added = {key: value for key, value in pending_records.items()
                     if key not in before_registration}
            assert len(added) == 1 and all(key.startswith("request:") for key in added)
            assert all(data["client_id"] == recovery_client for _, data in added.values())
            unchanged()

            allow_approval = True
            responses.clear()
            await recover_registration()
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 2
            assert registration_storage.client_info.client_id == recovery_client
            assert registration_storage.tokens and registration_storage.tokens.refresh_token is None
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            recovered_records = run_oauth_records()
            assert {key: recovered_records[key] for key in before_registration} == before_registration
            new_grants = {key: value for key, value in recovered_records.items()
                          if key not in before_registration and key.startswith("token:")}
            assert set(new_grants) == {"token:" + digest(registration_storage.tokens.access_token)}
            assert all(data["client_id"] == recovery_client and
                       time.time() < expiry <= min(original_expiry, time.time() + 600)
                       for expiry, data in new_grants.values())
            unchanged()
            await recover_registration()
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 2
            assert run_oauth_records() == recovered_records, "Cached retry changed grant lifetimes"
            with Session() as db:
                orphan = db.get(OAuthRecord, registration_drop.registration_id)
                registered = db.get(OAuthRecord, recovery_id)
                assert (orphan.expires, orphan.data) == orphan_snapshot
                assert (registered.expires, registered.data) == registration_snapshot
            unchanged()
            print("PASS: lost registration response fails without authorization; explicit retry registers afresh and needs approval; orphan and lifetimes unchanged")

            # Separate disposable SDK cache: approval commits a one-minute code,
            # but neither the redirect handler nor callback receives the response.
            approval_storage = MemoryStorage()
            approval_drop = DropApprovalResponseTransport()
            before_approval = run_oauth_records()
            prior_approvals, prior_redirects = approvals, redirects
            prior_callbacks = callbacks
            responses.clear()
            async with httpx.AsyncClient(
                    base_url=BASE, cookies=owner.cookies,
                    headers={"Origin": BASE, "X-Sandbox-Client": "browser"},
                    transport=approval_drop) as operator:
                async def lose_approval():
                    await connect(call=True, client_storage=approval_storage,
                                  approval_client=operator)

                await expect_failure(lose_approval, "approval response lost",
                                     error_type=LostApprovalResponse)
            assert approval_drop.dropped and approval_drop.approval_requests == 1, (
                "SDK retried a committed approval")
            assert approval_storage.client_info is not None and approval_storage.tokens is None
            assert callbacks == prior_callbacks and callback is None, (
                "Lost approval response reached the callback")
            assert approvals == prior_approvals and redirects == prior_redirects + 1
            assert not any(path == "/api/sandbox/oauth/token" or
                           (path == urlsplit(resource).path and status == 200)
                           for path, status in responses), "Lost approval authorized the SDK"
            approval_client_id = approval_storage.client_info.client_id
            values = parse_qs(urlsplit(approval_drop.redirect).query)
            orphan_code = values["code"][0]
            orphan_code_id = "code:" + digest(orphan_code)
            orphan_request_id = "request:" + digest(approval_drop.request_id)
            approval_records = run_oauth_records()
            assert {key: approval_records[key] for key in before_approval} == before_approval
            added = {key: value for key, value in approval_records.items()
                     if key not in before_approval}
            assert set(added) == {orphan_code_id}, "Approval must commit only one orphan code"
            orphan_code_snapshot = added[orphan_code_id]
            assert orphan_code_snapshot[1]["client_id"] == approval_client_id
            assert time.time() < orphan_code_snapshot[0] <= min(original_expiry, time.time() + 60)
            with Session() as db:
                assert db.get(OAuthRecord, orphan_request_id) is None
                row = db.get(OAuthRecord, "client:" + digest(approval_client_id))
                approval_registration = (row.expires, row.data)
            unchanged()

            # Retrying the consumed browser decision cannot return its old code.
            repeated = await owner.post("/api/sandbox/oauth/requests/" +
                                        approval_drop.request_id, json={"approve": True})
            assert repeated.status_code == 400 and repeated.json() == {"error": "invalid_request"}
            assert run_oauth_records() == approval_records
            unchanged()

            async def recover_approval(*, stale_code=None):
                await connect(client_storage=approval_storage, stale_code=stale_code)

            allow_approval = False
            responses.clear()
            await expect_failure(recover_approval, "fresh operator approval required")
            assert approvals == prior_approvals and redirects == prior_redirects + 2
            assert callbacks == prior_callbacks and approval_storage.tokens is None
            assert approval_storage.client_info.client_id == approval_client_id
            assert not any(path in ("/api/sandbox/oauth/token", "/api/sandbox/oauth/register")
                           for path, _ in responses)
            pending_approval = run_oauth_records()
            assert {key: pending_approval[key] for key in approval_records} == approval_records
            pending_added = {key: value for key, value in pending_approval.items()
                             if key not in approval_records}
            assert len(pending_added) == 1 and all(
                key.startswith("request:") and data["client_id"] == approval_client_id
                for key, (_, data) in pending_added.items())
            unchanged()

            # Even after a fresh approval, an old delayed code cannot complete
            # this new SDK flow: its fresh verifier does not match the old PKCE.
            allow_approval = True
            responses.clear()

            async def delayed_approval():
                await recover_approval(stale_code=orphan_code)

            await expect_failure(delayed_approval,
                                 'Token exchange failed (400): {"error":"invalid_grant"}',
                                 error_type=OAuthTokenError)
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 3
            assert callbacks == prior_callbacks + 1 and approval_storage.tokens is None
            assert callback[0] != orphan_code and callback[1] != values["state"][0]
            assert responses.count(("/api/sandbox/oauth/token", 400)) == 1
            delayed_records = run_oauth_records()
            assert {key: delayed_records[key] for key in pending_approval} == pending_approval
            delayed_added = {key: value for key, value in delayed_records.items()
                             if key not in pending_approval}
            assert set(delayed_added) == {"code:" + digest(callback[0])}
            unchanged()

            responses.clear()
            await recover_approval()
            assert approvals == prior_approvals + 2 and redirects == prior_redirects + 4
            assert callbacks == prior_callbacks + 2
            assert approval_storage.client_info.client_id == approval_client_id
            assert approval_storage.tokens and approval_storage.tokens.refresh_token is None
            assert approval_storage.tokens.scope == "sandbox:act"
            assert callback[0] != orphan_code
            assert responses.count(("/api/sandbox/oauth/token", 200)) == 1
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            recovered_approval = run_oauth_records()
            assert {key: recovered_approval[key] for key in delayed_records} == delayed_records
            new_approval_grants = {key: value for key, value in recovered_approval.items()
                                  if key not in delayed_records}
            assert set(new_approval_grants) == {
                "token:" + digest(approval_storage.tokens.access_token)}
            assert all(data["client_id"] == approval_client_id and
                       time.time() < expiry <= min(original_expiry, time.time() + 600)
                       for expiry, data in new_approval_grants.values())
            unchanged()
            await recover_approval()
            assert approvals == prior_approvals + 2 and redirects == prior_redirects + 4
            assert callbacks == prior_callbacks + 2
            assert run_oauth_records() == recovered_approval, "Cached recovery extended OAuth lifetimes"
            with Session() as db:
                row = db.get(OAuthRecord, "client:" + digest(approval_client_id))
                assert (row.expires, row.data) == approval_registration
                row = db.get(OAuthRecord, orphan_code_id)
                assert (row.expires, row.data) == orphan_code_snapshot
            unchanged()
            # All requests/codes are run-scoped and the cached registration is
            # tracked by connect's finally, including failed approval attempts.
            print("PASS: lost approval response leaves only an orphan code; no callback, token or action; retry needs fresh approval; delayed code fails PKCE; consent and lifetimes unchanged")

            def expire(kind, secret):
                with Session.begin() as db:
                    row = db.get(OAuthRecord, kind + ":" + digest(secret))
                    assert row is not None
                    if kind == "token":
                        assert row.data["run"] == run
                    else:
                        assert secret in clients
                    row.expires = time.time() - 1

            async def rejected_token(token, target_run=run):
                # Negative server probe only; all reconnects use OAuthClientProvider.
                async with httpx.AsyncClient() as probe:
                    response = await probe.post(BASE+f"/api/sandbox/agent/{target_run}/mcp", headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/json, text/event-stream"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "inspect_offer",
                                         "arguments": {"idempotency_key": "cross-run-denied"}}})
                    assert response.status_code == 401

            prior_approvals, prior_redirects = approvals, redirects
            await connect()
            assert (approvals, redirects) == (prior_approvals, prior_redirects), (
                "Valid cache should not ask for approval")
            unchanged()
            old_token = storage.tokens.access_token
            old_client = storage.client_info.client_id
            expire("token", old_token)
            allow_approval = False
            responses.clear()
            await expect_failure(connect, "fresh operator approval required")
            assert (urlsplit(resource).path, 401) in responses
            assert approvals == prior_approvals and storage.tokens.access_token == old_token
            unchanged()
            allow_approval = True
            await connect()
            assert approvals == prior_approvals + 1 and storage.tokens.access_token != old_token
            assert storage.client_info.client_id == old_client
            assert storage.tokens.refresh_token is None
            await rejected_token(old_token)
            unchanged()
            print("PASS: cached expired token requires explicit reapproval; run lifetime and ledger unchanged")

            old_token = storage.tokens.access_token
            revoked_id = "token:" + digest(old_token)

            def revocation_grants():
                with Session() as db:
                    return {row.id: (row.expires, row.data) for row in
                            db.query(OAuthRecord).filter(
                                OAuthRecord.id.startswith("token:"),
                                OAuthRecord.data["run"].astext == run).all()}

            with Session() as db:
                registration = db.get(OAuthRecord, "client:" + digest(old_client))
                registration_before = (registration.expires, registration.data)

            def registration_unchanged():
                with Session() as db:
                    row = db.get(OAuthRecord, "client:" + digest(old_client))
                    assert row is not None
                    assert (row.expires, row.data) == registration_before, (
                        "Revocation/reconnect changed the client registration or lifetime")

            # Sequential response loss, not concurrent disconnects or stale
            # browser decisions: retain the SDK cache and retry the exact form.
            before_revocation = revocation_grants()
            assert revoked_id in before_revocation
            drop_revoke = DropRevocationResponseTransport()
            prior_approvals, prior_redirects = approvals, redirects
            async with httpx.AsyncClient(base_url=BASE, transport=drop_revoke) as revoker:
                async def revoke_with_lost_response():
                    await revoker.post("/api/sandbox/oauth/revoke", data={
                        "client_id": old_client, "token": old_token})

                await expect_failure(revoke_with_lost_response, "revocation response lost",
                                     error_type=LostRevocationResponse)
                assert drop_revoke.dropped and drop_revoke.revocation_requests == 1
                revoked = revocation_grants()
                assert revoked == {key: value for key, value in before_revocation.items()
                                   if key != revoked_id}, "Lost response did not commit revocation"
                await rejected_token(old_token)
                unchanged()
                registration_unchanged()

                response = await revoker.post("/api/sandbox/oauth/revoke",
                    content=drop_revoke.body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                assert response.status_code == 200 and response.json() == {}
                assert drop_revoke.revocation_requests == 2
            assert revocation_grants() == revoked, "Retry restored or extended a grant"
            assert (approvals, redirects) == (prior_approvals, prior_redirects)
            assert storage.tokens.access_token == old_token
            await rejected_token(old_token)
            unchanged()
            registration_unchanged()

            allow_approval = False
            responses.clear()
            await expect_failure(connect, "fresh operator approval required")
            assert (urlsplit(resource).path, 401) in responses
            assert approvals == prior_approvals and redirects == prior_redirects + 1
            assert storage.tokens.access_token == old_token
            assert storage.client_info.client_id == old_client
            assert revocation_grants() == revoked, "Unapproved reconnect changed grants"
            await rejected_token(old_token)
            unchanged()
            registration_unchanged()
            allow_approval = True
            await connect()
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 2
            assert storage.tokens.access_token != old_token
            assert storage.tokens.refresh_token is None
            assert storage.client_info.client_id == old_client
            recovered = revocation_grants()
            recovered_id = "token:" + digest(storage.tokens.access_token)
            assert set(recovered) == set(revoked) | {recovered_id}
            assert recovered_id not in before_revocation
            assert {key: recovered[key] for key in revoked} == revoked
            assert time.time() < recovered[recovered_id][0] <= original_expiry
            assert recovered[recovered_id][0] <= time.time() + 600
            # Overlapping retries of the old credential must not remove the
            # newly approved grant, resurrect the old grant, or extend either lifetime.
            duplicate_revocations = await asyncio.gather(*(
                owner.post("/api/sandbox/oauth/revoke",
                    content=drop_revoke.body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                for _ in range(2)))
            assert all(response.status_code == 200 and response.json() == {}
                       for response in duplicate_revocations)
            assert revocation_grants() == recovered
            responses.clear()
            await connect()
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 2
            assert (urlsplit(resource).path, 200) in responses
            assert revocation_grants() == recovered
            await rejected_token(old_token)
            unchanged()
            registration_unchanged()
            print("PASS: lost revocation response commits; concurrent retries target only the old token; fresh approval remains usable with no actions, consent or lifetime changes")

            expire("token", storage.tokens.access_token)
            expire("client", old_client)
            await expect_failure(connect, "authorization: invalid_client")
            assert approvals == prior_approvals + 1
            unchanged()
            # Recovery is an explicit operator/client action, never an SDK patch.
            storage.tokens = None
            storage.client_info = None
            await connect()
            assert approvals == prior_approvals + 2 and storage.client_info.client_id != old_client
            unchanged()
            print("PASS: expired registration fails clearly; clearing both caches registers and approves afresh")

            # Separate from disconnect races and stale browser decisions: the
            # server commits a valid exchange, but the SDK never sees its token.
            lost_storage = MemoryStorage()
            drop = DropTokenResponseTransport()
            prior_approvals, prior_redirects = approvals, redirects

            async def lost_connect():
                await connect(client_storage=lost_storage, transport=drop)

            await expect_failure(lost_connect, "token response lost", error_type=LostTokenResponse)
            assert drop.dropped and drop.token_requests == 1, "SDK retried a committed exchange"
            assert lost_storage.tokens is None, "Lost token response populated the SDK cache"
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 1
            lost_client = lost_storage.client_info.client_id
            exchange = parse_qs(drop.exchange_body.decode())
            assert exchange["grant_type"] == ["authorization_code"]
            assert exchange["client_id"] == [lost_client]
            assert exchange["resource"] == [resource]
            consumed_code = "code:" + digest(exchange["code"][0])

            def lost_grants():
                with Session() as db:
                    return {row.id: (row.expires, row.data) for row in
                            db.query(OAuthRecord).filter(
                                OAuthRecord.id.startswith("token:"),
                                OAuthRecord.data["run"].astext == run,
                                OAuthRecord.data["client_id"].astext == lost_client).all()}

            with Session() as db:
                assert db.get(OAuthRecord, consumed_code) is None, "Exchange did not consume its code"
            orphaned = lost_grants()
            assert len(orphaned) == 1, "Lost response must still commit exactly one grant"
            assert all(time.time() < expiry <= original_expiry
                       and expiry <= time.time() + 600 for expiry, _ in orphaned.values())
            unchanged()

            async with httpx.AsyncClient() as probe:
                replay = await probe.post(drop.exchange_url, content=drop.exchange_body,
                                          headers={"Content-Type": "application/x-www-form-urlencoded"})
            assert replay.status_code == 400 and replay.json() == {"error": "invalid_grant"}
            assert lost_grants() == orphaned, "Code replay minted or extended a grant"
            assert lost_storage.tokens is None
            unchanged()

            async def reconnect_lost():
                await connect(client_storage=lost_storage)

            allow_approval = False
            await expect_failure(reconnect_lost, "fresh operator approval required")
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 2
            assert lost_storage.tokens is None and lost_storage.client_info.client_id == lost_client
            assert lost_grants() == orphaned, "Unapproved reconnect changed the lost-response grant"
            unchanged()
            allow_approval = True
            await reconnect_lost()
            assert approvals == prior_approvals + 2 and redirects == prior_redirects + 3
            assert lost_storage.tokens and lost_storage.tokens.refresh_token is None
            assert lost_storage.client_info.client_id == lost_client
            assert callback[0] != exchange["code"][0], "Recovery reused the consumed code"
            recovered = lost_grants()
            recovered_id = "token:" + digest(lost_storage.tokens.access_token)
            assert recovered_id not in orphaned and recovered_id in recovered
            assert len(recovered) == 2
            assert {key: recovered[key] for key in orphaned} == orphaned, (
                "Recovery extended or replaced the lost-response grant")
            unchanged()
            await reconnect_lost()
            assert approvals == prior_approvals + 2 and redirects == prior_redirects + 3
            assert lost_grants() == recovered, "Cached reconnect changed grant lifetimes"
            unchanged()
            print("PASS: lost token response consumes code; replay fails; reconnect needs fresh approval; no actions or lifetime changes")

            created = await owner.post("/api/sandbox/runs", json={"scenario": "non_delivery"})
            created.raise_for_status()
            second_run = created.json()["run"]["id"]
            created_runs.append(second_run)
            second_resource = BASE+f"/api/sandbox/agent/{second_run}/mcp"
            with Session() as db:
                row = db.get(Run, second_run)
                assert row is not None
                second_expiry, second_data = row.expires, row.data
                second_lifetimes = (row.created, row.deletes)

            def both_unchanged():
                unchanged()
                with Session() as db:
                    row = db.get(Run, second_run)
                    assert row.expires == second_expiry and row.consent is False
                    assert (row.created, row.deletes) == second_lifetimes
                    assert row.data == second_data, "Reconnect changed the second run's ledger"

            def bound_to(token, expected_run, expected_resource):
                with Session() as db:
                    row = db.get(OAuthRecord, "token:" + digest(token.access_token))
                    assert row is not None and row.expires > time.time()
                    assert row.data["run"] == expected_run
                    assert row.data["resource"] == expected_resource
                    assert row.data["client_id"] == storage.client_info.client_id
                    assert row.data["scope"] == "sandbox:act"

            first_tokens = storage.tokens
            first_client = storage.client_info.client_id
            prior_approvals, prior_redirects = approvals, redirects
            await rejected_token(first_tokens.access_token, second_run)
            responses.clear()
            token_resources.clear()
            rejected_cached_tokens.clear()
            allow_approval = False
            await expect_failure(lambda: connect(target_run=second_run),
                                 "fresh operator approval required")
            assert (urlsplit(second_resource).path, 401) in responses
            assert (urlsplit(second_resource).path, True) in rejected_cached_tokens, (
                "The SDK must actually present the first run's cached token")
            assert redirects == prior_redirects + 1 and approvals == prior_approvals
            assert not token_resources, "No token exchange is allowed without approval"
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            assert storage.tokens == first_tokens
            assert storage.client_info.client_id == first_client
            bound_to(first_tokens, run, resource)
            both_unchanged()

            allow_approval = True
            await connect(target_run=second_run)
            second_tokens = storage.tokens
            assert approvals == prior_approvals + 1 and redirects == prior_redirects + 2
            assert token_resources == [[second_resource]]
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            assert storage.client_info.client_id == first_client
            assert second_tokens.access_token != first_tokens.access_token
            assert second_tokens.refresh_token is None
            bound_to(first_tokens, run, resource)
            bound_to(second_tokens, second_run, second_resource)
            await rejected_token(first_tokens.access_token, second_run)
            await rejected_token(second_tokens.access_token)
            both_unchanged()
            prior_redirects = redirects
            await connect(target_run=second_run)
            assert redirects == prior_redirects and approvals == prior_approvals + 1
            storage.tokens = first_tokens
            await connect()
            assert redirects == prior_redirects and approvals == prior_approvals + 1
            both_unchanged()
            print("PASS: cached registration is reused across runs, but tokens remain resource-bound; second run requires explicit approval without ledger, lifetime or consent changes")

            # A registration and token cached by one browser owner must not
            # grant access to a different owner's run or approval request.
            async with httpx.AsyncClient(base_url=BASE, headers={
                    "Origin": BASE, "X-Sandbox-Client": "browser"}) as foreign_owner:
                foreign_owner_response = await foreign_owner.post("/api/sandbox/owner")
                foreign_owner_response.raise_for_status()
                foreign_owner_id = digest(
                    foreign_owner.cookies["sandbox_owner"]) if lifecycle else None
                foreign_created = await foreign_owner.post(
                    "/api/sandbox/runs", json={"scenario": "non_delivery"})
                foreign_created.raise_for_status()
                foreign_run = foreign_created.json()["run"]["id"]
                foreign_resource = BASE+f"/api/sandbox/agent/{foreign_run}/mcp"
                foreign_owner_runs = [foreign_run]
                foreign_tokens = MemoryStorage()
                foreign_tokens.client_info = storage.client_info
                foreign_tokens.tokens = first_tokens

                try:
                    with Session() as db:
                        row = db.get(Run, foreign_run)
                        assert row is not None
                        foreign_expiry, foreign_data = row.expires, row.data
                        foreign_lifetimes = (row.created, row.deletes)
                    first_records = run_oauth_records()

                    def foreign_run_unchanged():
                        unchanged()
                        with Session() as db:
                            row = db.get(Run, foreign_run)
                            assert row is not None
                            assert row.expires == foreign_expiry and row.consent is False
                            assert (row.created, row.deletes) == foreign_lifetimes
                            assert row.data == foreign_data, (
                                "Cross-owner reconnect changed the foreign run ledger")

                    def foreign_run_records():
                        with Session() as db:
                            return {row.id: (row.expires, row.data) for row in
                                    db.query(OAuthRecord).filter(
                                        OAuthRecord.data["run"].astext == foreign_run).all()}

                    async def reject_first_owner_approval(url):
                        nonlocal approvals, callback, redirects
                        redirects += 1
                        assert parse_qs(urlsplit(url).query).get("resource") == [
                            foreign_resource], "SDK authorization must target the foreign run"
                        page = await owner.get(url)
                        page.raise_for_status()
                        pending_id = re.search(
                            r"/requests/([A-Za-z0-9_-]+)", page.text)[1]
                        pending_requests.append(pending_id)
                        details = await owner.get(
                            "/api/sandbox/oauth/requests/"+pending_id)
                        assert details.status_code == 404, (
                            "First owner viewed another owner's authorization request")
                        rejected = await owner.post(
                            "/api/sandbox/oauth/requests/"+pending_id,
                            json={"approve": True})
                        assert rejected.status_code == 404, (
                            "First owner approved another owner's authorization request")
                        assert not token_resources, (
                            "Token exchange occurred before the correct owner approved")
                        pending_records = foreign_run_records()
                        assert set(pending_records) == {"request:" + digest(pending_id)}
                        foreign_run_unchanged()

                        # The request remains pending for the actual run owner.
                        details = await foreign_owner.get(
                            "/api/sandbox/oauth/requests/"+pending_id)
                        details.raise_for_status()
                        assert details.json()["run_id"] == foreign_run
                        assert details.json()["research_consent"] is False
                        approved = await foreign_owner.post(
                            "/api/sandbox/oauth/requests/"+pending_id,
                            json={"approve": True})
                        approved.raise_for_status()
                        approvals += 1
                        values = parse_qs(
                            urlsplit(approved.json()["redirect"]).query)
                        callback = (values["code"][0], values["state"][0])
                        assert not token_resources, (
                            "SDK exchanged a token before receiving the owner's approval")
                        approved_records = foreign_run_records()
                        assert len(approved_records) == 1 and next(
                            iter(approved_records)).startswith("code:")
                        assert not any(key.startswith("token:")
                                       for key in approved_records), (
                            "Token was issued before the SDK received the owner's approval")
                        foreign_run_unchanged()

                    responses.clear()
                    token_resources.clear()
                    rejected_cached_tokens.clear()
                    prior_approvals, prior_redirects = approvals, redirects
                    await connect(client_storage=foreign_tokens,
                                  target_run=foreign_run,
                                  redirect_handler=reject_first_owner_approval)
                    assert (urlsplit(foreign_resource).path, True) in rejected_cached_tokens, (
                        "The SDK must present the first owner's cached token")
                    assert approvals == prior_approvals + 1
                    assert redirects == prior_redirects + 1
                    assert token_resources == [[foreign_resource]], (
                        "The only token exchange must follow the foreign owner's approval")
                    assert foreign_tokens.client_info.client_id == first_client
                    assert foreign_tokens.tokens and (
                        foreign_tokens.tokens.access_token != first_tokens.access_token)
                    assert not any(path == "/api/sandbox/oauth/register"
                                   for path, _ in responses), (
                        "Cross-owner recovery unexpectedly replaced cached registration")
                    assert run_oauth_records() == first_records, (
                        "Cross-owner recovery changed the first owner's OAuth grants")
                    foreign_run_unchanged()
                    print("PASS: cached credentials cannot view or approve another owner's request; the foreign owner must approve before token exchange; both ledgers and consent remain unchanged")
                finally:
                    try:
                        (await foreign_owner.delete(
                            "/api/sandbox/runs/"+foreign_run)).raise_for_status()
                    finally:
                        with Session.begin() as db:
                            db.execute(delete(OAuthRecord).where(
                                OAuthRecord.data["run"].astext.in_(foreign_owner_runs)))
                            db.execute(delete(Run).where(
                                Run.id.in_(foreign_owner_runs)))
                            db.execute(delete(Owner).where(
                                Owner.id == foreign_owner_id))
                        with Session() as db:
                            assert db.get(Run, foreign_run) is None, (
                                "Foreign disposable run cleanup failed")
                            assert db.get(Owner, foreign_owner_id) is None, (
                                "Foreign disposable owner cleanup failed")
                            assert not db.query(OAuthRecord).filter(
                                OAuthRecord.data["run"].astext.in_(foreign_owner_runs)).count(), (
                                "Foreign disposable OAuth record cleanup failed")
            # Keep both independently approved run grants cached under one
            # registration, as two separate SDK client instances would.
            first_storage = storage
            second_storage = MemoryStorage()
            second_storage.client_info = first_storage.client_info
            second_storage.tokens = second_tokens
            assert first_storage.client_info.client_id == second_storage.client_info.client_id == first_client

            shared_registration_id = "client:" + digest(first_client)
            with Session() as db:
                shared_registration = db.get(OAuthRecord, shared_registration_id)
                assert shared_registration is not None
                shared_registration_snapshot = (
                    shared_registration.expires, shared_registration.data)

            def shared_registration_unchanged():
                with Session() as db:
                    row = db.get(OAuthRecord, shared_registration_id)
                    assert row is not None
                    assert (row.expires, row.data) == shared_registration_snapshot, (
                        "Reapproval changed or extended the shared SDK registration")

            def active_run_tokens(target_run):
                with Session() as db:
                    return {row.id: (row.expires, row.data) for row in
                            db.query(OAuthRecord).filter(
                                OAuthRecord.id.startswith("token:"),
                                OAuthRecord.data["run"].astext == target_run,
                                OAuthRecord.expires > time.time()).all()}

            first_tokens_before_disconnect = active_run_tokens(run)
            second_tokens_before_disconnect = active_run_tokens(second_run)
            assert "token:" + digest(first_tokens.access_token) in first_tokens_before_disconnect
            assert "token:" + digest(second_tokens.access_token) in second_tokens_before_disconnect
            assert first_tokens_before_disconnect and second_tokens_before_disconnect
            prior_approvals, prior_redirects = approvals, redirects

            disconnected = await owner.delete(
                f"/api/sandbox/runs/{run}/oauth-grants/{first_client}")
            assert disconnected.status_code == 200 and disconnected.json() == {"revoked": True}
            first_tokens_after_disconnect = active_run_tokens(run)
            assert first_tokens_after_disconnect == {
                key: value for key, value in first_tokens_before_disconnect.items()
                if value[1]["client_id"] != first_client
            }, "Operator disconnect did not revoke only this client's first-run grants"
            assert active_run_tokens(second_run) == second_tokens_before_disconnect, (
                "Disconnecting the first run changed the second run's approved grant")
            await rejected_token(first_tokens.access_token)
            both_unchanged()
            shared_registration_unchanged()

            allow_approval = False
            responses.clear()
            token_resources.clear()
            rejected_cached_tokens.clear()
            await expect_failure(
                lambda: connect(target_run=run, client_storage=first_storage),
                "fresh operator approval required")
            assert (urlsplit(resource).path, 401) in responses
            assert (urlsplit(resource).path, True) in rejected_cached_tokens, (
                "The SDK must present its cached token after operator disconnect")
            assert not token_resources, "A reconnect without approval must not exchange a token"
            assert (approvals, redirects) == (prior_approvals, prior_redirects + 1)
            assert first_storage.tokens == first_tokens
            assert first_storage.client_info.client_id == first_client
            assert active_run_tokens(run) == first_tokens_after_disconnect
            assert active_run_tokens(second_run) == second_tokens_before_disconnect
            both_unchanged()
            shared_registration_unchanged()

            allow_approval = True
            responses.clear()
            token_resources.clear()
            rejected_cached_tokens.clear()
            await connect(target_run=second_run, client_storage=second_storage)
            assert (approvals, redirects) == (prior_approvals, prior_redirects + 1), (
                "The second run must reconnect with its existing approval")
            assert (urlsplit(second_resource).path, 200) in responses, (
                "The second run's cached grant must still authorize its SDK request")
            assert not token_resources
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            assert second_storage.tokens == second_tokens
            assert second_storage.client_info.client_id == first_client
            assert active_run_tokens(run) == first_tokens_after_disconnect
            assert active_run_tokens(second_run) == second_tokens_before_disconnect
            both_unchanged()
            shared_registration_unchanged()

            # Reapprove the disconnected run through the same SDK registration.
            # The second run's independently cached grant must remain untouched.
            prior_approvals, prior_redirects = approvals, redirects
            responses.clear()
            token_resources.clear()
            reconnected_first_tokens = first_storage.tokens
            await connect(target_run=run, client_storage=first_storage)
            restored_first_tokens = first_storage.tokens
            assert restored_first_tokens and (
                restored_first_tokens.access_token != reconnected_first_tokens.access_token)
            assert restored_first_tokens.refresh_token is None
            assert first_storage.client_info.client_id == first_client
            assert (approvals, redirects) == (prior_approvals + 1, prior_redirects + 2)
            assert token_resources == [[resource]]
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            bound_to(restored_first_tokens, run, resource)
            restored_first_id = "token:" + digest(restored_first_tokens.access_token)
            restored_first_run_tokens = active_run_tokens(run)
            assert set(restored_first_run_tokens) == (
                set(first_tokens_after_disconnect) | {restored_first_id})
            assert {key: restored_first_run_tokens[key]
                    for key in first_tokens_after_disconnect} == first_tokens_after_disconnect
            assert time.time() < restored_first_run_tokens[restored_first_id][0] <= min(
                original_expiry, time.time() + 600)
            assert active_run_tokens(second_run) == second_tokens_before_disconnect, (
                "Reapproving the first run changed the second run's grant")
            await rejected_token(restored_first_tokens.access_token, second_run)
            assert active_run_tokens(run) == restored_first_run_tokens
            assert active_run_tokens(second_run) == second_tokens_before_disconnect
            both_unchanged()
            shared_registration_unchanged()

            # The second SDK instance still uses its existing cached token; no
            # further approval, registration, or token exchange should occur.
            prior_approvals, prior_redirects = approvals, redirects
            responses.clear()
            token_resources.clear()
            await connect(target_run=second_run, client_storage=second_storage)
            assert (approvals, redirects) == (prior_approvals, prior_redirects)
            assert (urlsplit(second_resource).path, 200) in responses
            assert not token_resources
            assert not any(path == "/api/sandbox/oauth/register" for path, _ in responses)
            assert second_storage.tokens == second_tokens
            assert second_storage.client_info.client_id == first_client
            assert active_run_tokens(run) == restored_first_run_tokens
            assert active_run_tokens(second_run) == second_tokens_before_disconnect
            both_unchanged()
            shared_registration_unchanged()
            print("PASS: fresh approval restores only the disconnected run's grant; the other run reconnects from its existing SDK cache; both ledgers, consent, lifetimes, and registration remain unchanged")
            expired_at = time.time() - 1
            with Session.begin() as db:
                db.get(Run, run).expires = expired_at
            terminal_approvals, prior_redirects = approvals, redirects
            await expect_failure(connect, "HTTP 410: run expired; stop", http_status=410)
            assert redirects == prior_redirects, "Expired run must not start reapproval"
            assert approvals == terminal_approvals
            unchanged(expiry=expired_at)
            # Even discarding the expired grant cannot authorize an ended run.
            cached_tokens = storage.tokens
            storage.tokens = None
            await expect_failure(connect, "run unavailable: invalid_grant")
            assert approvals == terminal_approvals
            unchanged(expiry=expired_at)
            # Restore only this fixture's original deadline to test terminal revocation.
            with Session.begin() as db:
                db.get(Run, run).expires = original_expiry
            storage.tokens = cached_tokens
            (await owner.post("/api/sandbox/runs/"+run+"/revoke")).raise_for_status()
            await expect_failure(connect, "run unavailable: invalid_grant")
            assert approvals == terminal_approvals
            unchanged(status="revoked")
            print("PASS: expired and revoked runs fail at the SDK caller without approval, new actions or lifetime extension")
        finally:
            try:
                for created_run in created_runs:
                    (await owner.delete("/api/sandbox/runs/"+created_run)).raise_for_status()
            finally:
                if lifecycle:
                    cleanup_lifecycle_records(
                        created_runs, [disposable_owner], include_clients=True)
                    with Session() as db:
                        assert all(db.get(Run, created_run) is None for created_run in created_runs), (
                            "Disposable run cleanup failed")
                        assert db.get(Owner, disposable_owner) is None, "Disposable owner cleanup failed"
                        assert not db.query(OAuthRecord).filter(
                            (OAuthRecord.data["run"].astext.in_(created_runs)) |
                            OAuthRecord.id.in_(["client:" + digest(client) for client in clients] +
                                               list(orphan_registration_ids))).count(), (
                                "Disposable OAuth record cleanup failed")


if __name__ == "__main__":
    asyncio.run(main())
