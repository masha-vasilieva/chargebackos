"""Idempotent development/post-merge migration; never called at API startup."""
from sqlalchemy import text

from database import engine
from services.sandbox_store import (
    ResearchDailyCounter,
    ResearchExportAudit,
    ResearchExportEvent,
)


if __name__ == "__main__":
    with engine.begin() as db:
        db.execute(text(
            "ALTER TABLE sandbox_runs ADD COLUMN IF NOT EXISTS research_ref varchar(64)"
        ))
        db.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS sandbox_runs_research_ref_unique
            ON sandbox_runs(research_ref) WHERE research_ref IS NOT NULL
        """))
        for model in (ResearchExportEvent, ResearchDailyCounter, ResearchExportAudit):
            model.__table__.create(db, checkfirst=True)
    print("Sandbox research export storage ready.")
