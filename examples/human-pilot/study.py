"""The consented honest-human pilot's harness (docs/human-pilot.md, design.json).

Everything runs on the operator's machine with the repository's own Godot pilot; the server binds 127.0.0.1,
or a private LAN address given with --bind for a participant on another machine. Nothing else is touched.

    python examples/human-pilot/study.py controls-check --godot PATH                  # the human client's controls, no person
    python examples/human-pilot/study.py enroll --data ~/study --agree [--publish]       # a random participant id
    python examples/human-pilot/study.py practice --data ~/practice --study ~/study --participant hp-... --godot PATH
    python examples/human-pilot/study.py withdraw --data ~/study --participant hp-... --reason participant_request --practice ~/practice
    python examples/human-pilot/study.py session --data ~/study --participant hp-... --mode free --godot PATH
    python examples/human-pilot/study.py analyze --data ~/study --out examples/human-pilot/result.json
    python examples/human-pilot/study.py dry-run --data /tmp/dry --godot PATH         # machine stand-ins, never people

A session plans its challenges with fpsdet's planner and a fresh secret, runs the study server and the
participant's client, asks the five questions, then scores the telemetry live and again offline, checks every
packet and graph, reproduces the plan and the realization with the secret and deletes the secret, scans every
public file for secrets and personal data, computes the study's metrics, and checks the stop conditions. A stop
condition writes STOP into the data folder, and no session starts until the operator clears it; review-grade
evidence on an honest session cannot be cleared (amendment 1).

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
AMENDMENT = HERE / "amendment-1.json"
AMENDMENT_2 = HERE / "amendment-2.json"
CONSENT = HERE / "CONSENT.md"
# Amendment 2: what a participant can be, which of those are out of play, and why a session may not count.
INACTIVE = ("practice_failed", "withdrawn", "discontinued")
WITHDRAWALS = ("participant_request", "unable_to_continue")
# The stop that answers the primary question, and that clear-stop refuses (amendment 2).
FALSIFYING = "review-grade challenge evidence on a protocol-valid honest session"
# What a session leaves behind for the operator only: deleted after the post-match check, never kept (amendment 2).
OPERATIONAL = ("server.out", "client.out", "godot-user", "cases-live", "cases-offline")
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


def amendment() -> dict:
    return json.loads(AMENDMENT.read_text(encoding="utf-8"))


def amendment_2() -> dict:
    return json.loads(AMENDMENT_2.read_text(encoding="utf-8"))


def questions() -> list[str]:
    """design.json's questions, then amendment 1's, then amendment 2's."""
    return [*design()["questionnaire"], *amendment()["questionnaire"]["added"], amendment_2()["validity"]["question_added"]]


def validity_reasons() -> list[str]:
    """Why a session may not count: amendment 2's closed list. A crossing is never one of them."""
    return amendment_2()["validity"]["reasons"]


def bindings() -> dict:
    """What a participant agreed to and under which protocol, bound at enrollment (amendment 2)."""
    return {"consent": pilot.sha256_file(CONSENT), "design": pilot.sha256_file(DESIGN), "amendment_1": pilot.sha256_file(AMENDMENT),
            "amendment_2": pilot.sha256_file(AMENDMENT_2)}


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
    reason = stopped(data)
    if reason:
        raise SystemExit(f"The study is stopped: {reason.strip()} Nobody is enrolled while it is stopped.")
    refusal = staging(data)
    if refusal:
        raise SystemExit(refusal)
    participant = "hp-" + secrets.token_hex(4)
    folder = data / participant
    folder.mkdir(parents=True)
    record = {"participant": participant, "kind": args.kind, "consent": True, "publish_consent": bool(args.publish),
              "enrolled": datetime.date.today().isoformat(), "consent_notice_sha256": pilot.sha256_file(CONSENT), "bindings": bindings()}
    set_status(record, "enrolled")
    save_participant(data, record)
    order = session_order(participant)
    print(f"{participant}: sessions in this order: {', '.join(order)}")
    return 0


def load_participant(data: Path, participant: str) -> dict:
    return json.loads((data / participant / "participant.json").read_text(encoding="utf-8"))


def save_participant(data: Path, record: dict) -> None:
    (data / record["participant"] / "participant.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def set_status(record: dict, status: str, **detail) -> None:
    record["status"] = status
    record.setdefault("history", []).append({"status": status, "date": datetime.date.today().isoformat(), **detail})


def session_records(data: Path, participant: str) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((data / participant).glob("*/session.json"))]


def valid(row: dict) -> bool:
    """Whether a session counts. Sessions from before amendment 2 carry no validity: they count by design.json's rule."""
    if "validity" in row:
        return bool(row["validity"]["valid"])
    return row.get("played_ms", 0) >= 120_000 and "timeout" not in row.get("exit_codes", [])


def state(data: Path, record: dict) -> str:
    """A participant's state (amendment 2): stored, or derived from their protocol-valid sessions."""
    status = record.get("status", "enrolled")
    if status in INACTIVE:
        return status
    played = {row["mode"] for row in session_records(data, record["participant"]) if valid(row)}
    return "completed" if set(design()["sessions"]["modes"]) <= played else status


def humans(data: Path) -> list[dict]:
    people = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(data.glob("hp-*/participant.json"))]
    return [row for row in people if row.get("kind", "human") == "human"]


