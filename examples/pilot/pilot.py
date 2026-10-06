"""The live challenge pilot's harness (docs/pilot.md).

It plans each match with fpsdet's own planner and a fresh server secret, runs the Godot dedicated server
and two stock clients on this machine (localhost only), captures the NDJSON the server wrote, scores it
with fpsdet, and checks everything examples/pilot/qualification.json declares: the outcome of every
scenario, the packets and graphs, live against offline scoring, the realization reproduced from the
secret after the match, no secret in any public output, the stock client's view, and gameplay with and
without the probe.

    python examples/pilot/pilot.py godot --dest ~/godot-4.7.2          # download and check the pinned engine (user-local)
    python examples/pilot/pilot.py qualify --godot PATH --out /tmp/pilot  # every scenario, failure and A/B run; writes result.json
    python examples/pilot/pilot.py run --godot PATH --scenario illicit_follower --out /tmp/one
    python examples/pilot/pilot.py verify                                # offline, no engine: the committed capture against result.json

Everything a run writes stays in --out: Godot's own user data too (XDG_*_HOME point there). The secret
and the realization the server ran are written to <run>/private, readable by the owner only, and are
never copied into the repository. Standard library only, beside fpsdet.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

PROJECT = HERE / "godot"
PROFILE = HERE / "pilot.json"
QUALIFICATION = HERE / "qualification.json"
CAPTURED = HERE / "captured"
RESULT = HERE / "result.json"
PILOT_FORMAT = "fpsdet.pilot/1"
SUBJECT, ENEMY = "pilot-subject", "pilot-enemy"
MATCH_MS = 36_000
BUDGET = ["--from-ms", "8000", "--to-ms", "30000", "--count", "1", "--min-duration-ms", "8000", "--max-duration-ms", "12000"]
# What a realization holds; none of these names may appear in a public output.
REALIZATION_WORDS = ("heading_offset_deg", "replay_delay_ms", "route_pick", "placement_pick", "placement_index", "path_sha256")
MEDIA = ROOT / "docs" / "pilot"
CODE = ("project.godot", "main.tscn", "main.gd", "scripts/arena.gd", "scripts/recipe.gd", "scripts/server.gd", "scripts/client.gd")


def env() -> dict:
    return {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}


def fpsdet(*argv: str, check: bool = True) -> subprocess.CompletedProcess:
    done = subprocess.run([sys.executable, "-m", "fpsdet", *argv], cwd=ROOT, env=env(), capture_output=True, text=True)
    if check and done.returncode != 0:
        raise SystemExit(f"fpsdet {' '.join(argv)} failed:\n{done.stdout}\n{done.stderr}")
    return done


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_identity() -> dict:
    from fpsdet.provenance import normalized_source

    files = {f"examples/pilot/godot/{name}": "sha256:" + hashlib.sha256(normalized_source((PROJECT / name).read_bytes())).hexdigest() for name in CODE}
    files["examples/pilot/pilot.py"] = "sha256:" + hashlib.sha256(normalized_source(Path(__file__).read_bytes())).hexdigest()
    return files


def qualification() -> dict:
    return json.loads(QUALIFICATION.read_text(encoding="utf-8"))


# The engine: the official build, checked against the SHA-512 the qualification pins.


def cmd_godot(args: argparse.Namespace) -> int:
    engine = qualification()["engine"]
    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / Path(engine["download"]).name
    if not archive.exists():
        import urllib.request

        with urllib.request.urlopen(engine["download"]) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
    found = hashlib.sha512(archive.read_bytes()).hexdigest()
    if found != engine["sha512"]:
        raise SystemExit(f"{archive}: SHA-512 {found}, not the pinned {engine['sha512']}. Not unpacked.")
    with zipfile.ZipFile(archive) as unpacked:
        unpacked.extractall(dest)
    binary = dest / archive.stem
    binary.chmod(0o755)
    print(f"Godot {engine['version']} checked and unpacked: {binary}. Nothing was installed system-wide.")
    return 0


# One live run.


def godot_env(run: Path) -> dict:
    data = run / "godot-user"
    data.mkdir(parents=True, exist_ok=True)
    return {**os.environ, "XDG_DATA_HOME": str(data), "XDG_CONFIG_HOME": str(data), "XDG_CACHE_HOME": str(data)}


def plan_match(run: Path, match_id: str) -> tuple[Path, Path]:
    public, private = run / "public", run / "private"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    secret = private / "secret.hex"
    if not secret.exists():
        fpsdet("challenge", "keygen", "--out", str(secret))
    plan = public / "plan.json"
    fpsdet("challenge", "plan", "--profile", str(PROFILE), "--match", match_id, "--player", SUBJECT, *BUDGET, "--secret-file", str(secret), "--out", str(plan))
    return plan, secret


def run_scenario(godot: str, scenario: str, run: Path, port: int, render: bool = False, server_only: bool = False,
                 plan: Path | None = None, secret: Path | None = None, match_id: str | None = None, extra: tuple = ()) -> dict:
    """Run one live match. The server and the clients are separate Godot processes on 127.0.0.1."""
    match_id = match_id or f"pilot-{scenario}"
    public, private = run / "public", run / "private"
    public.mkdir(parents=True, exist_ok=True)
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    if plan is None and not server_only:
        plan, secret = plan_match(run, match_id)
    environment = godot_env(run)
    server = [godot, "--headless", "--path", str(PROJECT)]
    if server_only:
        server.append("--fixed-fps=60")
    server += ["--", "--role=server", f"--port={port}", f"--match={match_id}", f"--scenario={scenario}", f"--match-ms={MATCH_MS}",
               f"--events={public / 'events.ndjson'}", f"--log={public / 'server.log'}", f"--private={private}", f"--perf={public / 'perf.json'}"]
    if plan is not None:
        server.append(f"--plan={plan}")
    if secret is not None:
        server.append(f"--secret-file={secret}")
    if server_only:
        server.append("--deterministic=true")
    server += list(extra)
    started = time.time()
    processes = [subprocess.Popen(server, env=environment, stdout=(run / "server.out").open("w"), stderr=subprocess.STDOUT)]
    if not server_only:
        time.sleep(1.5)
        for name in (ENEMY, SUBJECT):
            client = [godot]
            draws = render and name == SUBJECT
            if draws:
                # A private virtual display, so no window opens on anyone's desktop; software OpenGL.
                client = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24", godot, "--rendering-driver", "opengl3", "--resolution", "640x360"]
                (public / "shots").mkdir(exist_ok=True)
            else:
                client.append("--headless")
            behaviour = "spinner" if scenario == "accidental_crossing" else "honest"
            client += ["--path", str(PROJECT), "--", "--role=client", f"--name={name}", f"--port={port}", f"--behaviour={behaviour}",
                       f"--log={public / ('client-subject.jsonl' if name == SUBJECT else 'client-enemy.jsonl')}"]
            if draws:
                client += ["--render=true", f"--shots={public / 'shots'}", "--check-every-ms=2000"]
            processes.append(subprocess.Popen(client, env=environment, stdout=(run / f"{name}.out").open("w"), stderr=subprocess.STDOUT))
    codes = []
    for process in processes:
        try:
            codes.append(process.wait(timeout=MATCH_MS / 1000 + 90))
        except subprocess.TimeoutExpired:
            process.kill()
            codes.append("timeout")
    return {"scenario": scenario, "match_id": match_id, "exit_codes": codes, "wall_s": round(time.time() - started, 1), "plan": plan, "secret": secret}


# Scoring, packets and graphs.


def score(events: Path, plan: Path | None, out: Path) -> dict:
    """Score the captured events with fpsdet's own command, and read every case's identity."""
    from fpsdet.challenge import ChallengeRegistry, case_problems, plan_file_from_dict
    from fpsdet.graph import verify_graph
    from fpsdet.provenance import verify_packet

    argv = ["score", str(events), "--profile", str(PROFILE), "--out", str(out)]
    if plan is not None:
        argv += ["--challenges", str(plan)]
    done = fpsdet(*argv)
    registry = ChallengeRegistry.from_files([plan_file_from_dict(json.loads(plan.read_text()))]) if plan is not None else ChallengeRegistry()
    cases = {}
    for path in sorted(out.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(case, dict) or "evidence" not in case or "player_id" not in case:
            continue
        name = case["player_id"]
        evidence = case["evidence"]
        cases[name] = {
            "decision": case["decision"],
            "observations": sorted(obs["observation_id"] for obs in evidence["observations"]),
            "kinds": sorted(obs["kind"] for obs in evidence["observations"]),
            "graph": evidence["graph"]["digest"],
            "packet": evidence["packet"]["digest"],
            "packet_recipe": evidence["packet"]["recipe"],
            "inputs": {key: evidence["provenance"]["inputs"][key] for key in ("recipe", "digest", "events")},
            "detector": evidence["provenance"]["detector"]["digest"],
            "challenges": evidence.get("challenges", []),
            "notes": [line for line in case.get("observations", []) if "hallenge" in line],
            "problems": verify_packet(case) + [f"graph: {problem}" for problem in verify_graph(case)] + case_problems(case, registry),
        }
    return {"cases": cases, "stdout": done.stdout}


def semantic(scored: dict) -> dict:
    """What must be identical between live and offline scoring: everything but run-local notes."""
    return {name: {key: value for key, value in case.items() if key != "notes"} for name, case in scored["cases"].items()}


def outcome(scored: dict, player: str = SUBJECT) -> dict:
    subject = scored["cases"].get(player, {})
    results = subject.get("challenges", [])
    result = results[0] if results else {}
    return {
        "status": result.get("status", "none"),
        "cause": result.get("cause"),
        "tracked_samples": result.get("tracked_samples", 0),
        "verified_samples": result.get("verified_samples", 0),
        "eligible_samples": result.get("eligible_samples", 0),
        "total_ms": result.get("total_ms", 0.0),
        "not_counted": result.get("not_counted", {}),
        "evidence": "occluded_motion_replay" in subject.get("kinds", []),
        "decision": subject.get("decision"),
        "diagnostic": bool(subject.get("notes")),
    }


def as_expected(expected: dict, found: dict, bar_ms: float) -> list[str]:
    problems = []
    for key in ("status", "cause", "evidence", "decision"):
        if key in expected and expected[key] != found.get(key):
            problems.append(f"{key} is {found.get(key)!r}, expected {expected[key]!r}")
    if expected.get("tracked_below_bar") and not found["total_ms"] < bar_ms:
        problems.append(f"tracked {found['total_ms']} ms, not below the {bar_ms} ms bar")
    if "not_counted" in expected and not found["not_counted"].get(expected["not_counted"]):
        problems.append(f"no sample was left out as {expected['not_counted']}")
    if expected.get("diagnostic") and not found["diagnostic"]:
        problems.append("no diagnostic note")
    return problems


# The secret, after the match.


def reproduce(run: Path, plan: Path, secret: Path | None) -> dict:
    """With the secret: does it make exactly this plan, and the realization the server ran? Without it:
    say so. Never prints a value that came from the secret."""
    from fpsdet.challenge import plan_file_from_dict
    from fpsdet.challenge_plan import SecretError, load_secret, realize
    from fpsdet.challenge_plan import reproduce as reproduce_plan

    plan_file = plan_file_from_dict(json.loads(plan.read_text()))
    try:
        key, _warnings = load_secret(secret, environ={}) if secret is not None else load_secret(None, environ={})
    except SecretError as error:
        return {"status": "secret_unavailable", "detail": str(error)}
    problems = list(reproduce_plan(key, plan_file))
    checked = []
    for record in plan_file.plans:
        ran_path = run / "private" / f"realization-{record.challenge_id}.json"
        if not ran_path.exists():
            problems.append(f"{record.challenge_id}: the server did not run it")
            continue
        ran = json.loads(ran_path.read_text())
        realization = realize(key, record, plan_file.budget)
        derived = dict(realization.parameters)
        if {name: int(value) for name, value in ran["parameters"].items()} != derived:
            problems.append(f"{record.challenge_id}: the server's realization is not the one the secret derives")
        if ran["placement_index"] != derived["placement_pick"] % 6 or ran["source"] != ENEMY:
            problems.append(f"{record.challenge_id}: the server's placement or route is not the realization's")
        if not (record.start_ms <= ran["first_ms"] and ran["last_ms"] <= record.end_ms) or ran["challenge_id"] != record.challenge_id:
            problems.append(f"{record.challenge_id}: the server ran it outside its planned window")
        checked.append(record.challenge_id)
    return {"status": "reproduced" if not problems else "differs", "challenges": checked, "problems": problems}


def secret_material(plan: Path, secret: Path) -> list[bytes]:
    """The secret and every realization's material and parameter values, in every spelling a leak could take."""
    from fpsdet.challenge import plan_file_from_dict
    from fpsdet.challenge_plan import load_secret, message

    key, _ = load_secret(secret, environ={})
    raw_key = bytes.fromhex(secret.read_text().strip())
    found = [raw_key]
    plan_file = plan_file_from_dict(json.loads(plan.read_text()))
    for record in plan_file.plans:
        found.append(key.mac(message("realization", record.game_id, record.match_id, record.subject_id, record.nonce, record.spec, record.counter)))
    out = []
    for value in found:
        out += [value, value.hex().encode(), value.hex().upper().encode(), base64.b64encode(value), base64.urlsafe_b64encode(value)]
    return out


