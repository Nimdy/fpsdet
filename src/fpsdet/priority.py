"""Report leads jump the scan queue. They do not change the decision."""

from __future__ import annotations

from .models import Case

_REVIEW_RANK = {"review": 0, "watch": 1, "clean": 2, "insufficient_data": 3}


def scan_order(cases: list[Case]) -> list[Case]:
    """Hydrate reported players first, then a higher batch queue rank."""
    return sorted(
        cases,
        key=lambda case: (case.reports <= 0, -case.reports, -case.queue_rank, case.player_id),
    )


def review_order(cases: list[Case]) -> list[Case]:
    """What a person should read first, after the numbers exist."""
    return sorted(
        cases,
        key=lambda case: (_REVIEW_RANK.get(case.decision, 9), -case.reports, case.player_id),
    )
