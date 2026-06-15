"""Thin service layer between routes and the DB.

Routes hold no business logic; they call these functions. Functions take a Session
(they never open their own). Scheduler scoring stays in scheduler.py; this module only
loads rows, maps them into the scheduler's plain inputs, and persists writes.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import metrics
from app import scheduler as sch
from app.exceptions import BadRequestError, NotFoundError
from app.models import (
    Domain,
    Drill,
    DrillKind,
    DrillLog,
    FactReview,
    LessonFact,
    LogOutcome,
    User,
    UserDomainPref,
)
from app.schemas import LogCreate

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


# --------------------------------------------------------------------------- #
# Progress: streaks + coverage (per domain)
# --------------------------------------------------------------------------- #


def _done_dates_by_domain(session: Session, user_id: int) -> dict[int, list[date]]:
    """Dates with >=1 completed drill, grouped by domain."""
    rows = session.execute(
        select(Drill.domain_id, DrillLog.logged_at)
        .join(Drill, Drill.id == DrillLog.drill_id)
        .where(DrillLog.user_id == user_id, DrillLog.outcome == LogOutcome.done)
    ).all()
    out: dict[int, list[date]] = {}
    for domain_id, logged_at in rows:
        out.setdefault(domain_id, []).append(logged_at.date())
    return out


def _done_drill_ids_by_domain(session: Session, user_id: int) -> dict[int, set[int]]:
    rows = session.execute(
        select(Drill.domain_id, DrillLog.drill_id)
        .join(Drill, Drill.id == DrillLog.drill_id)
        .where(DrillLog.user_id == user_id, DrillLog.outcome == LogOutcome.done)
    ).all()
    out: dict[int, set[int]] = {}
    for domain_id, drill_id in rows:
        out.setdefault(domain_id, set()).add(drill_id)
    return out


def _reviewed_fact_ids_by_domain(session: Session, user_id: int) -> dict[int, set[int]]:
    rows = session.execute(
        select(LessonFact.domain_id, FactReview.fact_id)
        .join(LessonFact, LessonFact.id == FactReview.fact_id)
        .where(FactReview.user_id == user_id)
    ).all()
    out: dict[int, set[int]] = {}
    for domain_id, fact_id in rows:
        out.setdefault(domain_id, set()).add(fact_id)
    return out


def domain_progress(session: Session, user_id: int, today: date) -> list[dict]:
    """Per-domain streak + coverage for every domain, in priority order."""
    user = _load_user(session, user_id)
    domains = list(session.scalars(select(Domain).order_by(Domain.default_priority)))

    fact_totals: dict[int, int] = dict(
        session.execute(
            select(LessonFact.domain_id, func.count()).group_by(LessonFact.domain_id)
        ).all()
    )
    drill_totals: dict[int, int] = dict(
        session.execute(select(Drill.domain_id, func.count()).group_by(Drill.domain_id)).all()
    )

    done_dates = _done_dates_by_domain(session, user_id)
    done_ids = _done_drill_ids_by_domain(session, user_id)
    reviewed_ids = _reviewed_fact_ids_by_domain(session, user_id)

    result = []
    for d in domains:
        streak = metrics.domain_streak(done_dates.get(d.id, []), user.active_days_per_week, today)
        coverage = metrics.domain_coverage(
            facts_total=fact_totals.get(d.id, 0),
            facts_reviewed=len(reviewed_ids.get(d.id, set())),
            drills_total=drill_totals.get(d.id, 0),
            drills_done=len(done_ids.get(d.id, set())),
        )
        result.append({"domain": d, "streak": streak, "coverage": coverage})
    return result


def domain_detail_progress(session: Session, user_id: int, slug: str) -> dict:
    """Domain with its facts (reviewed flag) and drills (done flag) for the detail view."""
    domain = get_domain_detail(session, slug)
    reviewed = _reviewed_fact_ids_by_domain(session, user_id).get(domain.id, set())
    done = _done_drill_ids_by_domain(session, user_id).get(domain.id, set())
    facts = [{"fact": f, "reviewed": f.id in reviewed} for f in domain.facts]
    drills = [{"drill": dr, "done": dr.id in done} for dr in domain.drills]
    return {"domain": domain, "facts": facts, "drills": drills}


# --------------------------------------------------------------------------- #
# Today's queue
# --------------------------------------------------------------------------- #


def _load_user(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError("user", user_id)
    return user


def build_today(session: Session, user_id: int, today: date) -> dict:
    """Load rows, run the pure scheduler, return everything routes need to render.

    Returns: {goal, scored (list[ScoredItem]), drill_by_id, lesson_by_id,
    domain_title_by_id}.
    """
    user = _load_user(session, user_id)

    domains = list(session.scalars(select(Domain)))
    drills = list(session.scalars(select(Drill)))
    lessons = list(session.scalars(select(LessonFact)))
    prefs = list(session.scalars(select(UserDomainPref).where(UserDomainPref.user_id == user_id)))
    logs = list(session.scalars(select(DrillLog).where(DrillLog.user_id == user_id)))
    reviewed = set(session.scalars(select(FactReview.fact_id).where(FactReview.user_id == user_id)))

    drill_by_id = {d.id: d for d in drills}
    lesson_by_id = {lsn.id: lsn for lsn in lessons}
    domain_title_by_id = {d.id: d.title for d in domains}

    scored = sch.build_queue(
        lessons=[sch.LessonInfo(lsn.id, lsn.domain_id, lsn.order) for lsn in lessons],
        drills=[sch.DrillInfo(d.id, d.domain_id, d.est_minutes) for d in drills],
        domains=[sch.DomainInfo(d.id, d.default_priority) for d in domains],
        prefs=[sch.PrefInfo(p.domain_id, p.weight, p.active) for p in prefs],
        logs=[
            sch.LogEntry(
                drill_id=lg.drill_id,
                domain_id=drill_by_id[lg.drill_id].domain_id,
                logged_at=lg.logged_at,
                outcome=str(lg.outcome),
                difficulty=lg.difficulty,
            )
            for lg in logs
            if lg.drill_id in drill_by_id
        ],
        reviewed_lesson_ids=reviewed,
        config=sch.Config(
            daily_items=user.daily_items,
            active_days_per_week=user.active_days_per_week,
        ),
        today=today,
    )

    return {
        "goal": user.daily_items,
        "scored": scored,
        "drill_by_id": drill_by_id,
        "lesson_by_id": lesson_by_id,
        "domain_title_by_id": domain_title_by_id,
    }
