from __future__ import annotations

from datetime import date, datetime, timedelta

from app import scheduler as sch
from app.scheduler import (
    ACTIVITY,
    BREADTH_FLOOR_DAYS,
    COLD_START_MIN_EVENTS,
    LESSON,
    SPACING_COOLDOWN_DAYS,
    Config,
    DomainInfo,
    DrillInfo,
    LessonInfo,
    LogEntry,
    PrefInfo,
    build_queue,
)

TODAY = date(2026, 6, 15)

# Catalog: 2 domains. Lessons 101/102 (d1), 201 (d2). Activities 11/12 (d1), 21/22 (d2).
DOMAINS = [DomainInfo(1, 1), DomainInfo(2, 2)]
LESSONS = [LessonInfo(101, 1, 0), LessonInfo(102, 1, 1), LessonInfo(201, 2, 0)]
DRILLS = [
    DrillInfo(11, 1, 5),
    DrillInfo(12, 1, 5),
    DrillInfo(21, 2, 5),
    DrillInfo(22, 2, 5),
]


def _log(drill_id, domain_id, days_ago, outcome="done", difficulty=2) -> LogEntry:
    ts = datetime(2026, 6, 15, 9, 0) - timedelta(days=days_ago)
    return LogEntry(drill_id, domain_id, ts, outcome, difficulty if outcome == "done" else None)


def _build(logs=None, reviewed=None, prefs=None, daily_items=20):
    return build_queue(
        lessons=LESSONS,
        drills=DRILLS,
        domains=DOMAINS,
        prefs=prefs or [],
        logs=logs or [],
        reviewed_lesson_ids=reviewed or set(),
        config=Config(daily_items=daily_items),
        today=TODAY,
    )


def _ids(queue):
    return [(it.kind, it.item_id) for it in queue]


def _filler(n):
    """n activity logs keeping both domains active and out of spacing/cold-start."""
    out = []
    for i in range(n):
        drill, dom = (11, 1) if i % 2 == 0 else (21, 2)
        out.append(_log(drill, dom, days_ago=SPACING_COOLDOWN_DAYS + 1))
    return out


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #


def test_cold_start_lessons_before_activities():
    q = _build()  # no history
    assert all("cold_start" in it.factors for it in q)
    kinds = [it.kind for it in q]
    # every lesson precedes every activity
    last_lesson = max(i for i, k in enumerate(kinds) if k == LESSON)
    first_activity = min(i for i, k in enumerate(kinds) if k == ACTIVITY)
    assert last_lesson < first_activity


def test_cold_start_count_goal_trims_to_lessons_first():
    q = _build(daily_items=2)
    assert len(q) == 2
    assert all(it.kind == LESSON for it in q)  # lessons fill the goal first


def test_leaves_cold_start_at_threshold():
    q = _build(logs=_filler(COLD_START_MIN_EVENTS))
    assert all("cold_start" not in it.factors for it in q)


# --------------------------------------------------------------------------- #
# Lessons
# --------------------------------------------------------------------------- #


def test_reviewed_lessons_drop_out():
    q = _build(logs=_filler(COLD_START_MIN_EVENTS), reviewed={101, 102, 201})
    assert all(it.kind != LESSON for it in q)


def test_unreviewed_lesson_present():
    q = _build(logs=_filler(COLD_START_MIN_EVENTS), reviewed={101, 201})
    assert (LESSON, 102) in _ids(q)


def test_lessons_ordered_before_activities_when_scored():
    q = _build(logs=_filler(COLD_START_MIN_EVENTS))
    kinds = [it.kind for it in q]
    assert kinds == sorted(kinds, key=lambda k: k != LESSON)  # all lessons first


# --------------------------------------------------------------------------- #
# Activity scoring (review all lessons so activities surface cleanly)
# --------------------------------------------------------------------------- #

ALL_REVIEWED = {101, 102, 201}


def _activity(q, drill_id):
    return next(it for it in q if it.kind == ACTIVITY and it.item_id == drill_id)


def test_spacing_suppresses_recent_activity():
    logs = _filler(COLD_START_MIN_EVENTS) + [_log(12, 1, days_ago=SPACING_COOLDOWN_DAYS - 1)]
    q = _build(logs=logs, reviewed=ALL_REVIEWED)
    assert (ACTIVITY, 12) not in _ids(q)


