# Scoped MCP OAuth security design

This service is the authorization server for its own synthetic MCP resource, not
a login broker or a third-party connector. Existing anonymous browser ownership
is retained; Clerk/Replit account authentication would not authorize a sandbox
run and is not introduced. No external provider credentials are needed.

## Trust boundaries (implementation contract)

- Public clients register at a rate-limited endpoint. Registration is not trust:
  names are unverified, no supplied URLs are fetched, and each grant needs an
  explicit browser-owner approval for an already-created run.
- Redirects must be absolute HTTPS URLs or HTTP on literal loopback addresses
  (127.0.0.1 / ::1). No userinfo, fragments, query strings, wildcards, or custom
  schemes. HTTPS redirects must match exactly. Loopback redirects match on
  host and path with any port (RFC 8252 7.3), so desktop clients such as
  VS Code can pick a free port. One exact native-app callback is allowlisted:
  `cursor://anysphere.cursor-mcp/oauth/callback`. Registration keeps only
  supported callbacks and fails if none remain (RFC 7591 lets the server adjust
  metadata). The redirect used at sign-in is bound to the
  request and code. Registration lasts one day; clients may register again.
- Only authorization_code, public token authentication (`none`), and S256 PKCE
  are supported. No implicit flow, refresh tokens, auto-approval, or client URL
  metadata fetching. State is required and returned unchanged.
- Discovery uses a configured canonical HTTPS origin, never an untrusted Host
  header. OAuth is unavailable without that configuration; Bearer stays usable.
- A five-minute request becomes a one-minute, single-use hashed code only after
  same-origin, non-simple-header owner approval. Approval checks ownership and
  active run state under lock. Denial consumes the request.
- A token is an opaque random secret stored only as a hash. It has only
  `sandbox:act` authority for exactly one MCP resource/run and at most ten minutes.
  The same run's direct HTTP action/resume adapter accepts the grant too; it has
  exactly the same simulator authority, not authority over any other resource.
  The token is tied to the run's current credential generation (agent hash).
  Run deletion, expiry, revocation, or rotation therefore invalidates it without
  waiting for token expiry. No owner/report/consent/create-run permission.
- Token redemption checks client, redirect, PKCE, resource, code expiry and run
  generation transactionally. Concurrent code redemption cannot succeed twice.
  RFC 7009 revocation invalidates one token; owner run controls invalidate all.
- Agent transport never sends owner cookies. Browser cookies stay on same-origin
  approval requests; no cookie is copied into a redirect, code or token response.
  The approval page has no third-party resources, no framing, no referrers and
  no cached responses. Secrets and authorization query strings must not be logged.
- Research consent is neither requested nor changed by OAuth. Simulator safety,
  idempotency, run/action limits and emergency pause continue to apply.

## Abuse and failure behavior

All OAuth requests use shared ingress limits, bounded bodies and timeouts.
Registration and authorization have additional global quotas and bounded record
lifetimes. Expired records are purged by the existing cleanup loop. Invalid
redirects fail locally, never redirect to an unvalidated destination. Missing
owner sessions fail closed with instructions to use the browser that created the
run. Lost callback/code requires a fresh approval. Clients needing refresh,
custom schemes or non-exact redirects are deliberately unsupported.

## Verification boundary

Verified using the official MCP SDK 1.30.0 OAuthClientProvider as an actual OAuth-only client:
discovery, dynamic registration, PKCE, operator approval, exchange, initialize,
tools/list and tool call, with no manually supplied Authorization header.
This establishes SDK interoperability only, not Claude/VS Code compatibility.

Reproduce with:

```
SANDBOX_TEST_URL=https://<canonical-origin> uv run --no-sync python artifacts/api-server/tests/check_oauth_client.py
uv run --no-sync python -m unittest discover -s artifacts/api-server/tests
```

The SDK check simulates the operator approval HTTP exchange, while using the
unmodified SDK for all OAuth and MCP client protocol behavior. Browser approval
requires a separate real-browser check.

The full development lifecycle check also reuses cached client registration and
tokens against a second operator-created run. It verifies that the SDK presents
the old token and receives 401, cannot exchange a new token without fresh
approval, and binds authorization and token exchange to the second resource.
After approval, both grants remain scoped to their original runs; cross-run tool
calls fail, and each run reconnects with its own cached token. Neither ledger,
run lifetime, nor research consent changes. These are SDK HTTP checks, not
external client-product compatibility claims. Disposable records from both runs
are removed even when an assertion fails.

## Configuration and migrations

Set `SANDBOX_OAUTH_ORIGIN` to the exact external HTTPS origin (scheme and host,
no path). It must be set for each environment; do not copy a development origin
to production. No Host or forwarded-header fallback is used. Production remains
fail-closed for OAuth until its canonical origin is configured. Bearer continues
to work without it.

In Replit development only, the explicit value `replit-development` uses the
trusted runtime `REPLIT_DEV_DOMAIN`, so fork-specific URLs do not get merged into
the main app. This option is rejected when NODE_ENV=production or
REPLIT_DEPLOYMENT=1. Production always requires a literal canonical HTTPS origin.

The API artifact explicitly routes `/.well-known/oauth-authorization-server`.
Keep this route alongside `/api` on external reverse proxies too.
Apply the added `sandbox_oauth` table/index from `sandbox_schema.sql` before
running the updated API. The development post-merge script uses the idempotent
`uv run --no-sync python artifacts/api-server/migrate_oauth.py` migration.
Never run DDL at production application startup.

OAuth endpoints and approval responses are no-store. Reverse proxies must also
avoid logging authorization query strings or token/revocation form bodies.
The only credential in a redirect is the standard one-minute single-use
PKCE-bound OAuth authorization code, never an access token or owner cookie.
