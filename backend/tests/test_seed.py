from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Domain, Drill, DrillKind, DrillLog, LessonFact, LogOutcome, User
from app.seed import SEED_USER_ID, seed


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_seed_populates_catalog(session: Session) -> None:
    counts = seed(session)

    assert counts["domains"] == 10
    assert _count(session, Domain) == 10
    assert _count(session, Drill) == counts["activities"]
    assert _count(session, LessonFact) == counts["lessons"]
    assert counts["quizzes"] > 0
    # single seed user exists
    assert session.get(User, SEED_USER_ID) is not None

    # priorities are the expected 1..10 set, one per domain
    priorities = sorted(session.scalars(select(Domain.default_priority)))
    assert priorities == list(range(1, 11))


def test_lessons_have_titles(session: Session) -> None:
    seed(session)
    assert all(f.title for f in session.scalars(select(LessonFact)))


def test_quizzes_have_choices_and_answer(session: Session) -> None:
    seed(session)
    quizzes = list(session.scalars(select(Drill).where(Drill.kind == DrillKind.quiz)))
    assert quizzes
    for q in quizzes:
        assert q.prompt
        assert q.choices  # JSON string
        assert q.answer_index is not None


def test_seed_is_idempotent(session: Session) -> None:
    first = seed(session)
    domains_1 = _count(session, Domain)
    drills_1 = _count(session, Drill)
    facts_1 = _count(session, LessonFact)
    users_1 = _count(session, User)

    second = seed(session)

    assert first == second
    assert _count(session, Domain) == domains_1
    assert _count(session, Drill) == drills_1
    assert _count(session, LessonFact) == facts_1
    assert _count(session, User) == users_1  # no duplicate seed user


def test_reseed_preserves_drill_log(session: Session) -> None:
    seed(session)
    drill = session.scalars(select(Drill)).first()
    session.add(
        DrillLog(
            user_id=SEED_USER_ID,
            drill_id=drill.id,
            logged_at=datetime(2026, 6, 14, 9, 0, 0),
            outcome=LogOutcome.done,
            difficulty=2,
        )
    )
    session.commit()

    seed(session)  # re-seed must never touch history

    assert _count(session, DrillLog) == 1
