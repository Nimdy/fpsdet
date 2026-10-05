"""Score a batch of events against a frozen cohort, or against itself."""

from __future__ import annotations

from .baseline import CohortTable, build_cohorts
from .models import Case, Event, GameProfile, HistoryWindow
from .provenance import stamp
from .score import annotate_batch, assess_player, history_for
from .summarize import summarize_timelines
from .timeline import player_timelines

IN_FILE_NOTE = (
    "Cohort was fit on this same file, including players under review. "
    "Freeze a baseline from an earlier clean window before you rely on it."
)


def run_score(
    events: list[Event],
    profile: GameProfile,
    cohort: CohortTable | None = None,
    history: list[HistoryWindow] | None = None,
    reports: dict[str, int] | None = None,
) -> list[Case]:
    # One canonical timeline per player, built once: the scorer reads it and the input digest binds it.
    timelines = player_timelines(events)
    records = summarize_timelines(timelines, profile)
    in_file = cohort is None
    table = build_cohorts(records, profile) if cohort is None else cohort
    report_map = reports or {}
    cases: list[Case] = []
    for record in records:
        case = assess_player(
            record,
            table,
            profile,
            history or [],
            report_map.get(record.player_id, 0),
        )
        if in_file:
            case.observations.append(IN_FILE_NOTE)
        cases.append(case)
    annotate_batch(cases, records, profile)
    readable = {record.player_id: history_for(record, history) for record in records} if history else None
    stamp(cases, profile, cohort=table, cohort_mode="in_file" if in_file else "external", timelines=timelines, history=readable)
    return cases