def staging(data: Path) -> str | None:
    """Amendment 2: at most 4 participants in play until 4 have completed every mode, then at most 12. A
    participant who withdraws, is discontinued or fails practice frees a place for a replacement."""
    states = [state(data, row) for row in humans(data)]
    first, maximum = design()["participants"]["minimum_to_report"], design()["participants"]["maximum"]
    completed = states.count("completed")
    in_play = sum(1 for status in states if status not in INACTIVE)
    if completed >= first:
        if in_play >= maximum:
            return f"The study already has {in_play} participants in play, its maximum."
        return None
    if in_play >= first:
        return (f"Staging (amendment 2): at most {first} participants are in play until {first} have completed every mode with no finding; "
                f"{completed} have. A participant who withdraws, is discontinued or fails practice frees a place.")
    return None


def next_mode(data: Path, record: dict) -> str | None:
    """The participant's next mode in their pre-registered order: the first without a protocol-valid session."""
    played = {row["mode"] for row in session_records(data, record["participant"]) if valid(row)}
    return next((mode for mode in session_order(record["participant"]) if mode not in played), None)


def session_refusal(data: Path, record: dict, mode: str) -> str | None:
    """Why a human session may not start (amendment 2), or None."""
    participant = record["participant"]
    if record.get("bindings") != bindings():
        return (f"The consent notice, the design or an amendment changed since {participant} enrolled. Their sessions cannot join this study: "
                "that would be a new study version.")
    status = state(data, record)
    if status == "completed":
        return f"{participant} has completed every mode."
    if status != "practice_passed":
        return f"{participant} is {status}: a session needs a passed practice first (amendment 2)."
    expected = next_mode(data, record)
    if mode != expected:
        return f"The next session for {participant} is {expected}, in their pre-registered order: {', '.join(session_order(participant))}."
    return None


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
    record = load_participant(data, participant)
    if not record.get("consent"):
        raise SystemExit(f"{participant} has not agreed to take part.")
    if kind == "human":
        refusal = session_refusal(data, record, mode)
        if refusal:
            raise SystemExit(refusal)
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
    engine = build(godot)
    started = time.time()
    instructions = found["sessions"]["instructions"][mode]
    client = [godot, "--path", str(PROJECT), "res://study.tscn", "--", "--role=client", f"--participant={participant}", f"--port={port}",
              f"--server={bind}", f"--match-ms={length}", f"--instructions={instructions}", f"--log={public / 'client.jsonl'}"]
    if stand_in is not None:
        client = [client[0], "--headless", *client[1:], "--input=standin", f"--behaviour={stand_in}"]
    codes = play(run, environment, server, client, remote, length)
    answers = questionnaire(found, kind, ask, run)
    declared = declare_validity(kind, answers, ask)  # before the session is scored (amendment 2)
    return post_session(run, plan, secret, participant, mode, kind, stand_in, codes, round(time.time() - started, 1), answers, engine, declared)


def play(run: Path, environment: dict, server: list[str], client: list[str], remote: bool, length: int) -> list:
    """The server, then the client (here, or printed for the participant's machine), until both exit."""
    processes = [subprocess.Popen(server, env=environment, stdout=(run / "server.out").open("w"), stderr=subprocess.STDOUT)]
    time.sleep(1.5)
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
    return codes


def build(godot: str) -> dict:
    """The engine a session ran on: the binary's own digest, and the pinned release it should come from."""
    engine = pilot.qualification()["engine"]
    return {"name": engine["name"], "version": engine["version"], "release_sha512": engine["sha512"], "binary": pilot.sha256_file(Path(godot))}


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


def questionnaire(found: dict, kind: str, ask, run: Path | None = None) -> dict:
    """The fixed questions, yes or no. An optional short comment per answer goes only to the session's private
    folder (amendment 1): free text could identify someone, so it never reaches the artifact."""
    if kind != "human":
        return {"asked": False, "why": "machine stand-in: nobody to ask"}
    answers, comments = {}, {}
    print("Ask the participant, and type y or n; then a short comment, or Enter for none:")
    for question in questions():
        reply = ""
        while reply not in ("y", "n"):
            reply = ask(f"  {question} [y/n] ").strip().lower()[:1]
        answers[question] = reply == "y"
        comment = ask("    comment (optional): ").strip()
        if comment:
            comments[question] = comment[:200]
    if comments and run is not None:
        (run / "private" / "comments.json").write_text(json.dumps(comments, indent=1) + "\n", encoding="utf-8")
    return {"asked": True, "answers": answers, "comments": len(comments)}


def declare_validity(kind: str, answers: dict, ask) -> dict:
    """Whether the session ran as the protocol says, fixed before it is scored (amendment 2): the participant's
    answer to the added question, then the operator's declaration, with a reason from the closed list."""
    if kind != "human":
        return {"valid": True, "reason": None, "by": "machine"}
    if answers["answers"].get(amendment_2()["validity"]["question_added"]):
        return {"valid": False, "reason": "protocol_deviation", "by": "participant"}
    reply = ""
    while reply not in ("y", "n"):
        reply = ask("Operator: did this session run as the protocol says (the right mode, the participant playing normally, nothing broken)? [y/n] ").strip().lower()[:1]
    if reply == "y":
        return {"valid": True, "reason": None, "by": "operator"}
    listed = validity_reasons()
    for number, reason in enumerate(listed, 1):
        print(f"  {number}. {reason}")
    choice = ""
    while not (choice.isdigit() and 1 <= int(choice) <= len(listed)):
        choice = ask("  Which reason? ").strip()
    return {"valid": False, "reason": listed[int(choice) - 1], "by": "operator"}


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


