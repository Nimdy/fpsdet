"""The fpsdet Arena's harness: a tiny reference FPS built to show what fpsdet sees (examples/fps-arena).

It plans each challenge scenario with fpsdet's own planner and a fresh server secret, runs the Godot dedicated
server and the lab window (or a headless autopilot client) on this machine, loopback only, scores the server's
telemetry with fpsdet while the match runs, scores it again offline when the match ends, and compares the two.
Expected behaviour lives in each scenario's metadata; actual behaviour comes from fpsdet alone, and the two are
compared, never reconciled.

    python examples/fps-arena/harness/arena.py doctor --godot PATH          # can this machine run it?
    python examples/fps-arena/harness/arena.py run --godot PATH             # the playable lab: player view, server view, scenarios
    python examples/fps-arena/harness/arena.py scenario impossible_speed --godot PATH [--deterministic]
    python examples/fps-arena/harness/arena.py qualify --godot PATH --out DIR   # every scenario, headless; writes result.json and captures/
    python examples/fps-arena/harness/arena.py verify                       # offline, no engine: the committed captures against result.json
    python examples/fps-arena/harness/arena.py replay --run DIR --match ID  # rebuild one match's case timeline for the lab's replay mode

Everything a run writes stays in its folder: Godot's own user data too (XDG_*_HOME point there). The secret and
the realization the server ran are written to <run>/private, readable by the owner only, and are never copied
into the repository. Standard library only, beside fpsdet. Nothing is installed system-wide.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "examples" / "pilot"))

import pilot  # noqa: E402  (P12's harness: fpsdet calls, the Godot environment, the secret material and the leak scan)

PROJECT = HERE / "godot"
PROFILE = HERE / "arena.json"
SCENARIOS = HERE / "scenarios"
CAPTURED = HERE / "captures"
RESULT = HERE / "result.json"
FORMAT = "fpsdet.arena/1"
CLASS = "interactive_reference_demo"
SUBJECT = "arena-player"
ENGINE_VERSION = "4.7.2.stable.official.ed1daf0bf"
PLANS_PER_CHALLENGE_SCENARIO = 4
LIVE_EVERY_S = 1.0
TIMELINE_STEP_MS = 1000
CODE = ("project.godot", "main.tscn", "main.gd", "scripts/arena_map.gd", "scripts/arena_server.gd", "scripts/server_operator.gd",
        "scripts/arena_client.gd", "scripts/pixel_check_client.gd", "scripts/operator_view.gd", "scripts/lab.gd", "scripts/autopilot.gd",
        "scripts/bots.gd", "scripts/scenario.gd", "scripts/weapon.gd", "scripts/recipe.gd", "scripts/sounds.gd")
REQUIRED_SCENARIOS = ("normal_play", "impossible_speed", "fire_rate", "recoil_floor", "recoil_mirror", "audible_hidden_enemy",
                      "unknowable_hidden_enemy", "unknowable_hidden_tracked", "unchecked_audio_channel", "wire_vs_picture",
                      "active_challenge", "angle_hold_false_positive", "external_record", "exposed_challenge_control")
# The player pixel proof: the hidden control is the real challenge in the sealed chamber; the positive control puts the
# same probe in the open. Both are run with the pixel-check client, drawing in a private virtual display.
PIXEL_HIDDEN, PIXEL_VISIBLE = "active_challenge", "exposed_challenge_control"
CARD_FIELDS = ("what_this_tests", "player_can_know", "server_knows", "expected_fpsdet_behavior", "invalid_if")
EXPERIMENTAL_LABEL = "EXPERIMENTAL CHALLENGE RESULT: NOT PRODUCTION QUALIFIED"


# Files and identity.


def scenarios() -> dict:
    out = {}
    for path in sorted(SCENARIOS.glob("*.json")):
        found = json.loads(path.read_text(encoding="utf-8"))
        if found.get("format") != "fpsdet.arena-scenario/1":
            raise SystemExit(f"{path.name} is not a scenario file")
        out[found["id"]] = found
    return out


def code_identity() -> dict:
    from fpsdet.provenance import normalized_source

    files = {f"examples/fps-arena/godot/{name}": "sha256:" + hashlib.sha256(normalized_source((PROJECT / name).read_bytes())).hexdigest() for name in CODE}
    files["examples/fps-arena/harness/arena.py"] = "sha256:" + hashlib.sha256(normalized_source(Path(__file__).read_bytes())).hexdigest()
    return files


def profile():
    from fpsdet.parse import load_profile

    return load_profile(PROFILE)


def identifiers(text: str) -> list[str]:
    """What in a text could name a person or a machine: an IP address, this machine's name, the operator's user
    name or home folder. The arena never writes them into a public file; this checks it."""
    needles = {"hostname": socket.gethostname(), "user": getpass.getuser(), "home": str(Path.home())}
    found = ["an IP address"] if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text) else []
    for name, value in needles.items():
        if value and len(value) > 2 and re.search(r"\b" + re.escape(value) + r"\b", text):
            found.append(f"the {name}")
    return found


def personal_data(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        if path.is_file():
            found += [f"{path.name}: {what}" for what in identifiers(path.read_text(encoding="utf-8", errors="replace"))]
    return found


# A run folder.


def new_run(out: Path) -> Path:
    out = Path(out).expanduser()
    for name in ("public/matches", "public/plans", "public/live", "public/control", "operator", "godot-user"):
        (out / name).mkdir(parents=True, exist_ok=True)
    private = out / "private"
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    return out


def match_id_for(scenario_id: str, number: int) -> str:
    return f"arena-{scenario_id}-{number}"


def plan_scenario(run: Path, scenario: dict, match_id: str) -> Path:
    """One match's public plan for a challenge scenario, from the run's secret and the scenario's schedule."""
    secret = run / "private" / "secret.hex"
    if not secret.exists():
        pilot.fpsdet("challenge", "keygen", "--out", str(secret))
    schedule = scenario["challenge"]["schedule"]
    plan = run / "public" / "plans" / f"{match_id}.json"
    pilot.fpsdet("challenge", "plan", "--profile", str(PROFILE), "--match", match_id, "--player", SUBJECT, "--version", str(scenario["challenge"]["version"]),
                 *[f"--{key.replace('_', '-')}={value}" for key, value in schedule.items()], "--secret-file", str(secret), "--out", str(plan))
    return plan


def plan_all(run: Path, found: dict, per_scenario: int = PLANS_PER_CHALLENGE_SCENARIO) -> dict:
    plans = {}
    for scenario_id, scenario in found.items():
        if "challenge" in scenario:
            for number in range(1, per_scenario + 1):
                match_id = match_id_for(scenario_id, number)
                plans[match_id] = plan_scenario(run, scenario, match_id)
    return plans


def operator_token(run: Path) -> Path:
    path = run / "private" / "operator.token"
    if not path.exists():
        path.write_text(secrets.token_hex(16) + "\n", encoding="utf-8")
        path.chmod(0o600)
    return path


def external_record(scenario: dict, match_id: str) -> dict | None:
    """The fictional provider's record for one match, in the fpsdet.external/1 format."""
    template = scenario.get("external")
    if not template:
        return None
    return {"format": "fpsdet.external/1", **template, "subject_id": SUBJECT, "match_id": match_id, "started_ms": 2000, "ended_ms": 20000,
            "observed_at": "2026-10-09T12:00:00Z"}


def write_external(folder: Path, scenario: dict, match_id: str) -> Path | None:
    record = external_record(scenario, match_id)
    if record is None:
        return None
    path = folder / "external.ndjson"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    return path


# Processes: the server, a client, the lab window.


def godot_env(run: Path) -> dict:
    return pilot.godot_env(run)


def server_command(godot: str, run: Path, port: int, scenario: str | None = None, match: str | None = None, once: bool = False,
                   deterministic: bool = False, headless: bool = True, subject_input: str = "autopilot", bind: str | None = None) -> list[str]:
    command = [godot]
    if headless:
        command.append("--headless")
    if deterministic:
        command += ["--fixed-fps", "60"]
    command += ["--path", str(PROJECT), "--", "--role=server", f"--run={run}", f"--scenarios={SCENARIOS}", f"--port={port}",
                f"--secret-file={run / 'private' / 'secret.hex'}", f"--operator-token-file={operator_token(run)}", f"--subject-input={subject_input}"]
    if bind:
        command.append(f"--bind={bind}")
    if scenario:
        command.append(f"--scenario={scenario}")
    if match:
        command.append(f"--match={match}")
    if once:
        command.append("--once=true")
    if deterministic:
        command.append("--deterministic=true")
    return command


