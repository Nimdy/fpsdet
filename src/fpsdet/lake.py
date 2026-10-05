"""Append-only lake. NDJSON first. Compact to Parquet later if you want DuckDB.

Raw shots are not a search index. Dump them, then scan the players you care about.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Event text becomes a folder name. A separator in it would put the partition outside the lake.
_UNSAFE = frozenset("/\\:\x00")


def safe_game_id(game_id: str) -> bool:
    return bool(game_id) and not any(ch in _UNSAFE for ch in game_id)


def _dt_for(obj: dict, default_dt: str) -> str | None:
    """The partition day: the event's ``utc`` date, else the default. None when ``utc`` is not a date."""
    utc = obj.get("utc")
    if not isinstance(utc, str) or len(utc) < 10:
        return default_dt
    return utc[:10] if _DAY.fullmatch(utc[:10]) else None


def ingest_lines(lines: list[str], lake_dir: str | Path, *, default_dt: str | None = None) -> dict:
    """Append each line to its game and day. A line whose game id or day cannot be a folder is skipped."""
    lake = Path(lake_dir)
    if default_dt is not None and not _DAY.fullmatch(default_dt):
        raise ValueError(f"--dt must be YYYY-MM-DD, not {default_dt!r}")
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
        day = _dt_for(obj, today)
        if day is None or not safe_game_id(game_id):
            skipped += 1
            continue
        buckets.setdefault((game_id, day), []).append(raw)
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
    if game_id is not None and not safe_game_id(game_id):
        raise ValueError(f"game id {game_id!r} cannot be a lake folder")
    if not lake.exists():
        return []
    folders = [lake / f"game={game_id}"] if game_id else [path for path in lake.glob("game=*") if path.is_dir()]
    lines: list[str] = []
    for folder in folders:
        for path in sorted(folder.glob("dt=*/events.ndjson")):
            lines.extend(path.read_text(encoding="utf-8").splitlines())
    return lines