def identifiers(text: str) -> list[str]:
    """What in a text could name a person or a machine: an IP address, this machine's name, the operator's user
    name or home folder. The study never writes them; this checks it."""
    needles = {"hostname": socket.gethostname(), "user": getpass.getuser(), "home": str(Path.home())}
    found = ["an IP address"] if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text) else []
    for name, value in needles.items():
        if value and len(value) > 2 and re.search(r"\b" + re.escape(value) + r"\b", text):
            found.append(f"the {name}")
    return found


def personal_data(paths: list[Path]) -> list[str]:
    """identifiers() over every file given, whatever its kind."""
    found = []
    for path in paths:
        if path.is_file():
            found += [f"{path.name}: {what}" for what in identifiers(path.read_text(encoding="utf-8", errors="replace"))]
    return found


def privacy_sweep(run: Path) -> dict:
    """Amendment 2: scan everything the run wrote, delete what is operational only, and scan what is kept again.
    Identifiers in operational files are expected (paths, the engine's own logs) and go with them; any in a
    kept file is a stop."""
    every = [path for path in run.rglob("*") if path.is_file()]
    operational = [run / name for name in OPERATIONAL if (run / name).exists()]
    inside = [path for path in every if any(path == root or root in path.parents for root in operational)]
    seen = len(personal_data(inside))
    for root in operational:
        shutil.rmtree(root) if root.is_dir() else root.unlink()
    kept = [path for path in run.rglob("*") if path.is_file()]
    hits = [f"{path.relative_to(run).as_posix()}: {what}" for path in kept for what in identifiers(path.read_text(encoding="utf-8", errors="replace"))]
    return {"operational_deleted": sorted(root.name for root in operational), "operational_identifiers": seen, "kept_hits": hits}


def challenge_channels(run: Path) -> set[str]:
    """Which channels the server reported a challenge body known on, from the study telemetry."""
    path = run / "private" / "study.ndjson"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    return {name for name, key in (("vision", "v"), ("audio", "a")) if any(row.get(key) == "known" for row in rows)}


def post_session(run: Path, plan: Path, secret: Path, participant: str, mode: str, kind: str, stand_in: str | None, codes: list,
                 wall_s: float, answers: dict, engine: dict | None = None, declared: dict | None = None) -> dict:
    public = run / "public"
    live = score(public / "events.ndjson", plan, run / "cases-live")
    offline = score(public / "events.ndjson", plan, run / "cases-offline")
    identical = live == offline
    reproduced = reproduce(run, plan, secret)
    public_files = [path for path in run.rglob("*") if "private" not in path.parts and path.is_file()]
    leaks = pilot.leaks(public_files, plan, secret)
    secret.unlink()  # the post-match check is done; the design deletes the secret now
    without = reproduce(run, plan, None)["status"]
    case = live.get(participant, {})
    metrics = challenge_metrics(run, case, plan)
    log = [json.loads(line) for line in (public / "server.log").read_text().splitlines() if line.strip()]
    end = next((row for row in log if row["kind"] == "session_end"), {})
    client = next((json.loads(line) for line in (public / "client.jsonl").read_text().splitlines()[::-1] if '"summary"' in line), {}) if (public / "client.jsonl").exists() else {}
    perf = json.loads((public / "perf.json").read_text()) if (public / "perf.json").exists() else {}
    played_ms = end.get("t_ms", 0)
    review = case.get("decision") == "review" and "occluded_motion_replay" in case.get("kinds", [])
    sweep = privacy_sweep(run)  # the local case folders and the engine's logs are operational: gone now (amendment 2)
    people = sweep["kept_hits"]
    # Validity (amendment 2): the declaration made before scoring first, then facts that do not depend on the score.
    known = challenge_channels(run)
    automatic = [reason for reason, applies in (("technical_failure", played_ms < 120_000 or "timeout" in codes),
                                                ("visibility_failure", "vision" in known), ("audio_failure", "audio" in known),
                                                ("privacy_failure", bool(people))) if applies]
    declared = declared or {"valid": True, "reason": None, "by": "machine"}
    validity = {"valid": declared["valid"] and not automatic, "reason": declared["reason"] if not declared["valid"] else (automatic[0] if automatic else None),
                "declared": declared, "automatic": automatic}
    record = {
        "participant": participant, "kind": kind, "stand_in": stand_in, "mode": mode, "session": run.name, "match_id": f"study-{participant}-{run.name}",
        "played_ms": played_ms, "exit_codes": codes, "wall_s": wall_s, "questionnaire": answers, "engine": engine,
        "code": code_identity(),
        "plan": {"sha256": pilot.sha256_file(plan), "challenges": [{"challenge_id": row["challenge_id"], "plan": row["plan"]} for row in json.loads(plan.read_text())["challenges"]]},
        "events": {"sha256": pilot.sha256_file(public / "events.ndjson"), "lines": len((public / "events.ndjson").read_text().splitlines())},
        "case": {key: case.get(key) for key in ("decision", "observations", "kinds", "graph", "packet", "inputs", "detector", "problems")},
        "review_grade": review,
        "live_vs_offline": "identical" if identical else "DIFFERENT",
        "realization": reproduced, "without_secret": without, "secret_leaks": leaks, "personal_data": people, "privacy": sweep, "validity": validity,
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
        reasons.append("the participant reported an unexplained avatar or sound: inspect the verdicts, the placement and KnowledgeState; the challenge is not hidden evidence")
    if leaks:
        reasons.append("secret or realization material in a public output")
    if people:
        reasons.append("an address or a machine identity in a kept session file: " + "; ".join(people))
    if not identical:
        reasons.append("offline replay differs from live scoring")
    if any(case.get("problems") or [] for case in live.values()):
        reasons.append("a packet or graph does not verify")
    if review and kind != "controlled_follower":
        if validity["valid"]:
            reasons.append(f"{FALSIFYING}: stop collection, preserve and replay the session, inspect its episodes, classify the behaviour, "
                           "change nothing, and propose the next phase (amendments 1 and 2)")
        else:
            reasons.append(f"review-grade challenge evidence on a session already invalid ({validity['reason']}): investigate; "
                           "it does not answer the primary question (amendment 2)")
    record["stop"] = reasons
    text = json.dumps(record, indent=1) + "\n"
    if identifiers(text):
        stop(run.parents[1], [f"{participant} {run.name}: the session record itself holds {', '.join(identifiers(text))}, so it was not written"])
        raise SystemExit(f"{run.name}: the session record itself holds {', '.join(identifiers(text))}; it was not written, and the study is stopped.")
    (run / "session.json").write_text(text, encoding="utf-8")
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
            **motion_metrics(mine, overlap),
        })
    return out


