"""The operations view: a week of cases, the way the people who run the queue see it.

``ops_payload`` turns cases (and, when they are at hand, the events, the cohort,
and the nightly batches) into one JSON object. ``fpsdet score --out`` writes it
as ``ops.json`` next to the cases; ``fpsdet dashboard`` renders it as one offline
HTML page; the review desk shows the synthetic week through the same view.

Every panel reads fields a studio can chart in its own tools. dashboards/README.md
says which field drives which panel.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .baseline import CohortTable
from .models import BANDS, CHECKS, Case, Event, GameProfile
from .parse import aim_key
from .persist import case_to_dict

SEVERITY = {"insufficient_data": 0, "clean": 1, "watch": 2, "review": 3}
CODE = {"review": "R", "watch": "W", "clean": "C", "insufficient_data": "H"}

# The optional fields a server can send, the checks each one turns on, and the event kind.
FIELDS = [
    ("expected_max_ground_speed_mps", "speed cap the server applied", "movement"),
    ("on_ground", "ground speed only", "movement"),
    ("hitbox", "headshot rate (on hits)", "shot"),
    ("distance_m", "engagement distance", "shot"),
    ("spray_index", "recoil floor, mirror, leftover", "shot"),
    ("applied_recoil_pitch_deg", "recoil mirror, leftover", "shot"),
    ("information_state", "quiet aim, teammate check", "shot"),
    ("since_perceived_ms", "grace after line of sight breaks", "shot"),
    ("hidden_track_ms", "hidden mover", "shot"),
    ("wire_error_deg", "wire, not picture", "shot"),
    ("private_track_ms", "private replay", "shot"),
    ("through_geometry", "shots through walls", "shot"),
    ("acquire_ms", "reaction time (supporting)", "shot"),
    ("view_delta_deg", "view snaps (supporting)", "shot"),
]

# Which answer-key tape explains a check.
TAPES = {
    "speed": "tape-speed",
    "fire_rate": "tape-fire",
    "metronome": "tape-fire",
    "recoil_floor": "tape-recoil",
    "recoil_learned": "tape-recoil",
    "mirror": "tape-mirror",
    "accuracy": "tape-aim",
    "headshot_rate": "tape-aim",
    "median_distance": "tape-aim",
    "rank_tail": "tape-aim",
    "account_jump": "tape-population",
    "hidden": "tape-hidden",
    "quiet_aim": "tape-quiet",
    "private_replay": "tape-replay",
    "wire": "tape-wire",
    "leftover": "tape-vendor",
    "voice": "tape-party",
}


def merge_queue(weekly: list[Case], nightly: list[list[Case]] | None = None) -> dict[str, Case]:
    """The open queue: the latest batch, plus any review a nightly batch opened.

    A review stays open until a person closes it, so a cheat that a night
    caught is still in the queue even if the weekly numbers dilute it. A watch
    is a monitor flag, not a case, so it comes from the latest batch only.
    """
    queue = {case.player_id: case for case in weekly}
    for night in nightly or []:
        for case in night:
            current = queue.get(case.player_id)
            if case.decision == "review" and (current is None or current.decision != "review"):
                queue[case.player_id] = case
    return queue


def _label(day: str) -> str:
    return dt.date.fromisoformat(day).strftime("%a %d")


def _metric_rows(case: Case) -> list[dict]:
    rows = []
    for metric in case.metrics:
        if metric.skipped or metric.player_value is None:
            continue
        rows.append(
            {
                "name": metric.name,
                "key": metric.key,
                "value": round(metric.player_value, 4),
                "bound": None if metric.bound is None else round(metric.bound, 4),
                "rank": None if metric.own_p95 is None else round(metric.own_p95, 4),
                "human": None if metric.ceiling_extreme is None else round(metric.ceiling_extreme, 4),
                "band": metric.ceiling_band,
                "tail": metric.beyond_band,
                "past": metric.beyond_human,
            }
        )
    return rows


# Lines that explain a watch: a tail inside the best humans, or two supporting tells.
_WATCH_HINTS = ("above this rank", "human tail", "low for humans", "low tail", "past every measured human", "tighter than the human")
# Lines that explain a hold. "shots, need" and not "need": a glitch note says "(need 25)" too.
_HELD_HINTS = ("shots, need ", "waiting for a baseline", "no curve")


def _why(case: Case) -> str:
    """The one line a reviewer reads first: the line behind this decision. A clean case has none."""
    if case.reasons:
        return case.reasons[0]
    if case.decision == "clean":
        return ""
    first = _WATCH_HINTS if case.decision == "watch" else _HELD_HINTS
    for hints in (first, _WATCH_HINTS + _HELD_HINTS):
        for line in case.observations:
            if any(hint in line for hint in hints):
                return line
    return case.observations[0] if case.observations else ""


def _scatter_key(cases: Iterable[Case], cohort: CohortTable | None) -> str:
    """The weapon the human-ceiling scatter is drawn for: rifle when there is one, else the most scored."""
    if cohort is not None:
        keys = Counter(key for (_, key, metric) in cohort._values if metric == "accuracy")
    else:
        keys = Counter(metric.key for case in cases for metric in case.metrics if metric.name == "accuracy" and metric.key)
    if "rifle" in keys:
        return "rifle"
    return keys.most_common(1)[0][0] if keys else ""


def _scatter_point(case: Case, key: str) -> dict | None:
    """Accuracy and headshot rate on one weapon. Both must come from it, or there is no point."""
    values: dict[str, float] = {}
    for metric in case.metrics:
        if metric.key != key or metric.skipped or metric.player_value is None:
            continue
        values.setdefault(metric.name, metric.player_value)
    if "accuracy" not in values or "headshot_rate" not in values:
        return None
    return {"acc": round(values["accuracy"], 4), "hs": round(values["headshot_rate"], 4)}


def _coverage(events: list[Event], days: list[str] | None) -> list[dict]:
    """Share of events of the right kind that carry each optional field, per day when days are known."""
    shots = [event for event in events if event.event_type == "shot"]
    moves = [event for event in events if event.event_type == "movement"]

    def share(rows: list[Event], name: str) -> float | None:
        if name == "hitbox":
            rows = [row for row in rows if row.hit]  # a miss has no hitbox
        if not rows:
            return None
        have = sum(1 for row in rows if getattr(row, name) is not None)
        return round(have / len(rows), 3)

    def day_of(event: Event, index: dict[str, int]) -> int | None:
        for day, position in index.items():
            if event.match_id.startswith(day):
                return position
        return None

    split: list[tuple[list[Event], list[Event]]] = []
    if days:
        prefix = {"m" + day.replace("-", ""): position for position, day in enumerate(days)}
        buckets = [([], []) for _ in days]
        for event in shots:
            position = day_of(event, prefix)
            if position is not None:
                buckets[position][0].append(event)
        for event in moves:
            position = day_of(event, prefix)
            if position is not None:
                buckets[position][1].append(event)
        split = [(a, b) for a, b in buckets]
    rows = []
    for name, needs, kind in FIELDS:
        pool = shots if kind == "shot" else moves
        row = {"field": name, "needs": needs, "kind": kind, "share": share(pool, name)}
        if split:
            row["daily"] = [share(day_shots if kind == "shot" else day_moves, name) for day_shots, day_moves in split]
        rows.append(row)
    return rows


def _cohort_cells(cohort: CohortTable, profile: GameProfile, seen: Iterable[str] = ()) -> dict:
    """Players per band and weapon in the baseline. A weapon in play with no baseline shows as zeros."""
    keys = sorted({key for (_, key, metric) in cohort._values if metric == "accuracy"} | set(seen))
    bands = [band for band in (*BANDS, "unrated") if any(cohort.series(band, key, "accuracy", None) for key in keys)]
    cells = [
        {"band": band, "key": key, "players": len(cohort.series(band, key, "accuracy", None))}
        for band in bands
        for key in keys
    ]
    return {"bands": bands, "keys": keys, "cells": cells, "min": profile.min_cohort_players}


def _human_lines(cohort: CohortTable, profile: GameProfile, key: str) -> dict:
    lines: dict = {"key": key}
    for metric in ("accuracy", "headshot_rate"):
        top = cohort.extreme(key, metric, None, profile.min_cohort_players, high=True)
        lines[metric] = None if top is None else {"band": top[0], "value": round(top[1], 4)}
        lines[metric + "_p95"] = {}
        for band in BANDS:
            dist = cohort.dist(band, key, metric, None)
            if dist is not None and dist.n >= profile.min_cohort_players:
                lines[metric + "_p95"][band] = round(dist.p95, 4)
    return lines


def _movement(events: list[Event], cases: Iterable[Case]) -> list[dict]:
    causes = Counter(
        event.displacement_cause
        for event in events
        if event.event_type == "movement" and event.displacement_cause != "none"
    )
    airborne = sum(1 for event in events if event.event_type == "movement" and event.on_ground is False)
    spikes = sum(1 for case in cases if case.speed is not None and case.speed.spike_samples)
    rows = [{"label": cause, "count": count} for cause, count in causes.most_common()]
    rows.append({"label": "airborne", "count": airborne})
    rows.append({"label": "players with a short untagged spike", "count": spikes})
    return rows


def ops_payload(
    *,
    profile: GameProfile,
    cases: list[Case],
    events: list[Event] | None = None,
    cohort: CohortTable | None = None,
    nightly: list[list[Case]] | None = None,
    days: list[str] | None = None,
    truth: dict[str, str] | None = None,
    truth_notes: dict[str, str] | None = None,
    notes: dict[str, str] | None = None,
    bands: dict[str, str] | None = None,
    reports: dict[str, int] | None = None,
) -> dict:
    """One JSON object for the operations view. Only ``profile`` and ``cases`` are required."""
    queue = merge_queue(cases, nightly)
    weekly = {case.player_id: case for case in cases}
    scatter = _scatter_key(cases, cohort)
    nightly = nightly or []
    first_review: dict[str, int] = {}
    per_night: list[dict[str, str]] = []
    for position, night in enumerate(nightly):
        decisions = {}
        for case in night:
            decisions[case.player_id] = CODE[case.decision]
            if case.decision == "review":
                first_review.setdefault(case.player_id, position)
        per_night.append(decisions)
    rows = []
    for case in sorted(queue.values(), key=lambda c: (-SEVERITY[c.decision], -c.reports, c.player_id)):
        why = _why(case)
        row = {
            "id": case.player_id,
            "decision": case.decision,
            "band": (bands or {}).get(case.player_id, case.skill_band),
            "reports": case.reports if reports is None else reports.get(case.player_id, case.reports),
            "checks": case.checks,
            "why": why,
            "nights": [night.get(case.player_id, "") for night in per_night],
            "first": first_review.get(case.player_id),
            # Numbers come from the latest batch over the whole window. The decision may be a night's.
            "point": _scatter_point(weekly.get(case.player_id, case), scatter),
            "metrics": _metric_rows(weekly.get(case.player_id, case)),
        }
        if truth is not None:
            row["truth"] = truth.get(case.player_id, "honest")
        if case.decision != "clean" or case.reports or (truth and truth.get(case.player_id, "honest") != "honest"):
            row["case"] = case_to_dict(case)
        rows.append(row)

    events = events or []
    shots = sum(1 for event in events if event.event_type == "shot")
    matches = len({event.match_id for event in events})
    payload: dict = {
        "game": profile.game_id,
        "synthetic": truth is not None,
        "notes": notes or {},
        "days": [{"day": day, "label": _label(day)} for day in days or []],
        "totals": {
            "players": len(queue),
            "events": len(events),
            "shots": shots,
            "movement": len(events) - shots,
            "matches": matches,
        },
        "checks": {key: {"family": family, "label": label} for key, (family, label) in CHECKS.items()},
        "tapes": TAPES,
        "rows": rows,
        "integrity": (cohort.integrity if cohort is not None else {"status": "unchecked", "alarms": []}),
        "min_cohort": profile.min_cohort_players,
    }
    if events:
        payload["coverage"] = _coverage(events, days)
        payload["movement"] = _movement(events, queue.values())
    if cohort is not None:
        seen = {aim_key(event, profile) for event in events if event.event_type == "shot"}
        payload["cohort"] = _cohort_cells(cohort, profile, seen)
        payload["lines"] = _human_lines(cohort, profile, scatter)
    if truth is not None:
        payload["truth_notes"] = truth_notes or {}
    return payload


def week_payload(week) -> dict:
    """The synthetic week, through the same view a studio gets from ``score --out``."""
    from .week import CHEATS

    return ops_payload(
        profile=week.profile,
        cases=week.cases,
        events=week.events,
        cohort=week.cohort,
        nightly=week.nightly,
        days=week.days,
        truth=week.truth,
        truth_notes={label: text for label, (_, _, text) in CHEATS.items()},
        notes=week.features,
        bands={pid: player.band for pid, player in week.players.items()},
    )


def write_ops(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def merge_payloads(payloads: list[dict], labels: list[str]) -> dict:
    """Several ``score --out`` runs, oldest first, as one week: one night per run.

    The rule is the same as ``merge_queue``: a review from any night stays open,
    and everything else comes from the latest run. Coverage becomes a per-night
    series. The baseline shown is the latest run's.
    """
    if not payloads:
        raise ValueError("no payloads to merge")
    latest = dict(payloads[-1])
    nights = []
    for label in labels:
        try:
            nights.append({"day": label, "label": _label(label)})
        except ValueError:
            nights.append({"day": label, "label": label})
    by_night = [{row["id"]: row for row in payload["rows"]} for payload in payloads]
    ids = sorted({pid for night in by_night for pid in night})
    rows = []
    for pid in ids:
        seen = [night.get(pid) for night in by_night]
        last = next(row for row in reversed(seen) if row is not None)
        reviews = [index for index, row in enumerate(seen) if row is not None and row["decision"] == "review"]
        row = dict(last)
        if reviews and last["decision"] != "review":
            opened = seen[reviews[-1]]
            row.update({key: opened[key] for key in ("decision", "checks", "why") if key in opened})
            if "case" in opened:
                row["case"] = opened["case"]
        row["nights"] = [CODE[r["decision"]] if r is not None else "" for r in seen]
        row["first"] = reviews[0] if reviews else None
        rows.append(row)
    rows.sort(key=lambda r: (-SEVERITY[r["decision"]], -r["reports"], r["id"]))
    latest["rows"] = rows
    latest["days"] = nights
    totals = {key: sum(p["totals"].get(key, 0) for p in payloads) for key in ("events", "shots", "movement", "matches")}
    totals["players"] = len(rows)
    latest["totals"] = totals
    if all("coverage" in p for p in payloads):
        coverage = []
        for position, field in enumerate(payloads[-1]["coverage"]):
            daily = [p["coverage"][position]["share"] for p in payloads]
            weights = [p["totals"]["events"] for p in payloads]
            known = [(share, weight) for share, weight in zip(daily, weights) if share is not None]
            overall = round(sum(s * w for s, w in known) / max(1, sum(w for _, w in known)), 3) if known else None
            coverage.append({**field, "daily": daily, "share": overall})
        latest["coverage"] = coverage
    return latest


def read_ops(folder: str | Path) -> dict:
    path = Path(folder) / "ops.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found. Run fpsdet score --out {folder} first.")
    return json.loads(path.read_text(encoding="utf-8"))
