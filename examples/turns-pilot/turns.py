"""occluded_motion_replay/3 in a live Godot dedicated server: machine stand-ins, never people.

The human pilot's machine dry run found a stand-in holding the doorway's frame, over a probe in the sealed
room behind it, reaching version 2's bar without knowing anything. Version 3 turns the body at secret times
and asks whether the aim turned with it (docs/challenges.md). This runs the same arena, bots and stock client
under version 3, with the same scripted stand-ins and the same controlled follower, and checks:

- what each stand-in's challenges showed under version 3, and how long its aim sat on the body anyway (the
  time version 2 would have counted);
- that every turn the server ran, and every turn event it sent, is what the secret plans
  (fpsdet.challenge_plan.turn_schedule), within one server tick;
- that no public file carries the secret or a realization.

    python examples/pilot/pilot.py godot --dest ~/godot-4.7.2
    python examples/turns-pilot/turns.py run --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64 --data /tmp/turns [--publish]

Nothing here is a person, a deployment or a rate. The human pilot's files (study.tscn, study_server.gd,
study.py) and the P12 pilot's are untouched; this uses turns.tscn and scripts/turns_server.gd.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples" / "pilot"))

import pilot  # noqa: E402  (P12's harness: fpsdet calls, the Godot environment, the leak scan)

PROJECT = ROOT / "examples" / "pilot" / "godot"
PROFILE = ROOT / "examples" / "human-pilot" / "profile.json"
RESULT = HERE / "result.json"
CAPTURED = HERE / "captured"
FORMAT = "fpsdet.turns-pilot/1"
LENGTH_MS = 240_000
# The human pilot's count, play window and cooldown, with longer windows: each turn's time is secret only within its slot
# less 1,500 ms, and at 12 to 16 s that is 400 to 1,000 ms, about one reaction. 18 to 24 s gives 1,300 to 2,300.
SCHEDULE = {"count": 4, "from_ms": 20_000, "to_ms": 230_000, "cooldown_ms": 30_000, "min_duration_ms": 18_000, "max_duration_ms": 24_000}
TICK_MS = 1000 / 60
# Each scenario: the study mode (which bots), the client's stand-in, and whether the server's controlled follower aims.
SCENARIOS = {
    "holder": ("angle_holding", "holder", False),
    "sweeper": ("sweep_search", "sweeper", False),
    "tracker": ("tracking", "tracker", False),
    "follower": ("angle_holding", "holder", True),
}
CODE = ("turns.tscn", "turns.gd", "scripts/turns_server.gd", "scripts/turns_recipe.gd", "scripts/study_server.gd", "scripts/study_client.gd",
        "scripts/study_arena.gd", "scripts/arena.gd", "scripts/recipe.gd")


def code_identity() -> dict:
    from fpsdet.provenance import normalized_source

    files = {f"examples/pilot/godot/{name}": "sha256:" + hashlib.sha256(normalized_source((PROJECT / name).read_bytes())).hexdigest() for name in CODE}
    files["examples/turns-pilot/turns.py"] = "sha256:" + hashlib.sha256(normalized_source(Path(__file__).read_bytes())).hexdigest()
    return files


def plan(run: Path, match_id: str, participant: str) -> tuple[Path, Path]:
    public, private = run / "public", run / "private"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    secret = private / "secret.hex"
    pilot.fpsdet("challenge", "keygen", "--out", str(secret))
    out = public / "plan.json"
    pilot.fpsdet("challenge", "plan", "--profile", str(PROFILE), "--match", match_id, "--player", participant, "--version", "3",
                 *[f"--{key.replace('_', '-')}={value}" for key, value in SCHEDULE.items()], "--secret-file", str(secret), "--out", str(out))
    return out, secret


def session(godot: str, data: Path, name: str, repeat: int, port: int) -> dict:
    mode, stand_in, follower = SCENARIOS[name]
    participant = "hp-" + secrets.token_hex(4)
    run = data / f"{name}-{repeat}"
    match_id = f"turns-{participant}-{name}-{repeat}"
    plan_path, secret = plan(run, match_id, participant)
    public = run / "public"
    environment = pilot.godot_env(run)
    server = [godot, "--headless", "--path", str(PROJECT), "res://turns.tscn", "--", "--role=server", f"--participant={participant}", f"--mode={mode}",
              f"--match={match_id}", f"--match-ms={LENGTH_MS}", f"--port={port}", "--bind=127.0.0.1", f"--plan={plan_path}", f"--secret-file={secret}",
              f"--events={public / 'events.ndjson'}", f"--log={public / 'server.log'}", f"--private={run / 'private'}", f"--perf={public / 'perf.json'}"]
    if follower:
        server.append("--follower=true")
    client = [godot, "--headless", "--path", str(PROJECT), "res://turns.tscn", "--", "--role=client", f"--participant={participant}", f"--port={port}",
              "--server=127.0.0.1", f"--match-ms={LENGTH_MS}", "--instructions=machine stand-in", f"--log={public / 'client.jsonl'}",
              "--input=standin", f"--behaviour={stand_in}"]
    processes = [subprocess.Popen(server, env=environment, stdout=(run / "server.out").open("w"), stderr=subprocess.STDOUT)]
    time.sleep(1.5)
    processes.append(subprocess.Popen(client, env=environment, stdout=(run / "client.out").open("w"), stderr=subprocess.STDOUT))
    codes = []
    for process in processes:
        try:
            codes.append(process.wait(timeout=LENGTH_MS / 1000 + 300))
        except subprocess.TimeoutExpired:
            process.kill()
            codes.append("timeout")
    return check(run, plan_path, secret, participant, name, stand_in, follower, codes)


def turn_events(events: Path, participant: str) -> dict[str, list[tuple[int, int, int]]]:
    out: dict[str, list[tuple[int, int, int]]] = {}
    for line in events.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("player_id") == participant and row.get("challenge_turn_index") is not None:
            out.setdefault(row["challenge_id"], []).append((row["challenge_turn_index"], row["t_ms"], row["challenge_turn_sign"]))
    return out


def turns_check(run: Path, plan_path: Path, secret: Path, events: Path, participant: str) -> list[str]:
    """Every turn the server ran and every turn event it sent, against what the secret plans. A turn fires on
    the first server tick at or after its time, so it may land up to one tick late, never early."""
    from fpsdet.challenge import plan_file_from_dict
    from fpsdet.challenge_plan import load_secret, realize, turn_schedule

    key, _ = load_secret(secret, environ={})
    plan_file = plan_file_from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
    sent = turn_events(events, participant)
    problems = []
    for record in plan_file.plans:
        want = turn_schedule(record, realize(key, record, plan_file.budget))
        ran_path = run / "private" / f"realization-{record.challenge_id}.json"
        if not ran_path.exists():
            problems.append(f"{record.challenge_id}: the server ran no realization")
            continue
        record_ran = json.loads(ran_path.read_text(encoding="utf-8"))
        ran = record_ran["parameters"]
        if [list(turn) for turn in ran["schedule"]] != [list(turn) for turn in want]:
            problems.append(f"{record.challenge_id}: the server's schedule is not what the secret plans")
        for index, t_ms, sign in ran.get("fired", []):
            planned_ms, planned_sign = want[index]
            if sign != planned_sign or not 0 <= t_ms - planned_ms <= TICK_MS + 1:
                problems.append(f"{record.challenge_id}: turn {index} ran off its plan")
        for index, t_ms, sign in sent.get(record.challenge_id, []):
            planned_ms, planned_sign = want[index]
            if sign != planned_sign or not 0 <= t_ms - planned_ms <= TICK_MS + 1:
                problems.append(f"{record.challenge_id}: turn event {index} is not the planned turn")
        if record_ran["ended"] == "window over" and len(sent.get(record.challenge_id, [])) != len(want):
            problems.append(f"{record.challenge_id}: {len(sent.get(record.challenge_id, []))} turn events for {len(want)} turns")
    return problems


def time_on_body(events: Path, participant: str) -> dict[str, float]:
    """challenge_track_ms summed per challenge: the time version 2's bar would have counted."""
    out: dict[str, float] = {}
    for line in events.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("player_id") == participant and row.get("challenge_id") and row.get("challenge_turn_index") is None:
            out[row["challenge_id"]] = out.get(row["challenge_id"], 0.0) + float(row.get("challenge_track_ms") or 0.0)
    return out


