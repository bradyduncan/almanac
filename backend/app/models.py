"""SQLAlchemy 2.0 models.

Seven tables, split into a shared catalog (same for every user) and per-user progress.
The split is the multi-user seam: catalog stays global, progress keys on user_id.

Enums are stored as VARCHAR + CHECK (native_enum=False) so SQLite and a future Postgres
behave the same and Alembic diffs stay simple.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DrillKind(enum.StrEnum):
    script = "script"
    reflection = "reflection"
    rehearsal = "rehearsal"
    checklist = "checklist"
    audit = "audit"
    record_review = "record_review"


class LogOutcome(enum.StrEnum):
    done = "done"
    skipped = "skipped"
    snoozed = "snoozed"


def _enum(py_enum: type[enum.Enum]) -> SAEnum:
    return SAEnum(py_enum, native_enum=False, validate_strings=True)


# --------------------------------------------------------------------------- #
# Catalog (global, same for every user)
# --------------------------------------------------------------------------- #


class Domain(Base):
    __tablename__ = "domain"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    # lower = higher priority; seed value only, user weights override
    default_priority: Mapped[int] = mapped_column(Integer)
    core_idea: Mapped[str] = mapped_column(Text)

    facts: Mapped[list[LessonFact]] = relationship(
        back_populates="domain", cascade="all, delete-orphan", order_by="LessonFact.order"
    )
    drills: Mapped[list[Drill]] = relationship(
        back_populates="domain", cascade="all, delete-orphan"
    )


class LessonFact(Base):
    __tablename__ = "lesson_fact"
    __table_args__ = (UniqueConstraint("domain_id", "order", name="uq_fact_domain_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    order: Mapped[int] = mapped_column(Integer)

    domain: Mapped[Domain] = relationship(back_populates="facts")


class Drill(Base):
    __tablename__ = "drill"
    __table_args__ = (UniqueConstraint("domain_id", "title", name="uq_drill_domain_title"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[DrillKind] = mapped_column(_enum(DrillKind))
    est_minutes: Mapped[int] = mapped_column(Integer)
    instructions: Mapped[str] = mapped_column(Text)

    domain: Mapped[Domain] = relationship(back_populates="drills")


class SourceRef(Base):
    """Backs the optional fetch utility; never user-facing content."""

    __tablename__ = "source_ref"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int | None] = mapped_column(ForeignKey("domain.id"), nullable=True)
    url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cached_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


# --------------------------------------------------------------------------- #
# Progress (per user)
# --------------------------------------------------------------------------- #


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    active_days_per_week: Mapped[int] = mapped_column(Integer, default=7)
    daily_minutes: Mapped[int] = mapped_column(Integer, default=15)


class UserDomainPref(Base):
    """User's steering input. Absent row = use domain.default_priority."""

    __tablename__ = "user_domain_pref"
    __table_args__ = (UniqueConstraint("user_id", "domain_id", name="uq_pref_user_domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domain.id"), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DrillLog(Base):
    """The event log everything derives from. Never deleted by seeds/migrations."""

    __tablename__ = "drill_log"
    __table_args__ = (
        CheckConstraint(
            "difficulty IS NULL OR (difficulty BETWEEN 1 AND 3)", name="ck_log_difficulty_range"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), index=True)
    drill_id: Mapped[int] = mapped_column(ForeignKey("drill.id"), index=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    outcome: Mapped[LogOutcome] = mapped_column(_enum(LogOutcome))
    # 1 easy .. 3 hard, set only on outcome == done
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
