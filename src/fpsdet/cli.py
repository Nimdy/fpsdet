"""Command line. `python -m fpsdet demo` is the whole tour."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path

from .ai_triage import openai_compatible_transport, triage_case
from .baseline import build_cohorts, screen_matches
from .casefile import safe_name, write_case
from .challenge import CURRENT, Budget, ChallengeError, ChallengeRegistry, case_problems, plan_file_from_dict
from .challenge_plan import SECRET_ENV, SecretError, load_secret, new_secret_file, plan_match, reproduce
from .auth import AuthError, load_registry
from .external import ExternalError, load_adapter, read_external
from .lake import ingest_lines, read_lines
from .parse import iter_events, load_events, load_profile
from .provenance import ProvenanceMismatch
from .persist import (
    case_to_dict,
    cohort_from_dict,
    event_to_dict,
    cohort_to_dict,
    history_from_dict,
    history_to_dict,
    load_reports,
    read_json,
    write_json,
)
from .pipeline import run_score
from .priority import review_order, scan_order
from .summarize import summarize
from .board import write_board
from .ops import merge_payloads, ops_payload, read_ops, write_ops
from .opsview import render_dashboard
from .pages import write_pages
from .provenance import verify_packet
from .signals import poison_alarms
from .synthetic import build_demo, demo_rows
from .models import HistoryWindow


def _print_cases(cases) -> None:
    print(f"{'player':<22} {'decision':<18} {'reports':>7}  why")
    for case in review_order(cases):
        why = case.reasons[0] if case.reasons else (case.observations[0] if case.observations else "")
        print(f"{case.player_id:<22} {case.decision:<18} {case.reports:7d}  {why[:88]}")


def _write_outputs(cases, out: str, *, ai: bool, ops: dict | None = None) -> None:
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    if ai:
        _attach_ai(cases)
    for case in cases:
        write_case(folder, case)
    scan = [case_to_dict(case) for case in scan_order(cases)]
    review = [case_to_dict(case) for case in review_order(cases)]
    write_json(folder / "scan-index.json", {"order": "reports_first", "cases": scan})
    write_json(folder / "review-index.json", {"order": "evidence_first", "cases": review})
    _write_features(cases, folder / "features.csv")
    if ops is not None:
        write_ops(folder / "ops.json", ops)
        (folder / "dashboard.html").write_text(render_dashboard(ops), encoding="utf-8")


def _attach_ai(cases) -> None:
    base = os.environ.get("FPSDET_AI_BASE_URL", "")
    model = os.environ.get("FPSDET_AI_MODEL", "")
    key = os.environ.get("FPSDET_AI_API_KEY", "")
    if not base or not model:
        raise SystemExit("Set FPSDET_AI_BASE_URL and FPSDET_AI_MODEL. FPSDET_AI_API_KEY if the endpoint requires one.")
    transport = openai_compatible_transport(base, key, model)
    batch = [case.player_id for case in cases]
    for case in cases:
        if case.decision not in {"review", "watch"} and case.reports <= 0:
            continue
        case.ai_brief = triage_case(case_to_dict(case), transport, redact_ids=True, known_ids=batch)


def _write_features(cases, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "player_id",
                "decision",
                "recommended_action",
                "reports",
                "skill_band",
                "reason_count",
                "speed_sustained",
                "untrained",
            ],
        )
        writer.writeheader()
        for case in cases:
            writer.writerow(
                {
                    "player_id": case.player_id,
                    "decision": case.decision,
                    "recommended_action": case.recommended_action,
                    "reports": case.reports,
                    "skill_band": case.skill_band,
                    "reason_count": len(case.reasons),
                    "speed_sustained": bool(case.speed and case.speed.sustained),
                    "untrained": "|".join(case.untrained),
                }
            )


def _events_from_args(path: str | None, lake: str | None, game: str | None):
    if path and lake:
        raise SystemExit("Pass an events file or a lake, not both.")
    if lake:
        try:
            lines = read_lines(lake, game_id=game)
        except ValueError as error:
            raise SystemExit(str(error))
        return iter_events(lines)
    if not path:
        raise SystemExit("Pass an events file or --lake.")
    return load_events(path)


def _history_from_records(records) -> list[HistoryWindow]:
    windows = []
    for record in records:
        for weapon in record.weapons:
            windows.append(
                HistoryWindow(
                    record.player_id,
                    weapon.weapon_key,
                    weapon.skill_band,
                    weapon.shots,
                    weapon.hits,
                )
            )
    return windows


def cmd_demo(args: argparse.Namespace) -> int:
    demo = build_demo()
    desk = write_board(demo, Path(args.out) / "board.html" if args.out else None)
    print(demo_rows(demo))
    print()
    print(f"Review desk: {desk}")
    print("Open that file in a browser. The first tape is the 10 kg sprint beside the same sprint tagged as a blast.")
    if demo.failures:
        print("DEMO FAILED")
        for failure in demo.failures:
            print(f"- {failure}")
        return 2
    print("Planted cases matched profiles/example-loadout.json.")
    if args.out:
        _write_outputs(demo.cases, args.out, ai=False)
        print(f"Wrote case files to {args.out}")
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    demo = build_demo()
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = demo.population + demo.events
    with target.open("w", encoding="utf-8") as handle:
        for event in rows:
            handle.write(json.dumps(event_to_dict(event), separators=(",", ":")) + "\n")
    print(f"Wrote {len(rows)} events to {target}: a synthetic population and the planted players.")
    return 0


def cmd_pages(args: argparse.Namespace) -> int:
    demo = build_demo()
    if demo.failures:
        print("DEMO FAILED")
        for failure in demo.failures:
            print(f"- {failure}")
        return 2
    dest = write_pages(demo, args.out)
    print(f"Site: {dest}")
    print("The site pages and a fresh board.html are in that folder.")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    events, errors = _events_from_args(args.events, args.lake, args.game)
    for error in errors:
        print(error, file=sys.stderr)
    screen = screen_matches(events, profile)
    left_out: list[str] = []
    if args.screen_matches and screen.matches:
        dropped = set(screen.matches)
        events = [event for event in events if event.match_id not in dropped]
        left_out = screen.lines
    records = summarize(events, profile)
    table = build_cohorts(records, profile)
    alarms = [] if args.screen_matches else list(screen.lines)
    checked = screen.judged
    if args.previous:
        try:
            previous = cohort_from_dict(read_json(args.previous))
        except ProvenanceMismatch as error:
            raise SystemExit(f"{args.previous}: {error}")
        alarms += poison_alarms(
            previous,
            table,
            jump=profile.poison_jump,
            min_players=profile.min_cohort_players,
        )
        checked = True
    table.integrity = {"status": "poison_risk" if alarms else "ok" if checked else "unchecked", "alarms": alarms}
    if left_out:
        table.integrity["left_out"] = left_out
    for alarm in alarms:
        print(f"POISON RISK {alarm}", file=sys.stderr)
    for line in left_out:
        print(f"LEFT OUT {line}", file=sys.stderr)
    if screen.matches and not args.screen_matches:
        many = len(screen.matches) > 1
        print(
            f"{len(screen.matches)} {'matches look like lobbies' if many else 'match looks like a lobby'} where "
            f"cheaters played each other. Review {'them' if many else 'it'}, or pass --screen-matches to leave "
            f"{'them' if many else 'it'} out of the baseline.",
            file=sys.stderr,
        )
    write_json(args.out, cohort_to_dict(table))
    if args.history:
        write_json(args.history, history_to_dict(_history_from_records(records)))
    print(f"Wrote baseline {args.out} from {len(records)} players")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    events, errors = _events_from_args(args.events, args.lake, args.game)
    for error in errors:
        print(error, file=sys.stderr)
    try:
        cohort = cohort_from_dict(read_json(args.cohort)) if args.cohort else None
    except ProvenanceMismatch as error:
        raise SystemExit(f"{args.cohort}: {error}")
    if cohort is not None and cohort.integrity.get("status") == "poison_risk":
        print(
            "POISON RISK: this cohort may have learned a cheat. Review the alarms below, rebuild with "
            "--screen-matches, or freeze the last cohort you still trust.",
            file=sys.stderr,
        )
        for alarm in cohort.integrity.get("alarms") or []:
            print(f"- {alarm}", file=sys.stderr)
    history = history_from_dict(read_json(args.history)) if args.history else []
    reports = load_reports(read_json(args.reports)) if args.reports else {}
    challenges = None
    if args.challenges:
        try:
            challenges = ChallengeRegistry.from_files(plan_file_from_dict(read_json(path), path) for path in args.challenges)
        except ChallengeError as error:
            raise SystemExit(str(error))
    external = _external_from_args(args)
    if args.reported_only:
        wanted = {pid for pid, count in reports.items() if count > 0}
        events = [event for event in events if event.player_id in wanted]
    cases = run_score(events, profile, cohort, history, reports, challenges, external)
    _print_cases(cases)
    if args.out:
        table = cohort if cohort is not None else build_cohorts(summarize(events, profile), profile)
        ops = ops_payload(profile=profile, cases=cases, events=events, cohort=table)
        _write_outputs(cases, args.out, ai=args.ai, ops=ops)
        print(f"Wrote {len(cases)} cases to {args.out}, with ops.json and dashboard.html")
    elif args.ai:
        _attach_ai(cases)
        for case in review_order(cases):
            if case.ai_brief:
                print(f"\n{case.player_id}: {case.ai_brief}")
    return 0


def _external_sources(args: argparse.Namespace) -> list:
    sources = [(path, None) for path in args.external or []]
    sources += [(path, load_adapter(adapter)) for adapter, path in args.external_mapped or []]
    return sources


def _external_from_args(args: argparse.Namespace):
    """External records from --external (the native format) and --external-mapped (an adapter and its file),
    or None when neither was given. Signatures are checked against --external-registry when it is given.
    Bad lines and refused signatures are reported and skipped unless --external-strict."""
    try:
        sources = _external_sources(args)
        if not sources:
            if args.external_registry or args.require_signed_external:
                raise SystemExit("--external-registry and --require-signed-external need --external or --external-mapped records")
            return None
        if args.require_signed_external and not args.external_registry:
            raise SystemExit("--require-signed-external needs --external-registry: there is nothing to verify signatures against")
        registry = load_registry(args.external_registry) if args.external_registry else None
        external = read_external(sources, strict=args.external_strict, registry=registry, require_signed=args.require_signed_external)
    except (ExternalError, AuthError) as error:
        raise SystemExit(f"external evidence: {error}")
    for error in external.errors[:50]:
        print(f"external evidence: {error}", file=sys.stderr)
    if len(external.errors) > 50:
        print(f"external evidence: {len(external.errors) - 50} more lines not read", file=sys.stderr)
    states: dict[str, int] = {}
    for source in external.sources:
        for state, count in source.authentication.items():
            states[state] = states.get(state, 0) + count
    excluded = sum(source.excluded for source in external.sources)
    summary = ", ".join(f"{state} {count}" for state, count in sorted(states.items()))
    print(f"external evidence: {len(external.records)} records read; signatures: {summary or 'none'}"
          + (f"; {excluded} left out by --require-signed-external" if excluded else ""), file=sys.stderr)
    return external


def cmd_external_verify(args: argparse.Namespace) -> int:
    """Check external files' signatures and lines without scoring anything."""
    args.external_strict = False
    try:
        sources = _external_sources(args)
        if not sources:
            raise SystemExit("name at least one file with --external or --external-mapped")
        registry = load_registry(args.external_registry) if args.external_registry else None
        external = read_external(sources, registry=registry)
    except (ExternalError, AuthError) as error:
        raise SystemExit(f"external evidence: {error}")
    names = [path for path, _adapter in sources]
    for name, source in zip(names, external.sources):
        states = ", ".join(f"{state} {count}" for state, count in sorted(source.authentication.items())) or "none"
        print(f"{name}: {source.records} records, {source.duplicates} repeated, {source.errors} not read; signatures: {states}")
    if registry is None:
        print("No registry given (--external-registry): signatures were not checked.")
    else:
        print(f"Checked against registry {registry.digest} ({len(registry)} keys).")
    for error in external.errors[:50]:
        print(f"  {error}")
    return 1 if external.errors else 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    folders = [Path(folder) for folder in args.folders]
    try:
        payloads = [read_ops(folder) for folder in folders]
    except FileNotFoundError as error:
        raise SystemExit(str(error))
    payload = payloads[0] if len(payloads) == 1 else merge_payloads(payloads, [folder.name for folder in folders])
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dashboard(payload), encoding="utf-8")
    nights = f" across {len(folders)} runs" if len(folders) > 1 else ""
    print(f"Wrote {target}: {len(payload['rows'])} players{nights}. Open it in a browser; it needs no network.")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    lines = Path(args.events).read_text(encoding="utf-8").splitlines()
    try:
        result = ingest_lines(lines, args.lake, default_dt=args.dt)
    except ValueError as error:
        raise SystemExit(str(error))
    print(f"Wrote {result['written']} events, skipped {result['skipped']}")
    return 0


