"""Idempotent development/post-merge migration; never called at API startup."""
from database import engine
from services.sandbox_store import UsageDaily
from sqlalchemy import text

if __name__ == "__main__":
    with engine.begin() as db:
        exists = db.execute(
            text("SELECT to_regclass('sandbox_usage_daily')")
        ).scalar_one()
        if exists is None:
            UsageDaily.__table__.create(db, checkfirst=True)
        else:
            columns = set(db.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'sandbox_usage_daily'
            """)).scalars())
            if "bucket_day" in columns and "activity_date" not in columns:
                db.execute(text(
                    "ALTER TABLE sandbox_usage_daily RENAME COLUMN bucket_day TO activity_date"
                ))
            elif "activity_date" not in columns:
                raise RuntimeError("sandbox_usage_daily has an unsupported date column")
    print("Sandbox daily usage storage ready.")