def test_spacing_allows_at_cooldown_edge():
    logs = _filler(COLD_START_MIN_EVENTS) + [_log(12, 1, days_ago=SPACING_COOLDOWN_DAYS)]
    q = _build(logs=logs, reviewed=ALL_REVIEWED)
    assert (ACTIVITY, 12) in _ids(q)


def test_neglect_ranks_more_neglected_domain_higher():
    # Domain 1 fresh (1d), domain 2 neglected (8d). Compare never-done drills 12 & 22 so
    # neither is spacing-suppressed; equal priority weights isolate the neglect factor.
    logs = [_log(11, 1, days_ago=1), _log(21, 2, days_ago=8)]  # +3 reviewed = 5 events
    prefs = [PrefInfo(1, 1.0, True), PrefInfo(2, 1.0, True)]
    q = _build(logs=logs, reviewed=ALL_REVIEWED, prefs=prefs)
    assert _activity(q, 22).factors["neglect"] > _activity(q, 12).factors["neglect"]


def test_priority_weight_raises_score():
    logs = _filler(COLD_START_MIN_EVENTS) + [_log(11, 1, days_ago=3), _log(21, 2, days_ago=3)]
    prefs = [PrefInfo(1, 2.0, True), PrefInfo(2, 0.1, True)]
    q = _build(logs=logs, reviewed=ALL_REVIEWED, prefs=prefs)
    assert _activity(q, 11).factors["priority"] > _activity(q, 21).factors["priority"]


def test_difficulty_hard_outranks_easy():
    logs = _filler(COLD_START_MIN_EVENTS) + [
        _log(11, 1, days_ago=SPACING_COOLDOWN_DAYS, difficulty=3),
        _log(21, 2, days_ago=SPACING_COOLDOWN_DAYS, difficulty=1),
    ]
    q = _build(logs=logs, reviewed=ALL_REVIEWED)
    assert _activity(q, 11).factors["difficulty"] > _activity(q, 21).factors["difficulty"]


def test_novelty_bonus_for_never_attempted():
    logs = _filler(COLD_START_MIN_EVENTS) + [_log(11, 1, days_ago=3)]
    q = _build(logs=logs, reviewed=ALL_REVIEWED)
    assert _activity(q, 12).factors["novelty"] == 1.0
    assert _activity(q, 11).factors["novelty"] == 0.0


# --------------------------------------------------------------------------- #
# Breadth floor / packing / never empty
# --------------------------------------------------------------------------- #


def test_breadth_floor_forces_starved_domain():
    # All recent activity in domain 1; domain 2 untouched beyond the floor.
    logs = [_log(11, 1, days_ago=SPACING_COOLDOWN_DAYS + 1) for _ in range(COLD_START_MIN_EVENTS)]
    logs.append(_log(21, 2, days_ago=BREADTH_FLOOR_DAYS + 5))
    prefs = [PrefInfo(1, 2.0, True), PrefInfo(2, 0.01, True)]
    q = _build(logs=logs, reviewed=ALL_REVIEWED, prefs=prefs, daily_items=1)
    assert any(it.forced and it.domain_id == 2 for it in q)


def test_count_goal_limits_queue():
    q = _build(logs=_filler(COLD_START_MIN_EVENTS), daily_items=3)
    assert len([it for it in q if not it.forced]) <= 3


def test_never_empty_all_activities_suppressed_no_lessons():
    logs = _filler(COLD_START_MIN_EVENTS) + [
        _log(11, 1, days_ago=1),
        _log(12, 1, days_ago=1),
        _log(21, 2, days_ago=1),
        _log(22, 2, days_ago=1),
    ]
    q = _build(logs=logs, reviewed=ALL_REVIEWED)
    assert len(q) >= 1


def test_inactive_domain_excluded():
    q = _build(logs=_filler(COLD_START_MIN_EVENTS), prefs=[PrefInfo(2, 1.0, False)])
    assert all(it.domain_id != 2 for it in q)


def test_determinism():
    logs = _filler(COLD_START_MIN_EVENTS) + [_log(11, 1, days_ago=3)]
    assert _ids(_build(logs=logs)) == _ids(_build(logs=logs))


def test_purity_no_wall_clock():
    import inspect

    src = inspect.getsource(sch)
    assert "datetime.now" not in src
    assert "date.today" not in src