def leaks(paths: list[Path], plan: Path | None, secret: Path | None) -> list[str]:
    """Every public file, byte for byte: no secret, no realization material, no realization field."""
    needles = secret_material(plan, secret) if plan is not None and secret is not None and secret.exists() else []
    found = []
    for path in paths:
        if not path.is_file():
            continue
        data = path.read_bytes()
        for needle in needles:
            if needle in data:
                found.append(f"{path.name}: secret or realization material")
        for word in REALIZATION_WORDS:
            if word.encode() in data:
                found.append(f"{path.name}: {word}")
    return found


# What the stock client received, drew and heard, and what the server measured.


def client_facts(public: Path) -> dict:
    server_log = [json.loads(line) for line in (public / "server.log").read_text().splitlines() if line.strip()]
    end = next((row for row in server_log if row["kind"] == "match_end"), {})
    ids = end.get("entities", {})
    facts = {"entities": ids}
    for who, name in (("subject", "client-subject.jsonl"), ("enemy", "client-enemy.jsonl")):
        path = public / name
        if not path.exists():
            continue
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        summary = next((row for row in rows if row["kind"] == "summary"), {})
        received = summary.get("entities", {})
        probe = received.get(ids.get("probe"), {})
        facts[who] = {
            "probe_updates": probe.get("updates", 0),
            "probe_bytes": probe.get("bytes", 0),
            "enemy_or_subject_updates": sum(row["updates"] for entity, row in received.items() if entity != ids.get("probe")),
            "probe_sounds": sum(count for key, count in summary.get("sounds", {}).items() if key.split(":")[0] == ids.get("probe")),
            "scoreboard_rows": len(summary.get("scoreboard", [])),
        }
        checks = [row for row in rows if row["kind"] == "pixels"]
        if checks:
            probe_pixels = [cell["pixels"] for row in checks for cell in row["entities"] if cell["entity"] == ids.get("probe") and cell["drawn"]]
            enemy_pixels = [cell["pixels"] for row in checks for cell in row["entities"] if cell["entity"] == ids.get("enemy") and cell["drawn"]]
            facts[who]["pixel_checks"] = len(checks)
            facts[who]["probe_checks_drawn"] = len(probe_pixels)
            facts[who]["probe_pixels_max"] = max(probe_pixels, default=0)
            facts[who]["probe_checks_with_pixels"] = sum(1 for value in probe_pixels if value > 0)
            facts[who]["enemy_checks_with_pixels"] = sum(1 for value in enemy_pixels if value > 0)
            facts[who]["unchanged_frame_pixels_max"] = max((row["unchanged_frame_pixels"] for row in checks), default=0)
            facts[who]["screenshots"] = sorted(row["screenshot"] for row in checks if row.get("screenshot"))
            fps = summary.get("fps", [])
            facts[who]["fps_median"] = sorted(fps)[len(fps) // 2] if fps else None
    return facts


def timing(public: Path) -> dict:
    """Server cadence and timestamps, from the captured events and the server's own counters."""
    from fpsdet.parse import load_events

    perf = json.loads((public / "perf.json").read_text())
    events, errors = load_events(public / "events.ndjson")
    grid = all(abs(event.t_ms - round(round(event.t_ms * 60 / 1000) * 1000 / 60)) == 0 for event in events)
    by_player: dict[str, list[int]] = {}
    for event in events:
        if event.event_type == "movement":
            by_player.setdefault(event.player_id, []).append(event.t_ms)
    steps = sorted({b - a for rows in by_player.values() for a, b in zip(rows, rows[1:])})
    track = [event.challenge_track_ms for event in events if event.challenge_track_ms is not None and event.event_type == "movement"]
    tick_ms = 1000 / 60
    off_grid = max((abs(value / tick_ms - round(value / tick_ms)) * tick_ms for value in track), default=0.0)
    emitted = sum(event.challenge_track_ms for event in {(event.t_ms, event.challenge_track_ms): event for event in events if event.challenge_track_ms is not None}.values())
    seconds = perf["match_ms"] / 1000
    return {
        "server_tick_hz": 60,
        "challenge_update": "every server tick (60 Hz) while a window is open",
        "knowledge_queries": "every server tick (60 Hz) while a window is open",
        "movement_event_spacing_ms": steps,
        "t_ms_on_tick_grid": grid,
        "parse_errors": len(errors),
        "track_ms_resolution_ms": round(tick_ms, 3),
        "track_ms_off_grid_max_ms": round(off_grid, 6),
        "track_ms_emitted": round(emitted, 3),
        "track_ms_server": round(perf.get("tracked_ticks", 0) * tick_ms, 3),
        "tick_us_mean": round(perf["tick_us"] / max(perf["ticks"], 1), 1),
        "tick_us_max": perf["tick_us_max"],
        "challenge_us_per_probe_tick": round(perf["challenge_us"] / max(perf["probe_ticks"], 1), 1),
        "knowledge_us_per_probe_tick": round(perf["knowledge_us"] / max(perf["probe_ticks"], 1), 1),
        "telemetry_us_per_tick": round(perf["telemetry_us"] / max(perf["ticks"], 1), 1),
        "rays_per_probe_tick": round(perf["rays"] / max(perf["probe_ticks"], 1), 1),
        "event_bytes_per_s": round(perf["event_bytes"] / seconds, 1),
        "snapshot_bytes_per_s": {name: round(value / seconds, 1) for name, value in perf.get("snapshot_bytes", {}).items()},
        "server_memory_static_peak_bytes": perf.get("memory_static_peak_bytes"),
        "verdicts": perf.get("verdicts", {}),
        "probe_ticks": perf["probe_ticks"],
    }


# Failures: the captured telemetry damaged, scored again; and the server refusing what it must refuse.


def telemetry_failures(public: Path, work: Path) -> list[dict]:
    """Each damage to the follower's capture, scored. None may make the evidence stronger than undamaged."""
    lines = (public / "events.ndjson").read_text().splitlines()
    plan = public / "plan.json"
    base = outcome(score(public / "events.ndjson", plan, work / "undamaged"))
    plan_data = json.loads(plan.read_text())
    window = plan_data["challenges"][0]["end_ms"] - plan_data["challenges"][0]["start_ms"]

    def damaged(name: str, rows: list[str], with_plan: bool = True, player: str = SUBJECT) -> dict:
        path = work / f"{name}.ndjson"
        path.write_text("\n".join(rows) + "\n")
        found = outcome(score(path, plan if with_plan else None, work / name), player)
        stronger = found["total_ms"] > base["total_ms"] or found["tracked_samples"] > base["tracked_samples"] or (found["evidence"] and not base["evidence"])
        return {"failure": name, "status": found["status"], "cause": found["cause"], "tracked_samples": found["tracked_samples"],
                "evidence": found["evidence"], "not_counted": found["not_counted"], "stronger_than_undamaged": stronger}

    def edit(row: str, change) -> str:
        data = json.loads(row)
        change(data)
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    named = [line for line in lines if '"challenge_id"' in line]
    out = [
        damaged("missing_plan", lines, with_plan=False),
        damaged("unknown_challenge_id", [edit(line, lambda d: d.update(challenge_id="ch-000000000000000000000000")) if '"challenge_id"' in line else line for line in lines]),
        damaged("wrong_player", [edit(line, lambda d: d.update(player_id="pilot-someone-else")) if '"pilot-subject"' in line else line for line in lines],
                player="pilot-someone-else"),
        damaged("wrong_match", [edit(line, lambda d: d.update(match_id="pilot-another-match")) for line in lines]),
        damaged("stale_window", [edit(line, lambda d: d.update(t_ms=d["t_ms"] + window + 1000)) if '"challenge_id"' in line else line for line in lines]),
        damaged("duplicate_events", lines + lines),
        damaged("malformed_channel_state", [edit(line, lambda d: d.update(challenge_vision_state="hidden")) if line in named[::2] else line for line in lines]),
    ]
    return out


def server_failures(godot: str, root: Path, port: int) -> list[dict]:
    """The server's own refusals: no plan, a plan for another match, a tampered record, no secret. In each,
    no event names a challenge."""
    out = []
    template = root / "failure-plan"
    plan, secret = plan_match(template, "pilot-server-failures")
    data = json.loads(plan.read_text())
    tampered = template / "public" / "tampered.json"
    data_t = json.loads(plan.read_text())
    data_t["challenges"][0]["start_ms"] += 1000
    data_t["challenges"][0]["end_ms"] += 1000
    from fpsdet.challenge import plan_from_dict
    from fpsdet.challenge import ChallengePlan

    record = data_t["challenges"][0]
    rebuilt = ChallengePlan(**{key: record[key] for key in ("challenge_id", "challenge_type", "version", "game_id", "match_id", "subject_id", "counter", "nonce", "start_ms", "end_ms", "commitment")})
    record["plan"] = rebuilt.digest  # digested again: the public check passes, only the secret can tell
    plan_from_dict(record)
    tampered.write_text(json.dumps(data_t, indent=1))
    cases = {
        "missing_plan": {"plan": None, "secret": secret, "match": "pilot-server-failures"},
        "wrong_match": {"plan": plan, "secret": secret, "match": "pilot-some-other-match"},
        "tampered_plan": {"plan": tampered, "secret": secret, "match": "pilot-server-failures"},
        "secret_unavailable_on_server": {"plan": plan, "secret": template / "private" / "no-such-secret.hex", "match": "pilot-server-failures"},
    }
    for name, setup in cases.items():
        run = root / f"server-{name}"
        done = run_scenario(godot, "honest", run, port, server_only=True, plan=setup["plan"], secret=setup["secret"], match_id=setup["match"])
        log = [json.loads(line) for line in (run / "public" / "server.log").read_text().splitlines() if line.strip()]
        plan_row = next((row for row in log if row["kind"] == "plan"), {})
        named = sum(1 for line in (run / "public" / "events.ndjson").read_text().splitlines() if '"challenge_id"' in line)
        out.append({"failure": name, "server": plan_row.get("status"), "detail": plan_row.get("detail") or [row["detail"] for row in plan_row.get("refused", [])],
                    "events_naming_a_challenge": named, "exit_codes": done["exit_codes"]})
    return out


def non_interference(godot: str, root: Path, port: int) -> dict:
    """The same scripted match twice, server only and tick for tick: once with the probe running, once
    without. Every position, hit, damage and score must be identical."""
    run_with = root / "ab-with"
    plan, secret = plan_match(run_with, "pilot-ab")
    with_ = run_scenario(godot, "ab_with", run_with, port, server_only=True, plan=plan, secret=secret, match_id="pilot-ab", extra=(f"--ab-out={run_with / 'public' / 'ab.json'}",))
    run_without = root / "ab-without"
    without = run_scenario(godot, "ab_without", run_without, port, server_only=True, match_id="pilot-ab", extra=("--no-challenge=true", f"--ab-out={run_without / 'public' / 'ab.json'}"))
    a = json.loads((run_with / "public" / "ab.json").read_text())
    b = json.loads((run_without / "public" / "ab.json").read_text())
    perf_a = json.loads((run_with / "public" / "perf.json").read_text())
    perf_b = json.loads((run_without / "public" / "perf.json").read_text())
    return {
        "ticks": a["ticks"],
        "probe_ticks": perf_a["probe_ticks"],
        "gameplay_digest_with": a["digest"],
        "gameplay_digest_without": b["digest"],
        "identical": a["digest"] == b["digest"] and a["scoreboard"] == b["scoreboard"],
        "scoreboard": a["scoreboard"],
        "tick_us_mean_with": round(perf_a["tick_us"] / perf_a["ticks"], 1),
        "tick_us_mean_without": round(perf_b["tick_us"] / perf_b["ticks"], 1),
        "exit_codes": [with_["exit_codes"], without["exit_codes"]],
    }


# The qualification.


def qualify(godot: str, root: Path, port: int, render: tuple = ("illicit_follower", "exposed_vision")) -> dict:
    found = qualification()
    from fpsdet.parse import load_profile

    bar_ms = load_profile(PROFILE).hidden_track_min_ms
    scenarios = []
    for index, entry in enumerate(found["scenarios"]):
        name = entry["id"]
        run = root / name
        live = run_scenario(godot, name, run, port + index, render=name in render)
        public = run / "public"
        scored = [score(public / "events.ndjson", live["plan"], run / f"cases-{label}") for label in ("live", "offline-1", "offline-2")]
        identical = all(semantic(other) == semantic(scored[0]) for other in scored[1:])
        observed = outcome(scored[0])
        public_files = [path for path in run.rglob("*") if "private" not in path.parts and path.is_file()]
        scenarios.append({
            "id": name,
            "expected": entry["expected"],
            "observed": observed,
            "problems": as_expected(entry["expected"], observed, bar_ms),
            "live_vs_offline": "identical" if identical else "DIFFERENT",
            "realization": reproduce(run, live["plan"], live["secret"]),
            "secret_unavailable": reproduce(run, live["plan"], None)["status"],
            "leaks": leaks(public_files, live["plan"], live["secret"]),
            "client": client_facts(public),
            "timing": timing(public),
            "cases": semantic(scored[0]),
            "plan": live["plan"],
            "events": public / "events.ndjson",
            "exit_codes": live["exit_codes"],
            "wall_s": live["wall_s"],
        })
        print(f"{name}: {observed['status']} {observed['cause'] or ''} evidence={observed['evidence']} -> {'as expected' if not scenarios[-1]['problems'] else scenarios[-1]['problems']}", flush=True)
    follower = next(row for row in scenarios if row["id"] == "illicit_follower")
    damaged = telemetry_failures(root / "illicit_follower" / "public", root / "failures")
    refused = server_failures(godot, root / "server-failures", port + 50)
    ab = non_interference(godot, root / "non-interference", port + 60)
    return {"scenarios": scenarios, "telemetry_failures": damaged, "server_failures": refused, "non_interference": ab, "follower": follower["id"]}


def frame(run: Path, probe_pixels: bool) -> Path | None:
    """A screenshot from a pixel check inside the window where the probe was drawn: with no pixel of it on the
    screen, or with the most."""
    public = run / "public"
    probe = client_facts(public)["entities"].get("probe")
    best, chosen = -1, None
    for line in (public / "client-subject.jsonl").read_text().splitlines():
        row = json.loads(line)
        if row["kind"] != "pixels" or not row.get("screenshot"):
            continue
        for cell in row["entities"]:
            if cell["entity"] == probe and cell["drawn"]:
                score_ = cell["pixels"] if probe_pixels else (1 if cell["pixels"] == 0 else -1)
                if score_ > best:
                    best, chosen = score_, public / "shots" / row["screenshot"]
    return chosen


def capture(results: dict) -> dict:
    """Copy each scenario's public plan and server telemetry into the repository, and build fpsdet.pilot/1."""
    from fpsdet.challenge import plan_file_from_dict

    found = qualification()
    CAPTURED.mkdir(exist_ok=True)
    rows = []
    for row in results["scenarios"]:
        folder = CAPTURED / row["id"]
        folder.mkdir(exist_ok=True)
        shutil.copyfile(row["plan"], folder / "plan.json")
        shutil.copyfile(row["events"], folder / "events.ndjson")
        plan_file = plan_file_from_dict(json.loads((folder / "plan.json").read_text()))
        rows.append({
            "id": row["id"],
            "expected": row["expected"],
            "observed": row["observed"],
            "as_expected": not row["problems"],
            "problems": row["problems"],
            "plan": {"file": f"examples/pilot/captured/{row['id']}/plan.json", "sha256": sha256_file(folder / "plan.json"),
                     "challenges": [{"challenge_id": plan.challenge_id, "version": plan.version, "plan": plan.digest, "commitment": plan.commitment,
                                     "window": [plan.start_ms, plan.end_ms]} for plan in plan_file.plans]},
            "events": {"file": f"examples/pilot/captured/{row['id']}/events.ndjson", "sha256": sha256_file(folder / "events.ndjson"),
                       "lines": len((folder / "events.ndjson").read_text().splitlines())},
            "cases": row["cases"],
            "live_vs_offline": row["live_vs_offline"],
            "realization": {"status": row["realization"]["status"], "problems": row["realization"]["problems"]},
            "without_secret": row["secret_unavailable"],
            "secret_leaks": row["leaks"],
            "client": row["client"],
            "timing": row["timing"],
        })
    body = {
        "format": PILOT_FORMAT,
        "pilot": found["pilot"],
        "pilot_version": 1,
        "class": "live_controlled_pilot",
        "engine": {"name": found["engine"]["name"], "version": found["engine"]["version"], "sha512": found["engine"]["sha512"]},
        "code": code_identity(),
        "qualification": sha256_file(QUALIFICATION),
        "profile": sha256_file(PROFILE),
        "scenarios": rows,
        "telemetry_failures": results["telemetry_failures"],
        "server_failures": results["server_failures"],
        "non_interference": results["non_interference"],
        "media": media(results),
        "not": "Not a deployment and not a detection rate: a controlled qualification of one integration, with scripted players.",
    }
    body["digest"] = digest(body)
    RESULT.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return body


def media(results: dict) -> dict:
    """The stock client's own screen: the probe drawn behind the wall (no pixel shows), and placed in the open."""
    MEDIA.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, scenario, pixels in (("stock-client-probe-hidden.png", "illicit_follower", False), ("stock-client-probe-exposed.png", "exposed_vision", True)):
        run = next((Path(row["events"]).parents[1] for row in results["scenarios"] if row["id"] == scenario), None)
        chosen = frame(run, pixels) if run else None
        if chosen is not None:
            shutil.copyfile(chosen, MEDIA / name)
            out[name] = {"scenario": scenario, "frame": chosen.name, "sha256": sha256_file(MEDIA / name)}
    return out


def digest(body: dict) -> str:
    from fpsdet.evidence import canonical_json

    return "sha256:" + hashlib.sha256(b"fpsdet.pilot/1\0" + canonical_json({key: value for key, value in body.items() if key != "digest"}).encode("utf-8")).hexdigest()


# Offline: the committed capture against the committed result, with no engine.


def verify(result_path: Path = RESULT) -> list[str]:
    """Score every committed capture again and compare it with result.json: the same decisions,
    observation ids and graph; the same packets while the detector code is the same; every packet and
    graph verifying; the files the result names, byte for byte."""
    problems = []
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("format") != PILOT_FORMAT or result.get("digest") != digest(result):
        return ["result.json is not an intact fpsdet.pilot/1 result"]
    with tempfile.TemporaryDirectory() as work:
        for row in result["scenarios"]:
            events, plan = ROOT / row["events"]["file"], ROOT / row["plan"]["file"]
            for path, want in ((events, row["events"]["sha256"]), (plan, row["plan"]["sha256"])):
                if sha256_file(path) != want:
                    problems.append(f"{row['id']}: {path.name} is not the captured file")
            again = semantic(score(events, plan, Path(work) / row["id"]))
            for name, case in row["cases"].items():
                now = again.get(name, {})
                for key in ("decision", "observations", "graph", "challenges", "inputs"):
                    if now.get(key) != case[key]:
                        problems.append(f"{row['id']} {name}: {key} differs from the live run")
                if now.get("detector") == case["detector"] and now.get("packet") != case["packet"]:
                    problems.append(f"{row['id']} {name}: the packet differs with the same detector code")
                if now.get("problems"):
                    problems.append(f"{row['id']} {name}: {now['problems'][0]}")
            if row["observed"]["status"] != row["expected"]["status"]:
                problems.append(f"{row['id']}: observed {row['observed']['status']}, declared {row['expected']['status']}")
    return problems


def cmd_run(args: argparse.Namespace) -> int:
    run = Path(args.out).expanduser()
    live = run_scenario(args.godot, args.scenario, run, args.port, render=args.render)
    scored = score(run / "public" / "events.ndjson", live["plan"], run / "cases")
    print(json.dumps({"run": {k: str(v) for k, v in live.items()}, "outcome": outcome(scored)}, indent=1))
    return 0


def cmd_qualify(args: argparse.Namespace) -> int:
    root = Path(args.out).expanduser()
    results = qualify(args.godot, root, args.port)
    body = capture(results)
    failed = [row["id"] for row in body["scenarios"] if not row["as_expected"] or row["live_vs_offline"] != "identical" or row["secret_leaks"]
              or row["realization"]["status"] != "reproduced"]
    print(f"Wrote {RESULT.relative_to(ROOT)} ({body['digest']}). Not as expected: {failed or 'none'}. Non-interference identical: {body['non_interference']['identical']}.")
    return 1 if failed or not body["non_interference"]["identical"] else 0


def cmd_verify(_args: argparse.Namespace) -> int:
    problems = verify()
    for problem in problems:
        print(problem)
    print("the committed capture reproduces result.json" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    godot = sub.add_parser("godot", help="Download the pinned Godot build and check its SHA-512 (user-local; installs nothing)")
    godot.add_argument("--dest", required=True)
    godot.set_defaults(func=cmd_godot)
    run = sub.add_parser("run", help="One live scenario")
    run.add_argument("--godot", required=True)
    run.add_argument("--scenario", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--port", type=int, default=24700)
    run.add_argument("--render", action="store_true", help="Draw the subject's client in a private virtual display")
    run.set_defaults(func=cmd_run)
    qualify_ = sub.add_parser("qualify", help="Every scenario, failure and A/B run; writes result.json and the capture")
    qualify_.add_argument("--godot", required=True)
    qualify_.add_argument("--out", required=True)
    qualify_.add_argument("--port", type=int, default=24700)
    qualify_.set_defaults(func=cmd_qualify)
    check = sub.add_parser("verify", help="Offline: score the committed capture again and compare with result.json")
    check.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
