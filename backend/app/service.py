"""Thin service layer between routes and the DB.

Routes hold no business logic; they call these functions. Functions take a Session
(they never open their own). Scheduler scoring stays in scheduler.py; this module only
loads rows, maps them into the scheduler's plain inputs, and persists writes.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import scheduler as sch
from app.exceptions import NotFoundError
from app.models import Domain, Drill, DrillLog, LessonFact, User, UserDomainPref
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

    Returns a dict: {budget, scored, drill_by_id, domain_title_by_id}.
    """
    user = _load_user(session, user_id)

    domains = list(session.scalars(select(Domain)))
    drills = list(session.scalars(select(Drill)))
    prefs = list(session.scalars(select(UserDomainPref).where(UserDomainPref.user_id == user_id)))
    logs = list(session.scalars(select(DrillLog).where(DrillLog.user_id == user_id)))

    drill_by_id = {d.id: d for d in drills}
    domain_title_by_id = {d.id: d.title for d in domains}

    scored = sch.build_queue(
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
        config=sch.Config(
            daily_minutes=user.daily_minutes,
            active_days_per_week=user.active_days_per_week,
        ),
        today=today,
    )

    return {
        "budget": user.daily_minutes,
        "scored": scored,
        "drill_by_id": drill_by_id,
        "domain_title_by_id": domain_title_by_id,
    }
