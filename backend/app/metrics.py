"""Pure streak and coverage computations.

Like scheduler.py: no DB and no wall-clock reads. Today's date and all rows are passed
in, so these are deterministic and unit-tested in isolation.

Streak (per domain): consecutive "active days" with >=1 completed drill in that domain.
"Active day" respects active_days_per_week, so a deliberate off day doesn't break it: the
allowed gap between counted days grows as the weekly target shrinks.

Coverage (per domain): (distinct facts reviewed + distinct drills completed) over the
domain's total facts + drills.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date


def _max_gap_days(active_days_per_week: int) -> int:
    """Largest gap (in days) between two counted activity days that still continues a
    streak. 7/week -> 1 (every calendar day); fewer active days -> a wider allowed gap."""
    adpw = max(1, min(active_days_per_week, 7))
    return max(1, math.ceil(7 / adpw))


def domain_streak(done_dates: list[date], active_days_per_week: int, today: date) -> int:
    """Current per-domain streak in active days.

    done_dates: dates (any number, unsorted) on which >=1 drill in the domain was
    completed. The streak is live only if the most recent activity is within the allowed
    gap of today; otherwise it has lapsed and is 0.
    """
    if not done_dates:
        return 0

    max_gap = _max_gap_days(active_days_per_week)
    days = sorted(set(done_dates), reverse=True)

    # A streak only counts as current if recent enough relative to today.
    if (today - days[0]).days > max_gap:
        return 0

    streak = 1
    for newer, older in zip(days, days[1:], strict=False):
        if (newer - older).days <= max_gap:
            streak += 1
        else:
            break
    return streak


@dataclass(frozen=True)
class Coverage:
    facts_total: int
    facts_reviewed: int
    drills_total: int
    drills_done: int

    @property
    def covered(self) -> int:
        return self.facts_reviewed + self.drills_done

    @property
    def total(self) -> int:
        return self.facts_total + self.drills_total

    @property
    def ratio(self) -> float:
        return self.covered / self.total if self.total else 0.0


def domain_coverage(
    facts_total: int, facts_reviewed: int, drills_total: int, drills_done: int
) -> Coverage:
    return Coverage(
        facts_total=facts_total,
        facts_reviewed=facts_reviewed,
        drills_total=drills_total,
        drills_done=drills_done,
    )
