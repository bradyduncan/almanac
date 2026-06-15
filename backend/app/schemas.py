"""Pydantic v2 schemas — the API's wire contract.

Kept separate from the SQLAlchemy models: routes never return ORM objects, they return
these. Read models use from_attributes so they can be built with `model_validate(orm_row)`.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import DrillKind, LogOutcome


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Catalog read models
# --------------------------------------------------------------------------- #


class DomainOut(_ORMModel):
    id: int
    slug: str
    title: str
    default_priority: int
    core_idea: str


class LessonFactOut(_ORMModel):
    id: int
    body: str
    order: int


class DrillOut(_ORMModel):
    id: int
    domain_id: int
    title: str
    kind: DrillKind
    est_minutes: int
    instructions: str


class DomainDetailOut(DomainOut):
    facts: list[LessonFactOut]
    drills: list[DrillOut]


# --------------------------------------------------------------------------- #
# Logs
# --------------------------------------------------------------------------- #


class LogCreate(BaseModel):
    drill_id: int
    outcome: LogOutcome
    difficulty: int | None = Field(default=None, ge=1, le=3)
    note: str | None = None

    @model_validator(mode="after")
    def _difficulty_only_on_done(self) -> LogCreate:
        if self.outcome == LogOutcome.done:
            if self.difficulty is None:
                raise ValueError("difficulty (1-3) is required when outcome is 'done'")
        elif self.difficulty is not None:
            raise ValueError("difficulty may only be set when outcome is 'done'")
        return self


class LogOut(_ORMModel):
    id: int
    drill_id: int
    logged_at: datetime
    outcome: LogOutcome
    difficulty: int | None
    note: str | None


# --------------------------------------------------------------------------- #
# Today's queue
# --------------------------------------------------------------------------- #


class QueueItemOut(BaseModel):
    drill: DrillOut
    domain_title: str
    score: float
    forced: bool
    factors: dict[str, float]


class TodayOut(BaseModel):
    day: date
    budget_minutes: int
    queued_minutes: int
    items: list[QueueItemOut]
