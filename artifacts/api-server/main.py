import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
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
from services.error_reporting import init_error_reporting

init_error_reporting()

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
