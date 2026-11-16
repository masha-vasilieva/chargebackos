# Sandbox operator guide

## Scope

This release is bring-your-own-agent only. The developer runs the agent; the service accepts fixed JSON tool calls. The example is a scripted reference, not a model run. Public sharing and hosted model execution are deferred.

## Local validation

```sh
uv run --no-sync python -m unittest discover -s artifacts/api-server/tests -v
uv run --no-sync python artifacts/api-server/tests/check_sandbox_api.py
pnpm --filter @workspace/chargeback-os run typecheck
```

The integration test creates and deletes only synthetic runs. It defaults to localhost:8080 for development; SANDBOX_TEST_URL may point to a development API origin. Do not run tests against users' production sessions.

## Research

```sh
uv run --no-sync python artifacts/api-server/export_research.py
```

This local operator command emits aggregate check counts by scenario/mode and participating-owner count. It does not emit credentials, labels, raw request bodies or private conversations. Only active-consent, unexpired records are selected. No stored research dataset is created by the application.

For deeper analysis, access to the database is privileged operator access; do not build public endpoints that bypass owner scoping. Respect active consent, deletion, and retention when creating any later exports. Counts are self-selected synthetic tests, not fraud prevalence or verified model rankings. One prolific owner can dominate runs.

## Emergency stop

```sh
uv run --no-sync python artifacts/api-server/sandbox_control.py pause
uv run --no-sync python artifacts/api-server/sandbox_control.py status
uv run --no-sync python artifacts/api-server/sandbox_control.py resume
```

Pause prevents owner creation, run creation and agent actions. Existing report access, revocation, consent withdrawal and deletion remain available unless the global request cap is also exhausted. State lives in PostgreSQL and survives worker restarts.

## Operational usage history

Daily usage totals are available at `GET /api/sandbox/operator/usage/daily`.
The endpoint requires `Authorization: Bearer <SANDBOX_OPERATOR_TOKEN>`, where
the separate, high-entropy token is configured in Replit Secrets. A missing or
short token disables operator access; browser-owner cookies and agent tokens
are not accepted. Do not place the token in browser code, URLs, or logs.

The response contains 90 UTC dates and only the run-created, newly accepted
agent-action, and completed-run totals. These are UTC date buckets, written
with the successful run/action transaction. The retention worker removes
buckets older than 90 days. Run deletion cannot revise an unlinked aggregate,
and infrastructure backups have separate retention.

Access checks enforce run expiration and retention independently of cleanup. The process deletes expired live run records every minute when running. No research use permission is inferred from merely creating a run. Withdrawal restores the seven-day-from-creation retention deadline, which may immediately expire an older run.

Backups and infrastructure logs have separate operational retention; validate those policies before claiming specific backup erasure times. Application HTTP access logging is disabled to avoid retaining arbitrary query strings and network identifiers. Do not add third-party session replay or request-body logging.


## Data and retention

Apply the base schema in `artifacts/api-server/sandbox_schema.sql` before
starting against a new external database. The additive usage-table migration
runs from `scripts/post-merge.sh`.
## Public launch boundaries

Keep real credentials, production logs, arbitrary URLs, uploads, custom code, real payments and issuer connections out of the API. The request caps are conservative; a shared reverse proxy may group legitimate users for admission throttling. Adjust using observed traffic, not by trusting client-supplied forwarded-IP headers. Keep global caps enabled.

Privacy and results pages must not imply safety certification, guaranteed refunds, or access to an agent's hidden reasoning. Passing a scripted check is evidence about that scenario only.
