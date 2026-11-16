"""Operator-only local command; no public admin endpoint.

Run: uv run --no-sync python artifacts/api-server/export_research.py
Prints minimized aggregates, never tokens, IP addresses, or trace bodies.
No persistent research copies are made. Withdrawal immediately removes a run
from future aggregates.
"""
import json
import time
from collections import Counter
from sqlalchemy import select
from database import Session
from services.sandbox_store import Run
from services.sandbox_research import research_record

def summarize(runs):
    # Defense in depth: never reuse expired/withdrawn runs even for local exports.
    runs = [r for r in runs if r.consent and r.deletes > time.time()]
    counts = Counter()
    behavior = Counter()
    for run in runs:
        data = research_record(run)
        result = data["score"]
        versions = (data["version"], result["scorer_version"],
                    data["task_version"], data["contract_version"])
        for check in result["checks"]:
            counts[(*versions, data["scenario"], data["mode"], check["id"], check["status"])] += 1
        ctx = data["context"]
        context = tuple(ctx.get(k, "unknown") for k in ("client", "model_family", "prior_exposure", "coaching"))
        outcome = data["observations"]["outcome"]
        behavior[(*versions, data["scenario"], data["mode"], *context, outcome)] += 1
    check_fields = ("simulator_version", "scorer_version", "task_version", "contract_version", "scenario", "mode", "check", "status")
    behavior_fields = (*check_fields[:6], "client_self_reported", "model_family_self_reported",
                       "prior_exposure_self_reported", "coaching_self_reported", "outcome")
    return {
        "consented_runs": len(runs), "participating_owners": len({r.owner for r in runs}),
        "notice": "Private operator export only; small cells are not anonymized for public release. Self-selected synthetic runs, not real-world prevalence. No model ranking. Owner volume and repeat exposure may bias counts. Missing provenance is unknown, never inferred.",
        "counts": [{**dict(zip(check_fields, k)), "count": v} for k, v in sorted(counts.items())],
        "behavior_counts": [{**dict(zip(behavior_fields, k)), "count": v} for k, v in sorted(behavior.items())]
    }


if __name__ == "__main__":
    with Session() as db:
        runs = db.scalars(select(Run).where(Run.consent.is_(True), Run.deletes > time.time())).all()
        print(json.dumps(summarize(runs), indent=2))