def case_check(case: dict, plan_path: Path) -> list[str]:
    from fpsdet.challenge import ChallengeRegistry, case_problems, plan_file_from_dict
    from fpsdet.graph import verify_graph
    from fpsdet.provenance import verify_packet

    registry = ChallengeRegistry.from_files([plan_file_from_dict(json.loads(plan_path.read_text(encoding="utf-8")))])
    return verify_packet(case) + [f"graph: {problem}" for problem in verify_graph(case)] + case_problems(case, registry)


def check(run: Path, plan_path: Path, secret: Path, participant: str, name: str, stand_in: str, follower: bool, codes: list) -> dict:
    public = run / "public"
    events = public / "events.ndjson"
    cases = run / "cases"
    pilot.fpsdet("score", str(events), "--profile", str(PROFILE), "--challenges", str(plan_path), "--out", str(cases))
    case = next(found for found in (json.loads(path.read_text(encoding="utf-8")) for path in sorted(cases.glob("*.json")))
                if isinstance(found, dict) and found.get("player_id") == participant and "evidence" in found)
    rooms = {}
    for path in (run / "private").glob("realization-*.json"):
        ran = json.loads(path.read_text(encoding="utf-8"))
        rooms[ran["challenge_id"]] = {"room": ran["room"], "ended": ran["ended"]}
    on_body = time_on_body(events, participant)
    challenges = []
    for row in case["evidence"].get("challenges", []):
        challenges.append({
            "room": rooms.get(row["challenge_id"], {}).get("room"),
            "ended": rooms.get(row["challenge_id"], {}).get("ended"),
            "status": row["status"],
            "cause": row.get("cause"),
            "turns_counted": len(row.get("turns", [])),
            "followed_turns": row.get("followed_turns"),
            "p_value": row.get("p_value"),
            "time_on_body_ms": round(on_body.get(row["challenge_id"], 0.0), 1),
        })
    found = {
        "scenario": name,
        "kind": "controlled_follower" if follower else "machine_standin",
        "stand_in": stand_in,
        "exit_codes": codes,
        "session": run.name,
        "events_sha256": pilot.sha256_file(events),
        "decision": case["decision"],
        "challenges": challenges,
        "case_problems": case_check(case, plan_path),
        "inputs": case["evidence"]["provenance"]["inputs"]["recipe"],
        "turns_check": turns_check(run, plan_path, secret, events, participant),
        "leaks": pilot.leaks(sorted(public.iterdir()) + sorted(cases.rglob("*")), plan_path, secret),
    }
    secret.unlink()
    return found


