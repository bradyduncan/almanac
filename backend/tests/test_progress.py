from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from app import service
from app.exceptions import BadRequestError
from app.models import DrillKind
from app.schemas import LogCreate
from app.seed import SEED_USER_ID, seed

TODAY = date(2026, 6, 15)
NOW = datetime(2026, 6, 15, 9, 0)


@pytest.fixture
def seeded(session: Session) -> Session:
    seed(session)
    return session


def _beginner_items(session: Session, slug: str):
    domain = service.get_domain_detail(session, slug)
    lessons = [f for f in domain.facts if str(f.level) == "beginner"]
    drills = [d for d in domain.drills if str(d.level) == "beginner"]
    return lessons, drills


def _complete_level(session: Session, slug: str, level: str) -> None:
    domain = service.get_domain_detail(session, slug)
    for f in domain.facts:
        if str(f.level) == level:
            service.create_fact_review(session, SEED_USER_ID, f.id, NOW)
    for d in domain.drills:
        if str(d.level) != level:
            continue
        if d.kind == DrillKind.quiz:
            service.grade_quiz(session, SEED_USER_ID, d.id, d.answer_index, NOW)
        else:
            service.create_log(
                session,
                SEED_USER_ID,
                LogCreate(drill_id=d.id, outcome="done", difficulty=2),
                NOW,
            )


def test_overview_initial_state(seeded: Session) -> None:
    data = service.sections_overview(seeded, SEED_USER_ID, TODAY)
    assert data["xp"] == 0
    assert data["streak"] == 0
    assert len(data["sections"]) == 10
    social = next(s for s in data["sections"] if s["domain"].slug == "social-calibration")
    states = {lv["level"]: lv for lv in social["levels"]}
    assert states["beginner"]["unlocked"] is True
    assert states["intermediate"]["unlocked"] is False
    assert states["advanced"]["unlocked"] is False


def test_stub_section_higher_levels_unavailable(seeded: Session) -> None:
    data = service.sections_overview(seeded, SEED_USER_ID, TODAY)
    health = next(s for s in data["sections"] if s["domain"].slug == "health")
    states = {lv["level"]: lv for lv in health["levels"]}
    assert states["beginner"]["available"] is True
    assert states["intermediate"]["available"] is False
    assert states["advanced"]["available"] is False


def test_completing_beginner_unlocks_intermediate(seeded: Session) -> None:
    _complete_level(seeded, "social-calibration", "beginner")
    detail = service.section_detail(seeded, SEED_USER_ID, "social-calibration")
    states = {lv["level"]: lv for lv in detail["levels"]}
    assert states["beginner"]["complete"] is True
    assert states["intermediate"]["unlocked"] is True
    assert states["advanced"]["unlocked"] is False


def test_xp_awarded_for_completion(seeded: Session) -> None:
    lessons, drills = _beginner_items(seeded, "social-calibration")
    quizzes = [d for d in drills if d.kind == DrillKind.quiz]
    confirms = [d for d in drills if d.kind != DrillKind.quiz]
    _complete_level(seeded, "social-calibration", "beginner")
    expected = (
        len(lessons) * service.XP_LESSON
        + len(confirms) * service.XP_ACTIVITY
        + len(quizzes) * service.XP_QUIZ
    )
    data = service.sections_overview(seeded, SEED_USER_ID, TODAY)
    assert data["xp"] == expected


def test_wrong_quiz_does_not_complete(seeded: Session) -> None:
    _, drills = _beginner_items(seeded, "social-calibration")
    quiz = next(d for d in drills if d.kind == DrillKind.quiz)
    wrong = 0 if quiz.answer_index != 0 else 1
    service.grade_quiz(seeded, SEED_USER_ID, quiz.id, wrong, NOW)
    detail = service.level_detail(seeded, SEED_USER_ID, "social-calibration", "beginner")
    quiz_view = next(d for d in detail["drills"] if d["drill"].id == quiz.id)
    assert quiz_view["done"] is False  # only a correct answer completes a quiz


def test_level_detail_locked_raises(seeded: Session) -> None:
    with pytest.raises(BadRequestError):
        service.level_detail(seeded, SEED_USER_ID, "social-calibration", "intermediate")


def test_level_detail_coming_soon_raises(seeded: Session) -> None:
    with pytest.raises(BadRequestError):
        service.level_detail(seeded, SEED_USER_ID, "health", "intermediate")
