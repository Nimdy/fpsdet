"""Snapshot a scored run and compare two snapshots case by case. Standard library only.

The real-match runs in examples/ are too large to commit, so their regression check runs where the data
is. Snapshot the run before a change, snapshot it again after, and diff:

    PYTHONPATH=src python tools/regress.py snapshot scored.ndjson --profile examples/tf2/tf2.json \\
        --cohort cohort.json --out before.jsonl
    # ...change the scorer...
    PYTHONPATH=src python tools/regress.py snapshot ... --out after.jsonl
    python tools/regress.py diff before.jsonl after.jsonl --labels labels.json
    PYTHONPATH=src python tools/regress.py verify after.jsonl

A snapshot is one line per player: the case exactly as ``fpsdet score`` writes it, with floats cut to 10
significant digits so the last bit of a float sum cannot differ between Python versions. ``diff`` compares
the fields the first snapshot has. A field only the second one has is listed and allowed: that is how new
evidence is added without moving what consumers read. Any other change is printed by field and by player,
and the exit code is 1. With labels, decisions are also counted per label, the way the README reports them.
``verify`` checks that every case's structured evidence implies its decision and backs each check and reason,
that every case of the run carries the same detector, profile and cohort provenance, and that each has its own
input digest.
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


def subsumes(old, new) -> bool:
    """Is ``new`` the same as ``old``, give or take keys added inside objects?"""
    if isinstance(old, dict):
        return isinstance(new, dict) and all(key in new and subsumes(value, new[key]) for key, value in old.items())
    if isinstance(old, list):
        return isinstance(new, list) and len(old) == len(new) and all(subsumes(a, b) for a, b in zip(old, new))
    return old == new


def added_paths(old, new, prefix: str = "") -> set[str]:
    """Where ``new`` has keys ``old`` does not, as dotted paths. List positions read as []."""
    found: set[str] = set()
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in new.items():
            path = f"{prefix}.{key}" if prefix else key
            found |= {path} if key not in old else added_paths(old[key], value, path)
    elif isinstance(old, list) and isinstance(new, list):
        for a, b in zip(old, new):
            found |= added_paths(a, b, f"{prefix}[]")
    return found


def compare(before: dict[str, dict], after: dict[str, dict]) -> dict:
    """What moved between two snapshots, on the fields the first one has. Keys added at any depth are allowed."""
    gone = sorted(set(before) - set(after))
    new = sorted(set(after) - set(before))
    fields: Counter = Counter()
    moved: dict[str, list[str]] = {}
    transitions: Counter = Counter()
    added: Counter = Counter()
    for pid in sorted(set(before) & set(after)):
        old, now = before[pid], after[pid]
        changed = [key for key in old if key not in now or not subsumes(old[key], now[key])]
        if changed:
            moved[pid] = changed
            fields.update(changed)
        if old.get("decision") != now.get("decision"):
            transitions[(old.get("decision"), now.get("decision"))] += 1
        added.update(added_paths(old, now))
    return {"gone": gone, "new": new, "moved": moved, "fields": fields, "transitions": transitions, "added": added}


def without(rows: dict[str, dict], paths: list[str]) -> dict[str, dict]:
    """The rows with each dotted path removed, such as evidence.provenance.detector, which moves with any code change."""
    if not paths:
        return rows
    out = {}
    for pid, row in rows.items():
        row = json.loads(json.dumps(row))
        for path in paths:
            *parents, last = path.split(".")
            node = row
            for name in parents:
                node = node.get(name) if isinstance(node, dict) else None
            if isinstance(node, dict):
                node.pop(last, None)
        out[pid] = row
    return out


def by_label(rows: dict[str, dict], labels: dict[str, str]) -> dict[str, Counter]:
    table: dict[str, Counter] = {}
    for pid, row in rows.items():
        table.setdefault(labels.get(pid, "unlabelled"), Counter())[row["decision"]] += 1
    return table


def cmd_diff(args: argparse.Namespace) -> int:
    before, after = read_snapshot(args.before), read_snapshot(args.after)
    before, after = without(before, args.ignore), without(after, args.ignore)
    if args.ignore:
        print("Ignored: " + ", ".join(args.ignore))
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


def verify(rows: dict[str, dict]) -> dict:
    """Does each case's structured evidence explain its decision, checks and reasons?"""
    from fpsdet.evidence import KINDS, implied_decision

    result = {"cases": len(rows), "explained": 0, "problems": [], "quiet_watches": 0, "quiet_watches_explained": 0, "kinds": Counter(), "provenance": Counter(), "inputs": 0}
    for pid, row in sorted(rows.items()):
        block = row.get("evidence")
        if block is None:
            result["problems"].append(f"{pid}: no evidence")
            continue
        observations = block["observations"]
        result["kinds"].update(obs["kind"] for obs in observations)
        stamp = dict(block.get("provenance") or {})
        inputs = stamp.pop("inputs", None)
        result["provenance"][json.dumps(stamp, sort_keys=True) if stamp else "none"] += 1
        if inputs and str(inputs.get("digest", "")).startswith("sha256:") and inputs.get("events", 0) > 0:
            result["inputs"] += 1
        else:
            result["problems"].append(f"{pid}: no input digest for this player's events")
        problems = []
        if implied_decision(block) != row["decision"]:
            problems.append(f"{pid}: decision {row['decision']}, evidence implies {implied_decision(block)}")
        if {KINDS[obs["kind"]][1] for obs in observations} != set(row["checks"]):
            problems.append(f"{pid}: checks do not match the evidence")
        if sorted(obs["context"]["line"] for obs in observations if obs["context"]["printed_in"] == "reasons") != sorted(row["reasons"]):
            problems.append(f"{pid}: reasons do not match the evidence")
        if row["decision"] == "watch" and not row["reasons"]:
            result["quiet_watches"] += 1
            result["quiet_watches_explained"] += int(not problems)
        result["problems"] += problems
        result["explained"] += int(not problems)
    return result


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify(read_snapshot(args.snapshot))
    print(f"{result['explained']} of {result['cases']} cases explained by their evidence")
    print(f"Watches with no reason line: {result['quiet_watches']}, explained by evidence: {result['quiet_watches_explained']}")
    print("Observations by kind: " + ", ".join(f"{kind} {n}" for kind, n in result["kinds"].most_common()))
    for stamp, n in result["provenance"].most_common():
        if stamp == "none":
            print(f"Provenance: none on {n} cases")
            continue
        block = json.loads(stamp)
        print(f"Provenance on {n} cases: profile {block['profile']['digest']}, detector {block['detector']['digest']} ({len(block['detector']['modules'])} modules)")
        cohort = block.get("cohort") or {}
        print(f"  cohort {cohort.get('mode')} {cohort.get('digest')}, stored digest {cohort.get('stored_digest')}, integrity {(cohort.get('integrity') or {}).get('status')}")
    print(f"Cases with their own input digest: {result['inputs']} of {result['cases']}")
    if len(result["provenance"]) != 1 or "none" in result["provenance"]:
        result["problems"].append("the cases of one run do not share one detector, profile and cohort provenance")
    for problem in result["problems"][: args.show]:
        print(f"  {problem}")
    return 0 if not result["problems"] else 1


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
    diff.add_argument("--ignore", action="append", default=[], help="A dotted path to leave out of both, such as evidence.provenance.detector. Repeatable")
    diff.set_defaults(func=cmd_diff)
    check = sub.add_parser("verify", help="Check that each case's evidence explains its decision, checks and reasons. Needs PYTHONPATH=src")
    check.add_argument("snapshot")
    check.add_argument("--show", type=int, default=20)
    check.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
