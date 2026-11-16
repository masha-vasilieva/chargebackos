"""Narrow OAuth authorization server for operator-created synthetic runs."""
import base64
import hashlib
import os
import re
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from database import Session
from sandbox_api import Create, create_run as create_sandbox_run, guard, owner_id, owned
from services.sandbox_oauth_store import (
    OAuthRecord, add_record, record, active_grants, grant_summary, revoke_grant,
)
from services.sandbox_store import Run, digest, limit

class OAuthFailure(HTTPException):
    """Protocol errors must use OAuth's error envelope, not FastAPI's detail."""


class OAuthRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await handler(request)
            except OAuthFailure as exc:
                return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        return handle


router = APIRouter(tags=["Sandbox OAuth"], route_class=OAuthRoute)
PREFIX = "/api/sandbox/oauth"
SCOPE = "sandbox:act"
STABLE_MCP_PATH = "/mcp"
CLIENT_TTL = 90 * 86400
RUN_PATH = re.compile(r"^/api/sandbox/agent/([a-f0-9]{32})/mcp$")
PKCE = re.compile(r"^[A-Za-z0-9_-]{43}$")
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


def origin() -> str:
    value = os.environ.get("SANDBOX_OAUTH_ORIGIN", "").rstrip("/")
    # Explicit development-only option survives fork/merge domain changes.
    # Production must supply its own fixed origin; never trust request headers.
    if (value == "replit-development" and os.environ.get("NODE_ENV") != "production"
            and os.environ.get("REPLIT_DEPLOYMENT") != "1"):
        domain = os.environ.get("REPLIT_DEV_DOMAIN", "")
        value = "https://" + domain if domain else ""
    url = urlsplit(value)
    if (url.scheme != "https" or not url.netloc or url.username or
            url.password or url.path or url.query or url.fragment):
        raise HTTPException(503, "OAuth canonical HTTPS origin is not configured.")
    return value


def failure(error: str = "invalid_request") -> HTTPException:
    return OAuthFailure(400, error)


def resource_run(resource: str) -> str:
    root = origin()
    if not resource.startswith(root + "/"):
        raise failure("invalid_target")
    match = RUN_PATH.fullmatch(resource[len(root):])
    if not match:
        raise failure("invalid_target")
    return match[1]


def stable_resource() -> str:
    return origin() + STABLE_MCP_PATH


def active_owner_runs(db, owner: str):
    now = time.time()
    return db.scalars(select(Run).where(
        Run.owner == owner,
        Run.expires > now,
        Run.deletes > now,
        Run.data["state"]["status"].astext == "active",
    ).order_by(Run.created.desc()).limit(2)).all()


def run_summary(row: Run):
    return {"run_id": row.id, "scenario": row.data["scenario"],
            "agent_label": row.data["agent_label"], "created_at": row.created,
            "expires_at": row.expires}


# Exact app callbacks for desktop clients that use their own URL scheme.
NATIVE_APP_REDIRECTS = frozenset({"cursor://anysphere.cursor-mcp/oauth/callback"})


def redirect_valid(value: str) -> bool:
    if value in NATIVE_APP_REDIRECTS:
        return True
    try:
        url = urlsplit(value)
        loopback_http = (
            url.scheme == "http"
            and url.hostname in ("127.0.0.1", "::1", "localhost")
        )
        return bool(len(value) <= 2048 and url.hostname and url.port != 0 and
                    not any(c.isspace() or ord(c) < 32 for c in value) and
                    "\\" not in value and "*" not in value and
                    not url.username and not url.password and not url.fragment and
                    not url.query and (url.scheme == "https" or loopback_http))
    except ValueError:
        return False


def is_loopback(url) -> bool:
    return url.scheme == "http" and url.hostname in ("127.0.0.1", "::1", "localhost")


def redirect_matches(registered: list[str], requested: str) -> bool:
    """Exact match, except loopback callbacks may use any port (RFC 8252 7.3)."""
    if requested in registered:
        return True
    if not redirect_valid(requested):
        return False
    want = urlsplit(requested)
    if not is_loopback(want):
        return False
    for uri in registered:
        have = urlsplit(uri)
        if (is_loopback(have) and have.hostname == want.hostname and
                (have.path or "/") == (want.path or "/")):
            return True
    return False


