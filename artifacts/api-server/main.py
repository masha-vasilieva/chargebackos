import copy
import os
import secrets
from typing import Literal
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from database import Session, DemoSession
from services.demo import initial_state, advance, snapshot, perform
from services.evidence_compiler import compile_demo_pdf
from sandbox_api import router as sandbox_router
from sandbox_mcp import router as mcp_router
from sandbox_oauth import router as oauth_router
from services.sandbox_usage_history import router as usage_history_router
from services.sandbox_research_export import router as research_export_router
from services.sandbox_security import (
    ClientIpSelectionMiddleware, Ingress, ProxyPeerObservationMiddleware,
    TRUSTED_REPLIT_PROXY_IPS, lifespan, retention_health_snapshot,
)
from services.sandbox_openapi import install_openapi

app = FastAPI(title="Chargeback OS", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(Ingress)
app.include_router(sandbox_router)
app.include_router(mcp_router)
app.include_router(oauth_router)
app.include_router(usage_history_router)
app.include_router(research_export_router)
install_openapi(app)


@app.get("/api/healthz")
def health():
    return {
        "status": "ok",
        "retention": retention_health_snapshot(),
    }


@app.post("/api/webhooks/stripe")
@app.post("/webhooks/stripe")
def stripe_webhook():
    # Fail closed while credentials and merchant ownership are not configured.
    raise HTTPException(503, "Stripe is not connected. Live event ingestion is disabled.")


_PROTOCOL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@app.api_route("/.well-known", methods=_PROTOCOL_METHODS, include_in_schema=False)
@app.api_route("/.well-known/{unknown_path:path}", methods=_PROTOCOL_METHODS, include_in_schema=False)
@app.api_route("/api", methods=_PROTOCOL_METHODS, include_in_schema=False)
@app.api_route("/api/{unknown_path:path}", methods=_PROTOCOL_METHODS, include_in_schema=False)
def unknown_protocol_route(unknown_path: str = ""):
    return JSONResponse({"detail": "Not Found"}, status_code=404)


COOKIE = "chargeback_demo"


class OfficeAction(BaseModel):
    action: Literal["speed", "cancel", "dispatch", "snooze", "simulate", "reset"]
    id: str | None = Field(default=None, max_length=100)
    speed: int | None = Field(default=None, ge=0, le=3)


def load_session(db, request, response):
    session_id = request.cookies.get(COOKIE, "")
    row = db.scalar(select(DemoSession).where(DemoSession.id == session_id).with_for_update()) if len(session_id) == 64 else None
    if row is None:
        session_id = secrets.token_hex(32)
        row = DemoSession(id=session_id, state=initial_state())
        db.add(row)
        response.set_cookie(COOKIE, session_id, max_age=604800, httponly=True, samesite="lax", secure=request.headers.get("x-forwarded-proto") == "https", path="/api")
    return row


@app.get("/api/office", include_in_schema=False)
def office(request: Request, response: Response):
    with Session.begin() as db:
        row = load_session(db, request, response)
        state = copy.deepcopy(row.state)
        advance(state)
        data = snapshot(state)
        row.state = state
    response.headers["Cache-Control"] = "no-store"
    return data


@app.post("/api/office/actions", include_in_schema=False)
def action(body: OfficeAction, request: Request, response: Response):
    with Session.begin() as db:
        row = load_session(db, request, response)
        state = copy.deepcopy(row.state)
        advance(state)
        try:
            perform(state, body.action, body.id, body.speed)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        data = snapshot(state)
        row.state = state
    response.headers["Cache-Control"] = "no-store"
    return data


@app.get("/api/evidence/{dispute_id}", include_in_schema=False)
def evidence(dispute_id: str, request: Request):
    session_id = request.cookies.get(COOKIE, "")
    with Session() as db:
        row = db.get(DemoSession, session_id)
        if row is None:
            raise HTTPException(404, "Demo session not found")
        d = next((d for d in row.state["disputes"] if d["id"] == dispute_id), None)
        if d is None:
            raise HTTPException(404, "Dispute not found")
        result = compile_demo_pdf(d)
    return Response(result, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{d["id"]}-SYNTHETIC-DEMO.pdf"',
        "Cache-Control": "private, no-store",
    })


if __name__ == "__main__":
    import uvicorn
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    observed_app = ProxyPeerObservationMiddleware(
        ProxyHeadersMiddleware(
            ClientIpSelectionMiddleware(app),
            trusted_hosts=TRUSTED_REPLIT_PROXY_IPS,
        )
    )
    uvicorn.run(
        observed_app,
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        access_log=False,
        proxy_headers=False,
    )
