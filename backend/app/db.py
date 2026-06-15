"""Engine and session factory.

SQLite, single local file. The SQLAlchemy layer is the seam that makes a future
Postgres swap small: change DATABASE_URL, nothing else here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Default DB lives at backend/almanac.db unless overridden.
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "almanac.db"
DATABASE_URL = os.environ.get("ALMANAC_DATABASE_URL", f"sqlite:///{_DEFAULT_PATH}")

# check_same_thread=False is the standard SQLite + threaded-server setting.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a session, always close it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
