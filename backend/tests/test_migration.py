"""Smoke test the real Alembic upgrade path (review finding 3).

The other fixtures build the schema with Base.metadata.create_all(), which would hide a
migration that has drifted from the models. This test runs `alembic upgrade head` against
a temp DB and asserts the resulting schema contains every model table.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.models import Base

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "smoke.db"
    monkeypatch.setenv("ALMANAC_DATABASE_URL", f"sqlite:///{db_file}")

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")
    return db_file


def test_migration_creates_all_model_tables(migrated_db) -> None:
    engine = create_engine(f"sqlite:///{migrated_db}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    expected = set(Base.metadata.tables) | {"alembic_version"}
    missing = expected - tables
    assert not missing, f"migration is missing tables: {missing}"


def test_migration_has_server_defaults(migrated_db) -> None:
    """Guard the fix from review finding 1/2: user defaults live in the DB schema."""
    engine = create_engine(f"sqlite:///{migrated_db}")
    try:
        cols = {c["name"]: c for c in inspect(engine).get_columns("user")}
    finally:
        engine.dispose()

    assert cols["active_days_per_week"]["default"] is not None
    assert cols["daily_minutes"]["default"] is not None