def active(row) -> None:
    if (row.expires <= time.time() or row.deletes <= time.time() or
            row.data["state"]["status"] != "active"):
        raise failure("invalid_grant")


def pending_request(db, request_id: str, lock: bool = False):
    query = select(OAuthRecord).where(
        OAuthRecord.id == "request:" + digest(request_id))
    pending = db.scalar(query.with_for_update() if lock else query)
    if not pending or pending.expires <= time.time():
        raise OAuthFailure(410, "authorization_request_expired")
    return pending


def unique_params(items) -> dict[str, str]:
    params = dict(items)
    if len(params) != len(items):
        raise failure()
    return params


@router.get("/.well-known/oauth-authorization-server")
def metadata():
    root = origin()
    return {"issuer": root,
            "authorization_endpoint": root + PREFIX + "/authorize",
            "token_endpoint": root + PREFIX + "/token",
            "registration_endpoint": root + PREFIX + "/register",
            "revocation_endpoint": root + PREFIX + "/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": [SCOPE]}


def protected_resource_document(resource: str, run_id: str | None = None):
    if run_id is not None:
        with Session() as db:
            row = db.get(Run, run_id)
            if row is None or row.deletes <= time.time():
                raise HTTPException(404, "Protected resource not found.")
    return {"resource": resource, "authorization_servers": [origin()],
            "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"]}


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_root():
    # RFC 9728's pathless form describes the origin's root resource. A run-
    # specific resource is published at the path-suffixed form below.
    return protected_resource_document(origin())


@router.get("/.well-known/oauth-protected-resource/{resource_path:path}")
def protected_resource_path(resource_path: str):
    resource = origin() + "/" + resource_path
    if resource == stable_resource():
        return protected_resource_document(resource)
    try:
        run_id = resource_run(resource)
    except OAuthFailure as exc:
        if exc.status_code == 400:
            raise HTTPException(404, "Protected resource not found.") from exc
        raise
    return protected_resource_document(resource, run_id)


@router.get("/api/sandbox/oauth/resource/{run_id}")
def protected_resource(run_id: str):
    resource = origin() + f"/api/sandbox/agent/{run_id}/mcp"
    resource_run(resource)
    return protected_resource_document(resource, run_id)


class ApprovalRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: Literal[
        "non_delivery", "late_delivery", "refund_pending",
        "already_refunded", "refund_trap", "legitimate_seller",
        "off_platform_seller", "chatty_honest_seller",
    ]


@router.post(PREFIX + "/requests/{request_id}/create-run")
def create_run_for_approval(request_id: str, body: ApprovalRunCreate, request: Request):
    guard(request)
    owner = owner_id(request)
    with Session() as db:
        pending = pending_request(db, request_id)
        if not pending.data.get("stable"):
            raise failure("invalid_request")
        available = active_owner_runs(db, owner)
        if available:
            raise HTTPException(409, "An active run is already available. Refresh this approval and choose it.")

    # Reuse the normal run-creation policy and limits, but never return its
    # separately usable bearer credential through the OAuth approval page.
    result = create_sandbox_run(
        Create(scenario=body.scenario, mode="practice", consent=False),
        request,
    )
    run = result["run"]
    return {"run": {key: run[key] for key in (
        "id", "scenario", "agent_label", "created_at", "expires_at",
    )}}


class Registration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    redirect_uris: list[str] = Field(min_length=1, max_length=10)
    client_name: str = Field(default="Unnamed client", min_length=1, max_length=80)
    token_endpoint_auth_method: str = "none"
    grant_types: list[str] = Field(default=["authorization_code"], max_length=2)
    response_types: list[str] = Field(default=["code"], max_length=2)


