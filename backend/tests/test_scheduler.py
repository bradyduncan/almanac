from __future__ import annotations

from datetime import date, datetime, timedelta

from app import scheduler as sch
from app.scheduler import (
    BREADTH_FLOOR_DAYS,
    COLD_START_MIN_LOGS,
    SPACING_COOLDOWN_DAYS,
    Config,
    DomainInfo,
    DrillInfo,
    LogEntry,
    PrefInfo,
    build_queue,
)

TODAY = date(2026, 6, 14)


def _log(drill_id: int, domain_id: int, days_ago: int, outcome="done", difficulty=2) -> LogEntry:
    ts = datetime(2026, 6, 14, 9, 0) - timedelta(days=days_ago)
    return LogEntry(
        drill_id=drill_id,
        domain_id=domain_id,
        logged_at=ts,
        outcome=outcome,
        difficulty=difficulty if outcome == "done" else None,
    )


def _ids(queue) -> list[int]:
    return [sd.drill.id for sd in queue]


# Catalog: 2 domains, 2 drills each, every drill 5 minutes.
DOMAINS = [DomainInfo(id=1, default_priority=1), DomainInfo(id=2, default_priority=2)]
DRILLS = [
    DrillInfo(id=11, domain_id=1, est_minutes=5),
    DrillInfo(id=12, domain_id=1, est_minutes=5),
    DrillInfo(id=21, domain_id=2, est_minutes=5),
    DrillInfo(id=22, domain_id=2, est_minutes=5),
]


def _filler_logs(n: int) -> list[LogEntry]:
    """n recent logs that keep both domains 'active' (within breadth floor) and out of
    cold start, without suppressing any specific drill via spacing."""
    out = []
    for i in range(n):
        # alternate domains, all SPACING_COOLDOWN_DAYS+ days old so nothing is suppressed
        drill = 11 if i % 2 == 0 else 21
        domain = 1 if i % 2 == 0 else 2
        out.append(_log(drill, domain, days_ago=SPACING_COOLDOWN_DAYS + 1))
    return out


def _build(logs, prefs=None, daily_minutes=100):
    return build_queue(
        drills=DRILLS,
        domains=DOMAINS,
        prefs=prefs or [],
        logs=logs,
        config=Config(daily_minutes=daily_minutes),
        today=TODAY,
    )


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def test_cold_start_uses_rotation_order():
    queue = _build(logs=[])  # zero logs -> cold start
    # domain 1 (priority 1) drills come before domain 2 (priority 2)
    assert _ids(queue) == [11, 12, 21, 22]
    assert all("cold_start" in sd.factors for sd in queue)


def test_cold_start_boundary():
    # Exactly COLD_START_MIN_LOGS logs leaves cold start.
    queue = _build(logs=_filler_logs(COLD_START_MIN_LOGS))
    assert all("cold_start" not in sd.factors for sd in queue)


# --------------------------------------------------------------------------- #
# Spacing (hard filter)
# --------------------------------------------------------------------------- #


def test_spacing_suppresses_recent_drill():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    logs.append(_log(12, 1, days_ago=SPACING_COOLDOWN_DAYS - 1))  # done inside cooldown
    queue = _build(logs)
    assert 12 not in _ids(queue)


def test_spacing_allows_drill_past_cooldown():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    logs.append(_log(12, 1, days_ago=SPACING_COOLDOWN_DAYS))  # exactly at edge -> eligible
    queue = _build(logs)
    assert 12 in _ids(queue)


# --------------------------------------------------------------------------- #
# Scoring factors
# --------------------------------------------------------------------------- #


def test_neglect_ranks_more_neglected_domain_higher():
    # Domain 2 last touched long ago, domain 1 touched recently. Equal priority weights.
    logs = _filler_logs(COLD_START_MIN_LOGS)
    logs.append(_log(11, 1, days_ago=1))  # domain 1 fresh
    logs.append(_log(21, 2, days_ago=8))  # domain 2 neglected (but < breadth floor)
    prefs = [PrefInfo(1, weight=1.0, active=True), PrefInfo(2, weight=1.0, active=True)]
    queue = _build(logs, prefs=prefs)
    d1 = next(sd for sd in queue if sd.drill.domain_id == 1)
    d2 = next(sd for sd in queue if sd.drill.domain_id == 2)
    assert d2.factors["neglect"] > d1.factors["neglect"]
    assert d2.score > d1.score


