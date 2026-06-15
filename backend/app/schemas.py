"""Pydantic v2 schemas — the API's wire contract.

Kept separate from the SQLAlchemy models: routes never return ORM objects, they return
these. Read models use from_attributes so they can be built with `model_validate(orm_row)`.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    title: str
    body: str
    order: int


class DrillOut(_ORMModel):
    """Activity. For quizzes, `prompt` + `choices` are populated; the correct answer is
    never serialized (grading is server-side)."""

    id: int
    domain_id: int
    title: str
    kind: DrillKind
    est_minutes: int
    instructions: str
    prompt: str | None = None
    choices: list[str] | None = None

    @field_validator("choices", mode="before")
    @classmethod
    def _parse_choices(cls, v: object) -> object:
        if isinstance(v, str):
            return json.loads(v)
        return v


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


class FactReviewCreate(BaseModel):
    fact_id: int


class FactReviewOut(_ORMModel):
    id: int
    fact_id: int
    reviewed_at: datetime


# --------------------------------------------------------------------------- #
# Quizzes
# --------------------------------------------------------------------------- #


class QuizAnswer(BaseModel):
    drill_id: int
    choice_index: int


class QuizResult(BaseModel):
    drill_id: int
    correct: bool
    chosen_index: int
    answer_index: int
