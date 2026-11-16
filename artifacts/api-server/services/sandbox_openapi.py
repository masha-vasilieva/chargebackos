"""Describe capability authentication in FastAPI's public machine-readable API."""
from fastapi.openapi.utils import get_openapi


def install_openapi(app):
    def document():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title="Chargeback OS", version="1.0.0", routes=app.routes,
                             description="Synthetic merchant sandbox. No real payments. Runs use simulated money by default; a run created with rail \"paypal_sandbox\" settles on the PayPal Developer sandbox (sandbox money only, not affiliated with or endorsed by PayPal). See /guide for the agent task and safety boundaries.")
        schema.setdefault("components", {})["securitySchemes"] = {
            "AgentCapability": {"type": "http", "scheme": "bearer", "description": "Temporary run-scoped credential. Never use a real payment key."},
            "OwnerCookie": {"type": "apiKey", "in": "cookie", "name": "sandbox_owner"},
            "ServiceOperator": {"type": "http", "scheme": "bearer", "description": "Private service-operator access. Requires SANDBOX_OPERATOR_TOKEN."},
        }
        for path, methods in schema["paths"].items():
            if path.startswith("/api/sandbox/oauth"):
                continue  # OAuth public protocol endpoints do not require owner headers.
            if not path.startswith("/api/sandbox"):
                continue
            for method, operation in methods.items():
                if not isinstance(operation, dict):
                    continue
                params = operation.setdefault("parameters", [])
                if "/operator/" in path:
                    operation["security"] = [{"ServiceOperator": []}]
                elif "/agent/" in path:
                    operation["security"] = [{"AgentCapability": []}]
                if "/agent/" in path and method == "post":
                    params.append({"name": "Idempotency-Key", "in": "header", "required": True,
                                   "schema": {"type": "string", "minLength": 1, "maxLength": 64},
                                   "description": "Reuse only when retrying the same logical action with the exact same body."})
                elif "/runs" in path:
                    operation["security"] = [{"OwnerCookie": []}]
                if "/agent/" not in path and method in ("post", "patch", "delete"):
                    params.extend([
                        {"name": "X-Sandbox-Client", "in": "header", "required": True, "schema": {"type": "string", "enum": ["browser"]}},
                        {"name": "Origin", "in": "header", "required": True, "schema": {"type": "string"}, "description": "Must match this service's request host."},
                    ])
        app.openapi_schema = schema
        return schema
    app.openapi = document
