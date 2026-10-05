"""Snapshot a scored run and compare two snapshots case by case. Standard library only.

The real-match runs in examples/ are too large to commit, so their regression check runs where the data
is. Snapshot the run before a change, snapshot it again after, and diff:

    PYTHONPATH=src python tools/regress.py snapshot scored.ndjson --profile examples/tf2/tf2.json \\
        --cohort cohort.json --out before.jsonl
    # ...change the scorer...
    PYTHONPATH=src python tools/regress.py snapshot ... --out after.jsonl
    python tools/regress.py diff before.jsonl after.jsonl --labels labels.json

A snapshot is one line per player: the case exactly as ``fpsdet score`` writes it, with floats cut to 10
significant digits so the last bit of a float sum cannot differ between Python versions. ``diff`` compares
the fields the first snapshot has. A field only the second one has is listed and allowed: that is how new
evidence is added without moving what consumers read. Any other change is printed by field and by player,
and the exit code is 1. With labels, decisions are also counted per label, the way the README reports them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path


def canon(value):
    if isinstance(value, float):
        return float(f"{value:.10g}") if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {key: canon(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canon(item) for item in value]
    return value


def cmd_snapshot(args: argparse.Namespace) -> int:
    from fpsdet.parse import load_events, load_profile
    from fpsdet.persist import case_to_dict, cohort_from_dict, history_from_dict, load_reports, read_json
    from fpsdet.pipeline import run_score

    events, errors = load_events(args.events)
    for error in errors[:20]:
        print(error, file=sys.stderr)
    profile = load_profile(args.profile)
    cohort = cohort_from_dict(read_json(args.cohort)) if args.cohort else None
    history = history_from_dict(read_json(args.history)) if args.history else []
    reports = load_reports(read_json(args.reports)) if args.reports else {}
    cases = run_score(events, profile, cohort, history, reports)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for case in sorted(cases, key=lambda c: c.player_id):
            handle.write(json.dumps(canon(case_to_dict(case)), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    decisions = Counter(case.decision for case in cases)
    print(f"Wrote {len(cases)} cases from {len(events)} events to {target}: {dict(sorted(decisions.items()))}")
    return 0


def read_snapshot(path: str | Path) -> dict[str, dict]:
    rows = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[row["player_id"]] = row
    return rows


def compare(before: dict[str, dict], after: dict[str, dict]) -> dict:
    """What moved between two snapshots, on the fields the first one has."""
    gone = sorted(set(before) - set(after))
    new = sorted(set(after) - set(before))
    fields: Counter = Counter()
    moved: dict[str, list[str]] = {}
    transitions: Counter = Counter()
    added: Counter = Counter()
    for pid in sorted(set(before) & set(after)):
        old, now = before[pid], after[pid]
        changed = [key for key in old if now.get(key, "<missing>") != old[key]]
        if changed:
            moved[pid] = changed
            fields.update(changed)
        if old.get("decision") != now.get("decision"):
            transitions[(old.get("decision"), now.get("decision"))] += 1
        added.update(key for key in now if key not in old)
    return {"gone": gone, "new": new, "moved": moved, "fields": fields, "transitions": transitions, "added": added}


def by_label(rows: dict[str, dict], labels: dict[str, str]) -> dict[str, Counter]:
    table: dict[str, Counter] = {}
    for pid, row in rows.items():
        table.setdefault(labels.get(pid, "unlabelled"), Counter())[row["decision"]] += 1
    return table


def cmd_diff(args: argparse.Namespace) -> int:
    before, after = read_snapshot(args.before), read_snapshot(args.after)
    result = compare(before, after)
    print(f"{len(before)} cases before, {len(after)} after")
    if result["added"]:
        print("New fields (allowed): " + ", ".join(f"{key} on {n}" for key, n in sorted(result["added"].items())))
    if args.labels:
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        old, new = by_label(before, labels), by_label(after, labels)
        print("Decisions by label, before -> after:")
        for label in sorted(set(old) | set(new)):
            decisions = sorted(set(old.get(label, {})) | set(new.get(label, {})))
            cells = ", ".join(
                f"{d} {old.get(label, Counter())[d]}"
                + ("" if old.get(label, Counter())[d] == new.get(label, Counter())[d] else f" -> {new.get(label, Counter())[d]}")
                for d in decisions
            )
            print(f"  {label}: {cells}")
    clean = not (result["gone"] or result["new"] or result["moved"])
    if clean:
        print("No case moved.")
        return 0
    if result["gone"] or result["new"]:
        print(f"Players only before: {len(result['gone'])} {result['gone'][:5]}; only after: {len(result['new'])} {result['new'][:5]}")
    for (old, now), n in sorted(result["transitions"].items(), key=lambda item: -item[1]):
        print(f"  {old} -> {now}: {n}")
    print("Fields that moved: " + ", ".join(f"{key} on {n}" for key, n in result["fields"].most_common()))
    for pid, keys in list(result["moved"].items())[: args.show]:
        print(f"  {pid}: {', '.join(keys)}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot", help="Score events and write every case, one line per player")
    snap.add_argument("events")
    snap.add_argument("--profile", required=True)
    snap.add_argument("--cohort", help="Frozen baseline. Without one the batch is scored against itself")
    snap.add_argument("--history")
    snap.add_argument("--reports")
    snap.add_argument("--out", required=True)
    snap.set_defaults(func=cmd_snapshot)
    diff = sub.add_parser("diff", help="Compare two snapshots. Exit 1 if a case moved")
    diff.add_argument("before")
    diff.add_argument("after")
    diff.add_argument("--labels", help="JSON map of player id to label, to count decisions per label")
    diff.add_argument("--show", type=int, default=20, help="How many moved players to list")
    diff.set_defaults(func=cmd_diff)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
