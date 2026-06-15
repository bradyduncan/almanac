"""Self-guided daily-queue selection policy.

Pure functions only: every input (the user's logs, their prefs, config, and *today's
date*) is passed in. No DB access and no wall-clock reads in this module — that is what
makes it deterministic and the most-tested code in the repo.

Pipeline (see CLAUDE.md "Scheduler / selection policy"):

  1. Cold start: if the user has fewer than COLD_START_MIN_LOGS logs, ignore scoring and
     return drills in source rotation order (domain.default_priority, then drill order),
     packed to the minute budget. Seeds variety before there is history to score against.
  2. Otherwise score every *eligible* drill as a transparent weighted sum of named
     factors (neglect, priority, difficulty, novelty). Spacing is a hard filter, not a
     weight: a drill done inside the cooldown window is ineligible.
  3. Minimum-breadth floor: any domain untouched longer than BREADTH_FLOOR_DAYS gets its
     top-scored drill force-included, regardless of priority weight. Non-negotiable.
  4. Greedy pack by score against daily_minutes; always return at least one drill.

Scoring weights and thresholds are module constants on purpose — tune them here, do not
bury them in the formula.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# --------------------------------------------------------------------------- #
# Tunable policy constants
# --------------------------------------------------------------------------- #

# Relative weights of the transparent scoring factors (each factor is normalized to ~0..1).
W_NEGLECT = 1.0
W_PRIORITY = 1.0
W_DIFFICULTY = 0.6
W_NOVELTY = 0.4

# Days since a drill's last completion below which it is suppressed (no same-drill repeat).
SPACING_COOLDOWN_DAYS = 2

# A domain untouched for longer than this is force-included by the breadth floor.
BREADTH_FLOOR_DAYS = 10

# Neglect saturates here: a domain untouched this long scores the same as one untouched
# much longer, so a single ancient domain can't dominate the ranking.
NEGLECT_CAP_DAYS = 14

# Below this many total logs we are in cold start and fall back to rotation order.
COLD_START_MIN_LOGS = 5

# Difficulty (1 easy .. 3 hard) maps to a 0..1 resurfacing factor. Never-rated drills sit
# in the middle so they neither boost nor decay until the user rates them.
_DIFFICULTY_FACTOR = {1: 0.0, 2: 0.5, 3: 1.0}
_DIFFICULTY_NEUTRAL = 0.5


# --------------------------------------------------------------------------- #
# Plain inputs (no ORM dependency — the service layer maps rows into these)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DrillInfo:
    id: int
    domain_id: int
    est_minutes: int


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
    difficulty: int | None  # 1..3, only on "done"


@dataclass(frozen=True)
class Config:
    daily_minutes: int = 15
    active_days_per_week: int = 7


@dataclass(frozen=True)
class ScoredDrill:
    drill: DrillInfo
    score: float
    forced: bool = False  # included by the breadth floor regardless of score
    factors: dict[str, float] = field(default_factory=dict)  # transparency / debugging


# --------------------------------------------------------------------------- #
# Factor helpers
# --------------------------------------------------------------------------- #


def _priority_weight(domain: DomainInfo, pref: PrefInfo | None) -> float:
    """User weight if a pref row exists, else derived from default_priority.

    default_priority 1 (highest) -> ~1.0, larger numbers decay toward 0. An absent pref
    row means "use the seed priority", per the data model.
    """
    if pref is not None:
        return max(pref.weight, 0.0)
    return max(0.0, (11 - domain.default_priority) / 10)


def _neglect_factor(days_since_domain: float | None) -> float:
    """0..1; never-touched domain (None) saturates at 1.0."""
    if days_since_domain is None:
        return 1.0
    return min(days_since_domain, NEGLECT_CAP_DAYS) / NEGLECT_CAP_DAYS


def _difficulty_factor(last_difficulty: int | None) -> float:
    if last_difficulty is None:
        return _DIFFICULTY_NEUTRAL
    return _DIFFICULTY_FACTOR.get(last_difficulty, _DIFFICULTY_NEUTRAL)


# --------------------------------------------------------------------------- #
# Derived views over the log
# --------------------------------------------------------------------------- #


def _days_between(later: date, earlier: date) -> int:
    return (later - earlier).days


def _last_domain_activity(logs: list[LogEntry]) -> dict[int, date]:
    """Most recent log date per domain (any outcome counts as activity)."""
    out: dict[int, date] = {}
    for log in logs:
        d = log.logged_at.date()
        if log.domain_id not in out or d > out[log.domain_id]:
            out[log.domain_id] = d
    return out


def _last_drill_done(logs: list[LogEntry]) -> dict[int, tuple[date, int | None]]:
    """Most recent 'done' completion per drill: (date, difficulty)."""
    out: dict[int, tuple[date, int | None]] = {}
    for log in logs:
        if log.outcome != "done":
            continue
        d = log.logged_at.date()
        if log.drill_id not in out or d > out[log.drill_id][0]:
            out[log.drill_id] = (d, log.difficulty)
    return out


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #


def _pack(ranked: list[ScoredDrill], budget_minutes: int) -> list[ScoredDrill]:
    """Greedy pack by score order. Forced drills always go in. Never returns empty if
    any drills exist (the single highest-ranked drill is admitted even if over budget)."""
    chosen: list[ScoredDrill] = []
    spent = 0
    for sd in ranked:
        if sd.forced:
            chosen.append(sd)
            spent += sd.drill.est_minutes
            continue
        if spent + sd.drill.est_minutes <= budget_minutes:
            chosen.append(sd)
            spent += sd.drill.est_minutes
    if not chosen and ranked:
        chosen.append(ranked[0])
    return chosen


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def _cold_start_queue(
    drills: list[DrillInfo], domains: dict[int, DomainInfo], config: Config
) -> list[ScoredDrill]:
    priority = {d.id: d.default_priority for d in domains.values()}
    ordered = sorted(
        drills,
        key=lambda dr: (priority.get(dr.domain_id, 10**6), dr.id),
    )
    ranked = [ScoredDrill(drill=dr, score=0.0, factors={"cold_start": 1.0}) for dr in ordered]
    return _pack(ranked, config.daily_minutes)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def build_queue(
    *,
    drills: list[DrillInfo],
    domains: list[DomainInfo],
    prefs: list[PrefInfo],
    logs: list[LogEntry],
    config: Config,
    today: date,
) -> list[ScoredDrill]:
    """Return today's ranked, budget-packed drill queue for one user.

    Pure: identical inputs always yield an identical queue.
    """
    domain_by_id = {d.id: d for d in domains}
    pref_by_domain = {p.domain_id: p for p in prefs}

    # Inactive domains (explicit pref active=False) are excluded entirely.
    inactive = {p.domain_id for p in prefs if not p.active}
    eligible_drills = [dr for dr in drills if dr.domain_id not in inactive]

    if len(logs) < COLD_START_MIN_LOGS:
        return _cold_start_queue(eligible_drills, domain_by_id, config)

    last_domain = _last_domain_activity(logs)
    last_done = _last_drill_done(logs)

    ranked: list[ScoredDrill] = []
    for dr in eligible_drills:
        # Spacing: hard-suppress a drill completed inside the cooldown window.
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

        score = (
            W_NEGLECT * neglect
            + W_PRIORITY * priority
            + W_DIFFICULTY * difficulty
            + W_NOVELTY * novelty
        )
        ranked.append(
            ScoredDrill(
                drill=dr,
                score=score,
                factors={
                    "neglect": neglect,
                    "priority": priority,
                    "difficulty": difficulty,
                    "novelty": novelty,
                },
            )
        )

    ranked.sort(key=lambda sd: (-sd.score, sd.drill.id))

    # Minimum-breadth floor: force the top drill of any starved domain.
    _apply_breadth_floor(ranked, eligible_drills, domain_by_id, last_domain, today)

    # Re-sort so forced drills surface first, then by score.
    ranked.sort(key=lambda sd: (not sd.forced, -sd.score, sd.drill.id))
    return _pack(ranked, config.daily_minutes)


def _apply_breadth_floor(
    ranked: list[ScoredDrill],
    eligible_drills: list[DrillInfo],
    domain_by_id: dict[int, DomainInfo],
    last_domain: dict[int, date],
    today: date,
) -> None:
    """Mark (or add) one drill per starved domain as forced, in place on `ranked`."""
    starved: set[int] = set()
    for domain_id in domain_by_id:
        last_act = last_domain.get(domain_id)
        days_since = _days_between(today, last_act) if last_act is not None else None
        if days_since is None or days_since > BREADTH_FLOOR_DAYS:
            starved.add(domain_id)

    by_id = {sd.drill.id: sd for sd in ranked}
    for domain_id in starved:
        # Prefer the already-ranked (not spacing-suppressed) drills for this domain.
        candidates = [sd for sd in ranked if sd.drill.domain_id == domain_id]
        if candidates:
            candidates[0].factors["forced"] = 1.0
            # dataclass is frozen; replace the entry with a forced copy.
            idx = ranked.index(candidates[0])
            ranked[idx] = _force(candidates[0])
            continue
        # Every drill in this domain was spacing-suppressed; pull one back in anyway so
        # the floor is never silently skipped.
        fallback = next((dr for dr in eligible_drills if dr.domain_id == domain_id), None)
        if fallback is not None and fallback.id not in by_id:
            ranked.append(
                ScoredDrill(drill=fallback, score=0.0, forced=True, factors={"forced": 1.0})
            )


def _force(sd: ScoredDrill) -> ScoredDrill:
    return ScoredDrill(drill=sd.drill, score=sd.score, forced=True, factors={**sd.factors})
