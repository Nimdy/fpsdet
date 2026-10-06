"""The consented honest-human pilot's harness (docs/human-pilot.md, design.json).

Everything runs on the operator's machine with the repository's own Godot pilot; the server binds 127.0.0.1,
or a private LAN address given with --bind for a participant on another machine. Nothing else is touched.

    python examples/human-pilot/study.py enroll --data ~/study --agree [--publish]       # a random participant id
    python examples/human-pilot/study.py session --data ~/study --participant hp-... --mode free --godot PATH
    python examples/human-pilot/study.py analyze --data ~/study --out examples/human-pilot/result.json
    python examples/human-pilot/study.py dry-run --data /tmp/dry --godot PATH         # machine stand-ins, never people

A session plans its challenges with fpsdet's planner and a fresh secret, runs the study server and the
participant's client, asks the four questions, then scores the telemetry live and again offline, checks every
packet and graph, reproduces the plan and the realization with the secret and deletes the secret, scans every
public file for secrets and personal data, computes the study's metrics, and checks the stop conditions. A stop
condition writes STOP into the data folder, and no session starts until the operator clears it.

Standard library only, beside fpsdet.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples" / "pilot"))

import pilot  # noqa: E402  (P12's harness: fpsdet calls, Godot environment, secret material)

PROJECT = ROOT / "examples" / "pilot" / "godot"
DESIGN = HERE / "design.json"
PROFILE = HERE / "profile.json"
FORMAT = "fpsdet.human-pilot/1"
TICK_MS = 1000 / 60
KINDS = ("human", "machine_standin", "controlled_follower")
# The machine dry run: which scripted stand-in plays each mode. Never a person.
STAND_INS = {"free": "tracker", "combat": "tracker", "angle_holding": "holder", "sweep_search": "sweeper", "tracking": "tracker",
             "high_motion": "flicker", "stress": "prefire"}
CODE = ("study.tscn", "study.gd", "scripts/study_arena.gd", "scripts/study_server.gd", "scripts/study_client.gd", "scripts/arena.gd", "scripts/recipe.gd")


def design() -> dict:
    return json.loads(DESIGN.read_text(encoding="utf-8"))


def code_identity() -> dict:
    from fpsdet.provenance import normalized_source

    files = {f"examples/pilot/godot/{name}": "sha256:" + hashlib.sha256(normalized_source((PROJECT / name).read_bytes())).hexdigest() for name in CODE}
    files["examples/human-pilot/study.py"] = "sha256:" + hashlib.sha256(normalized_source(Path(__file__).read_bytes())).hexdigest()
    return files


def stopped(data: Path) -> str | None:
    path = data / "STOP"
    return path.read_text(encoding="utf-8") if path.exists() else None


def stop(data: Path, reasons: list[str]) -> None:
    (data / "STOP").write_text("\n".join(reasons) + "\n", encoding="utf-8")


# Enrollment: a random id, the consent answers, and the day. Nothing else.


def cmd_enroll(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    if not args.agree:
        raise SystemExit("A participant takes part only after agreeing to CONSENT.md: pass --agree once they have.")
    participant = "hp-" + secrets.token_hex(4)
    folder = data / participant
    folder.mkdir(parents=True)
    record = {"participant": participant, "kind": args.kind, "consent": True, "publish_consent": bool(args.publish),
              "enrolled": datetime.date.today().isoformat(), "consent_notice_sha256": pilot.sha256_file(HERE / "CONSENT.md")}
    (folder / "participant.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    order = session_order(participant)
    print(f"{participant}: sessions in this order: {', '.join(order)}")
    return 0


def session_order(participant: str) -> list[str]:
    modes = design()["sessions"]["modes"]
    middle = sorted(modes[1:-1], key=lambda mode: hashlib.sha256(f"fpsdet.human-pilot-order/1\0{participant}\0{mode}".encode()).hexdigest())
    return [modes[0], *middle, modes[-1]]


# One session.


def run_session(godot: str, data: Path, participant: str, mode: str, port: int, kind: str, bind: str = "127.0.0.1",
                remote: bool = False, stand_in: str | None = None, ask=input) -> dict:
    reason = stopped(data)
    if reason:
        raise SystemExit(f"The study is stopped: {reason.strip()} Investigate, then clear it with study.py clear-stop.")
    found = design()
    record = json.loads((data / participant / "participant.json").read_text(encoding="utf-8"))
    if not record["consent"]:
        raise SystemExit(f"{participant} has not agreed to take part.")
    count = len(list((data / participant).glob(f"{mode}-*")))
    session = f"{mode}-{count + 1}"
    run = data / participant / session
    match_id = f"study-{participant}-{session}"
    plan, secret = plan_session(run, match_id, participant)
    environment = pilot.godot_env(run)
    public = run / "public"
    length = found["sessions"]["length_ms"]
    server = [godot, "--headless", "--path", str(PROJECT), "res://study.tscn", "--", "--role=server", f"--participant={participant}",
              f"--mode={mode}", f"--match={match_id}", f"--match-ms={length}", f"--port={port}", f"--bind={bind}", f"--plan={plan}",
              f"--secret-file={secret}", f"--events={public / 'events.ndjson'}", f"--log={public / 'server.log'}",
              f"--private={run / 'private'}", f"--perf={public / 'perf.json'}"]
    if kind == "controlled_follower":
        server.append("--follower=true")
    started = time.time()
    processes = [subprocess.Popen(server, env=environment, stdout=(run / "server.out").open("w"), stderr=subprocess.STDOUT)]
    time.sleep(1.5)
    instructions = found["sessions"]["instructions"][mode]
    client = [godot, "--path", str(PROJECT), "res://study.tscn", "--", "--role=client", f"--participant={participant}", f"--port={port}",
              f"--server={bind}", f"--match-ms={length}", f"--instructions={instructions}", f"--log={public / 'client.jsonl'}"]
    if stand_in is not None:
        client = [client[0], "--headless", *client[1:], "--input=standin", f"--behaviour={stand_in}"]
    if remote:
        print("On the participant's machine, from a copy of examples/pilot/godot, run:\n  godot --path . res://study.tscn -- " + " ".join(client[5:]))
    else:
        processes.append(subprocess.Popen(client, env=environment, stdout=(run / "client.out").open("w"), stderr=subprocess.STDOUT))
    codes = []
    for process in processes:
        try:
            codes.append(process.wait(timeout=length / 1000 + 900))
        except subprocess.TimeoutExpired:
            process.kill()
            codes.append("timeout")
    answers = questionnaire(found, kind, ask)
    return post_session(run, plan, secret, participant, mode, kind, stand_in, codes, round(time.time() - started, 1), answers)


def plan_session(run: Path, match_id: str, participant: str) -> tuple[Path, Path]:
    schedule = design()["challenges"]["schedule"]
    public, private = run / "public", run / "private"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    secret = private / "secret.hex"
    pilot.fpsdet("challenge", "keygen", "--out", str(secret))
    plan = public / "plan.json"
    pilot.fpsdet("challenge", "plan", "--profile", str(PROFILE), "--match", match_id, "--player", participant, "--count", str(schedule["count"]),
                 "--from-ms", str(schedule["from_ms"]), "--to-ms", str(schedule["to_ms"]), "--cooldown-ms", str(schedule["cooldown_ms"]),
                 "--min-duration-ms", str(schedule["min_duration_ms"]), "--max-duration-ms", str(schedule["max_duration_ms"]),
                 "--secret-file", str(secret), "--out", str(plan))
    return plan, secret


def questionnaire(found: dict, kind: str, ask) -> dict:
    if kind != "human":
        return {"asked": False, "why": "machine stand-in: nobody to ask"}
    answers = {}
    print("Ask the participant, and type y or n:")
    for question in found["questionnaire"]:
        reply = ""
        while reply not in ("y", "n"):
            reply = ask(f"  {question} [y/n] ").strip().lower()[:1]
        answers[question] = reply == "y"
    return {"asked": True, "answers": answers}


# After the session.


def score(events: Path, plan: Path, out: Path) -> dict:
    from fpsdet.challenge import ChallengeRegistry, case_problems, plan_file_from_dict
    from fpsdet.graph import verify_graph
    from fpsdet.provenance import verify_packet

    pilot.fpsdet("score", str(events), "--profile", str(PROFILE), "--challenges", str(plan), "--out", str(out))
    registry = ChallengeRegistry.from_files([plan_file_from_dict(json.loads(plan.read_text()))])
    cases = {}
    for path in sorted(out.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(case, dict) or "evidence" not in case or "player_id" not in case:
            continue
        evidence = case["evidence"]
        cases[case["player_id"]] = {
            "detector": evidence["provenance"]["detector"]["digest"],
            "decision": case["decision"],
            "observations": sorted(obs["observation_id"] for obs in evidence["observations"]),
            "kinds": sorted(obs["kind"] for obs in evidence["observations"]),
            "graph": evidence["graph"]["digest"],
            "packet": evidence["packet"]["digest"],
            "inputs": evidence["provenance"]["inputs"]["recipe"],
            "challenges": evidence.get("challenges", []),
            "problems": verify_packet(case) + [f"graph: {problem}" for problem in verify_graph(case)] + case_problems(case, registry),
        }
    return cases


def personal_data(paths: list[Path]) -> list[str]:
    """Anything in a public file that could name a person or a machine: an IP address, this machine's name,
    the operator's user name or home folder. The study never writes them; this checks it."""
    needles = {"hostname": socket.gethostname(), "user": getpass.getuser(), "home": str(Path.home())}
    found = []
    for path in paths:
        if not path.is_file() or path.suffix not in (".json", ".jsonl", ".ndjson", ".log", ".out"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
            found.append(f"{path.name}: an IP address")
            break
        for name, value in needles.items():
            if value and len(value) > 2 and re.search(r"\b" + re.escape(value) + r"\b", text):
                found.append(f"{path.name}: the {name}")
    return found


def post_session(run: Path, plan: Path, secret: Path, participant: str, mode: str, kind: str, stand_in: str | None, codes: list,
                 wall_s: float, answers: dict) -> dict:
    public = run / "public"
    live = score(public / "events.ndjson", plan, run / "cases-live")
    offline = score(public / "events.ndjson", plan, run / "cases-offline")
    identical = live == offline
    reproduced = reproduce(run, plan, secret)
    public_files = [path for path in run.rglob("*") if "private" not in path.parts and path.is_file()]
    leaks = pilot.leaks(public_files, plan, secret)
    secret.unlink()  # the post-match check is done; the design deletes the secret now
    without = reproduce(run, plan, None)["status"]
    # What could ever be published: the session's own public folder (the local case folders hold the operator's paths).
    people = personal_data([path for path in public.rglob("*") if path.is_file()])
    case = live.get(participant, {})
    metrics = challenge_metrics(run, case, plan)
    log = [json.loads(line) for line in (public / "server.log").read_text().splitlines() if line.strip()]
    end = next((row for row in log if row["kind"] == "session_end"), {})
    client = next((json.loads(line) for line in (public / "client.jsonl").read_text().splitlines()[::-1] if '"summary"' in line), {}) if (public / "client.jsonl").exists() else {}
    perf = json.loads((public / "perf.json").read_text()) if (public / "perf.json").exists() else {}
    played_ms = end.get("t_ms", 0)
    review = case.get("decision") == "review" and "occluded_motion_replay" in case.get("kinds", [])
    record = {
        "participant": participant, "kind": kind, "stand_in": stand_in, "mode": mode, "session": run.name, "match_id": f"study-{participant}-{run.name}",
        "played_ms": played_ms, "exit_codes": codes, "wall_s": wall_s, "questionnaire": answers,
        "plan": {"sha256": pilot.sha256_file(plan), "challenges": [{"challenge_id": row["challenge_id"], "plan": row["plan"]} for row in json.loads(plan.read_text())["challenges"]]},
        "events": {"sha256": pilot.sha256_file(public / "events.ndjson"), "lines": len((public / "events.ndjson").read_text().splitlines())},
        "case": {key: case.get(key) for key in ("decision", "observations", "kinds", "graph", "packet", "inputs", "detector", "problems")},
        "review_grade": review,
        "live_vs_offline": "identical" if identical else "DIFFERENT",
        "realization": reproduced, "without_secret": without, "secret_leaks": leaks, "personal_data": people,
        "challenges": metrics,
        "performance": {
            "tick_us_mean": round(perf.get("tick_us", 0) / max(perf.get("ticks", 1), 1), 1), "tick_us_max": perf.get("tick_us_max"),
            "event_bytes_per_s": round(perf.get("event_bytes", 0) / max(played_ms / 1000, 1), 1),
            "snapshot_bytes_per_s": round(perf.get("snapshot_bytes", 0) / max(played_ms / 1000, 1), 1),
            "client_fps_median": sorted(client.get("fps", []))[len(client.get("fps", [])) // 2] if client.get("fps") else None,
            "probe_sounds_heard": sum(count for key, count in client.get("sounds", {}).items() if key.split(":")[0] == end.get("entities", {}).get("probe")),
        },
    }
    reasons = []
    if any(row["verdicts"].get("known") for row in metrics):
        reasons.append("a challenge body was reported known: visible or audible")
    if answers.get("asked") and (answers["answers"].get(design()["questionnaire"][0]) or answers["answers"].get(design()["questionnaire"][1])):
        reasons.append("the participant reported an unexplained avatar or sound")
    if leaks:
        reasons.append("secret or realization material in a public output")
    if people:
        reasons.append("personal data in a public output: " + "; ".join(people))
    if not identical:
        reasons.append("offline replay differs from live scoring")
    if any(case.get("problems") or [] for case in live.values()):
        reasons.append("a packet or graph does not verify")
    if review and kind != "controlled_follower":
        reasons.append("review-grade challenge evidence on an honest session: investigate before any more data")
    record["stop"] = reasons
    (run / "session.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    if reasons and kind != "machine_standin":
        stop(run.parents[1], [f"{participant} {run.name}: {reason}" for reason in reasons])
    return record


def reproduce(run: Path, plan: Path, secret: Path | None) -> dict:
    from fpsdet.challenge import plan_file_from_dict
    from fpsdet.challenge_plan import SecretError, load_secret, realize
    from fpsdet.challenge_plan import reproduce as reproduce_plan

    plan_file = plan_file_from_dict(json.loads(plan.read_text()))
    try:
        key, _ = load_secret(secret, environ={}) if secret is not None and secret.exists() else load_secret(None, environ={})
    except SecretError as error:
        return {"status": "secret_unavailable", "detail": str(error)}
    problems = list(reproduce_plan(key, plan_file))
    for record in plan_file.plans:
        ran_path = run / "private" / f"realization-{record.challenge_id}.json"
        if not ran_path.exists():
            problems.append(f"{record.challenge_id}: did not run")
            continue
        ran = json.loads(ran_path.read_text())
        derived = dict(realize(key, record, plan_file.budget).parameters)
        if {name: int(value) for name, value in ran["parameters"].items()} != derived:
            problems.append(f"{record.challenge_id}: the server's realization is not the secret's")
        if not (record.start_ms <= ran["first_ms"] and ran["last_ms"] <= record.end_ms):
            problems.append(f"{record.challenge_id}: ran outside its window")
    return {"status": "reproduced" if not problems else "differs", "problems": problems}


# The study's metrics: from the server's per-tick study telemetry, fpsdet's result, and the realization record.


def episodes(rows: list[dict], gap_ms: float) -> list[tuple[int, int]]:
    """Runs of overlap ticks, joined across stretches out of the cone of at most gap_ms. Each episode is
    (first overlap tick, last overlap tick), in ms."""
    out: list[list[int]] = []
    for row in rows:
        if not row["in"]:
            continue
        if out and row["t"] - out[-1][1] - TICK_MS <= gap_ms + 1e-6:
            out[-1][1] = row["t"]
        else:
            out.append([row["t"], row["t"]])
    return [(first, last) for first, last in out]


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def challenge_metrics(run: Path, case: dict, plan: Path) -> list[dict]:
    gap = design()["frozen"]["episode_gap_ms"]
    rows = [json.loads(line) for line in (run / "private" / "study.ndjson").read_text().splitlines() if line.strip()] if (run / "private" / "study.ndjson").exists() else []
    results = {row["challenge_id"]: row for row in case.get("challenges", [])}
    out = []
    for record in json.loads(plan.read_text())["challenges"]:
        cid = record["challenge_id"]
        mine = [row for row in rows if row["c"] == cid]
        ran_path = run / "private" / f"realization-{cid}.json"
        ran = json.loads(ran_path.read_text()) if ran_path.exists() else {}
        overlap = [row for row in mine if row["in"]]
        found = episodes(mine, gap)
        result = results.get(cid, {})
        verdicts: dict[str, int] = {}
        for row in mine:
            for channel in ("v", "a"):
                verdicts[row[channel]] = verdicts.get(row[channel], 0) + 1
        out.append({
            "room": ran.get("room"),
            "ran_ticks": len(mine),
            "ended_early": ran.get("ended") == "perceivable",
            "status": result.get("status", "none"),
            "cause": result.get("cause"),
            "eligible_moments": result.get("eligible_samples", 0),
            "counted_moments": result.get("tracked_samples", 0),
            "counted_ms": result.get("total_ms", 0.0),
            "verified_moments": result.get("verified_samples", 0),
            "not_counted": result.get("not_counted", {}),
            "overlap_ms": round(len(overlap) * TICK_MS, 3),
            "explained_ms": round(sum(1 for row in overlap if row["vis_in"]) * TICK_MS, 3),
            "challenge_only_ms": round(sum(1 for row in overlap if not row["vis_in"]) * TICK_MS, 3),
            "episodes": len(found),
            "episode_ms": [round(last - first + TICK_MS, 3) for first, last in found],
            "longest_episode_ms": round(max((last - first + TICK_MS for first, last in found), default=0.0), 3),
            "time_to_first_overlap_ms": (overlap[0]["t"] - record["start_ms"]) if overlap else None,
            "aim_error_deg": {"median": quantile([row["ang"] for row in mine], 0.5), "min": min((row["ang"] for row in mine), default=None)},
            "turn_rate_dps": {"median": quantile([row["turn"] for row in overlap], 0.5), "p95": quantile([row["turn"] for row in overlap], 0.95)},
            "enemy_separation_deg": quantile([row["sep"] for row in overlap if row["sep"] is not None], 0.5),
            "verdicts": verdicts,
        })
    return out


# The artifact: fpsdet.human-pilot/1.


def upper_zero(n: int) -> float | None:
    """The exact one-sided 95% upper bound on a proportion with no event in n trials (Clopper-Pearson)."""
    return round(1 - 0.05 ** (1 / n), 4) if n else None


def interval(k: int, n: int) -> list[float] | None:
    from fpsdet.strength import beta_quantile

    if not n:
        return None
    low = 0.0 if k == 0 else beta_quantile(0.025, k, n - k + 1)
    high = 1.0 if k == n else beta_quantile(0.975, k + 1, n - k)
    return [round(low, 4), round(high, 4)]


def summary(values: list[float]) -> dict:
    return {"n": len(values), "median": quantile(values, 0.5), "p90": quantile(values, 0.9), "p95": quantile(values, 0.95), "max": max(values, default=None)}


def analyze(data: Path, kind: str, samples: list[dict] | None = None) -> dict:
    found = design()
    sessions = [json.loads(path.read_text()) for path in sorted(data.glob("hp-*/*/session.json"))]
    sessions = [row for row in sessions if row["kind"] in (kind, "controlled_follower")]
    honest = [row for row in sessions if row["kind"] == kind]
    included = [row for row in honest if row["played_ms"] >= 120_000 and "timeout" not in row["exit_codes"]]
    excluded = [{"participant": row["participant"], "session": row["session"], "played_ms": row["played_ms"]} for row in honest if row not in included]
    challenges = [dict(challenge, participant=row["participant"], session=row["session"], mode=row["mode"]) for row in included for challenge in row["challenges"]]
    participants = sorted({row["participant"] for row in included})
    bar = {"min_samples": found["frozen"]["hidden_track_min_samples"], "min_total_ms": found["frozen"]["hidden_track_min_ms"]}
    reviews = {"participants": sorted({row["participant"] for row in included if row["review_grade"]}),
               "sessions": sum(1 for row in included if row["review_grade"]),
               "challenges": sum(1 for challenge in challenges if challenge["status"] == "followed")}
    units = {"participants": len(participants), "sessions": len(included), "challenges": len(challenges)}
    bounds = {}
    for unit, n in units.items():
        k = len(reviews["participants"]) if unit == "participants" else reviews[unit]
        bounds[unit] = {"with_findings": k, "of": n, "upper_95_if_none": upper_zero(n) if k == 0 else None, "interval_95": interval(k, n) if k else None}
    per_participant = []
    for participant in participants:
        mine = [challenge for challenge in challenges if challenge["participant"] == participant]
        counted = [challenge["counted_ms"] for challenge in mine]
        per_participant.append({
            "participant": participant,
            "sessions": sum(1 for row in included if row["participant"] == participant),
            "challenges_exposed": len(mine),
            "challenges_with_any_overlap": sum(1 for challenge in mine if challenge["overlap_ms"] > 0),
            "counted_ms": {"max": max(counted, default=0.0), "median": quantile(counted, 0.5), "p95": quantile(counted, 0.95)},
            "reacquired": sum(1 for challenge in mine if challenge["episodes"] >= 2),
            "review_grade_findings": sum(1 for challenge in mine if challenge["status"] == "followed"),
            "decisions": sorted({row["case"]["decision"] for row in included if row["participant"] == participant}),
        })
    worst = max(challenges, key=lambda challenge: (challenge["counted_ms"], challenge["counted_moments"], challenge["overlap_ms"]), default=None)
    rooms = {}
    for room in found["challenges"]["rooms"]:
        mine = [challenge for challenge in challenges if challenge["room"] == room]
        rooms[room] = {"challenges": len(mine), "with_overlap": sum(1 for challenge in mine if challenge["overlap_ms"] > 0),
                       "overlap_ms": summary([challenge["overlap_ms"] for challenge in mine]), "counted_ms": summary([challenge["counted_ms"] for challenge in mine])}
    followers = [challenge for row in sessions if row["kind"] == "controlled_follower" for challenge in row["challenges"]]
    body = {
        "format": FORMAT,
        "study": found["study"],
        "kind": kind,
        "statement": ("Machine stand-ins only: scripted honest-style aiming that qualifies the instruments. Not people, and never evidence about how people aim."
                      if kind == "machine_standin" else
                      "A consented honest-human pilot: a small behavioural baseline. Not a population, a calibration, a validation, or a false-positive rate."),
        "design": {"path": "examples/human-pilot/design.json", "sha256": pilot.sha256_file(DESIGN)},
        "code": code_identity(),
        "units": units,
        "excluded_sessions": excluded,
        "review_grade": {"bar": bar, **reviews, "bounds": bounds,
                         "note": "Counts at three levels. A participant's sessions and challenges are not independent: the participant level is the primary unit."},
        "distributions": {key: summary([challenge[key] for challenge in challenges]) for key in
                          ("counted_ms", "counted_moments", "overlap_ms", "challenge_only_ms", "explained_ms", "longest_episode_ms", "episodes")},
        "any_overlap": {"challenges": sum(1 for challenge in challenges if challenge["overlap_ms"] > 0), "of": len(challenges)},
        "explained": {"overlap_ms": round(sum(challenge["overlap_ms"] for challenge in challenges), 3),
                      "explained_by_visible_bot_ms": round(sum(challenge["explained_ms"] for challenge in challenges), 3),
                      "challenge_only_ms": round(sum(challenge["challenge_only_ms"] for challenge in challenges), 3),
                      "fpsdet_not_counted_seen": sum(challenge["not_counted"].get("seen", 0) for challenge in challenges)},
        "reacquired": {"challenges_with_2_or_more_episodes": sum(1 for challenge in challenges if challenge["episodes"] >= 2), "of": len(challenges)},
        "rooms": rooms,
        "per_participant": per_participant,
        "worst_case": None if worst is None else {
            **{key: worst[key] for key in ("participant", "mode", "room", "status", "counted_moments", "counted_ms", "overlap_ms", "explained_ms",
                                           "challenge_only_ms", "episodes", "episode_ms", "longest_episode_ms", "time_to_first_overlap_ms",
                                           "aim_error_deg", "turn_rate_dps", "enemy_separation_deg", "not_counted")},
            "against_the_bar": {"moments": f"{worst['counted_moments']} of {bar['min_samples']}", "ms": f"{worst['counted_ms']:.0f} of {bar['min_total_ms']:.0f}",
                                "crossed": worst["status"] == "followed"}},
        "controlled_follower": {
            "label": "the P12 stand-in for a reader of the probe it was sent, in this arena; for comparison only, never trained on",
            "challenges": len(followers),
            "counted_ms": summary([challenge["counted_ms"] for challenge in followers]),
            "longest_episode_ms": summary([challenge["longest_episode_ms"] for challenge in followers]),
            "followed": sum(1 for challenge in followers if challenge["status"] == "followed"),
        },
        "runtime": {
            "verdicts": {state: sum(challenge["verdicts"].get(state, 0) for challenge in challenges) for state in ("absent", "known", "unchecked")},
            "ended_early": sum(1 for challenge in challenges if challenge["ended_early"]),
            "abstained": sum(1 for challenge in challenges if challenge["status"] == "abstained"),
            "live_vs_offline": sorted({row["live_vs_offline"] for row in included}),
            "packets_and_graphs_verify": all(not (row["case"]["problems"] or []) for row in included),
            "realization": sorted({row["realization"]["status"] for row in included}),
            "secret_leaks": sum(len(row["secret_leaks"]) for row in included),
            "personal_data": sum(len(row["personal_data"]) for row in included),
        },
        "questionnaire": {question: sum(1 for row in included if row["questionnaire"].get("asked") and row["questionnaire"]["answers"].get(question))
                          for question in found["questionnaire"]} if kind == "human" else "not asked: machine stand-ins",
        "performance": {key: summary([row["performance"][key] for row in included if row["performance"].get(key) is not None])
                        for key in ("tick_us_mean", "tick_us_max", "event_bytes_per_s", "snapshot_bytes_per_s", "client_fps_median")},
        "sessions": [{key: row[key] for key in ("participant", "kind", "stand_in", "mode", "session", "played_ms", "review_grade", "live_vs_offline")}
                     | {"plan": row["plan"]["sha256"], "events": row["events"]["sha256"], "packet": row["case"]["packet"], "graph": row["case"]["graph"],
                        "decision": row["case"]["decision"], "detector": row["case"]["detector"]}
                     for row in sessions],
        "samples": samples or [],
    }
    body["digest"] = digest(body)
    return body


def digest(body: dict) -> str:
    from fpsdet.evidence import canonical_json

    return "sha256:" + hashlib.sha256(b"fpsdet.human-pilot/1\0" + canonical_json({key: value for key, value in body.items() if key != "digest"}).encode("utf-8")).hexdigest()


# Samples: one session's public telemetry and plan, copied into the repository so anyone can replay it.


def publish_sample(data: Path, participant: str, session: str, dest: Path) -> dict:
    """Copy one session's public events and plan, if its participant agreed to publication (or it is a
    machine stand-in). Returns the entry a result records for it."""
    record = json.loads((data / participant / "participant.json").read_text(encoding="utf-8"))
    if not record.get("publish_consent") and record.get("kind") == "human":
        raise SystemExit(f"{participant} did not agree to publication; their gameplay stays out of the repository")
    source = data / participant / session / "public"
    folder = dest / f"{participant}-{session}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("events.ndjson", "plan.json"):
        shutil.copyfile(source / name, folder / name)
    return {"participant": participant, "session": session, "folder": folder.relative_to(ROOT).as_posix(),
            "events": pilot.sha256_file(folder / "events.ndjson"), "plan": pilot.sha256_file(folder / "plan.json")}


def verify_samples(result_path: Path) -> list[str]:
    """Score every committed sample again, offline, and compare with the session it came from: the same
    decision, observations, graph, and packet while the detector code is the same; every packet and graph
    verifying. Needs no engine and no secret."""
    result = json.loads(result_path.read_text(encoding="utf-8"))
    problems = [] if result.get("digest") == digest(result) else ["the result's digest does not match its contents"]
    sessions = {(row["participant"], row["session"]): row for row in result["sessions"]}
    with tempfile.TemporaryDirectory() as work:
        for sample in result.get("samples", []):
            folder = ROOT / sample["folder"]
            if pilot.sha256_file(folder / "events.ndjson") != sample["events"] or pilot.sha256_file(folder / "plan.json") != sample["plan"]:
                problems.append(f"{sample['folder']}: not the captured files")
                continue
            cases = score(folder / "events.ndjson", folder / "plan.json", Path(work) / sample["session"])
            case = cases.get(sample["participant"], {})
            row = sessions[(sample["participant"], sample["session"])]
            if case.get("graph") != row["graph"] or case.get("decision") != row["decision"]:
                problems.append(f"{sample['folder']}: the replay differs from the live session")
            if case.get("detector") == row.get("detector") and case.get("packet") != row["packet"]:
                problems.append(f"{sample['folder']}: the packet differs with the same detector code")
            if case.get("problems"):
                problems.append(f"{sample['folder']}: {case['problems'][0]}")
    return problems


# Commands.


def cmd_session(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    record = run_session(args.godot, data, args.participant, args.mode, args.port, "controlled_follower" if args.follower else "human",
                         bind=args.bind, remote=args.remote)
    print(json.dumps({key: record[key] for key in ("participant", "mode", "session", "played_ms", "review_grade", "live_vs_offline", "stop")}, indent=1))
    return 1 if record["stop"] else 0


def cmd_clear_stop(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    reason = stopped(data)
    if reason is None:
        print("The study is not stopped.")
        return 0
    with (data / "stops.log").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"stopped": reason, "cleared": datetime.date.today().isoformat(), "investigation": args.reason}) + "\n")
    (data / "STOP").unlink()
    print("Cleared; the stop and the investigation are in stops.log.")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    samples = []
    for pick in args.sample or []:
        participant, _, session = pick.partition("/")
        samples.append(publish_sample(data, participant, session, ROOT / args.samples_dir))
    body = analyze(data, args.kind, samples)
    Path(args.out).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}: {body['units']}, review-grade {body['review_grade']['sessions']} sessions, digest {body['digest']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    problems = verify_samples(Path(args.result))
    print("\n".join(problems) or "every sample replays as recorded")
    return 1 if problems else 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Machine stand-ins play every mode, two of them, and the controlled follower plays twice: the instruments
    checked end to end, and the arena checked for honest-style aiming that reaches the bar, before any person plays."""
    from concurrent.futures import ThreadPoolExecutor

    data = Path(args.data).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    jobs = []
    for index in range(args.machines):
        participant = "hp-" + secrets.token_hex(4)
        (data / participant).mkdir()
        (data / participant / "participant.json").write_text(json.dumps({"participant": participant, "kind": "machine_standin", "consent": True,
                                                                        "publish_consent": True}) + "\n")
        for mode in session_order(participant):
            jobs.append((participant, mode, "machine_standin", STAND_INS[mode]))
    follower = "hp-" + secrets.token_hex(4)
    (data / follower).mkdir()
    (data / follower / "participant.json").write_text(json.dumps({"participant": follower, "kind": "controlled_follower", "consent": True, "publish_consent": True}) + "\n")
    for mode in ("tracking", "stress"):
        jobs.append((follower, mode, "controlled_follower", "tracker"))

    def one(job: tuple, port: int) -> dict:
        participant, mode, kind, stand_in = job
        return run_session(args.godot, data, participant, mode, port, kind, stand_in=stand_in)

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        records = list(pool.map(one, jobs, range(args.port, args.port + len(jobs))))
    for record in records:
        print(f"{record['participant']} {record['mode']:13} {record['kind']:19} review={record['review_grade']} {record['live_vs_offline']} stop={record['stop']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    enroll = sub.add_parser("enroll", help="A new random participant id, once they have agreed to CONSENT.md")
    enroll.add_argument("--data", required=True)
    enroll.add_argument("--agree", action="store_true", help="They read CONSENT.md and agreed to take part")
    enroll.add_argument("--publish", action="store_true", help="They also ticked the optional box: their gameplay may be published as an example")
    enroll.add_argument("--kind", default="human", choices=["human"])
    enroll.set_defaults(func=cmd_enroll)
    session = sub.add_parser("session", help="One session: plan, play, ask, score, check")
    session.add_argument("--data", required=True)
    session.add_argument("--participant", required=True)
    session.add_argument("--mode", required=True, choices=design()["sessions"]["modes"])
    session.add_argument("--godot", required=True)
    session.add_argument("--port", type=int, default=24800)
    session.add_argument("--bind", default="127.0.0.1", help="A private LAN address for a participant on another machine; never a public one")
    session.add_argument("--remote", action="store_true", help="Print the client command to run on the participant's machine instead of opening it here")
    session.add_argument("--follower", action="store_true", help="The controlled follower, for the comparison only")
    session.set_defaults(func=cmd_session)
    clear = sub.add_parser("clear-stop", help="After investigating a stop condition, record why and allow sessions again")
    clear.add_argument("--data", required=True)
    clear.add_argument("--reason", required=True)
    clear.set_defaults(func=cmd_clear_stop)
    analyze_ = sub.add_parser("analyze", help="Write fpsdet.human-pilot/1 from every session in the data folder")
    analyze_.add_argument("--data", required=True)
    analyze_.add_argument("--out", required=True)
    analyze_.add_argument("--kind", default="human", choices=["human", "machine_standin"])
    analyze_.add_argument("--sample", action="append", help="PARTICIPANT/SESSION to copy into the repository as a replayable example (publication consent required)")
    analyze_.add_argument("--samples-dir", default="examples/human-pilot/sessions")
    analyze_.set_defaults(func=cmd_analyze)
    check = sub.add_parser("verify", help="Offline: replay every committed sample against the result that records it")
    check.add_argument("--result", required=True)
    check.set_defaults(func=cmd_verify)
    dry = sub.add_parser("dry-run", help="Machine stand-ins and the controlled follower, to qualify the instruments before any person plays")
    dry.add_argument("--data", required=True)
    dry.add_argument("--godot", required=True)
    dry.add_argument("--machines", type=int, default=2)
    dry.add_argument("--parallel", type=int, default=8)
    dry.add_argument("--port", type=int, default=25100)
    dry.set_defaults(func=cmd_dry_run)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
