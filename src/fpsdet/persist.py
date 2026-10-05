"""JSON shapes for cohorts, history, reports, and cases."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from .baseline import CohortTable
from .evidence import evidence_block
from .models import Case, Event, HistoryWindow, MetricView
from .provenance import check_cohort_seal, cohort_seal, packet_block


def cohort_to_dict(table: CohortTable) -> dict:
    metrics = []
    for (band, key, metric), pairs in sorted(table._values.items()):
        metrics.append(
            {
                "band": band,
                "key": key,
                "metric": metric,
                "players": [
                    {"player_id": pid, "value": value}
                    for pid, value in sorted(pairs, key=lambda item: (item[0], item[1]))
                ],
            }
        )
    return {"version": 1, "metrics": metrics, "integrity": table.integrity, "provenance": cohort_seal(table)}


def cohort_from_dict(obj: dict) -> CohortTable:
    table = CohortTable()
    for row in obj.get("metrics") or []:
        for player in row["players"]:
            table.add(row["band"], row["key"], row["metric"], player["player_id"], float(player["value"]))
    stored = obj.get("integrity")
    if isinstance(stored, dict):
        table.integrity = stored
    # A file that carries digests must match them, or it is refused. An older file without them still loads.
    if obj.get("provenance") is None:
        table.stored_digest = "absent"
    else:
        check_cohort_seal(table, obj["provenance"])
        table.stored_digest = "matched"
    return table


def history_from_dict(obj: dict) -> list[HistoryWindow]:
    return [
        HistoryWindow(
            player_id=row["player_id"],
            weapon_key=row["weapon_key"],
            skill_band=row["skill_band"],
            shots=int(row["shots"]),
            hits=int(row["hits"]),
        )
        for row in obj.get("windows") or []
    ]


def history_to_dict(windows: list[HistoryWindow]) -> dict:
    return {
        "version": 1,
        "windows": [
            {
                "player_id": row.player_id,
                "weapon_key": row.weapon_key,
                "skill_band": row.skill_band,
                "shots": row.shots,
                "hits": row.hits,
            }
            for row in windows
        ],
    }


def load_reports(obj: dict) -> dict[str, int]:
    players = obj["players"] if isinstance(obj.get("players"), dict) else obj
    reports: dict[str, int] = {}
    for key, value in players.items():
        if key in {"version", "players"}:
            continue
        if isinstance(value, dict):
            reports[str(key)] = int(value.get("reports") or 0)
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (int, float)):
            reports[str(key)] = int(value)
    return reports


def case_to_dict(case: Case) -> dict:
    """The case as JSON data. The evidence packet digest is computed from this same data, as a verifier would."""
    row = _case_fields(case)
    row["evidence"]["packet"] = packet_block(row)
    return row


def _case_fields(case: Case) -> dict:
    def metric(row: MetricView) -> dict:
        return {
            "name": row.name,
            "player_value": row.player_value,
            "bound": row.bound,
            "own_p95": row.own_p95,
            "own_max": row.own_max,
            "ceiling_band": row.ceiling_band,
            "ceiling_p95": row.ceiling_p95,
            "ceiling_extreme": row.ceiling_extreme,
            "beyond_band": row.beyond_band,
            "beyond_human": row.beyond_human,
            "skipped": row.skipped,
            "key": row.key,
        }

    speed = None
    if case.speed is not None:
        speed = {
            "eligible": case.speed.eligible,
            "excluded_innocent": case.speed.excluded_innocent,
            "excluded_unknown": case.speed.excluded_unknown,
            "excluded_airborne": case.speed.excluded_airborne,
            "missing_cap": case.speed.missing_cap,
            "violations": case.speed.violations,
            "longest_run": case.speed.longest_run,
            "sustained": case.speed.sustained,
            "spike_samples": case.speed.spike_samples,
            "cap_source": case.speed.cap_source,
            "detail": case.speed.detail,
        }
    return {
        "version": 1,
        "player_id": case.player_id,
        "game_id": case.game_id,
        "decision": case.decision,
        "recommended_action": case.recommended_action,
        "automated_action": case.automated_action,
        "skill_band": case.skill_band,
        "reports": case.reports,
        "reasons": case.reasons,
        "observations": case.observations,
        "metrics": [metric(row) for row in case.metrics],
        "match_ids": case.match_ids,
        "party_ids": case.party_ids,
        "party_note": case.party_note,
        "untrained": case.untrained,
        "speed": speed,
        "ai_brief": case.ai_brief,
        "limits": case.limits,
        "seal": case.seal,
        "queue_rank": case.queue_rank,
        "vendor_twin": case.vendor_twin,
        "vendor_r": case.vendor_r,
        "inherit_lags_ms": case.inherit_lags_ms,
        "checks": case.checks,
        "evidence": evidence_block(
            case.evidence,
            case.compared_on,
            case.provenance.to_dict() if case.provenance else None,
            challenges=[result.to_dict() for result in case.challenges],
        ),
    }


def event_to_dict(event: Event) -> dict:
    """One NDJSON line, the shape a dedicated server writes. Unset fields are left out."""
    row: dict = {}
    for spec in fields(Event):
        if spec.name == "extras":
            continue
        value = getattr(event, spec.name)
        if value is None or (spec.name == "mod_set" and not value):
            continue
        row[spec.name] = list(value) if spec.name == "mod_set" else value
    row.update(event.extras)
    return row


def write_json(path: str | Path, obj: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
