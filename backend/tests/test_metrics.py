from __future__ import annotations

from datetime import date

from app.metrics import domain_coverage, domain_streak

TODAY = date(2026, 6, 15)


def d(day: int) -> date:
    return date(2026, 6, day)


# --------------------------------------------------------------------------- #
# Streak
# --------------------------------------------------------------------------- #


def test_streak_zero_when_no_activity():
    assert domain_streak([], active_days_per_week=7, today=TODAY) == 0


def test_streak_counts_consecutive_days_daily_target():
    dates = [d(15), d(14), d(13)]
    assert domain_streak(dates, active_days_per_week=7, today=TODAY) == 3


def test_streak_breaks_on_gap_daily_target():
    # missed the 13th -> only 14th and 15th count
    dates = [d(15), d(14), d(12)]
    assert domain_streak(dates, active_days_per_week=7, today=TODAY) == 2


def test_streak_lapses_if_last_activity_too_old():
    # daily target, last activity 3 days ago -> streak has lapsed
    dates = [d(12), d(11)]
    assert domain_streak(dates, active_days_per_week=7, today=TODAY) == 0


def test_streak_tolerates_off_days_with_lower_weekly_target():
    # 3 days/week -> wider allowed gap; every-other-day activity keeps the streak
    dates = [d(15), d(13), d(11)]
    assert domain_streak(dates, active_days_per_week=3, today=TODAY) == 3


def test_streak_dedupes_same_day():
    dates = [d(15), d(15), d(14)]
    assert domain_streak(dates, active_days_per_week=7, today=TODAY) == 2


# --------------------------------------------------------------------------- #
# Coverage
# --------------------------------------------------------------------------- #


def test_coverage_ratio_and_parts():
    cov = domain_coverage(facts_total=3, facts_reviewed=1, drills_total=3, drills_done=2)
    assert cov.covered == 3
    assert cov.total == 6
    assert cov.ratio == 0.5


def test_coverage_empty_domain_is_zero_not_error():
    cov = domain_coverage(facts_total=0, facts_reviewed=0, drills_total=0, drills_done=0)
    assert cov.ratio == 0.0


def test_coverage_full():
    cov = domain_coverage(facts_total=2, facts_reviewed=2, drills_total=2, drills_done=2)
    assert cov.ratio == 1.0
