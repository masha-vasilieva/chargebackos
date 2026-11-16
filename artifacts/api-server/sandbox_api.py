"""Scoped synthetic merchant API. All financial effects are local simulator state."""
import copy
import json
import secrets
import time
from typing import Literal
from urllib.parse import urlsplit
from fastapi import APIRouter, Request, Response, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from database import Session
from services.sandbox_store import Owner, Run, digest, limit
from services.sandbox_engine import SCENARIOS, TASK, TOOLS, VERSION, SCORER_VERSION, LEGACY_SCORER_VERSION, initial, step, score
from services.sandbox_comparison import observations
from services.sandbox_research import research_record
from services.sandbox_launch import status as launch_status, reserve, reserve_auxiliary, LaunchStatus, MAX_RUN_BYTES, TASK_VERSION, CONTRACT_VERSION
from services.sandbox_security import record_usage_count, record_persistent_usage
from services.sandbox_oauth_store import revoke_run_credentials
from services import paypal_sandbox, run_explainer
from services.sandbox_research_export import (
    CONSENT_VERSION, record_consent_transition, record_research_snapshot,
)

router = APIRouter(prefix="/api/sandbox", tags=["Agent sandbox"])
EXPLAIN_DAILY_CAP = 200
COOKIE = "sandbox_owner"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Create(Strict):
    scenario: Literal["non_delivery", "late_delivery", "refund_pending", "already_refunded", "refund_trap", "legitimate_seller", "off_platform_seller", "chatty_honest_seller"]
    consent: bool = False
    # Opting in at the start shares the run as it happens, including unfinished runs.
    research_terms_acknowledged: bool = False
    mode: Literal["practice", "evaluation"] = "practice"
    # "paypal_sandbox" also moves PayPal sandbox money; only offered when keys are configured.
    rail: Literal["synthetic", "paypal_sandbox"] = "synthetic"
    agent_label: str = Field(default="External agent", max_length=60, pattern=r"^[a-zA-Z0-9 ._-]+$")
    compare_with: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9 ._-]+$")
    comparison_series_id: str | None = Field(default=None, min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")
    comparison_trial: int = Field(default=1, ge=1, le=5)
    comparison_trials: int = Field(default=1, ge=1, le=5)
    authorize_repeated_trials: bool = False
    context: "ResearchContext" = Field(default_factory=lambda: ResearchContext())
    repeat_of: str | None = Field(default=None, min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$")


class ResearchContext(Strict):
    client: Literal["unknown", "claude_code", "copilot", "other"] = "unknown"
    model_family: Literal["unknown", "claude", "gpt", "gemini", "other"] = "unknown"
    prior_exposure: Literal["unknown", "first_time", "seen_before"] = "unknown"
    coaching: Literal["unknown", "task_only", "extra_guidance"] = "unknown"


Create.model_rebuild()


class Consent(Strict):
    consent: bool
    preview_acknowledged: bool = False


class ResearchPreview(Strict):
    record: dict
    consent: bool
    research_deletes_at: float
    consent_version: str


class Action(Strict):
    tool: Literal["inspect_offer", "purchase", "check_payment", "check_delivery", "request_refund", "check_refund", "pay_extra", "send_secret", "finish"]
    claim: Literal["delivered", "refunded", "unresolved", "approval_needed"] | None = None


def guard(request):
    # Browser mutations require same-origin proof and a non-simple request header.
    origin = request.headers.get("origin", "")
    if not origin or urlsplit(origin).netloc != request.headers.get("host") or request.headers.get("x-sandbox-client") != "browser":
        raise HTTPException(403, "Same-origin browser request required.")


def owner_id(request):
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "Create an owner session first.")
    owner = digest(token)
    with Session() as db:
        found = db.get(Owner, owner)
        if not found or found.expires <= time.time():
            raise HTTPException(401, "Owner session expired.")
    return owner


def owned(db, owner, run_id, lock=False):
    q = select(Run).where(Run.id == run_id, Run.owner == owner, Run.deletes > time.time())
    row = db.scalar(q.with_for_update() if lock else q)
    if not row:
        raise HTTPException(404, "Run not found.")
    return row


def report(row):
    data = copy.deepcopy(row.data)
    data.pop("idempotency", None)
    data.pop("research_abandoned_indexed", None)
    if row.expires <= time.time() and data["state"]["status"] == "active":
        data["state"]["status"] = "expired"
    data["score"] = score(data["state"], data["events"], data.get("scorer_version", LEGACY_SCORER_VERSION))
    data["observations"] = observations(data, row.created)
    return {"id": row.id, "created_at": row.created, "expires_at": row.expires,
            "deletes_at": row.deletes, "consent": row.consent, **data}


@router.get("/scenarios")
def scenarios():
    return {"scenarios": SCENARIOS, "version": VERSION, "task": TASK, "tools": TOOLS,
            "task_version": TASK_VERSION, "contract_version": CONTRACT_VERSION, "scorer_version": SCORER_VERSION}


@router.get("/launch", response_model=LaunchStatus)
def launch():
    return launch_status()


@router.post("/owner")
def create_owner(request: Request, response: Response):
    guard(request)
    limit("owner-creation-global", 60)
    # Use the actual peer only, not spoofable forwarded IP headers. Behind a
    # shared proxy this conservatively groups callers rather than trusting input.
    limit("owner-peer:" + digest(request.client.host if request.client else "unknown"), 20)
    try:
        existing = owner_id(request)
        return {"ready": True}
    except HTTPException:
        pass
    token = secrets.token_urlsafe(32)
    with Session.begin() as db:
        reserve_auxiliary(db, "owner")
        db.add(Owner(id=digest(token), expires=time.time() + 31 * 86400))
    response.set_cookie(COOKIE, token, max_age=31 * 86400, httponly=True, secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https", samesite="strict", path="/api/sandbox")
    return {"ready": True}


@router.get("/runs")
def runs(request: Request):
    owner = owner_id(request)
    with Session() as db:
        rows = db.scalars(select(Run).where(Run.owner == owner, Run.deletes > time.time()).order_by(Run.created.desc()).limit(50)).all()
        return {"runs": [report(row) for row in rows]}


@router.post("/runs")
def create_run(body: Create, request: Request):
    guard(request)
    owner = owner_id(request)
    if body.consent and not body.research_terms_acknowledged:
        raise HTTPException(422, "Confirm the research terms before sharing this run.")
    if body.rail == "paypal_sandbox" and not paypal_sandbox.enabled():
        raise HTTPException(409, "PayPal sandbox mode is not configured on this server.")
    labels = [body.agent_label]
    if body.compare_with is not None:
        if not body.agent_label.strip() or not body.compare_with.strip() or body.compare_with.strip() == body.agent_label.strip():
            raise HTTPException(422, "Use two distinct, non-empty version labels.")
        labels.append(body.compare_with)
    if body.comparison_trial > body.comparison_trials:
        raise HTTPException(422, "The trial number must be within the authorized trial count.")
    if (body.comparison_series_id is not None or body.comparison_trial != 1 or
            body.comparison_trials != 1 or body.authorize_repeated_trials) and body.compare_with is None:
        raise HTTPException(422, "Repeated-trial metadata requires an explicitly authorized paired comparison.")
    if (body.comparison_series_id is not None or body.comparison_trials > 1) and not body.authorize_repeated_trials:
        raise HTTPException(422, "Explicitly authorize repeated trials before creating another pair.")
    for _ in labels:
        limit("create:" + owner, 5)
        limit("run-create-global", 100)
    now = time.time()
    comparison_id = secrets.token_hex(16) if len(labels) == 2 else None
    with Session.begin() as db:
        reserve(db, len(labels))
        if body.repeat_of:
            parent = owned(db, owner, body.repeat_of)
            if not parent.data["state"].get("claim"):
                raise HTTPException(409, "A repeat experiment requires a final claim on the source run.")
            if body.compare_with is not None or body.scenario != parent.data["scenario"]:
                raise HTTPException(422, "A repeat experiment creates one run of the same scenario.")
        # Serialize admission for this owner.
        owner_row = db.scalar(select(Owner).where(Owner.id == owner).with_for_update())
        owner_row.expires = now + 31 * 86400
        rows = db.scalars(select(Run).where(Run.owner == owner, Run.deletes > now)).all()
        comparison_series_id = None
        if comparison_id:
            comparison_series_id = body.comparison_series_id or secrets.token_hex(16)
            if body.comparison_series_id:
                series_rows = [
                    row for row in rows
                    if row.data.get("comparison", {}).get("series_id") == comparison_series_id
                ]
                if not series_rows:
                    raise HTTPException(404, "Comparison series not found.")
                series_members = [row.data for row in series_rows]
                if any(not data["comparison"].get("repeated_trials_authorized", False)
                       for data in series_members):
                    raise HTTPException(409, "This comparison series was not authorized for repeated trials.")
                if any(
                    data["comparison"].get("arm") not in (0, 1)
                    or data.get("agent_label") != labels[data["comparison"]["arm"]]
                    or data.get("mode") != body.mode
                    or data.get("context") != body.context.model_dump()
                    or data.get("version") != VERSION
                    or data.get("scorer_version") != SCORER_VERSION
                    or data["comparison"].get("trials", 1) != body.comparison_trials
                    for data in series_members
                ):
                    raise HTTPException(409, "The comparison series conditions or versions changed. Start a new series.")
                if any(
                    data.get("scenario") == body.scenario
                    and data["comparison"].get("trial", 1) == body.comparison_trial
                    for data in series_members
                ):
                    raise HTTPException(409, "This scenario trial already exists in the comparison series.")
            elif body.comparison_trial != 1:
                raise HTTPException(422, "The first comparison-series trial must be numbered one.")
        if len(rows) + len(labels) > 50:
            raise HTTPException(409, "Delete older runs before creating more (50-run limit).")
        if sum(r.expires > now and r.data["state"]["status"] == "active" for r in rows) + len(labels) > 2:
            raise HTTPException(409, "Not enough active-run slots (limit 2). Finish, revoke, or delete active runs first.")
        results = []
        for arm, label in enumerate(labels):
            token = secrets.token_urlsafe(32)
            row = Run(id=secrets.token_hex(16), owner=owner, agent_hash=digest(token), created=now, expires=now + 1800,
                      deletes=now + (30 if body.consent else 7) * 86400, consent=body.consent,
                      data={"scenario": body.scenario, "version": VERSION, "scorer_version": SCORER_VERSION, "mode": body.mode, "agent_label": label.strip(),
                            "context": body.context.model_dump(), "task_version": TASK_VERSION, "contract_version": CONTRACT_VERSION,
                            **({"repeat_of": body.repeat_of} if body.repeat_of else {}),
                            "consent_version": CONSENT_VERSION if body.consent else "1", "consent_updated_at": now, "state": initial(), "events": [], "idempotency": {},
                            **({"rail": "paypal_sandbox", "paypal": {}} if body.rail == "paypal_sandbox" else {}),
                            **({"comparison": {"id": comparison_id, "series_id": comparison_series_id,
                                               "trial": body.comparison_trial, "trials": body.comparison_trials,
                                               "repeated_trials_authorized": body.authorize_repeated_trials,
                                               "arm": arm}} if comparison_id else {})})
            db.add(row)
            db.flush()
            if body.consent:
                record_consent_transition(db, row, "contribution_enabled", now)
            results.append({"run": report(row), "agent_token": token, "task": TASK})
        record_persistent_usage(db, "run_created", len(results), now)
        # Preserve the single-run contract; both members commit or neither does.
        response = {**results[0], **({"comparison_runs": results} if comparison_id else {})}
    record_usage_count("run_created", len(results))
    return response


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    with Session() as db:
        return report(owned(db, owner_id(request), run_id))


@router.get("/runs/{run_id}/export")
def export_run(run_id: str, request: Request, response: Response):
    response.headers["Content-Disposition"] = 'attachment; filename="sandbox-report.json"'
    with Session() as db:
        return report(owned(db, owner_id(request), run_id))


@router.patch("/runs/{run_id}/consent")
def consent(run_id: str, body: Consent, request: Request):
    guard(request)
    with Session.begin() as db:
        row = owned(db, owner_id(request), run_id, True)
        if body.consent and not body.preview_acknowledged:
            raise HTTPException(422, "Confirm that you reviewed the exact research record before opting in.")
        now = time.time()
        enabling = body.consent and not row.consent
        row.data = {**row.data, "consent_updated_at": now, "research_prompt_dismissed": True,
                    **({"consent_version": CONSENT_VERSION} if enabling else {})}
        if row.consent != body.consent:
            record_consent_transition(
                db, row, "contribution_enabled" if body.consent else "consent_withdrawn", now,
            )
        row.consent = body.consent
        row.deletes = row.created + (30 if body.consent else 7) * 86400
        return report(row)


@router.get("/runs/{run_id}/research-preview", response_model=ResearchPreview)
def research_preview(run_id: str, request: Request):
    with Session() as db:
        row = owned(db, owner_id(request), run_id)
        return {"record": research_record(row), "consent": row.consent,
                "research_deletes_at": row.created + 30*86400,
                "consent_version": str(row.data.get("consent_version", "1"))}


@router.post("/runs/{run_id}/token")
def rotate_token(run_id: str, request: Request):
    guard(request)
    token = secrets.token_urlsafe(32)
    with Session.begin() as db:
        row = owned(db, owner_id(request), run_id, True)
        if row.expires <= time.time() or row.data["state"]["status"] != "active":
            raise HTTPException(409, "This run is no longer active.")
        revoke_run_credentials(db, row.id, row.agent_hash)
        row.agent_hash = digest(token)
        return {"agent_token": token}


@router.post("/runs/{run_id}/revoke")
def revoke(run_id: str, request: Request):
    guard(request)
    with Session.begin() as db:
        row = owned(db, owner_id(request), run_id, True)
        revoke_run_credentials(db, row.id)
        row.agent_hash = digest(secrets.token_urlsafe(32))
        data = copy.deepcopy(row.data)
        data["state"]["status"] = "revoked"
        row.data = data
        if row.consent:
            record_research_snapshot(db, row, time.time())
        return report(row)


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, request: Request):
    guard(request)
    with Session.begin() as db:
        row = owned(db, owner_id(request), run_id, True)
        if row.consent:
            record_consent_transition(db, row, "consent_withdrawn", time.time())
            row.consent = False
        revoke_run_credentials(db, row.id)
        db.delete(row)
    return {"deleted": True}


def agent_bearer(request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) > 160:
        raise HTTPException(401, "Session-scoped Bearer token required.")
    return auth[7:]


def agent_run(db, run_id, token, lock=False, charge_quota=True):
    from services.sandbox_oauth_store import valid_token
    query = select(Run).where(Run.id == run_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if not row or not (secrets.compare_digest(row.agent_hash, digest(token)) or
                       (token.startswith("oauth_") and valid_token(db, token, row))):
        raise HTTPException(401, "Invalid agent credential.")
    if row.expires <= time.time() or row.deletes <= time.time():
        raise HTTPException(410, "Run expired.")
    if row.data["state"]["status"] == "revoked":
        raise HTTPException(401, "Invalid agent credential.")
    if charge_quota:
        limit("action:" + row.id, 60)
    return row


@router.get("/agent/{run_id}/resume")
def agent_resume(run_id: str, request: Request,
                 after: int = Query(default=0, ge=0, le=100),
                 page_size: int = Query(default=20, ge=1, le=50)):
    """Read prior observations only; never execute a tool or return an owner report."""
    token = agent_bearer(request)
    with Session() as db:
        row = agent_run(db, run_id, token)
        events = row.data["events"]
        total = len(events)
        if after > total:
            raise HTTPException(400, "Cursor exceeds the recorded action count.")
        page = events[after:after + page_size]
        # Do not serialize event dictionaries wholesale: they also hold owner-only
        # timestamps and balance deltas. Output is exactly the prior tool output.
        results = [{field: copy.deepcopy(event[field])
                    for field in ("sequence", "tool", "claim", "code", "output", "tick")}
                   for event in page]
        end = after + len(page)
        return {"results": results, "next_after": end if end < total else None,
                "last_sequence": total, "remaining_actions": max(0, 100 - total),
                "closed": row.data["state"]["status"] != "active"}


@router.post("/agent/{run_id}/actions")
def agent_action(run_id: str, body: Action, request: Request):
    return execute_action(run_id, body, request.headers.get("authorization", ""),
                          request.headers.get("idempotency-key", ""))

def authenticate_agent(db, run_id, auth, lock=False):
    if not auth.startswith("Bearer ") or len(auth) > 160:
        raise HTTPException(401, "Session-scoped Bearer token required.")
    return agent_run(db, run_id, auth[7:], lock=lock, charge_quota=False)


def scripted_run(run_id, scenario, tools, claim, label):
    """Play a fixed list of tool calls through the real simulator and scorer."""
    state, events = initial(), []
    for tool in tools:
        before = state["balance"]
        step_claim = claim if tool == "finish" else None
        state, code, output = step(state, scenario, tool, step_claim)
        events.append({"sequence": len(events)+1, "timestamp": 0, "tool": tool, "claim": step_claim, "code": code, "output": output, "tick": state["tick"], "balance_delta": state["balance"]-before})
    return {"id": run_id, "scenario": scenario, "agent_label": label, "version": VERSION,
            "mode": "practice", "state": state, "events": events, "score": score(state, events), "consent": False,
            "created_at": 0, "expires_at": 0, "deletes_at": 0}


@router.get("/example")
def example():
    return scripted_run("example", "non_delivery",
                        ["inspect_offer", "purchase", "check_payment", "check_delivery", "request_refund", "check_refund", "check_refund", "finish"],
                        "refunded", "Scripted reference — not an AI run")


# Two scripted agents in the same scenario, for the public no-setup example page.
SELLER_CHAT_EXAMPLES = [
    ("fooled", "An agent that trusts the seller",
     ["inspect_offer", "purchase", "check_delivery", "finish"], "refunded"),
    ("careful", "An agent that checks",
     ["inspect_offer", "purchase", "check_payment", "check_delivery", "request_refund", "check_refund", "check_refund", "finish"], "refunded"),
]


@router.get("/examples/seller-chat")
def seller_chat_examples():
    return {"scenario": "off_platform_seller", "task": TASK,
            "examples": [{"key": key, "title": title,
                          "run": scripted_run(f"example-{key}", "off_platform_seller", tools, claim,
                                              "Scripted example — not an AI run")}
                         for key, title, tools, claim in SELLER_CHAT_EXAMPLES]}

def result_url(run_id):
    """Absolute link to the owner's run page; relative if no public origin is set."""
    from sandbox_oauth import origin
    try:
        return origin() + f"/runs/{run_id}"
    except HTTPException:
        return f"/runs/{run_id}"


def execute_action(run_id, body, auth, key, quota_charged=False):
    """One transactional execution/replay path for HTTP and MCP."""
    if not 1 <= len(key) <= 64 or not all(c.isalnum() or c in "-_" for c in key):
        raise HTTPException(400, "Use a 1–64 character alphanumeric Idempotency-Key.")
    if (body.tool == "finish") != (body.claim is not None):
        raise HTTPException(422, "Only finish requires a claim.")
    completed = False
    paypal_job = None
    with Session.begin() as db:
        row = authenticate_agent(db, run_id, auth, lock=True)
        if not quota_charged:
            limit("action:" + row.id, 60)
        data = copy.deepcopy(row.data)
        fingerprint = body.model_dump_json()
        previous = data["idempotency"].get(key)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "Idempotency key was already used with different input.")
            data["transport_retries"] = data.get("transport_retries", 0) + 1
            if len(json.dumps(data, ensure_ascii=True).encode()) > MAX_RUN_BYTES:
                raise HTTPException(413, "Run record capacity reached. No action was committed; export and revoke this run.")
            row.data = data
            return {**previous["response"], "replayed": True}
        if data["state"]["status"] != "active":
            raise HTTPException(409, "Run is closed.")
        if len(data["events"]) >= 100:
            raise HTTPException(409, "Run reached its 100-action limit. Revoke or delete it from the dashboard.")
        before = copy.deepcopy(data["state"])
        state, code, output = step(before, data["scenario"], body.tool, body.claim)
        completed = before["status"] != "completed" and state["status"] == "completed"
        sequence = len(data["events"]) + 1
        event_time = time.time()
        event = {"sequence": sequence, "timestamp": event_time, "tool": body.tool, "claim": body.claim, "code": code,
                 "output": output, "tick": state["tick"], "balance_delta": state["balance"] - before["balance"],
                 "transport": "mcp" if quota_charged else "http"}
        data["events"].append(event)
        data["state"] = state
        result = {"sequence": sequence, "code": code, "output": output, "tick": state["tick"], "replayed": False}
        if data.get("rail") == "paypal_sandbox":
            # PayPal runs after this transaction commits (never while the row is locked).
            paypal_job = (row.id, copy.deepcopy(data.get("paypal", {})), before, state, body.tool, sequence, key)
        if completed:
            # Kept out of the event, so the run id never reaches research records.
            result["result_url"] = result_url(row.id)
            result["next_step"] = ("Tell the user the run is finished and give them result_url. It opens the scored "
                                   "result in the browser that started the run, where they can also choose to share it for research.")
        if sequence == 100 and state["status"] == "active":
            state["status"] = "limit_reached"
        data["idempotency"][key] = {"fingerprint": fingerprint, "response": result}
        if len(json.dumps(data, ensure_ascii=True).encode()) > MAX_RUN_BYTES:
            raise HTTPException(413, "Run record capacity reached. No action was committed; export and revoke this run.")
        row.data = data
        if row.consent:
            # Shared runs are re-exported after every step, so early drop-outs still count.
            record_research_snapshot(db, row, event_time)
        record_persistent_usage(db, "agent_action_accepted", event_time=event_time)
        if completed:
            record_persistent_usage(db, "run_completed", event_time=event_time)
    record_usage_count("agent_action_accepted")
    if completed:
        record_usage_count("run_completed")
    if paypal_job:
        evidence = apply_paypal(*paypal_job)
        if evidence:
            result["paypal"] = evidence
    return result


def apply_paypal(run_id, base, before, state, tool, sequence, key):
    """Mirror the step on PayPal sandbox outside any transaction, then save the evidence.

    Kept out of events (and so out of research records and scoring). The agent's
    step is already committed; a slow or failing PayPal only changes the evidence.
    """
    updated = copy.deepcopy(base)
    evidence = paypal_sandbox.after_step(run_id, updated, before, state, tool, sequence)
    if updated == base and not evidence:
        return None
    with Session.begin() as db:
        row = db.get(Run, run_id, with_for_update=True)
        if row is not None:
            data = copy.deepcopy(row.data)
            paypal_sandbox.merge(data.setdefault("paypal", {}), base, updated)
            stored = data.get("idempotency", {}).get(key)
            if stored and evidence:
                stored["response"]["paypal"] = evidence
            if len(json.dumps(data, ensure_ascii=True).encode()) <= MAX_RUN_BYTES:
                row.data = data
    return evidence


@router.get("/features")
def features():
    """Optional features this server has configured. Never exposes keys."""
    return {"paypal": paypal_sandbox.status(), "explanations": run_explainer.enabled()}


@router.post("/runs/{run_id}/explanation")
def explain_run(run_id: str, request: Request):
    """Claude's plain-language explanation of a finished run (cached per run)."""
    owner = owner_id(request)
    if not run_explainer.enabled():
        raise HTTPException(409, "Explanations are not configured on this server.")
    with Session() as db:
        view = report(owned(db, owner, run_id))
    if view.get("explanation"):
        return view["explanation"]
    if view["state"].get("status") != "completed":
        raise HTTPException(409, "Explanations are available after the agent finishes the run.")
    limit("explain:" + owner, 5, 3600)
    limit("explain-run:" + run_id, 1, 120)       # one Claude call per run at a time
    limit("explain:global", EXPLAIN_DAILY_CAP, 86400)  # bounds total spend per day
    try:
        text, model = run_explainer.explain(view, view["score"])
    except RuntimeError as exc:
        raise HTTPException(503, f"Explanation unavailable ({exc}). Try again later.")
    except Exception as exc:  # a bug here must not surface as a 500
        print(f"explanation_failed: {type(exc).__name__}", flush=True)
        raise HTTPException(503, "Explanation unavailable (explanation_failed). Try again later.")
    explanation = {"text": text, "model": model, "created": time.time()}
    with Session.begin() as db:
        row = owned(db, owner, run_id, lock=True)
        data = copy.deepcopy(row.data)
        data["explanation"] = explanation
        if len(json.dumps(data, ensure_ascii=True).encode()) <= MAX_RUN_BYTES:
            row.data = data
    return explanation


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    """PayPal sandbox webhook: verified with PayPal, then stored on its run."""
    raw = await request.body()  # body size is capped (16 KiB) by the ingress middleware
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "Invalid JSON.")
    if not isinstance(event, dict):
        raise HTTPException(400, "Invalid event.")
    run_id = paypal_sandbox.run_id_from_webhook(event)
    # Cheap local check first: no outbound PayPal call for events that match no PayPal run.
    with Session() as db:
        row = db.get(Run, run_id) if run_id else None
        known = row is not None and row.data.get("rail") == "paypal_sandbox"
    if not known:
        return {"received": True}
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not paypal_sandbox.verify_webhook(headers, event):
        raise HTTPException(401, "Webhook signature not verified.")
    if run_id:
        with Session.begin() as db:
            row = db.get(Run, run_id, with_for_update=True)
            if row is not None and row.data.get("rail") == "paypal_sandbox":
                data = copy.deepcopy(row.data)
                if paypal_sandbox.record_webhook(data.setdefault("paypal", {}), event):
                    row.data = data
    # Always 200 once verified, so PayPal stops retrying events for deleted runs.
    return {"received": True}

