"""Idempotent development/post-merge migration; never called at API startup."""
from database import engine
from services.sandbox_oauth_store import OAuthRecord

if __name__ == "__main__":
    OAuthRecord.__table__.create(engine, checkfirst=True)
    print("Sandbox OAuth storage ready.")
