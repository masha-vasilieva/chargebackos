"""Portable PostgreSQL storage; never silently fall back to local storage."""
import os
from sqlalchemy import create_engine, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

url = os.environ["DATABASE_URL"]
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, pool_pre_ping=True, pool_size=4, max_overflow=4, pool_timeout=3,
                       connect_args={"connect_timeout": 5,
                                     "options": "-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=10000"})
Session = sessionmaker(engine)


class Base(DeclarativeBase):
    pass


class DemoSession(Base):
    __tablename__ = "chargeback_demo_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[dict] = mapped_column(JSONB)