def client_command(godot: str, run: Path, port: int, behaviour: str | None, log: Path, headless: bool = True, pixels: Path | None = None) -> list[str]:
    command = [godot]
    role = "client"
    if pixels is not None:
        # The pixel-check client (the stock client plus the frame check), drawing in a private virtual display with software OpenGL.
        role = "pixel-client"
        command = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24", godot, "--rendering-driver", "opengl3", "--resolution", "640x360", "--audio-driver", "Dummy"]
    elif headless:
        command += ["--headless", "--audio-driver", "Dummy"]
    command += ["--path", str(PROJECT), "--", f"--role={role}", f"--name={SUBJECT}", f"--port={port}", f"--log={log}", "--timeout-ms=600000"]
    if behaviour:
        command += ["--input=standin", f"--behaviour={behaviour}"]
    if pixels is not None:
        command += ["--check-every-ms=2000", f"--shots={pixels}"]
    return command


def lab_command(godot: str, run: Path, port: int, mode: str, behaviour: str | None, resolution: str, extra: tuple = (), virtual_display: bool = False) -> list[str]:
    command = [godot, "--resolution", resolution]
    if virtual_display:
        # A private virtual display, so no window opens on anyone's desktop; software OpenGL.
        command = ["xvfb-run", "-a", "-s", f"-screen 0 {resolution}x24", godot, "--rendering-driver", "opengl3", "--resolution", resolution, "--audio-driver", "Dummy"]
    command += ["--path", str(PROJECT), "--", "--role=lab", f"--run={run}", f"--scenarios={SCENARIOS}",
                f"--port={port}", f"--operator-token-file={operator_token(run)}", f"--mode={mode}", f"--name={SUBJECT}",
                f"--log={run / 'public' / 'client.jsonl'}", *extra]
    if behaviour:
        command += ["--input=standin", f"--behaviour={behaviour}"]
    return command


def start(command: list[str], run: Path, out_name: str) -> subprocess.Popen:
    return subprocess.Popen(command, env=godot_env(run), stdout=(run / out_name).open("w"), stderr=subprocess.STDOUT)


# Scoring. Live scoring runs fpsdet in this process on the match's events file; offline scoring runs the
# fpsdet command on the same file. Both are the real scorer; nothing here reads a detector's rule.


def match_folder(run: Path, match_id: str) -> Path:
    return run / "public" / "matches" / match_id


def registry_for(plan: Path | None):
    from fpsdet.challenge import ChallengeRegistry, plan_file_from_dict

    if plan is None or not plan.exists():
        return None
    return ChallengeRegistry.from_files([plan_file_from_dict(json.loads(plan.read_text(encoding="utf-8")))])


def external_for(path: Path | None):
    from fpsdet.external import read_external

    if path is None or not path.exists():
        return None
    return read_external([(str(path), None)])


def score_live(folder: Path, until_ms: int | None = None) -> dict:
    """fpsdet in this process on the match's events (all of them, or those up to ``until_ms``): every case as
    JSON, exactly as fpsdet score would write it."""
    from fpsdet.parse import load_events
    from fpsdet.persist import case_to_dict
    from fpsdet.pipeline import run_score

    started = time.perf_counter()
    events, errors = load_events(folder / "events.ndjson")
    if until_ms is not None:
        events = [event for event in events if event.t_ms <= until_ms]
    cases = run_score(events, profile(), None, [], {}, registry_for(folder / "plan.json"), external_for(folder / "external.ndjson"))
    out = {case.player_id: case_to_dict(case) for case in cases}
    return {"cases": out, "parse_errors": errors, "events": len(events), "scoring_ms": round((time.perf_counter() - started) * 1000, 1)}


