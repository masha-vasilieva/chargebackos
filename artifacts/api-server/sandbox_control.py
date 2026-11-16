"""Local operator emergency control; no remote administration surface."""
import argparse
import json
from sqlalchemy import text
from database import Session
from services.sandbox_launch import status

parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["pause", "resume", "status"])
args = parser.parse_args()
with Session.begin() as db:
    if args.action != "status":
        db.execute(text("UPDATE sandbox_control SET paused = :paused WHERE id = 1"), {"paused": args.action == "pause"})
    paused = db.execute(text("SELECT paused FROM sandbox_control WHERE id = 1")).scalar_one()
print("Sandbox admission/actions: " + ("PAUSED" if paused else "ENABLED"))
print("Public launch capacity: " + json.dumps(status().model_dump()))