def test_priority_weight_raises_score():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    # same neglect for both domains
    logs.append(_log(11, 1, days_ago=3))
    logs.append(_log(21, 2, days_ago=3))
    prefs = [PrefInfo(1, weight=2.0, active=True), PrefInfo(2, weight=0.1, active=True)]
    queue = _build(logs, prefs=prefs)
    d1 = next(sd for sd in queue if sd.drill.domain_id == 1)
    d2 = next(sd for sd in queue if sd.drill.domain_id == 2)
    assert d1.factors["priority"] > d2.factors["priority"]
    assert d1.score > d2.score


def test_difficulty_hard_outranks_easy_same_drill_conditions():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    # drill 11 last rated hard (3), drill 21 last rated easy (1). Use the cooldown edge so
    # these are the most-recent completions (more recent than filler) yet still eligible.
    logs.append(_log(11, 1, days_ago=SPACING_COOLDOWN_DAYS, difficulty=3))
    logs.append(_log(21, 2, days_ago=SPACING_COOLDOWN_DAYS, difficulty=1))
    prefs = [PrefInfo(1, weight=1.0, active=True), PrefInfo(2, weight=1.0, active=True)]
    queue = _build(logs, prefs=prefs)
    hard = next(sd for sd in queue if sd.drill.id == 11)
    easy = next(sd for sd in queue if sd.drill.id == 21)
    assert hard.factors["difficulty"] > easy.factors["difficulty"]


def test_novelty_bonus_for_never_attempted():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    logs.append(_log(11, 1, days_ago=3))  # 11 attempted, 12 never
    queue = _build(logs)
    attempted = next(sd for sd in queue if sd.drill.id == 11)
    never = next(sd for sd in queue if sd.drill.id == 12)
    assert never.factors["novelty"] == 1.0
    assert attempted.factors["novelty"] == 0.0


# --------------------------------------------------------------------------- #
# Minimum-breadth floor
# --------------------------------------------------------------------------- #


def test_breadth_floor_forces_starved_domain():
    # Domain 2 starved (untouched > floor), domain 1 active and high priority.
    logs = _filler_logs(COLD_START_MIN_LOGS)  # filler touches domains 1 and 2 recently...
    # ...so override: make ALL recent activity domain 1 only, domain 2 ancient.
    logs = [_log(11, 1, days_ago=SPACING_COOLDOWN_DAYS + 1) for _ in range(COLD_START_MIN_LOGS)]
    logs.append(_log(21, 2, days_ago=BREADTH_FLOOR_DAYS + 5))
    prefs = [PrefInfo(1, weight=2.0, active=True), PrefInfo(2, weight=0.01, active=True)]
    # Tiny budget so without forcing, domain 2 (low priority) would be packed out.
    queue = _build(logs, prefs=prefs, daily_minutes=5)
    forced = [sd for sd in queue if sd.forced]
    assert any(sd.drill.domain_id == 2 for sd in forced)
    assert 21 in _ids(queue) or 22 in _ids(queue)


# --------------------------------------------------------------------------- #
# Packing / never empty
# --------------------------------------------------------------------------- #


def test_pack_respects_budget():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    queue = _build(logs, daily_minutes=10)  # 2 drills of 5 min fit
    non_forced = [sd for sd in queue if not sd.forced]
    assert sum(sd.drill.est_minutes for sd in non_forced) <= 10


def test_never_empty_even_when_single_drill_over_budget():
    big = [DrillInfo(id=99, domain_id=1, est_minutes=60)]
    queue = build_queue(
        drills=big,
        domains=[DomainInfo(id=1, default_priority=1)],
        prefs=[],
        logs=[],
        config=Config(daily_minutes=15),
        today=TODAY,
    )
    assert len(queue) == 1


def test_inactive_domain_excluded():
    logs = _filler_logs(COLD_START_MIN_LOGS)
    prefs = [PrefInfo(2, weight=1.0, active=False)]
    queue = _build(logs, prefs=prefs)
    assert all(sd.drill.domain_id != 2 for sd in queue)


def test_determinism():
    logs = _filler_logs(COLD_START_MIN_LOGS) + [_log(11, 1, days_ago=3)]
    q1 = _build(logs)
    q2 = _build(logs)
    assert _ids(q1) == _ids(q2)


def test_no_datetime_now_import():
    # Guard the purity contract: scheduler must not reach for the wall clock.
    import inspect

    src = inspect.getsource(sch)
    assert "datetime.now" not in src
    assert "date.today" not in src