# The sessions whose public half is committed, as the human pilot's dry run committed two. Every other session
# is in result.json by the digest of its events.
PUBLISHED = ("holder-1", "follower-1")


def publish(data: Path, results: list[dict]) -> None:
    """The public half of the published sessions, as captured: the plan and the server's telemetry. Never the private folder."""
    if CAPTURED.exists():
        shutil.rmtree(CAPTURED)
    for run in sorted(data / name for name in PUBLISHED):
        dest = CAPTURED / run.name
        dest.mkdir(parents=True)
        for name in ("plan.json", "events.ndjson"):
            shutil.copyfile(run / "public" / name, dest / name)


def cmd_run(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    names = args.only or list(SCENARIOS)
    jobs = [(name, repeat) for name in names for repeat in range(1, args.repeat + 1)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(session, args.godot, data, name, repeat, args.port + offset) for offset, (name, repeat) in enumerate(jobs)]
        results = [future.result() for future in futures]
    result = {
        "format": FORMAT,
        "statement": "Machine stand-ins and a controlled follower in a live Godot dedicated server. Not people, not a deployment, not a rate.",
        "engine": pilot.qualification()["engine"]["version"],
        "rule": json.loads(pilot.fpsdet("challenge", "types").stdout)[2]["turns"],
        "schedule": SCHEDULE,
        "code": code_identity(),
        "sessions": results,
    }
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    (data / "result.json").write_text(text, encoding="utf-8")
    if args.publish and args.only:
        raise SystemExit("--publish needs every scenario")
    if args.publish:
        RESULT.write_text(text, encoding="utf-8")
        publish(data, results)
    print(json.dumps([{key: row[key] for key in ("scenario", "decision", "turns_check", "leaks", "case_problems")} | {"challenges": [
        (c["room"], c["status"], c["followed_turns"], c["p_value"], c["time_on_body_ms"]) for c in row["challenges"]]} for row in results], indent=1))
    bad = [row["scenario"] for row in results if row["turns_check"] or row["leaks"] or row["case_problems"]]
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run every scenario, score it, and check the turns and the public files")
    run.add_argument("--godot", required=True)
    run.add_argument("--data", required=True, help="Where the sessions go; private folders stay here")
    run.add_argument("--repeat", type=int, default=1, help="Sessions per scenario")
    run.add_argument("--parallel", type=int, default=4)
    run.add_argument("--port", type=int, default=24900)
    run.add_argument("--only", action="append", choices=sorted(SCENARIOS), help="Run only this scenario; repeat for more")
    run.add_argument("--publish", action="store_true", help="Write result.json and the public captures into examples/turns-pilot")
    run.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
