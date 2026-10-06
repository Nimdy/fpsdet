"""Turn an export of your game server's logs into fpsdet's event lines, for a first look at data you already have.

    python examples/historic/convert.py export.csv --map examples/historic/map.json --game my-game --out events.ndjson
    python examples/historic/convert.py --who ACCOUNT_ID              # which pseudonym stands for this account?

It reads CSV with a header row, or JSON lines, and writes one fpsdet event per row:

- **Only fields fpsdet knows** (schema/combat_event.schema.json). Every other column is dropped, so a name, an
  address or a chat line in your export never reaches the output.
- **Pseudonyms, not account ids.** player_id, enemy_id and party_id become HMAC-SHA256 pseudonyms under a salt
  that stays in a file on your machine (fpsdet-salt.hex, owner-only, never in the output). The same account gets
  the same pseudonym every run, and --who finds it again.
- **The schema's types.** Numbers, booleans and the named values (event_type, hitbox, displacement_cause, ...)
  are checked; a value that does not fit is left out and counted, never guessed. A t_ms column holding an
  ISO 8601 time becomes milliseconds.

A row without game_id, match_id, player_id or t_ms is skipped and counted. Columns already named like fpsdet's
fields need no mapping. It touches nothing but the files you name. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import hmac
import json
import os
import secrets
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schema" / "combat_event.schema.json"
PLAYER_FIELDS = ("player_id", "enemy_id")  # the same people, so one namespace
PARTY_FIELDS = ("party_id",)
TRUE, FALSE = {"true", "t", "yes", "y", "1"}, {"false", "f", "no", "n", "0"}


def schema() -> dict:
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def salt(path: Path) -> bytes:
    """The salt behind every pseudonym: read it, or make a new one, owner-only."""
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="utf-8").strip())
    value = secrets.token_bytes(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(value.hex() + "\n")
    print(f"New salt in {path}. Keep it private and keep it: the same salt gives the same pseudonyms next time.", file=sys.stderr)
    return value


def pseudonym(key: bytes, kind: str, value: str) -> str:
    digest = hmac.new(key, f"fpsdet.pseudonym/1\0{kind}\0{value}".encode("utf-8"), hashlib.sha256).hexdigest()
    return ("party-" if kind == "party" else "p-") + digest[:16]


def milliseconds(value: str) -> int | None:
    try:
        return int(round(float(value)))
    except ValueError:
        pass
    try:
        moment = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return int(round(moment.timestamp() * 1000))


def cast(spec: dict, value) -> tuple[bool, object]:
    """(fits, value) for one field, by the schema's own description of it."""
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return False, None
    if "enum" in spec:
        text = str(value).strip().lower()
        return (text in spec["enum"], text)
    kind = spec.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "boolean" in kinds:
        if isinstance(value, bool):
            return True, value
        text = str(value).strip().lower()
        if text in TRUE or text in FALSE:
            return True, text in TRUE
    if "integer" in kinds or "number" in kinds:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False, None
        if number != number or number in (float("inf"), float("-inf")):
            return False, None
        if "minimum" in spec and number < spec["minimum"]:
            return False, None
        if "maximum" in spec and number > spec["maximum"]:
            return False, None
        if "integer" in kinds and "number" not in kinds:
            return True, int(round(number))
        return True, number
    if "string" in kinds:
        return True, str(value)
    if "array" in kinds:
        items = value if isinstance(value, list) else [part for part in str(value).replace(";", ",").split(",") if part.strip()]
        return True, [str(part).strip() for part in items]
    return False, None


def rows(path: Path):
    if path.suffix.lower() in (".jsonl", ".ndjson", ".json"):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
        return
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def convert(sources: list[Path], mapping: dict[str, str], constants: dict[str, str], key: bytes) -> tuple[list[dict], dict]:
    found = schema()
    properties, required = found["properties"], found["required"]
    known_columns = {mapping.get(field, field) for field in properties}
    unknown = set(mapping) - set(properties)
    if unknown:
        raise SystemExit(f"--map names fields fpsdet does not have: {', '.join(sorted(unknown))}")
    events, report = [], {"read": 0, "written": 0, "skipped": Counter(), "invalid": Counter(), "dropped_columns": set()}
    for source in sources:
        for row in rows(source):
            report["read"] += 1
            event = {}
            for field, spec in properties.items():
                column = mapping.get(field, field)
                if column in row and row[column] not in (None, ""):
                    raw = row[column]
                elif field in constants:
                    raw = constants[field]
                else:
                    continue
                if field == "t_ms":
                    value = milliseconds(str(raw))
                    if value is None:
                        report["invalid"][field] += 1
                        continue
                    event[field] = value
                    if "utc" not in event and not str(raw).replace(".", "", 1).lstrip("-").isdigit():
                        event.setdefault("utc", datetime.datetime.fromtimestamp(value / 1000, datetime.timezone.utc).isoformat().replace("+00:00", "Z"))
                    continue
                fits, value = cast(spec, raw)
                if not fits:
                    report["invalid"][field] += 1
                    continue
                if field in PLAYER_FIELDS:
                    value = pseudonym(key, "player", str(value))
                elif field in PARTY_FIELDS:
                    value = pseudonym(key, "party", str(value))
                event[field] = value
            report["dropped_columns"].update(column for column in row if column not in known_columns)
            missing = [field for field in required if field not in event]
            if missing:
                report["skipped"]["missing " + ", ".join(missing)] += 1
                continue
            events.append(event)
    report["written"] = len(events)
    return events, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("exports", nargs="*", type=Path, help="CSV with a header row, or JSON lines (.jsonl, .ndjson)")
    parser.add_argument("--map", type=Path, help='JSON: {"fpsdet field": "your column"}; columns named like fpsdet fields need none')
    parser.add_argument("--game", help="game_id for every row, when the export has no column for it")
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE", help="A constant for every row, e.g. event_type=shot")
    parser.add_argument("--salt-file", type=Path, default=Path("fpsdet-salt.hex"))
    parser.add_argument("--out", type=Path, help="Where to write the events (NDJSON)")
    parser.add_argument("--who", metavar="ACCOUNT_ID", help="Print the pseudonym this account gets under the salt, and stop")
    args = parser.parse_args(argv)
    if args.who is not None:
        if not args.salt_file.exists():
            raise SystemExit(f"{args.salt_file} does not exist: pseudonyms come from the salt the conversion made")
        print(pseudonym(salt(args.salt_file), "player", args.who))
        return 0
    if not args.exports or not args.out:
        parser.error("give the exports to convert and --out")
    mapping = json.loads(args.map.read_text(encoding="utf-8")) if args.map else {}
    constants = dict(item.split("=", 1) for item in args.set)
    if args.game:
        constants["game_id"] = args.game
    events, report = convert(args.exports, mapping, constants, salt(args.salt_file))
    with args.out.open("w", encoding="utf-8") as out:
        for event in events:
            out.write(json.dumps(event, separators=(",", ":")) + "\n")
    print(f"{report['written']} of {report['read']} rows written to {args.out}.")
    for reason, count in sorted(report["skipped"].items()):
        print(f"  skipped {count}: {reason}")
    for field, count in sorted(report["invalid"].items()):
        print(f"  left out {count} values of {field} that do not fit the schema")
    if report["dropped_columns"]:
        print(f"  columns not copied (fpsdet has no field for them): {', '.join(sorted(report['dropped_columns']))}")
    fields = sorted({field for event in events for field in event})
    print(f"  fields present: {', '.join(fields)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