@router.post(PREFIX + "/register", status_code=201)
def register(body: Registration):
    origin()
    limit("oauth-register-global", 30)
    requested_grants = body.grant_types
    supported_grants = {"authorization_code", "refresh_token"}
    registered_grants = [grant for grant in requested_grants
                         if grant in supported_grants]
    if (body.token_endpoint_auth_method != "none" or
            "authorization_code" not in registered_grants or
            len(set(requested_grants)) != len(requested_grants) or
            set(requested_grants) - supported_grants or
            body.response_types != ["code"]):
        raise failure("invalid_client_metadata")
    # Register the callbacks we support and drop the rest (RFC 7591 lets the
    # server adjust metadata). Clients such as Cursor send several at once.
    redirects = list(dict.fromkeys(uri for uri in body.redirect_uris if redirect_valid(uri)))
    if not redirects:
        raise failure("invalid_client_metadata")
    client_id = secrets.token_urlsafe(32)
    metadata = body.model_dump()
    metadata["redirect_uris"] = redirects
    # Preserve exactly the supported grants requested by this public client.
    metadata["grant_types"] = registered_grants
    with Session.begin() as db:
        add_record(db, "client", client_id, time.time() + CLIENT_TTL, metadata)
    return {"client_id": client_id, **metadata}


@router.get(PREFIX + "/authorize", response_class=HTMLResponse)
def authorize(request: Request):
    limit("oauth-authorize-global", 60)
    if len(request.scope.get("query_string", b"")) > 8192:
        raise failure()
    params = unique_params(request.query_params.multi_items())
    required = {"client_id", "redirect_uri", "response_type", "state",
                "code_challenge", "code_challenge_method", "resource"}
    if (not required.issubset(params) or params["response_type"] != "code" or
            params.get("scope", SCOPE) != SCOPE or
            params["code_challenge_method"] != "S256" or
            not PKCE.fullmatch(params["code_challenge"]) or
            not 1 <= len(params["state"]) <= 512):
        raise failure()
    stable = params["resource"] == stable_resource()
    run_id = None if stable else resource_run(params["resource"])
    with Session.begin() as db:
        client = record(db, "client", params["client_id"], lock=True)
        if not client or not redirect_matches(client.data["redirect_uris"], params["redirect_uri"]):
            raise failure("invalid_client")
        # Keep a configured public client usable while it is in active use.
        # Approval is still required separately for every selected run.
        client.expires = time.time() + CLIENT_TTL
        pending = secrets.token_urlsafe(32)
        add_record(db, "request", pending, time.time() + 300, {
            **{k: params[k] for k in required}, "run": run_id, "stable": stable,
            "client_name": client.data["client_name"], "scope": SCOPE})
    html = Path(__file__).with_name("sandbox_oauth_approval.html").read_text()
    html = html.replace("__REQUEST_ID__", pending).replace(
        "__STABLE_REQUEST__", "true" if stable else "false")
    return HTMLResponse(html, headers={
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store"})


@router.get(PREFIX + "/requests/{request_id}")
def request_details(request_id: str, request: Request):
    owner = owner_id(request)
    with Session() as db:
        pending = pending_request(db, request_id)
        if pending.data.get("stable"):
            runs = active_owner_runs(db, owner)
            return {"client_name": pending.data["client_name"],
                    "redirect_uri": pending.data["redirect_uri"],
                    "resource": pending.data["resource"], "stable": True,
                    "runs": [run_summary(row) for row in runs],
                    "scope": SCOPE}
        row = owned(db, owner, pending.data["run"])
        active(row)
        return {"client_name": pending.data["client_name"],
                "redirect_uri": pending.data["redirect_uri"], "run_id": row.id,
                "scenario": row.data["scenario"], "expires_at": row.expires,
                "resource": pending.data["resource"], "stable": False,
                "scope": SCOPE, "research_consent": row.consent}


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    run_id: str | None = None


@router.post(PREFIX + "/requests/{request_id}")
def decide(request_id: str, body: Decision, request: Request):
    guard(request)
    owner = owner_id(request)
    with Session.begin() as db:
        pending = pending_request(db, request_id, True)
        data = pending.data
        stable = data.get("stable", False)
        selected_run_id = data["run"]
        row = None
        if body.approve and stable:
            selected_run_id = body.run_id
            if not selected_run_id:
                raise failure("invalid_target")
        if body.approve or not stable:
            row = owned(db, owner, selected_run_id, True)
            active(row)
        result = {"state": data["state"]}
        if body.approve:
            code = secrets.token_urlsafe(32)
            add_record(db, "code", code, min(time.time() + 60, row.expires),
                       {**data, "run": row.id,
                        "generation": row.agent_hash, "approved_at": time.time()})
            result["run_id"] = row.id
            result["code"] = code
        else:
            result["error"] = "access_denied"
        db.delete(pending)
        return {"redirect": data["redirect_uri"] + "?" + urlencode(result)}


async def form(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise failure()
    try:
        body = await request.body()
        if len(body) > 8192:
            raise failure()
        return unique_params(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    except (UnicodeError, ValueError):
        raise failure()


@router.post(PREFIX + "/token")
async def token(request: Request):
    params = await form(request)
    # Database work is kept off the async event loop.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(exchange, params)


def exchange(params: dict[str, str]):
    limit("oauth-token-global", 120)
    if params.get("grant_type") == "refresh_token":
        return refresh_exchange(params)
    if params.get("grant_type") != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, 400)
    verifier = params.get("code_verifier", "")
    if not VERIFIER.fullmatch(verifier):
        return JSONResponse({"error": "invalid_grant"}, 400)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with Session.begin() as db:
        code = record(db, "code", params.get("code", ""), True)
        if not code:
            return JSONResponse({"error": "invalid_grant"}, 400)
        data = code.data
        if (not all(params.get(k) == data[k] for k in ("client_id", "redirect_uri", "resource")) or
                not secrets.compare_digest(challenge, data["code_challenge"])):
            return JSONResponse({"error": "invalid_grant"}, 400)
        client = record(db, "client", data["client_id"], lock=True)
        if (not client or not redirect_matches(client.data["redirect_uris"], data["redirect_uri"])):
            return JSONResponse({"error": "invalid_grant"}, 400)
        row = db.scalar(select(Run).where(Run.id == data["run"]).with_for_update())
        if (not row or row.agent_hash != data["generation"] or
                row.expires <= time.time() or row.deletes <= time.time() or
                row.data["state"]["status"] != "active"):
            return JSONResponse({"error": "invalid_grant"}, 400)
        now = time.time()
        grant_id = (secrets.token_urlsafe(24)
                    if "refresh_token" in client.data.get(
                        "grant_types", ["authorization_code"]) else None)
        access_token = "oauth_" + secrets.token_urlsafe(32)
        expires = min(now + 600, row.expires, client.expires)
        token_data = {
            "run": row.id, "generation": row.agent_hash, "scope": SCOPE,
            "client_id": data["client_id"], "resource": data["resource"],
            "client_name": data["client_name"], "approved_at": data.get("approved_at"),
        }
        if grant_id:
            token_data["grant_id"] = grant_id
        add_record(db, "token", access_token, expires, {
            **token_data})
        result = {"access_token": access_token, "token_type": "Bearer",
                  "expires_in": max(0, int(expires - time.time())), "scope": SCOPE}
        if grant_id:
            refresh_token = "refresh_" + secrets.token_urlsafe(32)
            refresh_expires = min(row.expires, client.expires)
            add_record(db, "refresh", refresh_token, refresh_expires, {
                **token_data, "used": False,
            })
            result["refresh_token"] = refresh_token
        db.delete(code)
        return result


def refresh_exchange(params: dict[str, str]):
    secret = params.get("refresh_token", "")
    client_id = params.get("client_id", "")
    if (not secret.startswith("refresh_") or len(secret) > 160 or
            not client_id or len(client_id) > 256):
        return JSONResponse({"error": "invalid_grant"}, 400)

    # Read the run ID without locking the refresh row. Then lock in the same
    # run -> credential order used by dashboard rotation/revocation.
    with Session() as lookup:
        snapshot = record(lookup, "refresh", secret)
        if not snapshot:
            return JSONResponse({"error": "invalid_grant"}, 400)
        snapshot_data = dict(snapshot.data)
    run_id = snapshot_data.get("run")
    grant_id = snapshot_data.get("grant_id")
    if not run_id or not grant_id:
        return JSONResponse({"error": "invalid_grant"}, 400)

    with Session.begin() as db:
        row = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        current = record(db, "refresh", secret, lock=True)
        if not current:
            return JSONResponse({"error": "invalid_grant"}, 400)
        data = current.data

        requested_resource = params.get("resource")
        requested_scope = params.get("scope")
        if (data.get("client_id") != client_id or
                (requested_resource is not None and requested_resource != data.get("resource")) or
                (requested_scope is not None and requested_scope != data.get("scope"))):
            return JSONResponse({"error": "invalid_grant"}, 400)

        client = record(db, "client", client_id, lock=True)
        if (not client or "refresh_token" not in client.data.get(
                "grant_types", ["authorization_code"])):
            return JSONResponse({"error": "invalid_grant"}, 400)

        # A correctly bound replay means the client lost/ignored rotation.
        # Revoke the whole grant family, including all access tokens.
        if data.get("used"):
            revoke_grant(db, grant_id)
            return JSONResponse({"error": "invalid_grant"}, 400)

        now = time.time()
        resource = data.get("resource", "")
        expected_run_resource = origin() + f"/api/sandbox/agent/{run_id}/mcp"
        if (not row or row.agent_hash != data.get("generation") or
                row.expires <= now or row.deletes <= now or
                row.data["state"]["status"] != "active" or
                data.get("scope") != SCOPE or
                resource not in (stable_resource(), expected_run_resource)):
            revoke_grant(db, grant_id)
            return JSONResponse({"error": "invalid_grant"}, 400)

        current.data = {**data, "used": True, "rotated_at": now}
        access_token = "oauth_" + secrets.token_urlsafe(32)
        access_expires = min(now + 600, row.expires, client.expires)
        token_data = {key: value for key, value in data.items()
                      if key not in ("used", "rotated_at")}
        add_record(db, "token", access_token, access_expires, token_data)
        next_refresh = "refresh_" + secrets.token_urlsafe(32)
        add_record(db, "refresh", next_refresh, min(row.expires, client.expires), {
            **token_data, "used": False,
        })
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": max(0, int(access_expires - time.time())),
            "refresh_token": next_refresh,
            "scope": data["scope"],
        }


@router.post(PREFIX + "/revoke")
async def revoke_token(request: Request):
    params = await form(request)
    from starlette.concurrency import run_in_threadpool

    def remove():
        token = params.get("token", "")
        client_id = params.get("client_id", "")
        with Session() as lookup:
            refresh = record(lookup, "refresh", token)
            access = record(lookup, "token", token)

        if refresh and refresh.data.get("client_id") == client_id:
            run_id = refresh.data.get("run")
            with Session.begin() as db:
                if run_id:
                    db.scalar(select(Run).where(Run.id == run_id).with_for_update())
                current = record(db, "refresh", token, lock=True)
                if current and current.data.get("client_id") == client_id:
                    revoke_grant(db, current.data.get("grant_id"))
        elif access and access.data.get("client_id") == client_id:
            with Session.begin() as db:
                current = record(db, "token", token, True)
                if current and current.data.get("client_id") == client_id:
                    db.delete(current)
        return {}
    return await run_in_threadpool(remove)


@router.get("/api/sandbox/runs/{run_id}/oauth-grants")
def list_grants(run_id: str, request: Request):
    owner = owner_id(request)
    with Session.begin() as db:
        row = owned(db, owner, run_id, True)
        clients = {}
        for grant in active_grants(db, row):
            summary = grant_summary(db, grant)
            previous = clients.get(summary["id"])
            if previous:
                summary["expires_at"] = max(previous["expires_at"], summary["expires_at"])
                times = [t for t in (previous["approved_at"], summary["approved_at"]) if t is not None]
                summary["approved_at"] = min(times) if times else None
            clients[summary["id"]] = summary
        return JSONResponse({"grants": list(clients.values())}, headers={"Cache-Control": "no-store"})


@router.delete("/api/sandbox/runs/{run_id}/oauth-grants/{grant_id}")
def disconnect_grant(run_id: str, grant_id: str, request: Request):
    guard(request)
    owner = owner_id(request)
    with Session.begin() as db:
        row = owned(db, owner, run_id, True)
        removed = False
        for grant in active_grants(db, row):
            if grant.data["client_id"] == grant_id:
                db.delete(grant)
                removed = True
        if removed:
            return {"revoked": True}
        raise HTTPException(404, "Active OAuth grant not found.")
