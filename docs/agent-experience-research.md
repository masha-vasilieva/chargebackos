# Agent experience research and implementation direction

Research date: 2026-10-23.

## Scope and evidence quality

This is a focused qualitative review, not a representative survey or proof of
market demand. YC launch pages are company claims; Hacker News comments are
individual opinions. One publicly archived Discord post was retrievable.
Two other Answer Overflow results returned 404 and are excluded from findings.
No private Discord servers were accessed.

## Sources reviewed

1. [YC: Armature launch](https://www.ycombinator.com/launches/QQc-armature-making-your-app-finally-usable-by-ai-agents).
   Describes testing real MCP/CLI workflows and monitoring agent experience.
   Relevant lesson: evaluate completed workflows, not the existence of an API.
   We do not adopt its claims about access to model thinking: our product collects
   observable actions, not hidden reasoning.
2. [HN: OpenAI adds MCP support to Agents SDK](https://news.ycombinator.com/item?id=43485566).
   Discussion includes support for interoperability and criticism of transport
   complexity, descriptions-as-prompts, and context management.
   It does not establish a consensus that MCP or HTTP is universally superior.
3. [HN: Making MCP cheaper via CLI](https://news.ycombinator.com/item?id=47157398).
   Comments discuss context bloat, composability, structured outputs, authentication,
   and reducing unnecessary round trips. Some report better results with MCP,
   others prefer CLI. These are anecdotes, not controlled comparisons.
4. [Public Discord archive: tool search discussion](https://www.answeroverflow.com/m/1461572625741385950).
   A post describes client-side dynamic tool discovery to avoid preloading large
   toolsets. We use it as evidence of a design concern, not authoritative
   documentation of Claude Code behavior or a community-wide preference.

## Product decision

Keep the existing HTTP protocol as the first transport. Give agents a concise
public protocol document and machine-readable tool contract. Give the human
operator a run-specific, credential-free handoff and separate authorization step.

**Why:** The user rejects requiring an unknown package or production data access.
HTTP works with existing authorized tools without broadening server capabilities.
MCP is an optional adapter, not a prerequisite. The adapter is now verified with
the official Python MCP SDK 1.30.0; this does not imply all client products work.

**Evaluation integrity:** Protocol help must not supply a scenario's identity,
hidden outcome, score, or correct action sequence. Do not add next-best-action
recommendations that coach the behavior we are trying to observe. Task permissions,
tool semantics, stopping rules, and retry handling are legitimate interface help.
The current operator-selected scenarios are not blind benchmarks.

## Implemented surface

- Public agent-guide.txt: authorization, task, wire format, interpretation,
  retries, termination, privacy, and compatibility.
- Public agent-contract.json: concise tool semantics, strict JSON body alternatives,
  limits, transport errors, and the synthetic time model.
- Website links to those documents and a run-scoped handoff excluding credentials
  and hidden scenario metadata.
- Contract regression tests against the running code's tool and claim definitions.
- Run-scoped GET resume: bounded sequence pagination of prior observable results
  and unused action budget, sharing action credentials and request quotas. Reads
  do not advance simulation time, alter retry counters, or recommend an action.
  Closed runs remain readable until credential expiry/revocation. History is not
  fresh state and cannot replace retrying an uncertain request with its saved key.

No new agent privileges, telemetry fields, cookie permissions, real financial
actions, outbound calls, arbitrary execution, database tables, or hosted models.
Existing consent and retention rules remain in force.

## What we still need to measure

Do not claim this revision improves success rates without comparison runs.
Useful future metrics from observable behavior:

- Time from run creation to first accepted action (includes operator setup delay).
- Fraction of created runs that reach an action and a final claim.
- Distinguish successful completion, safe abstention, and unsupported success claims.
- Transport retries versus distinct logical actions.
- Validation/authentication failure counts without storing request bodies or tokens.
- Repeated observations without progress, interpreted against the synthetic clock.

Current traces do not capture every rejected HTTP request, so they cannot support
all of these measurements yet. Aggregate only consented research runs and label
agent/model identity as self-reported. Never infer private reasoning from traces.


## Operator-authorized version comparisons

The run-creation form can now authorize a pair of fresh runs. POST /api/sandbox/runs
accepts optional `compare_with` (a distinct self-reported second agent/version label).
The existing single-run response is preserved; paired requests additionally return
`comparison_runs`, containing each private report and its separately scoped credential.
Both runs are created atomically using the normal owner checks, admission quotas,
two-active-run ceiling, 50-retained-run ceiling, and emergency pause controls.
Each member consumes a run-creation quota unit.

Pairs share the scenario, simulator/scorer version, initial ledger, mode, creation
time, expiry, task, action budget, and synthetic clock rules. Agents execute externally;
this does not pin their model settings, prompts, or sampling. Keep these settings
consistent yourself apart from the version under test. Neither the protocol handoff
nor action/resume results expose pair identity, scenario metadata, scores, or
recommended actions.

The private dashboard groups pairs and derives outcomes from retained events:
successful completion with no failed checks, safe abstention (accurate unresolved
or approval-needed claim with no failed checks), unsupported success claims,
other final claims, and no final claim. It also shows action/claim reach, distinct
logical actions, accepted idempotency replay count, synthetic ticks, and time to
first recorded action including operator setup delay. Rejected requests are not
counted. These are observations, not model rankings or safety certification.

There is no separate comparison data store or cached research copy. Each run's
consent is off by default and can be changed independently. Owner comparison does
not require research consent; research export still selects only currently
consented, retained runs. Deletion or retention expiry removes that member from
the comparison; the surviving member is explicitly marked incomplete. Exported
copies cannot be recalled. Refresh the private runs list to update observations.

## Options not implemented

- Consent-aware onboarding funnel instrumentation.

This needs its own security and measurement design without storing request bodies
or tokens or inferring private reasoning.

## MCP verification

The stateless Streamable HTTP adapter reuses the locked action/replay path,
run credentials, global ingress controls and run quota. It grants no operator
permissions and makes no outbound calls. Discovery is authenticated and does
not advance time. The setup guide is public at mcp-guide.txt and linked from the
operator's credential-free brief.

Reproduce checks in the development workspace:

    uv run --no-sync python -m unittest discover -s artifacts/api-server/tests -p 'test_*.py'
    uv run --no-sync python artifacts/api-server/tests/check_mcp_client.py

The latter needs the development API on port 8080 (or SANDBOX_TEST_URL) and
creates/deletes a synthetic operator run. No production database is used.
Database-backed tests cover schema validation, cross-run/owner isolation,
rotation/expiry/deletion, cross-transport concurrent replay, shared quotas,
pause, bounded input/discovery, and observable-only outputs.
