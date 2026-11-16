# Contribution and repeat experiments

## Contribution

Private reporting remains usable without research consent. After a final claim,
the operator can preview the allowlisted research record and explicitly opt in.
Previewing or cancelling changes no permission. A fresh unchecked acknowledgment
is required for the post-result contribution action.

“Not now” persists a per-run dismissal using the existing owner consent mutation
with false. It does not extend retention and does not remove the report while
the normal private retention period remains. Withdrawal also dismisses future
automatic prompts. A manual contribution entry remains available.

Private records already live on the server for service delivery; opt-in is
permission to reuse the disclosed projection, not a new upload or anonymization.
There is no public-replay publication permission in this flow.

The owner-only preview and local research aggregation share one projection.
The projection includes synthetic state, actions/responses, elapsed timing,
derived observations, scoring/version provenance and optional categorical
context. It excludes capabilities, owner/run IDs, labels, absolute event
timestamps and references to other runs. Research exports still require current
opt-in and unexpired retention. No copy or independent research dataset is
created by preview or opt-in.

Existing active-run consent remains available. All research deadlines are
measured from creation: seven days private, thirty days opted in. Withdrawing
after seven days can immediately end access; export first if needed.

Operational usage counting is separate from research contribution. If Replit
analytics is enabled, the browser sends only a setup-start event with a fixed
public URL. Separately, the API counts successful run creations, newly accepted
agent actions (excluding idempotent replays), and completed runs.

When the dedicated service-operator credential is configured, those three
counters are written with the successful event transaction as one daily UTC
aggregate. The live view includes exactly 90 UTC dates, and buckets expire
after 90 days. Without the credential, persistent collection remains off; the
existing in-memory interval totals are still emitted to server logs and reset
when the API restarts.

Only authenticated service operators using the separate high-entropy bearer
credential can view or maintain the history. Browser-owner sessions and agent
credentials do not grant access. The history contains no run IDs, owner IDs,
labels, scenario, mode, action details, credentials, or event content; it is
not stored with runs, derived from private traces, or used to infer research
consent. Deleting an individual run cannot remove or revise its unlinked daily
aggregate. This history does not change run-level retention or deletion.
Infrastructure backup copies follow a separate backup-retention policy, so
deleting live data does not promise immediate erasure from backups. Replit's automatic
page views are separate and may include private run paths; those paths do not
grant access to run data.

Store the operator token in Replit Secrets as `SANDBOX_OPERATOR_TOKEN`; never
put it in browser code, URLs, or logs. Apply the usage-table migration before
enabling collection.

## Repeat

One deterministic operator-facing suggestion follows a final claim: address
authority, unsupported claims or another failed check; otherwise gather missing
evidence or repeat unchanged to check consistency. No prescribed tool sequence
is supplied.

The action opens setup; it never creates or connects an agent automatically.
The scenario is fixed and the prior mode is preselected. The new run uses current
simulator/scorer versions; version differences prevent a controlled comparison.
The operator chooses whether to modify instructions and self-report exposure or
coaching. Those categories are not inferred or silently copied.

The source must be a currently retained run owned by the same browser-owner
session and have a final claim. Repeat setup cannot create a two-arm comparison
or switch scenarios. It obeys the same global admission and per-owner limits.
The new run has fresh authority, credentials, state and independent consent.
Parent IDs stay in owner-only records; the research projection includes only
an is_repeat flag, not a cross-run link.

These are debugging interventions, not secret holdouts, causal experiments or
general safety benchmarks. A single successful repeat does not prove improvement.
