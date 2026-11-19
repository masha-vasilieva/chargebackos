"""Optional error reporting to Sentry. Off unless SENTRY_DSN is set.

Privacy rules (see docs/legal): only the error type and where in our code it
happened leave the server. No request bodies, headers, cookies, query strings,
IP addresses, user ids, local variables, logs or exception messages; no tracing.
"""
import os

import sentry_sdk

_DROP_KEYS = ("request", "user", "extra", "contexts", "breadcrumbs", "server_name", "modules", "tags")


def scrub_event(event, hint=None):
    """Strip everything except exception type, module and stack frames."""
    for key in _DROP_KEYS:
        event.pop(key, None)
    event.pop("message", None)
    event.pop("logentry", None)
    for exc in (event.get("exception") or {}).get("values") or []:
        exc["value"] = ""
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            for k in ("vars", "pre_context", "context_line", "post_context", "abs_path"):
                frame.pop(k, None)
    return event


def environment_name():
    """"staging" inside the Replit workspace (it sets REPLIT_DEV_DOMAIN), else "production"."""
    return "staging" if os.environ.get("REPLIT_DEV_DOMAIN") else "production"


def init_error_reporting(dsn=None):
    """Start Sentry if a DSN is configured. Returns True when reporting is on."""
    dsn = dsn if dsn is not None else os.environ.get("SENTRY_DSN", "")
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=environment_name(),
        send_default_pii=False,
        include_local_variables=False,
        max_breadcrumbs=0,
        traces_sample_rate=0,
        profiles_sample_rate=0,
        max_request_body_size="never",
        server_name="",
        before_send=scrub_event,
    )
    return True
