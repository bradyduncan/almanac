"""Thin service layer between routes and the DB.

Routes hold no business logic; they call these functions. Functions take a Session
(they never open their own). Streak math stays pure in metrics.py; this module loads
rows, computes section/level progression + gating + XP, and persists writes.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import metrics
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    LEVEL_ORDER,
    Domain,
    Drill,
    DrillKind,
    DrillLog,
    FactReview,
    LessonFact,
    LogOutcome,
    User,
)
from app.schemas import LogCreate

# XP awarded per completed item (computed, not stored).
XP_LESSON = 10
XP_ACTIVITY = 15
XP_QUIZ = 20

# The single API-boundary seam for identity. Multi-user later = resolve this from auth
# instead of hardcoding. Nothing below this line should care where the id came from.
CURRENT_USER_ID = 1


# --------------------------------------------------------------------------- #
# Catalog reads
# --------------------------------------------------------------------------- #


def list_domains(session: Session) -> list[Domain]:
    return list(session.scalars(select(Domain).order_by(Domain.default_priority)))


def get_domain_detail(session: Session, slug: str) -> Domain:
    domain = session.scalar(
        select(Domain)
        .where(Domain.slug == slug)
        .options(selectinload(Domain.facts), selectinload(Domain.drills))
    )
    if domain is None:
        raise NotFoundError("domain", slug)
    return domain


def list_drills(session: Session, domain_slug: str | None = None) -> list[Drill]:
    stmt = select(Drill).join(Domain).order_by(Domain.default_priority, Drill.id)
    if domain_slug is not None:
        stmt = stmt.where(Domain.slug == domain_slug)
    return list(session.scalars(stmt))


def list_facts(session: Session, domain_slug: str) -> list[LessonFact]:
    domain = get_domain_detail(session, domain_slug)
    return domain.facts


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #


def create_log(session: Session, user_id: int, data: LogCreate, now: datetime) -> DrillLog:
    drill = session.get(Drill, data.drill_id)
    if drill is None:
        raise NotFoundError("drill", data.drill_id)
    log = DrillLog(
        user_id=user_id,
        drill_id=data.drill_id,
        logged_at=now,
        outcome=data.outcome,
        difficulty=data.difficulty,
        note=data.note,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def create_fact_review(session: Session, user_id: int, fact_id: int, now: datetime) -> FactReview:
    fact = session.get(LessonFact, fact_id)
    if fact is None:
        raise NotFoundError("lesson_fact", fact_id)
    review = FactReview(user_id=user_id, fact_id=fact_id, reviewed_at=now)
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def grade_quiz(
    session: Session, user_id: int, drill_id: int, choice_index: int, now: datetime
) -> tuple[Drill, bool]:
    """Grade a quiz answer, log the attempt (outcome=done, correct=...), return (drill, correct)."""
    drill = session.get(Drill, drill_id)
    if drill is None:
        raise NotFoundError("drill", drill_id)
    if drill.kind != DrillKind.quiz or drill.answer_index is None:
        raise BadRequestError(f"drill {drill_id} is not a quiz")
    correct = choice_index == drill.answer_index
    session.add(
        DrillLog(
            user_id=user_id,
            drill_id=drill_id,
            logged_at=now,
            outcome=LogOutcome.done,
            difficulty=None,
            correct=correct,
        )
    )
    session.commit()
    return drill, correct


def _load_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("user", user_id)
    return user


# --------------------------------------------------------------------------- #
# Progression: sections -> levels -> gating -> XP
# --------------------------------------------------------------------------- #


def _reviewed_lesson_ids(session: Session, user_id: int) -> set[int]:
    return set(session.scalars(select(FactReview.fact_id).where(FactReview.user_id == user_id)))


def _completed_activity_split(session: Session, user_id: int) -> tuple[set[int], set[int]]:
    """(confirm activities completed, quizzes answered correctly)."""
    done_confirm = set(
        session.scalars(
            select(DrillLog.drill_id)
            .join(Drill, Drill.id == DrillLog.drill_id)
            .where(
                DrillLog.user_id == user_id,
                DrillLog.outcome == LogOutcome.done,
                Drill.kind != DrillKind.quiz,
            )
        )
    )
    correct_quiz = set(
        session.scalars(
            select(DrillLog.drill_id).where(DrillLog.user_id == user_id, DrillLog.correct.is_(True))
        )
    )
    return done_confirm, correct_quiz


def compute_xp(reviewed: set[int], done_confirm: set[int], correct_quiz: set[int]) -> int:
    return len(reviewed) * XP_LESSON + len(done_confirm) * XP_ACTIVITY + len(correct_quiz) * XP_QUIZ


def _level_states(domain: Domain, reviewed: set[int], completed: set[int]) -> list[dict]:
    """Per-level progress + gating for one domain. A level unlocks once the previous
    level is complete; an empty level is 'coming soon' and blocks what follows."""
    states: list[dict] = []
    prev_complete = True
    for level in LEVEL_ORDER:
        lvl = level.value
        lessons = [f for f in domain.facts if str(f.level) == lvl]
        drills = [d for d in domain.drills if str(d.level) == lvl]
        total = len(lessons) + len(drills)
        done = sum(1 for f in lessons if f.id in reviewed) + sum(
            1 for d in drills if d.id in completed
        )
        available = total > 0
        complete = available and done == total
        states.append(
            {
                "level": lvl,
                "total": total,
                "done": done,
                "ratio": (done / total) if total else 0.0,
                "available": available,
                "complete": complete,
                "unlocked": available and prev_complete,
            }
        )
        prev_complete = complete
    return states


def sections_overview(session: Session, user_id: int, today: date) -> dict:
    """Homepage data: global XP + streak and a per-section completion summary."""
    user = _load_user(session, user_id)
    domains = list(
        session.scalars(
            select(Domain)
            .order_by(Domain.default_priority)
            .options(selectinload(Domain.facts), selectinload(Domain.drills))
        )
    )
    reviewed = _reviewed_lesson_ids(session, user_id)
    done_confirm, correct_quiz = _completed_activity_split(session, user_id)
    completed = done_confirm | correct_quiz

    all_done_dates = [
        lg.date()
        for lg in session.scalars(
            select(DrillLog.logged_at).where(
                DrillLog.user_id == user_id, DrillLog.outcome == LogOutcome.done
            )
        )
    ]
    streak = metrics.domain_streak(all_done_dates, user.active_days_per_week, today)

    sections = []
    for d in domains:
        levels = _level_states(d, reviewed, completed)
        total = sum(lv["total"] for lv in levels)
        done = sum(lv["done"] for lv in levels)
        sections.append(
            {
                "domain": d,
                "levels": levels,
                "total": total,
                "done": done,
                "ratio": (done / total) if total else 0.0,
            }
        )

    return {
        "xp": compute_xp(reviewed, done_confirm, correct_quiz),
        "streak": streak,
        "sections": sections,
    }


def header_stats(session: Session, user_id: int, today: date) -> dict:
    """Lightweight XP + streak for the top bar on every page."""
    user = _load_user(session, user_id)
    reviewed = _reviewed_lesson_ids(session, user_id)
    done_confirm, correct_quiz = _completed_activity_split(session, user_id)
    dates = [
        lg.date()
        for lg in session.scalars(
            select(DrillLog.logged_at).where(
                DrillLog.user_id == user_id, DrillLog.outcome == LogOutcome.done
            )
        )
    ]
    return {
        "xp": compute_xp(reviewed, done_confirm, correct_quiz),
        "streak": metrics.domain_streak(dates, user.active_days_per_week, today),
    }


def section_detail(session: Session, user_id: int, slug: str) -> dict:
    """Section page: levels with lock state + per-level progress."""
    domain = get_domain_detail(session, slug)
    reviewed = _reviewed_lesson_ids(session, user_id)
    done_confirm, correct_quiz = _completed_activity_split(session, user_id)
    levels = _level_states(domain, reviewed, done_confirm | correct_quiz)
    return {"domain": domain, "levels": levels}


def level_detail(session: Session, user_id: int, slug: str, level: str) -> dict:
    """Level page: ordered lessons + activities with completion flags. Enforces gating."""
    if level not in {lvl.value for lvl in LEVEL_ORDER}:
        raise NotFoundError("level", level)
    domain = get_domain_detail(session, slug)
    reviewed = _reviewed_lesson_ids(session, user_id)
    done_confirm, correct_quiz = _completed_activity_split(session, user_id)
    completed = done_confirm | correct_quiz

    states = _level_states(domain, reviewed, completed)
    state = next(s for s in states if s["level"] == level)
    if not state["available"]:
        raise BadRequestError(f"level '{level}' has no content yet")
    if not state["unlocked"]:
        raise BadRequestError(f"level '{level}' is locked; finish the previous level first")

    lessons = sorted((f for f in domain.facts if str(f.level) == level), key=lambda f: f.order)
    drills = sorted((d for d in domain.drills if str(d.level) == level), key=lambda d: d.order)
    lesson_views = [{"lesson": f, "reviewed": f.id in reviewed} for f in lessons]
    drill_views = [{"drill": d, "done": d.id in completed} for d in drills]

    # the next level, if this one is complete and the next is available
    idx = [lvl.value for lvl in LEVEL_ORDER].index(level)
    next_state = states[idx + 1] if idx + 1 < len(states) else None

    return {
        "domain": domain,
        "level": level,
        "state": state,
        "lessons": lesson_views,
        "drills": drill_views,
        "next": next_state,
    }