def score_offline(folder: Path, out: Path) -> dict:
    """The fpsdet command on the same file: what an operator's own machine would produce later."""
    argv = ["score", str(folder / "events.ndjson"), "--profile", str(PROFILE), "--out", str(out)]
    if (folder / "plan.json").exists():
        argv += ["--challenges", str(folder / "plan.json")]
    if (folder / "external.ndjson").exists():
        argv += ["--external", str(folder / "external.ndjson")]
    pilot.fpsdet(*argv)
    cases = {}
    for path in sorted(out.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(case, dict) and "evidence" in case and "player_id" in case:
            cases[case["player_id"]] = case
    return {"cases": cases}


def problems_of(case: dict, registry) -> list[str]:
    from fpsdet.challenge import ChallengeRegistry, case_problems
    from fpsdet.graph import verify_graph
    from fpsdet.provenance import verify_packet

    return verify_packet(case) + [f"graph: {problem}" for problem in verify_graph(case)] + case_problems(case, registry or ChallengeRegistry())


def semantic(cases: dict) -> dict:
    """What must be identical between live and offline scoring: everything but run-local notes."""
    out = {}
    for name, case in cases.items():
        evidence = case["evidence"]
        out[name] = {
            "decision": case["decision"],
            "observations": sorted(obs["observation_id"] for obs in evidence["observations"]),
            "kinds": sorted(obs["kind"] for obs in evidence["observations"]),
            "graph": evidence["graph"]["digest"],
            "packet": evidence["packet"]["digest"],
            "packet_recipe": evidence["packet"]["recipe"],
            "inputs": {key: evidence["provenance"]["inputs"][key] for key in ("recipe", "digest", "events")},
            "detector": evidence["provenance"]["detector"]["digest"],
            "challenges": evidence.get("challenges", []),
            "fusion": evidence.get("fusion"),
            "eligibility": {kind: dict(units) for kind, units in evidence["detector_eligibility"]["detectors"].items()},
        }
    return out


def rollups(case: dict) -> dict:
    from fpsdet.evidence import rollup

    return {kind: rollup(units) for kind, units in case["evidence"]["detector_eligibility"]["detectors"].items()}


def knowledge_of(folder: Path, until_ms: int | None = None) -> dict:
    """For each enemy the subject's events named: fpsdet's own answer to "could this client know it", event by
    event (fpsdet.knowledge.shot_knowledge), counted by status, with the latest answer and its channels."""
    from fpsdet.knowledge import shot_knowledge
    from fpsdet.parse import load_events

    game = profile()
    events, _errors = load_events(folder / "events.ndjson")
    out: dict[str, dict] = {}
    for event in sorted((event for event in events if event.player_id == SUBJECT and event.enemy_id), key=lambda event: event.t_ms):
        if until_ms is not None and event.t_ms > until_ms:
            break
        state = shot_knowledge(event, game)
        row = out.setdefault(event.enemy_id, {"events": 0, "statuses": {}, "causes": {}})
        row["events"] += 1
        row["statuses"][state.status] = row["statuses"].get(state.status, 0) + 1
        row["causes"][state.cause] = row["causes"].get(state.cause, 0) + 1
        row["latest"] = {"t_ms": event.t_ms, "status": state.status, "cause": state.cause, "channels": dict(state.channels),
                         "required": list(state.required), "since_perceived_ms": event.since_perceived_ms,
                         "hidden_track_ms": event.hidden_track_ms, "event_type": event.event_type}
    for row in out.values():
        row["majority"] = max(row["statuses"], key=lambda status: (row["statuses"][status], status))
    return out


def knowledge_table() -> dict:
    """fpsdet's answer for every combination of channel states the server can report, tabulated once so the
    operator view can show the resolved KnowledgeState at tick rate without reimplementing anything."""
    from fpsdet.knowledge import CHANNEL_ABSENT, CHANNEL_KNOWN, CHANNEL_UNCHECKED, body_knowledge, resolve
    from fpsdet.challenge import OCCLUDED_MOTION_REPLAY_V2

    game = profile()
    states = (CHANNEL_KNOWN, CHANNEL_ABSENT, CHANNEL_UNCHECKED)
    enemy = {}
    for vision in states:
        for audio in states:
            for recent in states:
                state = resolve({"vision": vision, "audio": audio, "recent_perception": recent}, game.knowledge_channels)
                enemy[f"{vision}|{audio}|{recent}"] = {"status": state.status, "cause": state.cause}
    body = {}
    for vision in states:
        for audio in states:
            state = body_knowledge((("audio", audio), ("vision", vision)), OCCLUDED_MOTION_REPLAY_V2.not_applicable, game.knowledge_channels)
            body[f"{vision}|{audio}"] = {"status": state.status, "cause": state.cause}
    return {"format": "fpsdet.arena-knowledge-table/1", "source": "fpsdet.knowledge.resolve and body_knowledge, over the profile's declared channels",
            "required": list(game.knowledge_channels), "hidden_grace_ms": game.hidden_grace_ms, "enemy": enemy, "challenge_body": body,
            "key": "enemy: vision|audio|recent_perception; challenge_body: vision|audio; recent_perception is known under hidden_grace_ms, absent at or over it, unchecked when since_perceived_ms was not sent"}


def finding_summary(obs: dict) -> str:
    """One line per finding for the panel, from the observation's own evidence. Nothing here decides anything."""
    kind = obs["kind"]
    e = obs["evidence"]
    if kind == "speed":
        run = e.get("run", {})
        return f"run of {e.get('longest_run')} samples (bar {e['thresholds']['min_run']}), peak {run.get('peak_mps')} m/s against cap {run.get('cap_mps')}"
    if kind == "fire_rate":
        return f"{e.get('under_floor')}/{e.get('gaps')} gaps under {e.get('floor_ms')} ms"
    if kind == "recoil_floor":
        return f"{e.get('longest_low_run')} shots in a row under {e['thresholds']['floor_fraction']:.0%} of the {e.get('floor_deg')} deg floor"
    if kind == "mirror":
        return f"same-tick r {e.get('same_tick_r'):.2f}, lagged r {e.get('lagged_r') if e.get('lagged_r') is None else round(e.get('lagged_r'), 2)}, {e.get('shots')} shots" + (", pattern removed" if e.get("pattern_removed") else "")
    if kind == "hidden":
        return f"{e.get('total_ms'):.0f} ms over {e.get('shots')} shots (bar {e['thresholds']['min_total_ms']:.0f} ms, {e['thresholds']['min_shots']})"
    if kind == "wire":
        return f"{e.get('led_shots')} wire-led shots, {e.get('led_delay_ms'):.0f} ms of delay; median wire {e.get('median_wire_error_deg'):.2f} deg, picture {e.get('median_picture_error_deg'):.2f} deg"
    if kind == "occluded_motion_replay":
        c = e.get("challenge", {})
        return f"{e.get('total_ms'):.0f} ms over {e.get('tracked_samples')} of {e.get('eligible_samples')} moments on {c.get('challenge_id')} ({e.get('knowledge', {}).get('verification')})"
    if kind in ("external_signal", "external_context"):
        confidence = e.get("confidence", {})
        return f"{e.get('provider')} {e.get('source_class')} {e.get('direction')} {e.get('kind')}, confidence {confidence.get('value')} ({confidence.get('scale')}), {e.get('authenticity', {}).get('status')}"
    return ", ".join(f"{key} {value}" for key, value in e.items() if not isinstance(value, (dict, list)))[:160]


def panel_case(case: dict, folder: Path, until_ms: int | None = None) -> dict:
    """The subject's case as the operator panels read it: fpsdet's own output, trimmed and labelled."""
    from fpsdet.evidence import KINDS, NATIVE_KINDS
    from fpsdet.models import CHECKS

    evidence = case["evidence"]
    observations = evidence["observations"]
    eligibility = {}
    for kind in NATIVE_KINDS:
        units = evidence["detector_eligibility"]["detectors"].get(kind, {})
        family, check, role = KINDS[kind]
        eligibility[kind] = {"rollup": rollups(case)[kind], "units": {unit: status for status, names in units.items() for unit in names},
                             "count": sum(1 for obs in observations if obs["kind"] == kind), "role": role, "family": family, "check": check,
                             "label": CHECKS.get(check, (family, kind))[1]}
    graph = evidence["graph"]
    return {
        "player_id": case["player_id"],
        "decision": case["decision"],
        "recommended_action": case["recommended_action"],
        "automated_action": case["automated_action"],
        "checks": case["checks"],
        "reasons": case["reasons"],
        "notes": case["observations"],
        "seal": case["seal"],
        "findings": [{"observation_id": obs["observation_id"], "kind": obs["kind"], "family": obs["family"], "role": obs["role"], "source": obs["source"],
                      "subject_id": obs["subject_id"], "key": obs["key"], "match_ids": obs["match_ids"], "summary": finding_summary(obs),
                      "line": obs.get("context", {}).get("line", ""), "evidence": obs["evidence"]} for obs in observations],
        "eligibility": eligibility,
        "challenges": evidence.get("challenges", []),
        "fusion": evidence.get("fusion"),
        "packet": evidence["packet"],
        "graph": {"recipe": graph["recipe"], "digest": graph["digest"], "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]),
                  "nodes": [{"id": node["id"], "type": node["type"]} for node in graph["nodes"]],
                  "edges": [{"source": edge["source"], "relation": edge["relation"], "target": edge["target"]} for edge in graph["edges"]],
                  "independence": graph.get("summary", {}).get("independence", {})},
        "provenance": {"detector": evidence["provenance"]["detector"]["digest"], "profile": evidence["provenance"]["profile"]["digest"],
                       "inputs": evidence["provenance"]["inputs"], "cohort": evidence["provenance"].get("cohort")},
        "speed": case.get("speed"),
        "speed_cadence": speed_cadence(folder, case),
        "knowledge": knowledge_of(folder, until_ms),
    }


def speed_cadence(folder: Path, case: dict) -> dict:
    """The movement cadence the server actually emitted for the subject, and what the speed check's sample-count run spans
    at that cadence. Interpretation only: the bar counts samples, and nothing here changes it."""
    from fpsdet.parse import load_events

    events, _errors = load_events(folder / "events.ndjson")
    stamps = sorted(event.t_ms for event in events if event.player_id == SUBJECT and event.event_type == "movement")
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]) if b > a)
    if not gaps:
        return {}
    interval = gaps[len(gaps) // 2]
    speed = case.get("speed") or {}
    return {"interval_ms": interval, "samples": len(stamps), "longest_run": speed.get("longest_run", 0), "run_ms": int(speed.get("longest_run", 0)) * interval,
            "bar_samples": profile().speed_min_run, "bar_ms_at_this_cadence": profile().speed_min_run * interval,
            "note": "the speed run is counted in consecutive samples; at this emitter's cadence that is the duration shown"}


def case_timeline(folder: Path, step_ms: int = TIMELINE_STEP_MS) -> list[dict]:
    """The subject's case as it would have stood after each second of the match: fpsdet scored on the events up to
    that time. For the replay scrubber; every point is a real scoring."""
    from fpsdet.parse import load_events

    events, _errors = load_events(folder / "events.ndjson")
    if not events:
        return []
    end = max(event.t_ms for event in events)
    out = []
    for until in range(step_ms, end + step_ms, step_ms):
        scored = score_live(folder, min(until, end))
        case = scored["cases"].get(SUBJECT)
        if case is None:
            continue
        out.append({"t_ms": min(until, end), "decision": case["decision"], "kinds": sorted(obs["kind"] for obs in case["evidence"]["observations"]),
                    "observations": [obs["observation_id"] for obs in case["evidence"]["observations"]], "eligibility": rollups(case),
                    "challenges": [{key: result.get(key) for key in ("challenge_id", "status", "cause", "eligible_samples", "tracked_samples", "total_ms", "verified_samples")}
                                   for result in case["evidence"].get("challenges", [])],
                    "events": scored["events"], "scoring_ms": scored["scoring_ms"]})
        if until >= end:
            break
    return out


# The AI brief: fpsdet's own path (fpsdet.ai_triage), over the finished case, after every decision is made.


def ai_status() -> dict:
    base = os.environ.get("FPSDET_AI_BASE_URL", "")
    model = os.environ.get("FPSDET_AI_MODEL", "")
    # The endpoint's address is read from the environment when a brief is asked for; it is never written to a file.
    return {"configured": bool(base and model), "model": model,
            "note": "AI summarizes evidence. AI does not create evidence. AI does not change the decision." if base and model
            else "Not configured: set FPSDET_AI_BASE_URL and FPSDET_AI_MODEL to enable the reviewer brief."}


def ai_brief(case: dict, known_ids, transport=None) -> str:
    """A plain-language brief over the already-produced case, with every id aliased, as fpsdet score --ai does.
    The case passed in is never changed: the caller compares its packet before and after."""
    from fpsdet.ai_triage import openai_compatible_transport, triage_case

    if transport is None:
        status = ai_status()
        if not status["configured"]:
            return ""
        transport = openai_compatible_transport(os.environ.get("FPSDET_AI_BASE_URL", ""), os.environ.get("FPSDET_AI_API_KEY", ""), status["model"])
    return triage_case(json.loads(json.dumps(case)), transport, redact_ids=True, known_ids=list(known_ids))


# Expectations: the scenario's metadata against fpsdet's output. Problems are reported, never reconciled.


def outcome(scenario: dict, scored: dict, folder: Path) -> dict:
    case = scored["cases"].get(SUBJECT)
    if case is None:
        return {"decision": None, "kinds": [], "problems": ["no case for the subject"]}
    evidence = case["evidence"]
    challenges = evidence.get("challenges", [])
    return {
        "decision": case["decision"],
        "automated_action": case["automated_action"],
        "kinds": sorted(obs["kind"] for obs in evidence["observations"]),
        "observations": sorted(obs["observation_id"] for obs in evidence["observations"]),
        "eligibility": rollups(case),
        "challenge": {key: challenges[0].get(key) for key in ("challenge_id", "status", "cause", "verification", "eligible_samples", "tracked_samples", "verified_samples", "total_ms", "not_counted")} if challenges else None,
        "fusion": evidence.get("fusion"),
        "knowledge": {enemy: row["majority"] for enemy, row in knowledge_of(folder).items()},
        "knowledge_detail": knowledge_of(folder),
        "speed": case.get("speed"),
        "graph": evidence["graph"]["digest"],
        "packet": evidence["packet"]["digest"],
        "experimental": bool(scenario.get("experimental", False)),
        "label": EXPERIMENTAL_LABEL if scenario.get("experimental") and any(obs["kind"] == "occluded_motion_replay" for obs in evidence["observations"]) else None,
    }


def as_expected(scenario: dict, found: dict) -> list[str]:
    expected = scenario.get("expected", {})
    problems = []
    if found.get("decision") is None:
        return ["no case for the subject"]
    want = expected.get("decision")
    if want is not None:
        allowed = want if isinstance(want, list) else [want]
        if found["decision"] not in allowed:
            problems.append(f"decision is {found['decision']!r}, expected {' or '.join(allowed)}")
    if expected.get("no_review") and found["decision"] == "review":
        problems.append("a review appeared where none was expected")
    kinds = [kind for kind in found["kinds"] if kind not in ("external_context",)]
    if "kinds" in expected and sorted(kinds) != sorted(expected["kinds"]):
        problems.append(f"findings are {sorted(kinds)}, expected {sorted(expected['kinds'])}")
    for kind, status in expected.get("eligibility", {}).items():
        if found["eligibility"].get(kind) != status:
            problems.append(f"{kind} eligibility is {found['eligibility'].get(kind)!r}, expected {status!r}")
    for enemy, status in expected.get("knowledge", {}).items():
        if found["knowledge"].get(enemy) != status:
            problems.append(f"knowledge of {enemy} is {found['knowledge'].get(enemy)!r}, expected {status!r}")
    for enemy, status in expected.get("knowledge_seen", {}).items():
        statuses = found["knowledge_detail"].get(enemy, {}).get("statuses", {})
        if not statuses.get(status):
            problems.append(f"knowledge of {enemy} was never {status!r} ({statuses})")
    if "challenge" in expected:
        challenge = found.get("challenge") or {}
        for key, value in expected["challenge"].items():
            if challenge.get(key) != value:
                problems.append(f"challenge {key} is {challenge.get(key)!r}, expected {value!r}")
    if "fusion" in expected:
        fusion = found.get("fusion") or {}
        for key, value in expected["fusion"].items():
            allowed = value if isinstance(value, list) else [value]
            if fusion.get(key) not in allowed:
                problems.append(f"fusion {key} is {fusion.get(key)!r}, expected {' or '.join(map(str, allowed))}")
    if found["automated_action"] != "none":
        problems.append("automated_action is not none")
    return problems


# The secret, after the match.


def reproduce(run: Path, plan: Path | None, secret: Path | None) -> dict:
    """With the secret: does it make exactly this plan, and the realization the server ran? Without it: say so.
    Never prints a value that came from the secret."""
    from fpsdet.challenge import plan_file_from_dict
    from fpsdet.challenge_plan import SecretError, load_secret, realize
    from fpsdet.challenge_plan import reproduce as reproduce_plan

    if plan is None or not plan.exists():
        return {"status": "no_challenge"}
    plan_file = plan_file_from_dict(json.loads(plan.read_text(encoding="utf-8")))
    try:
        key, _warnings = load_secret(secret, environ={}) if secret is not None and secret.exists() else load_secret(None, environ={})
    except SecretError as error:
        return {"status": "secret_unavailable", "detail": str(error)}
    problems = list(reproduce_plan(key, plan_file))
    checked = []
    for record in plan_file.plans:
        ran_path = run / "private" / f"realization-{record.challenge_id}.json"
        if not ran_path.exists():
            problems.append(f"{record.challenge_id}: the server did not run it")
            continue
        ran = json.loads(ran_path.read_text(encoding="utf-8"))
        derived = dict(realize(key, record, plan_file.budget).parameters)
        if {name: int(value) for name, value in ran["parameters"].items()} != derived:
            problems.append(f"{record.challenge_id}: the server's realization is not the one the secret derives")
        if ran["placement_index"] != derived["placement_pick"] % 1:
            problems.append(f"{record.challenge_id}: the server's placement is not the realization's")
        if not (record.start_ms <= ran["first_ms"] and ran["last_ms"] <= record.end_ms) or ran["challenge_id"] != record.challenge_id:
            problems.append(f"{record.challenge_id}: the server ran it outside its planned window")
        checked.append(record.challenge_id)
    return {"status": "reproduced" if not problems else "differs", "challenges": checked, "problems": problems}


def leak_scan(paths: list[Path], plan: Path | None, secret: Path | None) -> list[str]:
    return pilot.leaks(paths, plan if plan is not None and plan.exists() else None, secret if secret is not None and secret.exists() else None)


# One scenario, headless: the server and a headless autopilot client (or the server alone, deterministic).


def wait_for(path: Path, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            return True
        time.sleep(0.2)
    return False


def run_scenario(godot: str, scenario_id: str, run: Path, port: int, deterministic: bool = False, number: int = 1, pixels: bool = False) -> dict:
    found = scenarios()
    scenario = found[scenario_id]
    run = new_run(run)
    match_id = match_id_for(scenario_id, number)
    plan = plan_scenario(run, scenario, match_id) if "challenge" in scenario else None
    folder = match_folder(run, match_id)
    folder.mkdir(parents=True, exist_ok=True)
    write_external(folder, scenario, match_id)
    started = time.time()
    processes = [start(server_command(godot, run, port, scenario_id, match_id, once=True, deterministic=deterministic), run, "server.out")]
    behaviour = scenario.get("subject", {}).get("autopilot", "tracker")
    if not deterministic:
        time.sleep(1.5)
        shots = None
        if pixels:
            shots = run / "public" / "shots"
            shots.mkdir(parents=True, exist_ok=True)
        processes.append(start(client_command(godot, run, port, behaviour, run / "public" / "client.jsonl", pixels=shots), run, "client.out"))
    codes = []
    for process in processes:
        try:
            codes.append(process.wait(timeout=scenario["duration_ms"] / 1000 + 120))
        except subprocess.TimeoutExpired:
            process.kill()
            codes.append("timeout")
    return {"scenario": scenario_id, "match_id": match_id, "folder": folder, "plan": plan, "secret": run / "private" / "secret.hex",
            "exit_codes": codes, "wall_s": round(time.time() - started, 1), "deterministic": deterministic, "run": run, "pixels": pixels}


def pixel_facts(run: Path, match_record: dict) -> dict:
    """What the pixel-check client drew: for the probe and for the bots, how many checks found each drawn, and the most
    pixels any check found each contributing. The probe is named from the server's own record, never by the client."""
    ids = match_record.get("entities", {})
    probe = ids.get("probe")
    bots = {ids[name] for name in ids if name not in ("probe", SUBJECT)}
    rows = [json.loads(line) for line in (run / "public" / "client.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    checks = [row for row in rows if row.get("kind") == "pixels"]
    probe_pixels = [cell["pixels"] for row in checks for cell in row["entities"] if cell["entity"] == probe and cell["drawn"]]
    bot_pixels = [cell["pixels"] for row in checks for cell in row["entities"] if cell["entity"] in bots and cell["drawn"]]
    return {
        "checks": len(checks),
        "probe_checks_drawn": len(probe_pixels),
        "probe_pixels_max": max(probe_pixels, default=0),
        "probe_checks_with_pixels": sum(1 for value in probe_pixels if value > 0),
        "bot_checks_with_pixels": sum(1 for value in bot_pixels if value > 0),
        "bot_pixels_max": max(bot_pixels, default=0),
        "unchanged_frame_pixels_max": max((row["unchanged_frame_pixels"] for row in checks), default=0),
        "screenshots": sorted(row["screenshot"] for row in checks if row.get("screenshot")),
        "resolution": "640x360, software OpenGL in a private virtual display",
    }


def pixel_proof(hidden: dict, visible: dict) -> dict:
    """The player pixel proof: in the sealed chamber the probe was drawn as a body and contributed no pixel, while the same
    method found pixels for the bots and, in the positive control, for the probe placed in the open. Not a scoring rule:
    qualification only."""
    reasons = []
    if hidden["probe_checks_drawn"] == 0:
        reasons.append("hidden control: the client never drew the probe (it was not received, or never in its window)")
    if hidden["probe_pixels_max"] != 0:
        reasons.append(f"hidden control: the probe contributed {hidden['probe_pixels_max']} pixels")
    if hidden["unchanged_frame_pixels_max"] != 0:
        reasons.append("hidden control: the frozen frame was not stable, so the method cannot be trusted")
    if hidden["bot_checks_with_pixels"] == 0:
        reasons.append("hidden control: the method found no pixels for any visible bot either")
    if visible["probe_checks_with_pixels"] == 0:
        reasons.append("positive control: the probe placed in the open changed no pixel, so the method could not detect a visible probe")
    return {
        "method": "the P12 pilot's: freeze the frame, draw it again with each body hidden in turn, count the pixels that changed",
        "hidden_scenario": PIXEL_HIDDEN, "visible_scenario": PIXEL_VISIBLE,
        "hidden_checks": hidden["probe_checks_drawn"], "hidden_pixels": hidden["probe_pixels_max"],
        "hidden_bot_checks_with_pixels": hidden["bot_checks_with_pixels"], "hidden_frame_stable": hidden["unchanged_frame_pixels_max"] == 0,
        "visible_checks": visible["probe_checks_with_pixels"], "visible_pixels": visible["probe_pixels_max"],
        "pass": not reasons, "problems": reasons,
        "statement": (f"PLAYER PIXEL PROOF  hidden: {hidden['probe_pixels_max']} challenge pixels in {hidden['probe_checks_drawn']} checks  "
                      f"visible control: {visible['probe_pixels_max']} changed pixels  {'PASS' if not reasons else 'FAIL'}"),
    }


def bind_refusal(godot: str, run: Path, port: int) -> dict:
    """The server asked to bind a public address must refuse and exit, and the default must be loopback."""
    run = new_run(run)
    process = start(server_command(godot, run, port, "normal_play", "arena-bind-check", once=True, bind="203.0.113.5"), run, "server.out")
    try:
        code = process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        code = "timeout"
    log = [json.loads(line) for line in (run / "public" / "server.log").read_text(encoding="utf-8").splitlines() if line.strip()] if (run / "public" / "server.log").exists() else []
    refused = any(row.get("kind") == "error" and "refusing a public one" in str(row.get("detail", "")) for row in log)
    source = (PROJECT / "scripts" / "arena_server.gd").read_text(encoding="utf-8")
    return {"default_bind": "127.0.0.1", "default_in_source": 'options.get("bind", "127.0.0.1")' in source, "public_address_tried": "203.0.113.5 (TEST-NET-3, never routed)",
            "public_bind_refused": refused and code == 3, "exit_code": code, "listener_after_exit": False}


def judge(live: dict, scenario: dict) -> dict:
    """Score one finished match three ways (live in-process, offline command twice), compare, check the plan,
    scan for leaks and identifiers, and compare with the scenario's expectation."""
    folder: Path = live["folder"]
    run: Path = live["run"]
    scored = score_live(folder)
    with tempfile.TemporaryDirectory() as work:
        offline = [score_offline(folder, Path(work) / f"offline-{index}") for index in (1, 2)]
    identical = all(semantic(other["cases"]) == semantic(scored["cases"]) for other in offline)
    registry = registry_for(folder / "plan.json")
    problems = {name: problems_of(case, registry) for name, case in scored["cases"].items()}
    found = outcome(scenario, scored, folder)
    public_files = [path for path in run.rglob("*") if "private" not in path.parts and path.is_file() and "godot-user" not in path.parts]
    match_record = json.loads((folder / "match.json").read_text(encoding="utf-8")) if (folder / "match.json").exists() else {}
    perf = match_record.get("perf", {})
    seconds = max(match_record.get("t_ms", 1), 1) / 1000
    client = {}
    client_log = run / "public" / "client.jsonl"
    if client_log.exists():
        rows = [json.loads(line) for line in client_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        summary = next((row for row in rows if row.get("kind") == "summary"), {})
        probe = match_record.get("entities", {}).get("probe")
        received = summary.get("entities", {})
        client = {"entities_received": len(received), "probe_updates": received.get(probe, {}).get("updates", 0) if probe else 0,
                  "probe_sounds": sum(count for key, count in summary.get("sounds", {}).items() if probe and key.split(":")[0] == probe),
                  "sounds": sum(summary.get("sounds", {}).values()), "fps_median": sorted(summary["fps"])[len(summary["fps"]) // 2] if summary.get("fps") else None}
    pixels = pixel_facts(run, match_record) if live.get("pixels") else None
    return {
        "id": scenario["id"],
        "title": scenario["title"],
        "match_id": live["match_id"],
        "actor": {"label": scenario.get("actor", ""), "subject_input": "honest autopilot (scripted, not a human)",
                  "standin": scenario.get("standin", {}).get("kind") or None,
                  "statement": "Every subject in qualification is a scripted autopilot; where a stand-in holds the aim it is a server-side script, never a human and never real cheat software."},
        "pixels": pixels,
        "expected": scenario.get("expected", {}),
        "observed": found,
        "problems": as_expected(scenario, found),
        "as_expected": not as_expected(scenario, found),
        "live_vs_offline": "identical" if identical else "DIFFERENT",
        "case_problems": {name: rows for name, rows in problems.items() if rows},
        "cases": semantic(scored["cases"]),
        "realization": reproduce(run, live["plan"], live["secret"]),
        "secret_leaks": leak_scan(public_files, live["plan"], live["secret"]),
        "personal_data": personal_data([path for path in public_files if path.suffix in (".ndjson", ".json", ".jsonl", ".log")]),
        "client": client,
        "timing": {
            "server_tick_hz": 60, "match_ms": match_record.get("t_ms"), "ticks": match_record.get("ticks"),
            "tick_us_mean": round(perf.get("tick_us", 0) / max(perf.get("ticks", 1), 1), 1), "tick_us_max": perf.get("tick_us_max"),
            "knowledge_us_per_tick": round(perf.get("knowledge_us", 0) / max(perf.get("ticks", 1), 1), 1),
            "challenge_us_per_probe_tick": round(perf.get("challenge_us", 0) / max(perf.get("probe_ticks", 1), 1), 1),
            "rays_per_tick": round(perf.get("rays", 0) / max(perf.get("ticks", 1), 1), 1),
            "event_bytes_per_s": round(perf.get("event_bytes", 0) / seconds, 1), "event_lines": perf.get("event_lines"),
            "snapshot_bytes_per_s": round(perf.get("snapshot_bytes", 0) / seconds, 1), "feed_bytes_per_s": round(perf.get("feed_bytes", 0) / seconds, 1),
            "scoring_ms_live": scored["scoring_ms"], "parse_errors": len(scored["parse_errors"]), "verdicts": perf.get("verdicts", {}),
            "probe_ticks": perf.get("probe_ticks", 0), "memory_static_peak_bytes": perf.get("memory_static_peak_bytes"),
        },
        "exit_codes": live["exit_codes"],
        "wall_s": live["wall_s"],
        "deterministic": live["deterministic"],
        "folder": folder,
        "plan": live["plan"],
    }


# Qualification: every scenario, the capture, and fpsdet.arena/1.


def qualify(godot: str, root: Path, port: int, parallel: int, only: list[str] | None = None, deterministic: bool = False, pixels: bool = True) -> dict:
    found = scenarios()
    names = only or list(found)
    rows = []
    proof = None
    jobs = [(name, root / name, False) for name in names]
    if pixels and not only:
        jobs += [(PIXEL_HIDDEN, root / "pixel-hidden", True), (PIXEL_VISIBLE, root / "pixel-visible", True)]
    pixel_rows = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {pool.submit(run_scenario, godot, name, folder, port + offset, deterministic, 1, pixel): (name, pixel) for offset, (name, folder, pixel) in enumerate(jobs)}
        for future in concurrent.futures.as_completed(futures):
            live = future.result()
            row = judge(live, found[live["scenario"]])
            label = f"{row['id']} (pixel run)" if live.get("pixels") else row["id"]
            print(f"{label}: {row['observed']['decision']} {row['observed']['kinds']} -> {'as expected' if row['as_expected'] else row['problems']}; "
                  f"offline {row['live_vs_offline']}; actor: {row['actor']['label']}", flush=True)
            if live.get("pixels"):
                pixel_rows[row["id"]] = row
            else:
                rows.append(row)
    rows.sort(key=lambda row: names.index(row["id"]))
    if pixel_rows:
        proof = pixel_proof(pixel_rows[PIXEL_HIDDEN]["pixels"], pixel_rows[PIXEL_VISIBLE]["pixels"])
        proof["runs"] = {name: {key: row[key] for key in ("match_id", "observed", "as_expected", "problems", "live_vs_offline", "pixels", "timing")} for name, row in pixel_rows.items()}
        print(proof["statement"], flush=True)
        for problem in proof["problems"]:
            print(f"  {problem}", flush=True)
    network = bind_refusal(godot, root / "bind-check", port + len(jobs) + 1) if not only else None
    if network:
        print(f"network: default bind 127.0.0.1; public address refused: {network['public_bind_refused']}", flush=True)
    return {"rows": rows, "pixel_proof": proof, "network": network}


def capture(rows: list[dict], godot: str, proof: dict | None = None, network: dict | None = None) -> dict:
    """Copy each scenario's public telemetry (events, plan, external record) into the repository and build fpsdet.arena/1."""
    CAPTURED.mkdir(exist_ok=True)
    out = []
    for row in rows:
        folder = CAPTURED / row["id"]
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir()
        files = {}
        for name in ("events.ndjson", "plan.json", "external.ndjson"):
            source = row["folder"] / name
            if source.exists():
                shutil.copyfile(source, folder / name)
                files[name] = {"file": f"examples/fps-arena/captures/{row['id']}/{name}", "sha256": pilot.sha256_file(folder / name)}
        files["events.ndjson"]["lines"] = len((folder / "events.ndjson").read_text(encoding="utf-8").splitlines())
        out.append({key: value for key, value in row.items() if key not in ("folder", "plan")} | {"files": files})
    body = {
        "format": FORMAT,
        "arena": "fpsdet-arena",
        "class": CLASS,
        "statement": ("An interactive reference demo: scripted stand-ins where a cheat would be, one player, one or two bots, one small map. "
                      "Demonstration and integration evidence, never a benchmark population, a deployment or a detection rate. Not Benchmark v1."),
        "engine": {"name": "Godot", "version": ENGINE_VERSION, "binary_sha256": pilot.sha256_file(Path(godot)) if godot else None,
                   "release_sha512": pilot.qualification()["engine"]["sha512"]},
        "code": code_identity(),
        "profile": pilot.sha256_file(PROFILE),
        "scenarios": out,
        "experimental": {"label": EXPERIMENTAL_LABEL, "applies_to": [row["id"] for row in rows if row["observed"].get("experimental")],
                         "note": "Challenge reviews are experimental and not production-qualified. The angle-hold scenario is kept as a false-positive control: its review is the known weakness of the time-on-body bar, shown, not fixed."},
        "no_person": "Every subject in this result is a scripted autopilot or a server-side stand-in. No person played these matches, no real cheat software was run, and nothing here is a human result or a detection rate.",
        "pixel_proof": proof,
        "network": network,
    }
    body["digest"] = digest(body)
    RESULT.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return body


def digest(body: dict) -> str:
    from fpsdet.evidence import canonical_json

    return "sha256:" + hashlib.sha256(f"{FORMAT}\0".encode() + canonical_json({key: value for key, value in body.items() if key != "digest"}).encode("utf-8")).hexdigest()


def verify(result_path: Path = RESULT) -> list[str]:
    """Offline, with no engine and no secret: score every committed capture again and compare it with result.json
    (the same decisions, observation ids, graph and challenges; the same packet while the detector code is the
    same; every packet and graph verifying); the files byte for byte; the expectation each scenario declares; no
    realization word and no personal data in any committed capture."""
    if not result_path.exists():
        return ["result.json is missing: run qualify first"]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("format") != FORMAT or result.get("digest") != digest(result):
        return ["result.json is not an intact fpsdet.arena/1 result"]
    found = scenarios()
    problems = []
    with tempfile.TemporaryDirectory() as work:
        for row in result["scenarios"]:
            folder = CAPTURED / row["id"]
            for name, entry in row["files"].items():
                if not (folder / name).exists() or pilot.sha256_file(folder / name) != entry["sha256"]:
                    problems.append(f"{row['id']}: {name} is not the captured file")
            if problems and problems[-1].startswith(row["id"]):
                continue
            scored = score_live(folder)
            again = semantic(scored["cases"])
            for name, case in row["cases"].items():
                now = again.get(name, {})
                for key in ("decision", "observations", "graph", "challenges", "inputs", "fusion", "eligibility"):
                    if now.get(key) != case[key]:
                        problems.append(f"{row['id']} {name}: {key} differs from the qualification run")
                if now.get("detector") == case["detector"] and now.get("packet") != case["packet"]:
                    problems.append(f"{row['id']} {name}: the packet differs with the same detector code")
                case_problems = problems_of(scored["cases"][name], registry_for(folder / "plan.json")) if name in scored["cases"] else ["no case"]
                if case_problems:
                    problems.append(f"{row['id']} {name}: {case_problems[0]}")
            offline = score_offline(folder, Path(work) / row["id"])
            if semantic(offline["cases"]) != again:
                problems.append(f"{row['id']}: the fpsdet command and in-process scoring disagree")
            observed = outcome(found[row["id"]], scored, folder)
            for problem in as_expected(found[row["id"]], observed):
                problems.append(f"{row['id']}: {problem}")
            if row["id"] in ("active_challenge", "angle_hold_false_positive") and observed.get("label") != EXPERIMENTAL_LABEL:
                problems.append(f"{row['id']}: the experimental label is missing")
            if not row.get("actor", {}).get("label"):
                problems.append(f"{row['id']}: no actor label")
            problems += [f"{row['id']}: {leak}" for leak in leak_scan(sorted(folder.iterdir()), None, None)]
            problems += [f"{row['id']}: {hit}" for hit in personal_data(sorted(folder.iterdir()))]
    proof = result.get("pixel_proof") or {}
    if not proof.get("pass") or proof.get("hidden_pixels") != 0 or not proof.get("hidden_checks") or not proof.get("visible_pixels"):
        problems.append("the player pixel proof is missing or did not pass")
    if not (result.get("network") or {}).get("public_bind_refused"):
        problems.append("the public-address refusal was not recorded")
    return problems


# The live lab: the server, the lab window, and the scorer beside them.


class Scorer:
    """Scores the current match with fpsdet every second while it runs, writes what the operator panels read,
    and when the match ends scores it offline too and says whether the two agree."""

    def __init__(self, run: Path, found: dict, transport=None):
        self.run = run
        self.found = found
        self.live = run / "public" / "live"
        self.control = run / "public" / "control"
        self.transport = transport
        self.finished: set[str] = set()
        self.timelines: dict[str, list] = {}
        self.ai_enabled = False
        self.stop = threading.Event()
        self.proof = qualified_pixel_proof()
        (self.live / "knowledge-table.json").write_text(json.dumps(knowledge_table(), indent=1) + "\n", encoding="utf-8")
        self._status({"state": "waiting"})

    def _status(self, extra: dict) -> None:
        body = {"ai": {**ai_status(), "enabled": self.ai_enabled}, "live_every_s": LIVE_EVERY_S, **extra}
        if self.proof is not None:
            body["pixel_proof"] = self.proof
        self._write(self.live / "status.json", body)

    def _write(self, path: Path, body: dict) -> None:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(body, indent=None, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def _control(self) -> None:
        path = self.control / "ai.json"
        if path.exists():
            try:
                self.ai_enabled = bool(json.loads(path.read_text(encoding="utf-8")).get("enabled"))
            except (OSError, ValueError):
                pass

    def current(self) -> dict:
        path = self.run / "public" / "current.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except ValueError:
            return {}

    def loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001  (the lab must keep running; the error is shown on the panel)
                self._status({"state": "error", "error": f"{type(error).__name__}: {error}"})
            self.stop.wait(LIVE_EVERY_S)

    def tick(self) -> None:
        self._control()
        current = self.current()
        live_match = current.get("match_id") or ""
        if current.get("ended"):
            live_match = ""
        # Any match that has ended and was not scored a final time yet: the server may already be in the lobby.
        matches = self.run / "public" / "matches"
        for folder in sorted(matches.iterdir()) if matches.exists() else []:
            if (folder / "match.json").exists() and folder.name not in self.finished and (folder / "events.ndjson").exists():
                record = json.loads((folder / "match.json").read_text(encoding="utf-8"))
                self.score_match(folder, record.get("scenario", ""), final=True)
        if live_match:
            self.score_match(match_folder(self.run, live_match), current.get("scenario", ""), final=False)
        elif not current.get("match_id"):
            self._status({"state": "lobby"})

    def score_match(self, folder: Path, scenario_id: str, final: bool) -> None:
        match_id = folder.name
        scenario = self.found.get(scenario_id, {})
        if scenario.get("external"):
            write_external(folder, scenario, match_id)
        if not (folder / "events.ndjson").exists():
            return
        scored = score_live(folder)
        subject = scored["cases"].get(SUBJECT)
        body = {"match_id": match_id, "scenario": scenario_id, "events": scored["events"], "scoring_ms": scored["scoring_ms"],
                "parse_errors": scored["parse_errors"][:5], "final": final, "live_vs_offline": None, "scored_at_t_ms": last_t_ms(folder),
                "subject": panel_case(subject, folder) if subject else None,
                "others": {name: {"decision": case["decision"], "kinds": sorted(obs["kind"] for obs in case["evidence"]["observations"])}
                           for name, case in scored["cases"].items() if name != SUBJECT},
                "case_timeline": self.timelines.setdefault(match_id, []), "ai_brief": "", "ai": {**ai_status(), "enabled": self.ai_enabled}}
        if subject is not None:
            self.timelines[match_id].append({"t_ms": body["scored_at_t_ms"], "decision": subject["decision"], "kinds": sorted(obs["kind"] for obs in subject["evidence"]["observations"]),
                                             "observations": [obs["observation_id"] for obs in subject["evidence"]["observations"]], "eligibility": rollups(subject),
                                             "challenges": [{key: result.get(key) for key in ("challenge_id", "status", "cause", "eligible_samples", "tracked_samples", "total_ms", "verified_samples")}
                                                            for result in subject["evidence"].get("challenges", [])], "events": scored["events"], "scoring_ms": scored["scoring_ms"]})
        if final:
            with tempfile.TemporaryDirectory() as work:
                offline = score_offline(folder, Path(work) / "offline")
            body["live_vs_offline"] = "identical" if semantic(offline["cases"]) == semantic(scored["cases"]) else "mismatch"
            body["case_problems"] = {name: problems_of(case, registry_for(folder / "plan.json")) for name, case in scored["cases"].items()}
            body["expected"] = scenario.get("expected", {})
            body["problems"] = as_expected(scenario, outcome(scenario, scored, folder)) if scenario else []
            body["realization"] = reproduce(self.run, folder / "plan.json", self.run / "private" / "secret.hex") if (folder / "plan.json").exists() else {"status": "no_challenge"}
            if subject is not None and self.ai_enabled and (ai_status()["configured"] or self.transport is not None):
                before = subject["evidence"]["packet"]["digest"]
                try:
                    body["ai_brief"] = ai_brief(subject, scored["cases"], self.transport)
                except Exception as error:  # noqa: BLE001
                    body["ai_brief"] = f"(the AI endpoint failed: {type(error).__name__})"
                body["ai"]["packet_before"] = before
                body["ai"]["packet_after"] = score_live(folder)["cases"][SUBJECT]["evidence"]["packet"]["digest"]
                body["ai"]["decision_unchanged"] = body["ai"]["packet_before"] == body["ai"]["packet_after"]
            (folder / "live-case.json").write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
            self.finished.add(match_id)
        self._write(self.live / f"{match_id}.json", body)
        self._write(self.live / "current.json", body)
        self._status({"state": "finished" if final else "scoring", "match_id": match_id, "scoring_ms": scored["scoring_ms"], "events": scored["events"]})


def qualified_pixel_proof() -> dict | None:
    """The committed result's player pixel proof, when it was made from the code that is running; else None."""
    if not RESULT.exists():
        return None
    try:
        result = json.loads(RESULT.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if result.get("code") != code_identity() or not result.get("pixel_proof"):
        return None
    proof = result["pixel_proof"]
    return {key: proof.get(key) for key in ("hidden_checks", "hidden_pixels", "visible_checks", "visible_pixels", "pass", "statement")}


def last_t_ms(folder: Path) -> int:
    last = 0
    with (folder / "events.ndjson").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    last = max(last, int(json.loads(line).get("t_ms", 0)))
                except ValueError:
                    continue
    return last


# Commands.


def godot_version(godot: str) -> str:
    try:
        done = subprocess.run([godot, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (done.stdout or done.stderr).strip().splitlines()[-1] if (done.stdout or done.stderr).strip() else ""


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def cmd_doctor(args: argparse.Namespace) -> int:
    checks = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append((name, ok, detail))

    godot = args.godot
    exists = bool(godot) and Path(godot).exists() and os.access(godot, os.X_OK)
    check("Godot executable", exists, godot or "pass --godot PATH (examples/pilot/pilot.py godot --dest ~/godot-4.7.2 downloads the pinned build, user-local)")
    version = godot_version(godot) if exists else ""
    check("Godot version", version == ENGINE_VERSION, f"{version or 'unknown'} (pinned {ENGINE_VERSION})")
    if exists:
        check("Godot binary digest", True, pilot.sha256_file(Path(godot)))
    check("Python", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        import fpsdet  # noqa: F401
        from fpsdet.provenance import detector_fingerprint

        check("fpsdet import", True, f"detector {detector_fingerprint().digest}")
    except Exception as error:  # noqa: BLE001
        check("fpsdet import", False, str(error))
    missing = [name for name in CODE if not (PROJECT / name).exists()]
    check("Project files", not missing and PROFILE.exists(), "all present" if not missing else f"missing {missing}")
    try:
        found = scenarios()
        absent = [name for name in REQUIRED_SCENARIOS if name not in found]
        bad = [name for name, scenario in found.items() if any(field not in scenario.get("card", {}) for field in CARD_FIELDS)]
        check("Scenario definitions", not absent and not bad, f"{len(found)} scenarios" + (f", missing {absent}" if absent else "") + (f", incomplete cards {bad}" if bad else ""))
    except SystemExit as error:
        check("Scenario definitions", False, str(error))
    try:
        profile()
        check("Profile", True, str(PROFILE.relative_to(ROOT)))
    except Exception as error:  # noqa: BLE001
        check("Profile", False, str(error))
    check("Port free (UDP, 127.0.0.1)", port_free(args.port), str(args.port))
    check("Bind address", True, "127.0.0.1 only; the server refuses a public address")
    out = Path(args.out).expanduser() if args.out else Path(tempfile.gettempdir())
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / f".arena-write-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check("Write permission", True, str(out))
    except OSError as error:
        check("Write permission", False, f"{out}: {error}")
    ai = ai_status()
    check("AI reviewer brief (optional)", True, "configured: " + ai["model"] if ai["configured"] else "not configured (optional; set FPSDET_AI_BASE_URL and FPSDET_AI_MODEL)")
    check("xvfb-run (optional, for headless screenshots)", True, shutil.which("xvfb-run") or "not found (only needed to render without a display)")
    width = max(len(name) for name, _ok, _detail in checks)
    failed = 0
    for name, ok, detail in checks:
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name.ljust(width)}  {detail}")
    print("The arena can run." if not failed else f"{failed} problems. Fix them, then run again.")
    return 1 if failed else 0


def cmd_run(args: argparse.Namespace) -> int:
    found = scenarios()
    run = new_run(Path(args.out).expanduser() if args.out else Path(tempfile.mkdtemp(prefix="fpsdet-arena-")))
    plan_all(run, found)
    (run / "public" / "control" / "ai.json").write_text(json.dumps({"enabled": bool(args.ai)}) + "\n", encoding="utf-8")
    print(f"Run folder: {run}")
    print("Server: 127.0.0.1 only. Secret and realizations: private/ (owner only). Press Esc in the lab to free the mouse; 1-9 and letters start scenarios.")
    server = start(server_command(args.godot, run, args.port, headless=not args.show_server, subject_input="autopilot" if args.behaviour else "human"), run, "server.out")
    time.sleep(1.5)
    windows = []
    if args.separate:
        windows.append(start(client_command(args.godot, run, args.port, args.behaviour, run / "public" / "client.jsonl", headless=False), run, "client.out"))
        time.sleep(0.5)
        windows.append(start([args.godot, "--resolution", args.resolution, "--path", str(PROJECT), "--", "--role=operator", f"--run={run}", f"--scenarios={SCENARIOS}",
                              f"--port={args.port}", f"--operator-token-file={operator_token(run)}", f"--mode={args.mode}"], run, "operator.out"))
    else:
        extra = []
        if args.screenshot_every_ms:
            extra.append(f"--screenshot-every-ms={args.screenshot_every_ms}")
        if args.quit_after_ms:
            extra.append(f"--quit-after-ms={args.quit_after_ms}")
        if args.start_scenario:
            extra.append(f"--start-scenario={args.start_scenario}")
        if args.start_replay_after_ms:
            extra.append(f"--start-replay-after-ms={args.start_replay_after_ms}")
        windows.append(start(lab_command(args.godot, run, args.port, args.mode, args.behaviour, args.resolution, tuple(extra), args.virtual_display), run, "lab.out"))
    scorer = Scorer(run, found)
    thread = threading.Thread(target=scorer.loop, daemon=True)
    thread.start()
    try:
        while any(window.poll() is None for window in windows) and server.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for window in windows:
            if window.poll() is None:
                window.terminate()
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        time.sleep(LIVE_EVERY_S)
        scorer.stop.set()
        thread.join(timeout=10)
    print(f"Matches played: {sorted(path.name for path in (run / 'public' / 'matches').iterdir())}")
    for path in sorted((run / "public" / "matches").iterdir()):
        live = path / "live-case.json"
        if live.exists():
            body = json.loads(live.read_text(encoding="utf-8"))
            subject = body.get("subject") or {}
            print(f"  {path.name}: {subject.get('decision')} {[row['kind'] for row in subject.get('findings', [])]} offline {body.get('live_vs_offline')}")
    print(f"Everything is in {run}. Replay a match in the lab with --replay, or rebuild its timeline with: arena.py replay --run {run} --match <id>")
    return 0


def cmd_scenario(args: argparse.Namespace) -> int:
    found = scenarios()
    if args.id not in found:
        raise SystemExit(f"unknown scenario {args.id}; one of {', '.join(found)}")
    run = Path(args.out).expanduser() if args.out else Path(tempfile.mkdtemp(prefix=f"fpsdet-arena-{args.id}-"))
    live = run_scenario(args.godot, args.id, run, args.port, deterministic=args.deterministic)
    row = judge(live, found[args.id])
    row["timeline"] = case_timeline(row["folder"])
    printable = {key: (str(value) if isinstance(value, Path) else value) for key, value in row.items() if key not in ("cases", "timeline")}
    print(json.dumps(printable, indent=1, default=str))
    print(f"{args.id}: {'as expected' if row['as_expected'] else 'EXPECTED != ACTUAL: ' + '; '.join(row['problems'])}; offline replay {row['live_vs_offline']}; run folder {run}")
    return 0 if row["as_expected"] and row["live_vs_offline"] == "identical" and not row["secret_leaks"] else 1


def cmd_qualify(args: argparse.Namespace) -> int:
    root = Path(args.out).expanduser()
    found = qualify(args.godot, root, args.port, args.parallel, args.only, args.deterministic, pixels=not args.no_pixels)
    rows = found["rows"]
    if args.only:
        for row in rows:
            print(f"{row['id']}: {'as expected' if row['as_expected'] else row['problems']}")
        print("Not written to result.json: --only runs a subset.")
        return 0 if all(row["as_expected"] and row["live_vs_offline"] == "identical" for row in rows) else 1
    if found["pixel_proof"] is None:
        raise SystemExit("the player pixel proof did not run; result.json is not written without it")
    body = capture(rows, args.godot, found["pixel_proof"], found["network"])
    failed = [row["id"] for row in body["scenarios"] if not row["as_expected"] or row["live_vs_offline"] != "identical" or row["secret_leaks"]
              or row["personal_data"] or row["case_problems"] or row["realization"].get("status") not in ("reproduced", "no_challenge")]
    if not body["pixel_proof"]["pass"]:
        failed.append("pixel_proof")
    if not body["network"]["public_bind_refused"]:
        failed.append("network")
    print(f"Wrote {RESULT.relative_to(ROOT)} ({body['digest']}) and {len(rows)} captures. Not as expected: {failed or 'none'}.")
    return 1 if failed else 0


def cmd_verify(_args: argparse.Namespace) -> int:
    problems = verify()
    for problem in problems:
        print(problem)
    print("the committed captures reproduce result.json" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


def cmd_replay(args: argparse.Namespace) -> int:
    run = Path(args.run).expanduser()
    folder = match_folder(run, args.match)
    if not (folder / "events.ndjson").exists():
        raise SystemExit(f"no events for {args.match} in {run}")
    found = scenarios()
    scored = score_live(folder)
    subject = scored["cases"].get(SUBJECT)
    with tempfile.TemporaryDirectory() as work:
        offline = score_offline(folder, Path(work) / "offline")
    current = json.loads((folder / "match.json").read_text(encoding="utf-8")) if (folder / "match.json").exists() else {}
    scenario = found.get(current.get("scenario"), {})
    body = {"match_id": args.match, "scenario": current.get("scenario"), "events": scored["events"], "scoring_ms": scored["scoring_ms"], "final": True,
            "scored_at_t_ms": last_t_ms(folder), "live_vs_offline": "identical" if semantic(offline["cases"]) == semantic(scored["cases"]) else "mismatch",
            "subject": panel_case(subject, folder) if subject else None, "case_timeline": case_timeline(folder),
            "others": {name: {"decision": case["decision"]} for name, case in scored["cases"].items() if name != SUBJECT},
            "expected": scenario.get("expected", {}), "problems": as_expected(scenario, outcome(scenario, scored, folder)) if scenario and subject else [],
            "ai_brief": "", "ai": ai_status()}
    (run / "public" / "live").mkdir(parents=True, exist_ok=True)
    (run / "public" / "live" / f"{args.match}.json").write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    (folder / "live-case.json").write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
    for row in body["case_timeline"]:
        print(f"{row['t_ms']:>6} ms  {row['decision']:<18} {row['kinds']}")
    print(f"offline replay: {body['live_vs_offline']}; packet {body['subject']['packet'].get('digest') if subject else None}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Check this machine can run the arena")
    doctor.add_argument("--godot", default="")
    doctor.add_argument("--port", type=int, default=24760)
    doctor.add_argument("--out", help="The folder runs will be written to")
    doctor.set_defaults(func=cmd_doctor)
    run = sub.add_parser("run", help="The playable lab: a server, a window with the player view and the server view, and the live scorer")
    run.add_argument("--godot", required=True)
    run.add_argument("--out", help="The run folder (default: a new temporary folder)")
    run.add_argument("--port", type=int, default=24760)
    run.add_argument("--mode", choices=("demo", "developer"), default="developer")
    run.add_argument("--resolution", default="1920x1080")
    run.add_argument("--behaviour", help="Put the honest autopilot at the keyboard instead of a person (tracker, trigger, runner, holder)")
    run.add_argument("--separate", action="store_true", help="A player window and an operator window as separate processes")
    run.add_argument("--show-server", action="store_true", help="Run the server with a window (it draws nothing; for debugging)")
    run.add_argument("--ai", action="store_true", help="Start with the AI reviewer brief switched on (needs FPSDET_AI_BASE_URL and FPSDET_AI_MODEL)")
    run.add_argument("--start-scenario", help="Start this scenario as soon as the lab joins (the lab's keys still work)")
    run.add_argument("--screenshot-every-ms", type=int, default=0, help="Save a still of the lab window this often into <run>/operator/shots")
    run.add_argument("--quit-after-ms", type=int, default=0, help="Close the lab after this long (for recordings and checks)")
    run.add_argument("--start-replay-after-ms", type=int, default=0, help="Enter the replay of the last finished match after this long")
    run.add_argument("--virtual-display", action="store_true", help="Open the lab in a private virtual display (xvfb-run), not on the desktop")
    run.set_defaults(func=cmd_run)
    scenario = sub.add_parser("scenario", help="One scenario, headless: run, score live and offline, compare with its expectation")
    scenario.add_argument("id")
    scenario.add_argument("--godot", required=True)
    scenario.add_argument("--out")
    scenario.add_argument("--port", type=int, default=24770)
    scenario.add_argument("--deterministic", action="store_true", help="Server only, fixed 60 Hz, the autopilot in-process: no client")
    scenario.set_defaults(func=cmd_scenario)
    qualify_ = sub.add_parser("qualify", help="Every scenario, headless; writes result.json and captures/")
    qualify_.add_argument("--godot", required=True)
    qualify_.add_argument("--out", required=True)
    qualify_.add_argument("--port", type=int, default=24800)
    qualify_.add_argument("--parallel", type=int, default=4)
    qualify_.add_argument("--only", action="append", help="Run only this scenario (repeatable); result.json is not written")
    qualify_.add_argument("--deterministic", action="store_true")
    qualify_.add_argument("--no-pixels", action="store_true", help="Skip the player pixel proof (result.json is then not written)")
    qualify_.set_defaults(func=cmd_qualify)
    check = sub.add_parser("verify", help="Offline: score the committed captures again and compare with result.json")
    check.set_defaults(func=cmd_verify)
    replay = sub.add_parser("replay", help="Rebuild one match's case timeline and live/offline comparison for the lab's replay mode")
    replay.add_argument("--run", required=True)
    replay.add_argument("--match", required=True)
    replay.set_defaults(func=cmd_replay)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