def _secret(path: str | None):
    try:
        secret, warnings = load_secret(path)
    except SecretError as error:
        raise SystemExit(str(error))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return secret


def cmd_challenge_keygen(args: argparse.Namespace) -> int:
    try:
        new_secret_file(args.out)
    except FileExistsError:
        raise SystemExit(f"{args.out} exists; fpsdet does not overwrite a secret")
    print(f"Wrote a new challenge secret to {args.out}, readable by its owner only. Keep it on the game server.")
    return 0


def cmd_challenge_plan(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    secret = _secret(args.secret_file)
    budget = Budget(
        to_ms=args.to_ms,
        count=args.count,
        from_ms=args.from_ms,
        cooldown_ms=args.cooldown_ms,
        min_duration_ms=args.min_duration_ms,
        max_duration_ms=args.max_duration_ms,
    )
    try:
        planned = plan_match(secret, profile, args.match, args.player, budget, nonce=args.nonce)
    except ChallengeError as error:
        raise SystemExit(str(error))
    write_json(args.out, planned.to_dict())
    players = len({plan.subject_id for plan in planned.plans})
    print(f"Wrote {len(planned.plans)} challenges for {players} players in {planned.match_id} to {args.out}.")
    print("It holds no key and no realization. Keep it on the server until the match is over.")
    return 0


def _read_cases(path: str) -> list[dict]:
    """Cases from a case file, an index written by score --out, a JSON list, or one case per line."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data["cases"]
    return data if isinstance(data, list) else [data]


def cmd_challenge_verify(args: argparse.Namespace) -> int:
    files, failed = [], False
    for path in args.plans:
        try:
            plan_file = plan_file_from_dict(read_json(path), str(path))
        except (OSError, ValueError) as error:
            print(f"FAIL {error}")
            failed = True
            continue
        files.append(plan_file)
        players = len({plan.subject_id for plan in plan_file.plans})
        print(f"{path}: {len(plan_file.plans)} challenges for {players} players in {plan_file.match_id}; digests and schedule consistent")
    try:
        registry = ChallengeRegistry.from_files(files)
    except ChallengeError as error:
        print(f"FAIL {error}")
        failed, registry = True, ChallengeRegistry()
    for path in args.cases or []:
        try:
            cases = _read_cases(path)
        except (OSError, ValueError) as error:
            print(f"FAIL {path}: {error}")
            failed = True
            continue
        findings = problems = 0
        for case in cases:
            planned = [
                obs for obs in (case.get("evidence") or {}).get("observations") or []
                if obs.get("family") == "challenge" and ((obs.get("evidence") or {}).get("challenge") or {}).get("origin") == "planned"
            ]
            if not planned:
                continue
            findings += len(planned)
            # The finding matches its plan, and the packet that binds the finding is intact.
            found = case_problems(case, registry) + [f"{case.get('player_id')}: {problem}" for problem in verify_packet(case)]
            for problem in found:
                print(f"FAIL {path}: {problem}")
            problems += len(found)
        failed = failed or bool(problems)
        print(f"{path}: {len(cases)} cases, {findings} planned challenge findings, {'every one matches its plan' if not problems else f'{problems} problems'}")
    if args.secret_file or os.environ.get(SECRET_ENV):
        secret = _secret(args.secret_file)
        for path, plan_file in zip(args.plans, files):
            problems = reproduce(secret, plan_file)
            if problems:
                failed = True
                print(f"FAIL {path}: the secret does not plan this file")
                for problem in problems[:20]:
                    print(f"  {problem}")
            else:
                print(f"{path}: reproduced from the secret, {len(plan_file.plans)} of {len(plan_file.plans)}")
    else:
        print(f"Not reproduced: no secret given (--secret-file or {SECRET_ENV}). Only the server can show its secret plans these challenges.")
    return 1 if failed else 0


def cmd_challenge_types(args: argparse.Namespace) -> int:
    print(json.dumps([spec.to_dict() for spec in CURRENT.values()], indent=2))
    return 0


def cmd_evaluate_run(args: argparse.Namespace) -> int:
    """Measure the detectors of one scored run against its labels. Reads cases; scores and changes nothing."""
    import time

    from .calibration import EvaluationError, census, dumps, evaluate, load_cases, load_dataset, render_markdown

    start = time.perf_counter()
    try:
        dataset = load_dataset(args.dataset)
        cases = load_cases(args.cases)
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        found = census(args.events) if args.events else None
        artifact = evaluate(cases, labels, dataset, load_profile(args.profile), telemetry_census=found)
    except EvaluationError as error:
        raise SystemExit(f"evaluate: {error}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(dumps(artifact), encoding="utf-8")
    if args.report:
        Path(args.report).write_text(render_markdown(artifact), encoding="utf-8")
    stats = artifact["statistics"]
    statuses = Counter(entry["status"] for entry in stats["detectors"])
    print(f"Evaluated {artifact['inputs']['cases']} cases against {len(labels)} labels in {time.perf_counter() - start:.2f}s")
    print("Detectors: " + ", ".join(f"{status} {statuses[status]}" for status in ("measured", "descriptive_only", "insufficient_sample", "not_observable")))
    if artifact["published"]:
        print(f"The decisions published in {artifact['published']['where']} reproduce.")
    print(f"Wrote {args.out}" + (f" and {args.report}" if args.report else "") + f": {artifact['digest']}")
    return 0


def cmd_evaluate_report(args: argparse.Namespace) -> int:
    from .calibration import render_markdown

    text = render_markdown(read_json(args.evaluation))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_evaluate_verify(args: argparse.Namespace) -> int:
    from .calibration import evaluator_digest, verify_artifact

    failed = 0
    for path in args.evaluations:
        artifact = read_json(path)
        problems = verify_artifact(artifact)
        same = artifact.get("evaluator", {}).get("digest") == evaluator_digest()
        note = "" if same else " (written by another version of the evaluator; this one recomputes the same statistics)"
        print(f"{path}: {'ok' if not problems else 'FAILED'}{note if not problems else ''}")
        for problem in problems:
            print(f"  {problem}")
        failed += bool(problems)
    return 1 if failed else 0


def cmd_evaluate_fixtures(args: argparse.Namespace) -> int:
    from .calibration import qualify_fixtures, render_fixtures

    result = qualify_fixtures()
    if args.out:
        write_json(args.out, result)
    print(render_fixtures(result))
    failed = [entry["kind"] for entry in result["detectors"] if entry["outcome"] == "fails_controlled_fixture"]
    return 1 if failed or result["demo_failures"] or result["fixture_failures"] or not all(result["honest_fixtures_clean"].values()) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpsdet", description="Human-play and gear-rule baselines for FPS servers.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="Run the planted matches and print decisions")
    demo.add_argument("--out", help="Also write HTML and JSON cases here")
    demo.set_defaults(func=cmd_demo)

    sample = sub.add_parser("sample", help="Write the planted week as NDJSON, to try ingest, baseline, and score")
    sample.add_argument("--out", required=True, help="NDJSON file to write")
    sample.set_defaults(func=cmd_sample)

    pages = sub.add_parser("pages", help="Write the public pages and a fresh desk into one folder")
    pages.add_argument("--out", help="Destination folder. Default is ./_site")
    pages.set_defaults(func=cmd_pages)

    baseline = sub.add_parser("baseline", help="Train a cohort file from a clean window")
    baseline.add_argument("events", nargs="?", help="NDJSON events")
    baseline.add_argument("--lake")
    baseline.add_argument("--game")
    baseline.add_argument("--profile", required=True)
    baseline.add_argument("--out", required=True)
    baseline.add_argument("--history", help="Also write per-player shot totals for the next window")
    baseline.add_argument("--previous", help="Prior cohort. A ceiling that jumped is stamped poison_risk")
    baseline.add_argument(
        "--screen-matches",
        action="store_true",
        help="Leave out matches whose whole lobby is far past the window's median match. Without it they are only flagged",
    )
    baseline.set_defaults(func=cmd_baseline)

    score = sub.add_parser("score", help="Score events into cases. Does not ban.")
    score.add_argument("events", nargs="?", help="NDJSON events")
    score.add_argument("--lake")
    score.add_argument("--game")
    score.add_argument("--profile", required=True)
    score.add_argument("--cohort", help="Frozen baseline from `fpsdet baseline`")
    score.add_argument("--history")
    score.add_argument("--reports", help="JSON map of player_id to report count. Priority, not proof.")
    score.add_argument("--reported-only", action="store_true", help="Only score players who have reports")
    score.add_argument("--challenges", action="append", help="A public challenge plan file. Repeat for more matches")
    score.add_argument("--external", action="append", help="External records in the fpsdet.external/1 format. Repeat for more files")
    score.add_argument(
        "--external-mapped", nargs=2, action="append", metavar=("ADAPTER", "RECORDS"),
        help="A provider's records and the fpsdet.external-adapter/1 file that maps them. Repeat for more",
    )
    score.add_argument("--external-strict", action="store_true", help="Stop on the first external line that cannot be read")
    score.add_argument("--external-registry", help="The operator's provider-key registry (fpsdet.provider-registry/1); signatures are checked against it")
    score.add_argument(
        "--require-signed-external", action="store_true",
        help="Read only external records a registered key signed. Native scoring is unaffected",
    )
    score.add_argument("--out")
    score.add_argument("--ai", action="store_true", help="Attach a brief from any OpenAI-compatible endpoint")
    score.set_defaults(func=cmd_score)

    dashboard = sub.add_parser(
        "dashboard",
        help="Render the operations view from score --out folders. Several folders, oldest first, become one week",
    )
    dashboard.add_argument("folders", nargs="+", help="Folders written by fpsdet score --out")
    dashboard.add_argument("--out", default="dashboard.html", help="HTML file to write")
    dashboard.set_defaults(func=cmd_dashboard)

    ingest = sub.add_parser("ingest", help="Append NDJSON into the lake")
    ingest.add_argument("events")
    ingest.add_argument("--lake", required=True)
    ingest.add_argument("--dt", help="UTC day YYYY-MM-DD when events have no utc field")
    ingest.set_defaults(func=cmd_ingest)

    challenge = sub.add_parser("challenge", help="Plan and check server challenges. The secret stays on the server")
    actions = challenge.add_subparsers(dest="action", required=True)
    keygen = actions.add_parser("keygen", help="Write a new random challenge secret to a new file, owner-only")
    keygen.add_argument("--out", required=True)
    keygen.set_defaults(func=cmd_challenge_keygen)
    plan = actions.add_parser("plan", help="Write one match's public challenge plan: ids, windows, commitments")
    plan.add_argument("--profile", required=True)
    plan.add_argument("--match", required=True)
    plan.add_argument("--player", action="append", required=True, help="A player to challenge. Repeat for more")
    plan.add_argument("--to-ms", type=int, required=True, help="The latest a challenge may end, in match time")
    plan.add_argument("--from-ms", type=int, default=Budget.from_ms, help="No challenge before this. Default 60000")
    plan.add_argument("--count", type=int, default=Budget.count, help="Challenges per player in this match, at most 4. Default 1")
    plan.add_argument("--cooldown-ms", type=int, default=Budget.cooldown_ms, help="At least this long between challenges. Default 60000")
    plan.add_argument("--min-duration-ms", type=int, default=Budget.min_duration_ms)
    plan.add_argument("--max-duration-ms", type=int, default=Budget.max_duration_ms)
    plan.add_argument("--nonce", help="Reuse a plan's nonce to reproduce it. Default: a fresh random one")
    plan.add_argument("--secret-file", help=f"Hex secret file; /dev/fd/N works. Default: {SECRET_ENV}")
    plan.add_argument("--out", required=True)
    plan.set_defaults(func=cmd_challenge_plan)
    verify = actions.add_parser("verify", help="Check plan files; with the secret, check the secret plans them")
    verify.add_argument("plans", nargs="+")
    verify.add_argument("--secret-file", help=f"Hex secret file. Default: {SECRET_ENV}, if set")
    verify.add_argument("--cases", action="append", help="Cases to check against the plans: a case file, a score --out index, or JSON lines")
    verify.set_defaults(func=cmd_challenge_verify)
    types = actions.add_parser("types", help="Print the challenge types, their requirements and limits")
    types.set_defaults(func=cmd_challenge_types)

    external = sub.add_parser("external", help="Check external records and their signatures. Scores nothing")
    external_actions = external.add_subparsers(dest="action", required=True)
    check = external_actions.add_parser("verify", help="Read external files and report every line's signature state")
    check.add_argument("--external", action="append", help="Records in the fpsdet.external/1 format. Repeat for more")
    check.add_argument("--external-mapped", nargs=2, action="append", metavar=("ADAPTER", "RECORDS"), help="A provider's records and their adapter")
    check.add_argument("--external-registry", help="The provider-key registry to check signatures against")
    check.set_defaults(func=cmd_external_verify)

    evaluate = sub.add_parser("evaluate", help="Measure detectors against labelled cases. Scores nothing and changes no threshold")
    evaluate_actions = evaluate.add_subparsers(dest="action", required=True)
    run = evaluate_actions.add_parser("run", help="Evaluate one scored run against its labels and write fpsdet.evaluation/1")
    run.add_argument("cases", help="The run's cases: a snapshot (one per line), a JSON list, or the index fpsdet score --out writes")
    run.add_argument("--labels", required=True, help="JSON map of player id to label")
    run.add_argument("--dataset", required=True, help="The dataset definition (fpsdet.evaluation-dataset/1): what each label means")
    run.add_argument("--profile", required=True, help="The profile the cases were scored with. Its digest must match theirs")
    run.add_argument("--events", help="The scored events, to check the dataset's telemetry declaration against every line")
    run.add_argument("--out", required=True, help="Where to write the evaluation JSON")
    run.add_argument("--report", help="Also write the Markdown report here")
    run.set_defaults(func=cmd_evaluate_run)
    report = evaluate_actions.add_parser("report", help="Print or write the Markdown report of an evaluation")
    report.add_argument("evaluation")
    report.add_argument("--out")
    report.set_defaults(func=cmd_evaluate_report)
    check_evaluation = evaluate_actions.add_parser("verify", help="Recompute an evaluation's statistics from its rows and check its digest")
    check_evaluation.add_argument("evaluations", nargs="+")
    check_evaluation.set_defaults(func=cmd_evaluate_verify)
    fixtures = evaluate_actions.add_parser("fixtures", help="Controlled-fixture qualification on the planted demo. Never a real-world rate")
    fixtures.add_argument("--out", help="Also write the result as JSON")
    fixtures.set_defaults(func=cmd_evaluate_fixtures)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