def motion_metrics(rows: list[dict], overlap: list[dict]) -> dict:
    """Amendment 1: the body's distance and motion, and how the aim moved against it, in the world over 250 ms
    windows. Overlap is split four ways: both still; the aim point still while the body moved (holding a spot);
    the aim point moving with the body (following); the aim point moving otherwise. Empty for telemetry
    written before the amendment (the machine dry run), which has none of it."""
    if not rows or "aw" not in rows[0]:
        return {}
    frozen = amendment()["metrics_added"]["frozen"]
    still, along = frozen["still_deg_per_s"], frozen["co_motion_deg"]

    def ms(test) -> float:
        return round(sum(1 for row in overlap if test(row)) * TICK_MS, 3)

    def moving_with(row: dict) -> bool:
        return row["pw"] >= still and row["co"] is not None and row["co"] <= along

    return {
        "probe_distance_m": {"median": quantile([row["dist"] for row in rows], 0.5), "during_overlap": quantile([row["dist"] for row in overlap], 0.5)},
        "probe_speed_mps": {"median": quantile([row["ps"] for row in rows], 0.5), "during_overlap": quantile([row["ps"] for row in overlap], 0.5)},
        "probe_speed_dps": {"median": quantile([row["pw"] for row in rows], 0.5), "during_overlap": quantile([row["pw"] for row in overlap], 0.5)},
        "aim_speed_dps": {"during_overlap": quantile([row["aw"] for row in overlap], 0.5), "p95": quantile([row["aw"] for row in overlap], 0.95)},
        "both_still_ms": ms(lambda row: row["aw"] < still and row["pw"] < still),
        "stationary_aim_moving_probe_ms": ms(lambda row: row["aw"] < still and row["pw"] >= still),
        "co_moving_ms": ms(lambda row: row["aw"] >= still and moving_with(row)),
        "aim_moving_otherwise_ms": ms(lambda row: row["aw"] >= still and not moving_with(row)),
    }


# The artifact: fpsdet.human-pilot/1.


MOTION_PARTS = ("both_still_ms", "stationary_aim_moving_probe_ms", "co_moving_ms", "aim_moving_otherwise_ms")


def motion_summary(challenges: list[dict]) -> dict:
    """Amendment 1, over challenges with any overlap: how their overlap divides between the aim point and the
    body both still, the aim holding while the body moved, the aim moving with it, and the aim moving otherwise."""
    touched = [challenge for challenge in challenges if challenge["overlap_ms"] > 0]
    overlap = max(sum(challenge["overlap_ms"] for challenge in touched), 1e-9)
    return {
        "challenges_with_overlap": len(touched),
        "share_of_overlap": {part[:-3]: round(sum(challenge[part] for challenge in touched) / overlap, 3) for part in MOTION_PARTS},
        **{part: summary([challenge[part] for challenge in touched]) for part in MOTION_PARTS},
        "aim_speed_dps": summary([challenge["aim_speed_dps"]["during_overlap"] for challenge in touched if challenge["aim_speed_dps"]["during_overlap"] is not None]),
        "probe_speed_dps": summary([challenge["probe_speed_dps"]["during_overlap"] for challenge in touched if challenge["probe_speed_dps"]["during_overlap"] is not None]),
        "probe_distance_m": summary([challenge["probe_distance_m"]["during_overlap"] for challenge in touched if challenge["probe_distance_m"]["during_overlap"] is not None]),
    }


def upper_zero(n: int) -> float | None:
    """The exact one-sided 95% upper bound on a proportion with no event in n trials (Clopper-Pearson)."""
    return round(1 - 0.05 ** (1 / n), 4) if n else None


