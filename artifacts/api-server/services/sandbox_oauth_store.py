"""Short-lived OAuth records. Secrets are hashed; never persisted in plaintext."""
import time
from sqlalchemy import String, Float, delete, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
from services.sandbox_store import digest
from services.sandbox_launch import reserve_auxiliary


class OAuthRecord(Base):
    __tablename__ = "sandbox_oauth"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    expires: Mapped[float] = mapped_column(Float, index=True)
    data: Mapped[dict] = mapped_column(JSONB)


def record(db, kind: str, secret: str, lock: bool = False):
    query = select(OAuthRecord).where(OAuthRecord.id == kind + ":" + digest(secret))
    row = db.scalar(query.with_for_update() if lock else query)
    return row if row and row.expires > time.time() else None


def add_record(db, kind: str, secret: str, expires: float, data: dict):
    reserve_auxiliary(db, "oauth")
    db.add(OAuthRecord(id=kind + ":" + digest(secret), expires=expires, data=data))


def valid_token(db, token: str, run) -> bool:
    grant = record(db, "token", token)
    return bool(grant and grant.data["run"] == run.id
                and grant.data["generation"] == run.agent_hash
                and grant.data["scope"] == "sandbox:act")


def active_grants(db, run):
    """Only usable grants, never client registrations or authorization codes."""
    now = time.time()
    if (run.expires <= now or run.deletes <= now or
            run.data["state"]["status"] != "active"):
        return []
    return list(db.scalars(select(OAuthRecord).where(
        or_(OAuthRecord.id.startswith("token:"),
            OAuthRecord.id.startswith("refresh:")),
        OAuthRecord.expires > now,
        OAuthRecord.data["run"].astext == run.id,
        OAuthRecord.data["generation"].astext == run.agent_hash,
        OAuthRecord.data["scope"].astext == "sandbox:act",
    ).with_for_update()))


def revoke_grant(db, grant_id: str):
    """Remove every access and refresh credential in one OAuth grant family."""
    if not grant_id:
        return
    db.execute(delete(OAuthRecord).where(
        or_(OAuthRecord.id.startswith("token:"),
            OAuthRecord.id.startswith("refresh:")),
        OAuthRecord.data["grant_id"].astext == grant_id,
    ))


def revoke_run_credentials(db, run_id: str, generation: str | None = None):
    """Invalidate credentials and unexchanged codes for a run generation."""
    kinds = or_(
        OAuthRecord.id.startswith("code:"),
        OAuthRecord.id.startswith("token:"),
        OAuthRecord.id.startswith("refresh:"),
    )
    conditions = [kinds, OAuthRecord.data["run"].astext == run_id]
    if generation is not None:
        conditions.append(OAuthRecord.data["generation"].astext == generation)
    db.execute(delete(OAuthRecord).where(*conditions))


def grant_summary(db, grant):
    if "client_name" not in grant.data:
        client = record(db, "client", grant.data["client_id"])
        grant.data = {**grant.data,
                      "client_name": client.data["client_name"] if client else "Unnamed client"}
    # OAuth public client IDs identify registrations, not bearer credentials.
    return {"id": grant.data["client_id"],
            "client_name": grant.data["client_name"],
            "scope": grant.data["scope"],
            "approved_at": grant.data.get("approved_at"),
            "expires_at": grant.expires}
