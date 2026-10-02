"""Append-only lake. NDJSON first. Compact to Parquet later if you want DuckDB.

Raw shots are not a search index. Dump them, then scan the players you care about.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _dt_for(obj: dict, default_dt: str) -> str:
    utc = obj.get("utc")
    if isinstance(utc, str) and len(utc) >= 10:
        return utc[:10]
    return default_dt


def ingest_lines(lines: list[str], lake_dir: str | Path, *, default_dt: str | None = None) -> dict:
    lake = Path(lake_dir)
    today = default_dt or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = 0
    skipped = 0
    buckets: dict[tuple[str, str], list[str]] = {}
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(obj, dict) or not obj.get("game_id"):
            skipped += 1
            continue
        game_id = str(obj["game_id"])
        buckets.setdefault((game_id, _dt_for(obj, today)), []).append(raw)
    for (game_id, day), rows in buckets.items():
        folder = lake / f"game={game_id}" / f"dt={day}"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "events.ndjson"
        with target.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(row + "\n")
                written += 1
    return {"written": written, "skipped": skipped}


def read_lines(lake_dir: str | Path, *, game_id: str | None = None) -> list[str]:
    lake = Path(lake_dir)
    if not lake.exists():
        return []
    folders = [lake / f"game={game_id}"] if game_id else [path for path in lake.glob("game=*") if path.is_dir()]
    lines: list[str] = []
    for folder in folders:
        for path in sorted(folder.glob("dt=*/events.ndjson")):
            lines.extend(path.read_text(encoding="utf-8").splitlines())
    return lines