def summary(values: list[float]) -> dict:
    return {"n": len(values), "median": quantile(values, 0.5), "p90": quantile(values, 0.9), "p95": quantile(values, 0.95), "max": max(values, default=None)}


def endpoint(findings: list[dict], complete: bool, units: dict) -> dict:
    """Amendment 2: the primary question, and which statistics its answer allows. The study stops on its first
    finding and continues only without one, so it is a falsification test, never a prevalence estimate."""
    question = amendment_2()["statistics"]["primary_question"]
    if findings:
        return {"question": question, "answer": "yes",
                "findings": [{key: row[key] for key in ("participant", "session", "mode")} for row in findings],
                "statistics": "the finding and descriptive counts only: no rate and no interval, at any level (amendment 2)"}
    if not complete:
        return {"question": question, "answer": "not yet: the planned group has not completed",
                "statistics": "descriptive counts only until the planned group completes (amendment 2)"}
    return {"question": question, "answer": "no",
            "upper_95_if_none": {unit: {"of": n, "bound": upper_zero(n)} for unit, n in units.items()},
            "statistics": ("exact one-sided 95% upper bounds with no finding, at each level, the participant level first. They follow the "
                           "pre-declared continuation rule, and none of them is a false-positive rate (amendment 2)")}


def analyze(data: Path, kind: str, samples: list[dict] | None = None) -> dict:
    found = design()
    people = humans(data) if kind == "human" else []
    if kind == "human":
        bound = {json.dumps(row.get("bindings"), sort_keys=True) for row in people}
        if bound != {json.dumps(bindings(), sort_keys=True)}:
            raise SystemExit("Participants were enrolled under different consent notices, designs or amendments, or under ones that have "
                             "changed since: they cannot be analysed as one study (amendment 2).")
    states = {row["participant"]: state(data, row) for row in people}
    sessions = [json.loads(path.read_text()) for path in sorted(data.glob("hp-*/*/session.json"))]
    sessions = [row for row in sessions if row["kind"] in (kind, "controlled_follower") and states.get(row["participant"]) != "withdrawn"]
    honest = [row for row in sessions if row["kind"] == kind]
    included = [row for row in honest if valid(row)]
    excluded = [{"participant": row["participant"], "session": row["session"], "played_ms": row["played_ms"],
                 "reason": (row.get("validity") or {}).get("reason") or "technical_failure"} for row in honest if row not in included]
    challenges = [dict(challenge, participant=row["participant"], session=row["session"], mode=row["mode"]) for row in included for challenge in row["challenges"]]
    participants = sorted({row["participant"] for row in included})
    bar = {"min_samples": found["frozen"]["hidden_track_min_samples"], "min_total_ms": found["frozen"]["hidden_track_min_ms"]}
    reviews = {"participants": sorted({row["participant"] for row in included if row["review_grade"]}),
               "sessions": sum(1 for row in included if row["review_grade"]),
               "challenges": sum(1 for challenge in challenges if challenge["status"] == "followed")}
    units = {"participants": len(participants), "sessions": len(included), "challenges": len(challenges)}
    counted = {unit: {"with_findings": len(reviews["participants"]) if unit == "participants" else reviews[unit], "of": n} for unit, n in units.items()}
    if kind == "human":
        tally = list(states.values())
        complete = tally.count("completed") >= found["participants"]["minimum_to_report"] and not any(
            status in ("enrolled", "practice_passed") for status in tally)
    else:
        complete = True
    primary = endpoint([row for row in included if row["review_grade"]], complete, units)
    per_participant = []
    for participant in participants:
        mine = [challenge for challenge in challenges if challenge["participant"] == participant]
        counted = [challenge["counted_ms"] for challenge in mine]
        strongest = max(mine, key=lambda challenge: (challenge["counted_ms"], challenge["longest_episode_ms"]), default=None)
        per_participant.append({
            "participant": participant,
            "sessions": sum(1 for row in included if row["participant"] == participant),
            "challenges_exposed": len(mine),
            "challenges_with_any_overlap": sum(1 for challenge in mine if challenge["overlap_ms"] > 0),
            "counted_ms": {"max": max(counted, default=0.0), "median": quantile(counted, 0.5), "p95": quantile(counted, 0.95)},
            "overlap_ms": {"max": max((challenge["overlap_ms"] for challenge in mine), default=0.0), "median": quantile([challenge["overlap_ms"] for challenge in mine], 0.5),
                           "p95": quantile([challenge["overlap_ms"] for challenge in mine], 0.95)},
            "longest_episode_ms": max((challenge["longest_episode_ms"] for challenge in mine), default=0.0),
            "reacquired": sum(1 for challenge in mine if challenge["episodes"] >= 2),
            "review_grade_findings": sum(1 for challenge in mine if challenge["status"] == "followed"),
            "strongest": None if strongest is None else {key: strongest[key] for key in ("mode", "room", "status", "counted_ms", "counted_moments", "longest_episode_ms", "episodes")},
            "decisions": sorted({row["case"]["decision"] for row in included if row["participant"] == participant}),
        })
    worst = max(challenges, key=lambda challenge: (challenge["counted_ms"], challenge["counted_moments"], challenge["overlap_ms"]), default=None)
    # How close people come to the bar (amendment 1): who ever reached each mark, and which sessions did.
    marks = {"any_overlap": lambda challenge: challenge["overlap_ms"] > 0, "counted_500_ms": lambda challenge: challenge["counted_ms"] >= 500,
             "counted_1200_ms": lambda challenge: challenge["counted_ms"] >= bar["min_total_ms"], "review_grade": lambda challenge: challenge["status"] == "followed"}
    closeness = {
        "participants": {name: sum(1 for participant in participants if any(test(challenge) for challenge in challenges if challenge["participant"] == participant))
                         for name, test in marks.items()},
        "sessions": {
            "any_overlap": sum(1 for row in included if any(challenge["overlap_ms"] > 0 for challenge in row["challenges"])),
            "repeated_overlap": sum(1 for row in included if any(challenge["episodes"] >= 2 for challenge in row["challenges"])),
            "review_grade": sum(1 for row in included if row["review_grade"]),
        },
        "of": {"participants": len(participants), "sessions": len(included)},
    }
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
                      "A consented honest-human pilot: a falsification test of the challenge rule on honest play, and a small behavioural baseline. "
                      "Not a population, a calibration, a validation, or a false-positive rate."),
        "primary": primary,
        "design": {"path": "examples/human-pilot/design.json", "sha256": pilot.sha256_file(DESIGN)},
        "code": code_identity(),
        "build": {"engines": [json.loads(text) for text in sorted({json.dumps(row["engine"], sort_keys=True) for row in included if row.get("engine")})],
                  "sessions_on_current_code": sum(1 for row in included if row.get("code") == code_identity()), "of": len(included)},
        "units": units,
        "participants": {status: list(states.values()).count(status) for status in ("enrolled", "practice_passed", "practice_failed", "completed", "discontinued", "withdrawn")},
        "excluded_sessions": excluded,
        "review_grade": {"bar": bar, **reviews, "counts": counted,
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
            **{key: worst[key] for key in ("probe_distance_m", "probe_speed_mps", "probe_speed_dps", "aim_speed_dps", *MOTION_PARTS)
               if key in worst},
            "against_the_bar": {"moments": f"{worst['counted_moments']} of {bar['min_samples']}", "ms": f"{worst['counted_ms']:.0f} of {bar['min_total_ms']:.0f}",
                                "crossed": worst["status"] == "followed"}},
        "controlled_follower": {
            "label": "the P12 stand-in for a reader of the probe it was sent, in this arena; for comparison only, never trained on",
            "challenges": len(followers),
            "counted_ms": summary([challenge["counted_ms"] for challenge in followers]),
            "longest_episode_ms": summary([challenge["longest_episode_ms"] for challenge in followers]),
            "followed": sum(1 for challenge in followers if challenge["status"] == "followed"),
            "reacquired": sum(1 for challenge in followers if challenge["episodes"] >= 2),
            **({"motion": motion_summary(followers)} if followers and "both_still_ms" in followers[0] else {}),
        },
        "closeness": closeness,
        **({"motion": motion_summary(challenges)} if challenges and "both_still_ms" in challenges[0] else {}),
        "amendment": {"path": "examples/human-pilot/amendment-1.json", "sha256": pilot.sha256_file(AMENDMENT)},
        "amendment_2": {"path": "examples/human-pilot/amendment-2.json", "sha256": pilot.sha256_file(AMENDMENT_2)},
        "stop": {"stopped": stopped(data) is not None, "reason": stopped(data),
                 "history": [json.loads(line) for line in (data / "stops.log").read_text().splitlines()] if (data / "stops.log").exists() else []},
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
                          for question in questions()} if kind == "human" else "not asked: machine stand-ins",
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


