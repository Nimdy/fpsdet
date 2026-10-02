"""JSON shapes for cohorts, history, reports, and cases."""

from __future__ import annotations

import json
from pathlib import Path

from .baseline import CohortTable
from .models import Case, HistoryWindow, MetricView


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
    return {"version": 1, "metrics": metrics, "integrity": table.integrity}


def cohort_from_dict(obj: dict) -> CohortTable:
    table = CohortTable()
    for row in obj.get("metrics") or []:
        for player in row["players"]:
            table.add(row["band"], row["key"], row["metric"], player["player_id"], float(player["value"]))
    stored = obj.get("integrity")
    if isinstance(stored, dict):
        table.integrity = stored
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
    }


def write_json(path: str | Path, obj: dict) -> None:
    Path(path).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
