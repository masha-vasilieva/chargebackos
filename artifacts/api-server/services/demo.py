"""Synthetic fixtures and deterministic simulation. Never used as real evidence."""
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def iso(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


def event(text, agent="AI CEO"):
    return {"id": str(uuid4()), "text": text, "time": iso(), "agent": agent}


def dispute(index, amount, status, telemetry=True):
    return {
        "id": f"demo_dp_{index:04d}", "merchant": ["Acme Cloud", "Orbit Studio", "Layers SaaS"][index % 3],
        "customer": f"demo-user-{index}@example.com", "amount": amount, "currency": "usd",
        "reason": "subscription_canceled" if index % 2 else "fraudulent", "status": status,
        "deadline": iso(86400 * (index % 5 + 2)), "telemetry": telemetry,
        "events": [
            {"id": f"{index}-{n}", "text": text, "time": iso(-86400 * days), "agent": "PostHog · synthetic"}
            for n, (days, text) in enumerate([
                (100, "Account created · 192.0.2.14 · demo-device-01"),
                (90, "Terms clickwrap accepted · demo terms version 1"),
                (30, "Renewal payment · synthetic transaction"),
                (8, "Successful login · 192.0.2.14 · demo-device-01"),
                (5, "Exported workspace report · 192.0.2.14"),
                (2, "Invited a team seat · demo-device-01"),
            ])
        ] if telemetry else [],
    }


def directive(d):
    return {
        "id": f"review_{d['id']}", "title": "Review high-value dispute" if d["amount"] > 500 else "Missing telemetry",
        "context": f"{d['merchant']} · ${d['amount']:,.2f}. " + (
            "Amount exceeds the $500 human-review threshold." if d["amount"] > 500 else "Activity evidence is missing."
        ) + " This is a synthetic demo case; no submission will be made.",
        "asset": "Please review the renewal date, the customer's cancellation request, and the original activity records. Confirm the evidence is accurate before any real representment. This demo dossier is not valid evidence.",
        "status": "pending", "disputeId": d["id"],
    }


def initial_state():
    disputes = [
        dispute(1, 249, "PENDING_TELEMETRY"), dispute(2, 399, "DRAFTING"),
        dispute(3, 849, "BLOCKED_ON_HUMAN"), dispute(4, 129, "BLOCKED_ON_HUMAN", False),
        dispute(5, 2490, "won"), dispute(6, 1490, "won"), dispute(7, 199, "lost"),
        dispute(8, 299, "DEMO_SUBMITTED"),
    ]
    return {
        "mode": "demo", "speed": 1, "_lastTick": time.time(), "_nextId": 9,
        "disputes": disputes,
        "tasks": [
            {"id": "task_ingest", "title": "Ingesting webhooks", "agent": "Gargoyle", "status": "listening", "progress": 0, "disputeId": ""},
            {"id": "task_1", "title": "Querying telemetry", "agent": "Gargoyle", "status": "active", "progress": 28, "disputeId": disputes[0]["id"]},
            {"id": "task_2", "title": "Drafting evidence dossier", "agent": "Rory", "status": "active", "progress": 64, "disputeId": disputes[1]["id"]},
            {"id": "task_8", "title": "Staging gateway upload", "agent": "Courier", "status": "completed", "progress": 100, "disputeId": disputes[7]["id"]},
        ],
        "directives": [directive(d) for d in disputes if d["status"] == "BLOCKED_ON_HUMAN"],
        "activity": [
            event("Two cases require your review. Live actions are disabled."),
            event("Sample dossier compiled for Orbit Studio.", "Rory"),
            event("Demo office initialized. No external services connected.", "Gargoyle"),
        ],
        "agents": [
            {"id": "ceo", "name": "AI CEO", "role": "Orchestrator", "status": "Directing", "mood": "Focused", "bonus": 40},
            {"id": "human", "name": "Human / Chair", "role": "Executive oversight", "status": "Reviewing", "mood": "Ready", "bonus": 15},
            {"id": "gargoyle", "name": "Gargoyle", "role": "Ingester", "status": "Listening · demo events", "mood": "Alert", "bonus": 25},
            {"id": "rory", "name": "Rory", "role": "Evidence synthesizer", "status": "Drafting dossier", "mood": "Inspired", "bonus": 30},
            {"id": "courier", "name": "Courier", "role": "Submitter", "status": "Staging demo upload", "mood": "Confident", "bonus": 20},
        ],
        "metrics": {},
        "integrations": [
            {"name": "Stripe", "status": "disconnected", "description": "Live refunds, merchant onboarding and submissions disabled."},
            {"name": "PostHog / Auth0", "status": "demo", "description": "Deterministic synthetic telemetry only."},
            {"name": "AWS S3", "status": "disconnected", "description": "Sample PDFs streamed in memory; no files stored."},
            {"name": "AI provider", "status": "disconnected", "description": "Rule-based demo orchestrator; no model calls or token charges."},
        ],
    }


def advance(state):
    now = time.time()
    elapsed = min(max(now - state["_lastTick"], 0), 60)
    state["_lastTick"] = now
    for item in state["directives"]:
        if item["status"] == "snoozed" and datetime.fromisoformat(item["snoozedUntil"]) <= datetime.now(timezone.utc):
            item["status"] = "pending"
    for task in state["tasks"]:
        if task["status"] != "active" or not state["speed"]:
            continue
        task["progress"] = min(100, task["progress"] + elapsed * state["speed"] * .8)
        if task["progress"] < 100:
            continue
        d = next(d for d in state["disputes"] if d["id"] == task["disputeId"])
        if d["status"] == "PENDING_TELEMETRY":
            d["status"] = "DRAFTING"
            task.update(title="Drafting evidence dossier", agent="Rory", progress=0)
        elif d["status"] == "DRAFTING":
            d["status"] = "READY"
            task.update(title="Staging gateway upload", agent="Courier", progress=0)
        else:
            d["status"] = "DEMO_SUBMITTED"
            task.update(status="completed")
            state["activity"].insert(0, event(f"{d['id']} simulated submission complete. Nothing sent to Stripe.", "Courier"))
    state["activity"] = state["activity"][:40]


def snapshot(state):
    closed = [d for d in state["disputes"] if d["status"] in ("won", "lost")]
    won = [d for d in closed if d["status"] == "won"]
    state["metrics"] = {
        "inFlight": sum(d["amount"] for d in state["disputes"] if d["status"] not in ("won", "lost", "CANCELED")),
        "earned": round(sum(d["amount"] for d in won) * .2, 2),
        "winRate": round(100 * len(won) / len(closed), 1) if closed else 0,
        "ratio": round(len(state["disputes"]) / 730 * 100, 2),
        "tokenBudget": 100, "storageCount": 0, "pilotCount": 3, "pilotCapacity": 10, "transactionCount": 730,
    }
    return {k: v for k, v in state.items() if not k.startswith("_")}


def perform(state, action, item_id=None, speed=None):
    if action == "speed":
        if speed is None:
            raise ValueError("Speed is required")
        state["speed"] = speed
    elif action == "cancel":
        task = next((t for t in state["tasks"] if t["id"] == item_id), None)
        if not task:
            raise LookupError("Task not found")
        if task["status"] == "completed":
            raise ValueError("Completed tasks cannot be canceled")
        task["status"] = "canceled"
        for d in state["disputes"]:
            if d["id"] == task["disputeId"]:
                d["status"] = "CANCELED"
        state["activity"].insert(0, event(f"Canceled {task['title']}.", "Human"))
    elif action in ("dispatch", "snooze"):
        item = next((i for i in state["directives"] if i["id"] == item_id), None)
        if not item:
            raise LookupError("Directive not found")
        if item["status"] == "dispatched":
            raise ValueError("Directive already dispatched")
        item["status"] = "dispatched" if action == "dispatch" else "snoozed"
        if action == "snooze":
            item["snoozedUntil"] = iso(7200)
        else:
            # Acknowledgement is not approval to send or fabricate missing evidence.
            state["activity"].insert(0, event(f"Human acknowledged {item['disputeId']}. Live submission remains blocked.", "Human"))
    elif action == "simulate":
        index = state["_nextId"]
        if len(state["disputes"]) >= 100:
            raise ValueError("Demo limit reached. Reset the demo to continue.")
        state["_nextId"] += 1
        amount = [149, 349, 729][index % 3]
        d = dispute(index, amount, "BLOCKED_ON_HUMAN" if amount > 500 else "PENDING_TELEMETRY")
        state["disputes"].insert(0, d)
        if amount > 500:
            state["directives"].insert(0, directive(d))
        else:
            state["tasks"].insert(1, {"id": f"task_{index}", "title": "Querying telemetry", "agent": "Gargoyle", "status": "active", "progress": 0, "disputeId": d["id"]})
        state["activity"].insert(0, event(f"Synthetic dispute {d['id']} received.", "Gargoyle"))
    elif action == "reset":
        state.clear()
        state.update(initial_state())