# Before the recorded sessions: the human client's controls checked without a person, and each participant's practice.


def server_saw(events: Path, participant: str) -> dict:
    """From the server's own telemetry: did the player move, and fire."""
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()] if events.exists() else []
    mine = [row for row in rows if row.get("player_id") == participant]
    return {"moving_ticks": sum(1 for row in mine if row["event_type"] == "movement" and row.get("speed_mps", 0) > 0),
            "shots": sum(1 for row in mine if row["event_type"] == "shot")}


def client_summary(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return next((json.loads(line) for line in lines[::-1] if '"summary"' in line), {})


def median(values: list[float]) -> float | None:
    return sorted(values)[len(values) // 2] if values else None


def cmd_controls_check(args: argparse.Namespace) -> int:
    """The study's human client, unchanged, driven by injected keyboard and mouse events under a virtual
    display: consent key, mouse look, arrow keys, W, fire. The server is the study server with no plan."""
    participant = "hp-00000000"
    work = Path(args.data).expanduser() if args.data else Path(tempfile.mkdtemp(prefix="fpsdet-controls-"))
    run = work / "controls-check"
    (run / "public").mkdir(parents=True, exist_ok=True)
    environment = pilot.godot_env(run)
    length = 20_000
    server = [args.godot, "--headless", "--path", str(PROJECT), "res://study.tscn", "--", "--role=server", f"--participant={participant}",
              "--mode=free", f"--match=controls-{participant}", f"--match-ms={length}", f"--port={args.port}", "--bind=127.0.0.1",
              f"--events={run / 'public' / 'events.ndjson'}", f"--log={run / 'public' / 'server.log'}"]
    display = [] if args.real_display or not shutil.which("xvfb-run") else ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24"]
    client = [*display, args.godot, "--path", str(PROJECT), "res://controls_check.tscn", "--", "--role=client", f"--participant={participant}",
              f"--port={args.port}", "--server=127.0.0.1", f"--match-ms={length}", "--instructions=controls check", f"--log={run / 'public' / 'client.jsonl'}"]
    play(run, environment, server, client, False, length)
    lines = (run / "public" / "client.jsonl").read_text(encoding="utf-8").splitlines() if (run / "public" / "client.jsonl").exists() else []
    report = next((json.loads(line)["report"] for line in lines if '"controls_check"' in line), {})
    saw = server_saw(run / "public" / "events.ndjson", participant)
    checks = {
        "consent key, then connected": report.get("consent_and_connect") is True,
        "mouse captured": report.get("mouse_captured") is True,
        "mouse turns the view": abs(report.get("mouse_look_yaw_deg", 0)) > 10 and abs(report.get("mouse_look_pitch_deg", 0)) > 2,
        "arrow keys turn the view": abs(report.get("arrow_keys_yaw_deg", 0)) > 10,
        "W moves the player (client)": report.get("w_moved_m", 0) > 0.5,
        "the server saw movement": saw["moving_ticks"] > 0,
        "the server saw shots": saw["shots"] > 0,
    }
    for name, passed in checks.items():
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
    print(json.dumps({"client": report, "server": saw, "folder": str(run)}, indent=1))
    return 0 if all(checks.values()) else 1


def practice_confirms() -> list[str]:
    """What a practice confirms: amendment 1's list, with amendment 2's Esc check after mouse look."""
    confirms = amendment()["practice"]["confirms"]
    return [confirms[0], amendment_2()["practice_gate"]["adds_confirm"], *confirms[1:]]


def practice_ready(answers: dict, fps: float | None) -> bool:
    """Amendment 2: the frame rate, and everything working, except that a mouse which cannot be captured falls back
    to the arrow keys; a mouse that can be captured must also let go with Esc."""
    confirms = practice_confirms()
    mouse, release = confirms[0], confirms[1]
    return (fps is not None and fps >= amendment()["practice"]["min_client_fps"]
            and all(answers[item] for item in confirms if item not in (mouse, release)) and (answers[release] or not answers[mouse]))


def cmd_practice(args: argparse.Namespace) -> int:
    """Amendment 1's practice: free mode, no challenge, 90 seconds, in its own data folder that is never analysed."""
    rules = amendment()["practice"]
    data, study = Path(args.data).expanduser().resolve(), Path(args.study).expanduser().resolve()
    if data == study or study in data.parents or data in study.parents:
        raise SystemExit("Practice goes in its own data folder, apart from the study's: it is never analysed.")
    record = load_participant(study, args.participant)
    if not record.get("consent"):
        raise SystemExit(f"{args.participant} has not agreed to take part.")
    if record.get("bindings") != bindings():
        raise SystemExit(f"The consent notice, the design or an amendment changed since {args.participant} enrolled: a new study version.")
    status = state(study, record)
    if status not in ("enrolled", "practice_failed"):
        raise SystemExit(f"{args.participant} is {status}: practice is for a participant who has not passed it yet.")
    if status == "practice_failed" and staging(study):
        raise SystemExit(f"{args.participant} failed practice and their place was taken: {staging(study)}")
    folder = data / args.participant
    run = folder / f"practice-{len(list(folder.glob('practice-*'))) + 1}"
    public = run / "public"
    public.mkdir(parents=True)
    environment = pilot.godot_env(run)
    length = rules["length_ms"]
    server = [args.godot, "--headless", "--path", str(PROJECT), "res://study.tscn", "--", "--role=server", f"--participant={args.participant}",
              f"--mode={rules['mode']}", f"--match=practice-{args.participant}-{run.name}", f"--match-ms={length}", f"--port={args.port}",
              f"--bind={args.bind}", f"--events={public / 'events.ndjson'}", f"--log={public / 'server.log'}"]
    instructions = "Practice, not part of the results: look with the mouse, then with the arrow keys; walk with W A S D; shoot a bot."
    client = [args.godot, "--path", str(PROJECT), "res://study.tscn", "--", "--role=client", f"--participant={args.participant}", f"--port={args.port}",
              f"--server={args.bind}", f"--match-ms={length}", f"--instructions={instructions}", f"--log={public / 'client.jsonl'}"]
    codes = play(run, environment, server, client, args.remote, length)
    confirms = practice_confirms()
    print("Ask the participant, and type y or n:")
    answers = {}
    for item in confirms:
        reply = ""
        while reply not in ("y", "n"):
            reply = input(f"  Did this work: {item}? [y/n] ").strip().lower()[:1]
        answers[item] = reply == "y"
    fps = median(client_summary(public / "client.jsonl").get("fps", []))
    result = {"participant": args.participant, "practice": run.name, "exit_codes": codes, "answers": answers,
              "client_fps_median": fps, "server": server_saw(public / "events.ndjson", args.participant)}
    result["ready"] = practice_ready(answers, fps)
    result["fallback"] = "arrow keys" if not answers[confirms[0]] else None
    (run / "practice.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    sweep = privacy_sweep(run)
    result["privacy"] = sweep
    if sweep["kept_hits"]:
        stop(study, [f"{args.participant} {run.name}: an address or a machine identity in a kept practice file: " + "; ".join(sweep["kept_hits"])])
    set_status(record, "practice_passed" if result["ready"] else "practice_failed")
    record["practice"] = {"date": datetime.date.today().isoformat(), "ready": result["ready"], "fallback": result["fallback"], "client_fps_median": fps}
    save_participant(study, record)
    print(json.dumps(result, indent=1))
    if fps is None or fps < rules["min_client_fps"]:
        print(f"The client ran at {fps} frames a second, under {rules['min_client_fps']}: {rules['below_min_fps']}")
    elif result["fallback"]:
        print(rules["fallback"])
    return 0 if result["ready"] else 1


def cmd_withdraw(args: argparse.Namespace) -> int:
    """Amendment 2. participant_request deletes every session, the practice and the comments, and keeps only an
    anonymous record that someone withdrew. unable_to_continue keeps the sessions and frees the place."""
    data = Path(args.data).expanduser()
    record = load_participant(data, args.participant)
    if record.get("kind", "human") != "human":
        raise SystemExit(f"{args.participant} is not a participant.")
    if args.reason == "unable_to_continue":
        set_status(record, "discontinued", reason_class=args.reason)
        save_participant(data, record)
        print(f"{args.participant} is discontinued. Their sessions stay; their place is free.")
        return 0
    folder = data / args.participant
    deleted = []
    for child in sorted(folder.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
            deleted.append(child.name)
    if args.practice:
        practice = Path(args.practice).expanduser().resolve()
        if practice == data.resolve():
            raise SystemExit("--practice is the practice folder, not the study's.")
        if (practice / args.participant).exists():
            shutil.rmtree(practice / args.participant)
            deleted.append("practice")
    kept = {"participant": record["participant"], "kind": "human", "status": "withdrawn", "sessions_deleted": True, "reason_class": args.reason,
            "withdrawn": datetime.date.today().isoformat(), "bindings": record.get("bindings")}
    save_participant(data, kept)
    print(f"{args.participant} withdrew: deleted {', '.join(deleted) or 'nothing (no sessions yet)'}. Kept: that someone withdrew, under the random id.")
    published = sorted((ROOT / "examples" / "human-pilot" / "sessions").glob(f"{args.participant}-*"))
    if published:
        print("Their published samples are in the repository; delete them too, and record the withdrawal in the next analysis:")
        for path in published:
            print(f"  {path.relative_to(ROOT)}")
    if args.practice is None:
        print("Their practice folder was not given: delete it with --practice, or by hand.")
    return 0


# Commands.


def cmd_session(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    record = run_session(args.godot, data, args.participant, args.mode, args.port, "controlled_follower" if args.follower else "human",
                         bind=args.bind, remote=args.remote)
    print(json.dumps({key: record[key] for key in ("participant", "mode", "session", "played_ms", "validity", "review_grade", "live_vs_offline", "stop")}, indent=1))
    return 1 if record["stop"] else 0


def cmd_clear_stop(args: argparse.Namespace) -> int:
    data = Path(args.data).expanduser()
    reason = stopped(data)
    if reason is None:
        print("The study is not stopped.")
        return 0
    if FALSIFYING in reason:
        raise SystemExit("A review-grade finding on a protocol-valid honest session ends collection (amendments 1 and 2): preserve and replay the "
                         "session, verify its packet and graph, inspect its episodes, classify the behaviour, and propose the next phase. "
                         "Continuing would be a new study version, not a cleared stop.")
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
    print(f"Wrote {args.out}: {body['units']}; primary question: {body['primary']['answer']}; digest {body['digest']}")
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
    controls = sub.add_parser("controls-check", help="The human client's controls, driven by injected keyboard and mouse events; no person")
    controls.add_argument("--godot", required=True)
    controls.add_argument("--data", help="Where to keep its telemetry (a new temporary folder if not given)")
    controls.add_argument("--port", type=int, default=24890)
    controls.add_argument("--real-display", action="store_true", help="Open the window on this display (it takes the mouse for a few seconds) instead of a virtual one")
    controls.set_defaults(func=cmd_controls_check)
    practice = sub.add_parser("practice", help="A participant's 90-second practice, no challenge, in its own data folder (amendment 1)")
    practice.add_argument("--data", required=True, help="The practice folder: never the study's, never analysed")
    practice.add_argument("--study", required=True, help="The study's data folder, where the participant is enrolled")
    practice.add_argument("--participant", required=True)
    practice.add_argument("--godot", required=True)
    practice.add_argument("--port", type=int, default=24800)
    practice.add_argument("--bind", default="127.0.0.1", help="A private LAN address for a participant on another machine; never a public one")
    practice.add_argument("--remote", action="store_true", help="Print the client command to run on the participant's machine instead of opening it here")
    practice.set_defaults(func=cmd_practice)
    withdraw = sub.add_parser("withdraw", help="A participant leaves (amendment 2): delete their data on request, or free their place")
    withdraw.add_argument("--data", required=True)
    withdraw.add_argument("--participant", required=True)
    withdraw.add_argument("--reason", required=True, choices=list(WITHDRAWALS),
                          help="participant_request deletes everything but an anonymous record; unable_to_continue keeps the sessions")
    withdraw.add_argument("--practice", help="The practice folder, so their practice is deleted too")
    withdraw.set_defaults(func=cmd_withdraw)
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
