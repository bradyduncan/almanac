"""Self-guided daily-queue selection policy.

Pure functions only: every input (the user's logs, lesson reviews, prefs, config, and
*today's date*) is passed in. No DB access and no wall-clock reads in this module — that
is what makes it deterministic and the most-tested code in the repo.

The queue mixes two item kinds:
  - lessons    — teaching units; eligible until the user has reviewed them.
  - activities — drills (including quizzes); scored and spacing-suppressed as before.

Pipeline (see CLAUDE.md "Scheduler / selection policy"):

  1. Cold start: with little history, return rotation order (lessons first by domain
     priority, then activities) trimmed to the item-count goal.
  2. Otherwise score eligible items. Lessons score on neglect + priority; activities add
     difficulty and novelty. Spacing is a hard filter on activities.
  3. Minimum-breadth floor: any domain untouched longer than BREADTH_FLOOR_DAYS gets its
     top item force-included. Non-negotiable.
  4. Order lessons before activities, then pack to daily_items by count. Always return
     at least one item.

Scoring weights and thresholds are module constants on purpose — tune them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# --------------------------------------------------------------------------- #
# Tunable policy constants
# --------------------------------------------------------------------------- #

W_NEGLECT = 1.0
W_PRIORITY = 1.0
W_DIFFICULTY = 0.6
W_NOVELTY = 0.4

# Days since an activity's last completion below which it is suppressed.
SPACING_COOLDOWN_DAYS = 2

# A domain untouched for longer than this is force-included by the breadth floor.
BREADTH_FLOOR_DAYS = 10

# Neglect saturates here so one ancient domain can't dominate the ranking.
NEGLECT_CAP_DAYS = 14

# Below this much engagement (activity logs + lesson reviews) we are in cold start.
COLD_START_MIN_EVENTS = 5

_DIFFICULTY_FACTOR = {1: 0.0, 2: 0.5, 3: 1.0}
_DIFFICULTY_NEUTRAL = 0.5

LESSON = "lesson"
ACTIVITY = "activity"


# --------------------------------------------------------------------------- #
# Plain inputs (no ORM dependency — the service layer maps rows into these)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DrillInfo:
    id: int
    domain_id: int
    est_minutes: int


@dataclass(frozen=True)
class LessonInfo:
    id: int
    domain_id: int
    order: int


@dataclass(frozen=True)
class DomainInfo:
    id: int
    default_priority: int  # 1..N, lower = higher priority


@dataclass(frozen=True)
class PrefInfo:
    domain_id: int
    weight: float
    active: bool


@dataclass(frozen=True)
class LogEntry:
    drill_id: int
    domain_id: int
    logged_at: datetime
    outcome: str  # "done" | "skipped" | "snoozed"
    difficulty: int | None


@dataclass(frozen=True)
class Config:
    daily_items: int = 5
    active_days_per_week: int = 7


@dataclass(frozen=True)
class ScoredItem:
    kind: str  # LESSON | ACTIVITY
    item_id: int
    domain_id: int
    score: float
    est_minutes: int = 0
    forced: bool = False
    factors: dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Factor helpers
# --------------------------------------------------------------------------- #


def _priority_weight(domain: DomainInfo, pref: PrefInfo | None) -> float:
    if pref is not None:
        return max(pref.weight, 0.0)
    return max(0.0, (11 - domain.default_priority) / 10)


def _neglect_factor(days_since_domain: float | None) -> float:
    if days_since_domain is None:
        return 1.0
    return min(days_since_domain, NEGLECT_CAP_DAYS) / NEGLECT_CAP_DAYS


def _difficulty_factor(last_difficulty: int | None) -> float:
    if last_difficulty is None:
        return _DIFFICULTY_NEUTRAL
    return _DIFFICULTY_FACTOR.get(last_difficulty, _DIFFICULTY_NEUTRAL)


def _days_between(later: date, earlier: date) -> int:
    return (later - earlier).days


def _last_domain_activity(logs: list[LogEntry]) -> dict[int, date]:
    out: dict[int, date] = {}
    for log in logs:
        d = log.logged_at.date()
        if log.domain_id not in out or d > out[log.domain_id]:
            out[log.domain_id] = d
    return out


def _last_drill_done(logs: list[LogEntry]) -> dict[int, tuple[date, int | None]]:
    out: dict[int, tuple[date, int | None]] = {}
    for log in logs:
        if log.outcome != "done":
            continue
        d = log.logged_at.date()
        if log.drill_id not in out or d > out[log.drill_id][0]:
            out[log.drill_id] = (d, log.difficulty)
    return out


# --------------------------------------------------------------------------- #
# Packing (by item count)
# --------------------------------------------------------------------------- #


def _pack(ordered: list[ScoredItem], goal_count: int) -> list[ScoredItem]:
    """Take forced items plus the top-ranked items up to the count goal. Never empty
    when any item exists."""
    chosen: list[ScoredItem] = []
    for item in ordered:
        if item.forced:
            chosen.append(item)
    for item in ordered:
        if item.forced:
            continue
        if len(chosen) >= goal_count:
            break
        chosen.append(item)
    if not chosen and ordered:
        chosen.append(ordered[0])
    # Preserve lessons-before-activities, forced-first ordering for display.
    chosen.sort(key=lambda it: (not it.forced, it.kind != LESSON, -it.score, it.item_id))
    return chosen


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def _cold_start_queue(
    lessons: list[LessonInfo],
    drills: list[DrillInfo],
    domain_by_id: dict[int, DomainInfo],
    config: Config,
) -> list[ScoredItem]:
    prio = {d.id: d.default_priority for d in domain_by_id.values()}
    items: list[ScoredItem] = []
    for lsn in sorted(lessons, key=lambda x: (prio.get(x.domain_id, 10**6), x.order)):
        items.append(ScoredItem(LESSON, lsn.id, lsn.domain_id, 0.0, factors={"cold_start": 1.0}))
    for dr in sorted(drills, key=lambda x: (prio.get(x.domain_id, 10**6), x.id)):
        items.append(
            ScoredItem(
                ACTIVITY, dr.id, dr.domain_id, 0.0, dr.est_minutes, factors={"cold_start": 1.0}
            )
        )
    return _pack(items, config.daily_items)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def build_queue(
    *,
    lessons: list[LessonInfo],
    drills: list[DrillInfo],
    domains: list[DomainInfo],
    prefs: list[PrefInfo],
    logs: list[LogEntry],
    reviewed_lesson_ids: set[int],
    config: Config,
    today: date,
) -> list[ScoredItem]:
    """Return today's ranked, count-packed item queue (lessons + activities) for one user."""
    domain_by_id = {d.id: d for d in domains}
    pref_by_domain = {p.domain_id: p for p in prefs}
    inactive = {p.domain_id for p in prefs if not p.active}

    eligible_lessons = [
        x for x in lessons if x.domain_id not in inactive and x.id not in reviewed_lesson_ids
    ]
    eligible_drills = [x for x in drills if x.domain_id not in inactive]

    if len(logs) + len(reviewed_lesson_ids) < COLD_START_MIN_EVENTS:
        return _cold_start_queue(eligible_lessons, eligible_drills, domain_by_id, config)

    last_domain = _last_domain_activity(logs)
    last_done = _last_drill_done(logs)

    items: list[ScoredItem] = []

    # Lessons: neglect + priority. Unreviewed by construction.
    for lsn in eligible_lessons:
        domain = domain_by_id.get(lsn.domain_id)
        if domain is None:
            continue
        last_act = last_domain.get(lsn.domain_id)
        days_since = _days_between(today, last_act) if last_act is not None else None
        neglect = _neglect_factor(days_since)
        priority = _priority_weight(domain, pref_by_domain.get(lsn.domain_id))
        items.append(
            ScoredItem(
                kind=LESSON,
                item_id=lsn.id,
                domain_id=lsn.domain_id,
                score=W_NEGLECT * neglect + W_PRIORITY * priority,
                factors={"neglect": neglect, "priority": priority, "lesson": 1.0},
            )
        )

    # Activities: full scoring + spacing hard-filter.
    for dr in eligible_drills:
        done = last_done.get(dr.id)
        if done is not None and _days_between(today, done[0]) < SPACING_COOLDOWN_DAYS:
            continue
        domain = domain_by_id.get(dr.domain_id)
        if domain is None:
            continue
        last_act = last_domain.get(dr.domain_id)
        days_since = _days_between(today, last_act) if last_act is not None else None
        neglect = _neglect_factor(days_since)
        priority = _priority_weight(domain, pref_by_domain.get(dr.domain_id))
        difficulty = _difficulty_factor(done[1] if done is not None else None)
        novelty = 1.0 if dr.id not in last_done else 0.0
        items.append(
            ScoredItem(
                kind=ACTIVITY,
                item_id=dr.id,
                domain_id=dr.domain_id,
                est_minutes=dr.est_minutes,
                score=(
                    W_NEGLECT * neglect
                    + W_PRIORITY * priority
                    + W_DIFFICULTY * difficulty
                    + W_NOVELTY * novelty
                ),
                factors={
                    "neglect": neglect,
                    "priority": priority,
                    "difficulty": difficulty,
                    "novelty": novelty,
                },
            )
        )

    _apply_breadth_floor(items, eligible_lessons, eligible_drills, domain_by_id, last_domain, today)

    # Never empty: if everything was spacing-suppressed and nothing starved, surface the
    # activity whose last completion is oldest.
    if not items and eligible_drills:
        oldest = min(
            eligible_drills,
            key=lambda dr: last_done[dr.id][0] if dr.id in last_done else date.min,
        )
        items.append(
            ScoredItem(
                ACTIVITY,
                oldest.id,
                oldest.domain_id,
                0.0,
                oldest.est_minutes,
                factors={"spacing_override": 1.0},
            )
        )

    return _pack(items, config.daily_items)


def _apply_breadth_floor(
    items: list[ScoredItem],
    eligible_lessons: list[LessonInfo],
    eligible_drills: list[DrillInfo],
    domain_by_id: dict[int, DomainInfo],
    last_domain: dict[int, date],
    today: date,
) -> None:
    """Force one item per starved domain (untouched > floor), in place on `items`."""
    starved: set[int] = set()
    for domain_id in domain_by_id:
        last_act = last_domain.get(domain_id)
        days_since = _days_between(today, last_act) if last_act is not None else None
        if days_since is None or days_since > BREADTH_FLOOR_DAYS:
            starved.add(domain_id)

    present_ids = {(it.kind, it.item_id) for it in items}
    for domain_id in starved:
        candidates = [it for it in items if it.domain_id == domain_id]
        if candidates:
            # Prefer a lesson, else the highest-scored item already ranked.
            candidates.sort(key=lambda it: (it.kind != LESSON, -it.score, it.item_id))
            idx = items.index(candidates[0])
            items[idx] = _force(candidates[0])
            continue
        # Everything for this domain was filtered out; pull one item back in.
        lesson = next((x for x in eligible_lessons if x.domain_id == domain_id), None)
        if lesson is not None and (LESSON, lesson.id) not in present_ids:
            items.append(
                ScoredItem(LESSON, lesson.id, domain_id, 0.0, forced=True, factors={"forced": 1.0})
            )
            continue
        drill = next((x for x in eligible_drills if x.domain_id == domain_id), None)
        if drill is not None and (ACTIVITY, drill.id) not in present_ids:
            items.append(
                ScoredItem(
                    ACTIVITY,
                    drill.id,
                    domain_id,
                    0.0,
                    drill.est_minutes,
                    forced=True,
                    factors={"forced": 1.0},
                )
            )


def _force(it: ScoredItem) -> ScoredItem:
    return ScoredItem(
        kind=it.kind,
        item_id=it.item_id,
        domain_id=it.domain_id,
        score=it.score,
        est_minutes=it.est_minutes,
        forced=True,
        factors={**it.factors, "forced": 1.0},
    )
