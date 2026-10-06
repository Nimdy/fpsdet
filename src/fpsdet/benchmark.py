"""The fpsdet benchmark: ``fpsdet.benchmark/1``. Reproducible, falsifiable, and never one score.

It turns the planted fixtures, the controlled scenarios and the two labelled real-data runs into one chain a
third party can rebuild or check:

    source data -> prepared input -> profile and cohort -> scored packets -> evaluation -> strength -> report

Three classes, never merged into one number, because each answers a different question:

- ``synthetic``: planted cheats, honest twins, eligibility probes, challenge scenarios and external-signature
  scenarios. Built by fpsdet's own code; they qualify code, never a rate.
- ``real``: TF2 league matches with RGL ban labels, and CS2 matchmaking with the CS2CD authors' labels. Built
  from public data by the example converters, with frozen selection and splits.

Each dataset has a manifest (``fpsdet.benchmark-dataset/1``) that names its source, licence, label
semantics, pseudonymization, the exact commands that acquire and prepare it, and the digests of what they
must produce. A run (``fpsdet.benchmark-result/1``) binds every step of the chain by digest and compares it
with the pinned expectation (``fpsdet.benchmark-expected/1``):

    MATCH              every identity and every number as pinned
    ENVIRONMENT_ONLY   the same, on another Python, platform or optional package
    PROVENANCE_ONLY    the same numbers, from changed detector code (a re-pin is due)
    PRESENTATION_ONLY  the same numbers; only a generated report's text moved
    DRIFT              a decision, finding, eligibility, evaluation or strength number moved
    INPUT_CHANGED      the prepared inputs, labels, cohort, selection code, profile or pseudonym key differ
    NOT_RUN            the prepared data is not on this machine

with the categories that explain it. It is a non-detection module: the scorer never imports it, and nothing
it pins is ever read as configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from .evidence import KINDS, NATIVE_KINDS, canonical_json, eligibility_unit, rollup
from .provenance import normalized_source, profile_digest

BENCHMARK = "fpsdet.benchmark/1"
DATASET = "fpsdet.benchmark-dataset/1"
RESULT = "fpsdet.benchmark-result/1"
EXPECTED = "fpsdet.benchmark-expected/1"
SPLIT = "fpsdet.benchmark-split/1"
CAPABILITIES = "fpsdet.benchmark-capabilities/1"
KEY_COMMITMENT = "fpsdet.pseudonym-key-commitment/1"
ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "benchmark" / "benchmark.json"
ALIASES = {"synthetic": "synthetic-v1", "tf2": "tf2-rgl-v1", "cs2": "cs2cd-v1"}
STATUSES = ("MATCH", "ENVIRONMENT_ONLY", "PROVENANCE_ONLY", "PRESENTATION_ONLY", "DRIFT", "INPUT_CHANGED", "NOT_RUN")
# The fields a result's identity and digest cover. Environment, timings and the comparison never do.
IDENTITY = ("format", "benchmark", "dataset", "chain", "semantic", "published")
# Strength numbers come from gamma functions, whose last digit can differ between C libraries. They are
# compared at this many significant digits; every other number is compared exactly.
STRENGTH_DIGITS = 4


class BenchmarkError(ValueError):
    """The benchmark cannot run or verify as asked."""


# Small, deterministic helpers.


def sha256_file(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return "sha256:" + hasher.hexdigest()


def code_digest(path: str | Path) -> str:
    """A source file as checked out: line ends and a byte-order mark do not move it."""
    return "sha256:" + hashlib.sha256(normalized_source(Path(path).read_bytes())).hexdigest()


def digest(recipe: str, value) -> str:
    return "sha256:" + hashlib.sha256(f"fpsdet.benchmark-{recipe}/1\0".encode("ascii") + canonical_json(value).encode("utf-8")).hexdigest()


def _rounded(value, digits: int = STRENGTH_DIGITS):
    if isinstance(value, float):
        return float(f"{value:.{digits}g}") if math.isfinite(value) else repr(value)
    if isinstance(value, dict):
        return {key: _rounded(item, digits) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item, digits) for item in value]
    return value


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def relative(path: str | Path) -> str:
    return Path(path).resolve().relative_to(ROOT).as_posix()


def environment() -> dict:
    """Reported, never part of a result's identity."""
    from . import __version__

    try:
        import cryptography

        crypto = cryptography.__version__
    except ImportError:
        crypto = None
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "fpsdet": __version__,
        "cryptography": crypto,
    }


# The definition and the manifests.


def definition(root: Path = ROOT) -> dict:
    found = read_json(root / "benchmark" / "benchmark.json")
    if found.get("format") != BENCHMARK:
        raise BenchmarkError(f"benchmark/benchmark.json is not {BENCHMARK}")
    return found


def dataset_id(name: str) -> str:
    return ALIASES.get(name, name)


def manifest(name: str, root: Path = ROOT) -> dict:
    wanted = dataset_id(name)
    for entry in definition(root)["datasets"]:
        if entry["id"] == wanted:
            found = read_json(root / entry["manifest"])
            problems = check_manifest(found)
            if problems:
                raise BenchmarkError(f"{entry['manifest']}: " + "; ".join(problems))
            return found
    raise BenchmarkError(f"no benchmark dataset {name!r}; the datasets are {', '.join(ALIASES)}")


REQUIRED = {
    "real": ("id", "class", "title", "question", "source", "license", "pseudonymization", "label_semantics", "acquisition",
             "selection", "prepare", "profile", "inputs", "split", "artifacts", "expected_counts"),
    "synthetic": ("id", "class", "title", "question", "components", "code", "artifacts", "license"),
}


def check_manifest(found: Mapping) -> list[str]:
    if found.get("format") != DATASET:
        return [f"not {DATASET}"]
    kind = found.get("class")
    if kind not in REQUIRED:
        return [f"class is real or synthetic, not {kind!r}"]
    return [f"it does not say its {field}" for field in REQUIRED[kind] if field not in found]


def manifest_digest(found: Mapping) -> str:
    return digest("dataset", found)


def code_identity(paths: Iterable[str], root: Path = ROOT) -> dict:
    return {path: code_digest(root / path) for path in sorted(paths)}


def profile_identity(path: str, root: Path = ROOT) -> str:
    from .parse import load_profile

    return profile_digest(load_profile(root / path))


def label_definition_digest(path: str, root: Path = ROOT) -> str:
    from .calibration import load_dataset

    return digest("label-definition", load_dataset(root / path))


# Identities of a scored case set.


def case_identities(cases: list[dict], key=lambda case: case["player_id"]) -> dict:
    """What a set of cases says, as digests: decisions, findings, eligibility and legacy seals are semantics;
    the packets, which bind the detector and inputs that produced them, are provenance."""
    from .calibration import inputs_digest

    rows = sorted(cases, key=lambda case: canonical_json(key(case)))
    return {
        "cases": len(rows),
        "decisions": dict(sorted(Counter(case["decision"] for case in rows).items())),
        "decisions_digest": digest("decisions", [[key(case), case["decision"]] for case in rows]),
        "observations_digest": digest("observations", [[key(case), sorted(obs["observation_id"] for obs in case["evidence"]["observations"])] for case in rows]),
        "eligibility_digest": digest("eligibility", [[key(case), case["evidence"].get("detector_eligibility")] for case in rows]),
        "seals_digest": digest("seals", [[key(case), case.get("seal")] for case in rows]),
        "packets": inputs_digest(rows) if all(key(case) == case["player_id"] for case in rows) else digest("packets", [[key(case), case["evidence"]["packet"].get("digest")] for case in rows]),
    }


def _shared_provenance(cases: list[dict]) -> dict:
    stamp = (cases[0]["evidence"].get("provenance") or {}) if cases else {}
    return {
        "detector": (stamp.get("detector") or {}).get("digest"),
        "profile": (stamp.get("profile") or {}).get("digest"),
        "cohort": (stamp.get("cohort") or {}).get("digest"),
    }


def _verify_cases(cases: list[dict]) -> dict:
    from .graph import verify_graphs
    from .provenance import verify_packet

    problems = [f"{case['player_id']}: {problem}" for case in cases for problem in verify_packet(case)]
    graph = verify_graphs(cases)
    return {"packets_verified": len(cases) - len({p.split(':', 1)[0] for p in problems}), "graphs_checked": len(cases), "problems": (problems + graph)[:20]}


def eligibility_states(cases: Iterable[Mapping]) -> dict[str, list[str]]:
    """Every eligibility state the cases show per detector, with eligible split into fired and quiet."""
    found: dict[str, set[str]] = {kind: set() for kind in NATIVE_KINDS}
    for case in cases:
        fired = {(obs["kind"], eligibility_unit(obs)) for obs in case["evidence"]["observations"] if obs["source"] == "fpsdet"}
        for kind, entry in case["evidence"]["detector_eligibility"]["detectors"].items():
            for name, units in entry.items():
                for unit in units:
                    found[kind].add(name if name != "eligible" else ("eligible, fired" if (kind, unit) in fired else "eligible, quiet"))
    return {kind: sorted(states) for kind, states in found.items()}


# The synthetic class.


def run_synthetic(found: Mapping) -> dict:
    """Every controlled fixture and scenario, rebuilt and qualified. Seconds, no data, no network."""
    from .calibration import qualify_fixtures
    from .fixtures import auth_scenarios, build_fixtures, challenge_scenarios, fixture_failures
    from .persist import case_to_dict
    from .synthetic import build_demo
    from .week import build_week

    timings: dict[str, float] = {}
    start = time.perf_counter()
    demo = build_demo()
    week = build_week()
    world = build_fixtures()
    timings["build"] = time.perf_counter() - start
    demo_cases = [case_to_dict(case) for case in demo.cases]
    fixture_cases = [case_to_dict(case) for case in world.cases]
    weekly = [case_to_dict(case) for case in week.cases]
    nightly = [(night, case_to_dict(case)) for night, cases in enumerate(week.nightly) for case in cases]
    qualification = qualify_fixtures(demo, world)
    challenge = challenge_scenarios()
    auth = auth_scenarios()
    timings["qualify"] = time.perf_counter() - start - timings["build"]
    nightly_ids = case_identities([dict(case, _night=night) for night, case in nightly], key=lambda case: [case["_night"], case["player_id"]])
    sets = {
        "demo": case_identities(demo_cases),
        "fixtures": case_identities(fixture_cases),
        "week": case_identities(weekly),
        "nightly": nightly_ids,
    }
    semantic = {
        **{name: {k: v for k, v in ids.items() if k != "packets"} for name, ids in sets.items()},
        "demo_failures": list(demo.failures),
        "fixture_failures": fixture_failures(world),
        "qualification": {
            entry["kind"]: {
                "outcome": entry["outcome"],
                "planted": [f"{row['world']}:{row['player']}" for row in entry["planted"]],
                "planted_fired": all(row["fired"] for row in entry["planted"]),
                "twins": [f"{row['world']}:{row['player']}" for row in entry["honest_twins"]],
                "twins_quiet": not any(row["fired"] for row in entry["honest_twins"]),
            }
            for entry in qualification["detectors"]
        },
        "honest_demo_fixtures_clean": sum(qualification["honest_fixtures_clean"].values()),
        "eligibility_states": eligibility_states(demo_cases + fixture_cases),
        "challenge": [{key: row[key] for key in ("scenario", "expected", "observed", "decision", "findings", "as_expected")} for row in challenge],
        "auth": {"status": auth["status"], "scenarios": [{key: row[key] for key in ("scenario", "expected", "observed", "read", "as_expected")} for row in auth["scenarios"]]},
    }
    chain = {
        "manifest": manifest_digest(found),
        "code": code_identity(found["code"]),
        "profiles": {name: profile_identity(path) for name, path in found["profiles"].items()},
        "detector": _shared_provenance(demo_cases)["detector"],
        "packets": {name: ids["packets"] for name, ids in sets.items()},
        "verified": {name: _verify_cases(cases) for name, cases in (("demo", demo_cases), ("fixtures", fixture_cases), ("week", weekly))},
    }
    published = {
        "qualification_passes": sum(entry["outcome"] == "passes_controlled_fixture" for entry in qualification["detectors"]),
        "detectors": len(qualification["detectors"]),
        "challenge_as_expected": sum(row["as_expected"] for row in challenge),
        "challenge_scenarios": len(challenge),
        "auth_as_expected": sum(row["as_expected"] for row in auth["scenarios"]),
        "auth_scenarios": len(auth["scenarios"]),
    }
    return assemble(found, chain, semantic, published, timings)


# The real classes.


def key_commitment(path: str | Path) -> str | None:
    """A commitment to the pseudonym key, so a rebuild can say whether its key is the curator's. It reveals
    nothing about the key: the key is 32 random bytes."""
    target = Path(path)
    if not target.exists():
        return None
    key = bytes.fromhex(target.read_text(encoding="utf-8").strip())
    return "sha256:" + hashlib.sha256(KEY_COMMITMENT.encode("ascii") + b"\0" + key).hexdigest()


def prepared_identity(found: Mapping, data: Path) -> dict:
    """What the prepared inputs in ``data`` are, by digest, as the manifest names them."""
    from .calibration import labels_digest
    from .persist import cohort_from_dict
    from .provenance import cohort_digest

    files = found["prepare"]["outputs"]
    out: dict = {}
    for name in ("events", "baseline"):
        path = data / files[name]
        out[name] = {"sha256": sha256_file(path) if path.exists() else None}
    labels_path = data / files["labels"]
    labels = read_json(labels_path) if labels_path.exists() else None
    out["labels"] = {"sha256": sha256_file(labels_path) if labels else None, "digest": labels_digest(labels) if labels else None,
                     "count": len(labels) if labels else 0}
    cohort_path = data / files["cohort"]
    if cohort_path.exists():
        table = cohort_from_dict(read_json(cohort_path))
        players = {pid for pairs in table._values.values() for pid, _ in pairs}
        out["cohort"] = {"digest": cohort_digest(table._values), "players": len(players)}
    else:
        out["cohort"] = {"digest": None, "players": 0}
    key = found["pseudonymization"].get("key_file")
    out["key"] = key_commitment(data / key) if key else None
    return out


def source_identity(found: Mapping, data: Path) -> dict | None:
    """The downloaded source data, by digest, where it is on this machine: what a rebuild started from."""
    source = found["source"]["identity"]
    files: list[Path] = []
    for pattern in source["files"]:
        files += sorted(path for path in data.glob(pattern) if path.is_file())
    if not files:
        return None
    return {"files": len(files), "digest": digest("source", [[path.relative_to(data).as_posix(), sha256_file(path)] for path in files])}


def run_steps(steps: list, data: Path, python: str) -> list[str]:
    """Run a manifest's commands in order. Each is an argv with {data}, {python} and {fpsdet} filled in, or a
    concatenation of prepared files. Stops at the first that fails."""
    import os
    import subprocess
    import sys

    log = []
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    for step in steps:
        if "concat" in step:
            target = data / step["out"]
            with target.open("wb") as out:
                for name in step["concat"]:
                    out.write((data / name).read_bytes())
            log.append(f"concatenated {', '.join(step['concat'])} into {step['out']}")
            continue
        argv: list[str] = []
        for part in step["run"]:
            if part == "{fpsdet}":
                argv += [sys.executable, "-m", "fpsdet"]
            else:
                argv.append(part.replace("{data}", str(data)).replace("{python}", python))
        completed = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
        log.append(" ".join(step["run"]) + f"  (exit {completed.returncode})")
        if completed.returncode != 0:
            raise BenchmarkError(f"{' '.join(argv)} failed:\n{completed.stderr[-2000:]}")
    return log


def fetch(found: Mapping, data: Path, python: str) -> list[str]:
    """Download a real dataset's public sources into ``data``, with the manifest's exact parameters. Network."""
    if found["class"] != "real":
        raise BenchmarkError(f"{found['id']} is built by fpsdet's own code; there is nothing to fetch")
    data.mkdir(parents=True, exist_ok=True)
    return run_steps(found["acquisition"]["steps"], data, python)


def prepare(found: Mapping, data: Path, python: str) -> dict:
    """Rebuild the scorer-ready inputs from the downloaded sources, offline, and say how they compare with
    the manifest. A difference is reported, never accepted silently."""
    if found["class"] != "real":
        raise BenchmarkError(f"{found['id']} is built by fpsdet's own code; there is nothing to prepare")
    log = run_steps(found["prepare"]["steps"], data, python)
    now = prepared_identity(found, data)
    now["source"] = source_identity(found, data)
    changes = input_changes(found, now) + selection_changes(found)
    return {"dataset": found["id"], "inputs": now, "changes": changes, "status": "INPUT_CHANGED" if changes else "MATCH", "log": log}


def input_changes(found: Mapping, now: Mapping) -> list[dict]:
    """How the prepared inputs differ from the manifest, with the category and what it means."""
    pinned = found["inputs"]
    changes = []
    meaning = {
        "events": ("input changed", "the scored events differ"),
        "baseline": ("input changed", "the baseline events differ"),
        "labels": ("label changed", "the labels differ"),
        "cohort": ("cohort changed", "the baseline cohort differs"),
        "key": ("input changed", "the pseudonym key is not the curator's: every player id, and any split by pseudonym, differs"),
    }
    want, got = pinned.get("source"), now.get("source")
    if want and got and want != got:
        changes.append({"category": "input changed", "input": "source", "detail": "the downloaded source data differs from what was pinned: the upstream data changed, or a different selection was fetched", "pinned": want["digest"], "found": got["digest"]})
    for name, (category, text) in meaning.items():
        want, got = pinned.get(name), now.get(name)
        if name == "key":
            if want != got:
                changes.append({"category": category, "input": name, "detail": text, "pinned": want, "found": got})
            continue
        field = {"events": "sha256", "baseline": "sha256", "labels": "digest", "cohort": "digest"}[name]
        if (want or {}).get(field) != (got or {}).get(field):
            changes.append({"category": category, "input": name, "detail": text, "pinned": (want or {}).get(field), "found": (got or {}).get(field)})
    return changes


def selection_changes(found: Mapping, root: Path = ROOT) -> list[dict]:
    """Selection code, profile and label definition as checked out, against the manifest."""
    changes = []
    for path, want in found["selection"]["code"].items():
        got = code_digest(root / path) if (root / path).exists() else None
        if got != want:
            changes.append({"category": "selection changed", "input": path, "detail": "the converter that selects and builds the data changed", "pinned": want, "found": got})
    got = profile_identity(found["profile"]["path"], root)
    if got != found["profile"]["digest"]:
        changes.append({"category": "profile changed", "input": found["profile"]["path"], "detail": "the profile the cases are scored with changed", "pinned": found["profile"]["digest"], "found": got})
    got = label_definition_digest(found["label_semantics"]["definition"], root)
    if got != found["label_semantics"]["digest"]:
        changes.append({"category": "label changed", "input": found["label_semantics"]["definition"], "detail": "what the labels mean, or the published decisions, changed", "pinned": found["label_semantics"]["digest"], "found": got})
    return changes


def split_manifest(found: Mapping, evaluation: Mapping, labels: Mapping[str, str], cohort_players: set[str]) -> dict:
    """Every subject's membership: development or evaluation (fpsdet.calibration-split/1, by the dataset's
    unit), baseline_only (built the baseline, never scored), not_scored (labelled, no case) or unlabelled."""
    from .strength import SPLIT_RECIPE, _unit, split_of

    dataset = evaluation["dataset"]
    rows = {row["player"]: row for row in evaluation["rows"]}
    subjects = []
    for player in sorted(set(labels) | cohort_players | set(rows)):
        label = labels.get(player)
        if player in rows:
            unit = _unit(rows[player], dataset)
            subjects.append([player, label, unit, split_of(unit)])
        elif player in cohort_players:
            subjects.append([player, label, None, "baseline_only"])
        elif label is not None:
            subjects.append([player, label, None, "not_scored"])
    counts: dict[str, Counter] = {}
    for _player, label, _unit_id, membership in subjects:
        counts.setdefault(membership, Counter())[label or "unlabelled"] += 1
    body = {
        "format": SPLIT,
        "dataset": found["id"],
        "recipe": SPLIT_RECIPE,
        "unit": dataset.get("split_unit", "player"),
        "memberships": ["development", "evaluation", "baseline_only", "not_scored"],
        "counts": {name: dict(sorted(counter.items())) for name, counter in sorted(counts.items())},
        "subjects": subjects,
    }
    body["digest"] = digest("split", {key: value for key, value in body.items() if key != "digest"})
    return body


def _strength_estimates(artifact: Mapping) -> dict:
    return _rounded({key: artifact[key] for key in ("detectors", "families", "co_occurrence", "split")})


def run_real(found: Mapping, data: Path, out: Path) -> tuple[dict, dict]:
    """Score one real dataset's prepared inputs offline, and evaluate, estimate and bind every step. Returns
    the result and the artifacts it wrote to ``out``."""
    from .calibration import census, dumps, evaluate, load_dataset, render_markdown
    from .parse import load_events, load_profile
    from .persist import case_to_dict, cohort_from_dict
    from .pipeline import run_score
    from .strength import dumps as strength_dumps
    from .strength import render_markdown as strength_markdown
    from .strength import strength

    timings: dict[str, float] = {}
    files = found["prepare"]["outputs"]
    started = time.perf_counter()
    now = prepared_identity(found, data)
    timings["verify_inputs"] = time.perf_counter() - started
    now["source"] = None  # a run reads only the prepared inputs; prepare is what checks the download
    missing = [name for name in ("events", "labels", "cohort") if not (data / files[name]).exists()]
    if missing:
        raise BenchmarkError(f"{found['id']}: no prepared {', '.join(missing)} in {data}. Run fpsdet benchmark prepare first.")
    mark = time.perf_counter()
    events, errors = load_events(data / files["events"])
    now["events"]["events"] = len(events) + len(errors)
    profile = load_profile(ROOT / found["profile"]["path"])
    table = cohort_from_dict(read_json(data / files["cohort"]))
    cases = sorted((case_to_dict(case) for case in run_score(events, profile, table)), key=lambda case: case["player_id"])
    del events
    timings["score"] = time.perf_counter() - mark
    mark = time.perf_counter()
    checked = _verify_cases(cases)
    timings["verify_packets"] = time.perf_counter() - mark
    out.mkdir(parents=True, exist_ok=True)
    with (out / "cases.jsonl").open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    mark = time.perf_counter()
    labels = read_json(data / files["labels"])
    dataset = load_dataset(ROOT / found["label_semantics"]["definition"])
    evaluation = evaluate(cases, labels, dataset, profile, telemetry_census=census(data / files["events"]))
    timings["evaluate"] = time.perf_counter() - mark
    mark = time.perf_counter()
    estimated = strength(evaluation)
    timings["strength"] = time.perf_counter() - mark
    cohort_players = {pid for pairs in table._values.values() for pid, _ in pairs}
    split = split_manifest(found, evaluation, labels, cohort_players)
    reports = {"evaluation": render_markdown(evaluation), "strength": strength_markdown(estimated)}
    (out / "evaluation.json").write_text(dumps(evaluation), encoding="utf-8")
    (out / "evaluation.md").write_text(reports["evaluation"], encoding="utf-8")
    (out / "strength.json").write_text(strength_dumps(estimated), encoding="utf-8")
    (out / "strength.md").write_text(reports["strength"], encoding="utf-8")
    write_json(out / "split.json", split)
    identities = case_identities(cases)
    chain = {
        "manifest": manifest_digest(found),
        "inputs": now,
        "input_errors": len(errors),
        "selection": {"code": code_identity(found["selection"]["code"]), "parameters": digest("selection-parameters", found["selection"]["parameters"])},
        "profile": profile_digest(profile),
        "label_definition": label_definition_digest(found["label_semantics"]["definition"]),
        **{name: value for name, value in _shared_provenance(cases).items() if name != "profile"},
        "scored": {"packets": identities["packets"], **checked},
        "evaluation": {"schema": evaluation["schema"], "digest": evaluation["digest"], "statistics": digest("statistics", evaluation["statistics"])},
        "strength": {"schema": estimated["schema"], "digest": estimated["digest"], "estimates": digest("estimates", _strength_estimates(estimated))},
        "split": {"digest": split["digest"]},
        "reports": {name: "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in reports.items()},
    }
    semantic = {
        **{key: value for key, value in identities.items() if key != "packets"},
        "evaluation_statistics": chain["evaluation"]["statistics"],
        "strength_estimates": chain["strength"]["estimates"],
        "split": split["counts"],
        "detector_status": {entry["kind"]: entry["status"] for entry in evaluation["statistics"]["detectors"]},
        "stability": {entry["kind"]: entry["evaluation"]["stability"] for entry in estimated["detectors"] if "evaluation" in entry},
    }
    published = {
        "decisions_reproduce": bool((evaluation.get("published") or {}).get("reproduced")),
        "where": (evaluation.get("published") or {}).get("where"),
        "decisions": {label: cell["counts"] for label, cell in evaluation["statistics"]["decisions"]["by_group"].items()},
    }
    result = assemble(found, chain, semantic, published, timings)
    write_json(out / "result.json", result)
    return result, {"evaluation": evaluation, "strength": estimated, "split": split, "reports": reports}


# The result, and its comparison with what was pinned.


def assemble(found: Mapping, chain: Mapping, semantic: Mapping, published: Mapping, timings: Mapping) -> dict:
    artifact = {
        "format": RESULT,
        "benchmark": BENCHMARK,
        "dataset": {"id": found["id"], "class": found["class"], "manifest": manifest_digest(found)},
        "chain": chain,
        "semantic": semantic,
        "published": published,
    }
    artifact = json.loads(canonical_json(artifact))
    artifact["digest"] = result_digest(artifact)
    artifact["environment"] = environment()
    artifact["timings_s"] = {name: round(value, 2) for name, value in timings.items()}
    return artifact


def result_digest(artifact: Mapping) -> str:
    return digest("result", {key: artifact[key] for key in IDENTITY if key in artifact})


# Each field a comparison reads, the category a difference in it falls under, and its kind: an input,
# a semantic number, provenance (who produced it), or presentation.
FIELDS = (
    ("chain.inputs.events.sha256", "input changed", "input"),
    ("chain.inputs.baseline.sha256", "input changed", "input"),
    ("chain.inputs.labels.digest", "label changed", "input"),
    ("chain.inputs.cohort.digest", "cohort changed", "input"),
    ("chain.inputs.key", "input changed", "input"),
    ("chain.selection", "selection changed", "input"),
    ("chain.code", "input changed", "input"),
    ("chain.profile", "profile changed", "input"),
    ("chain.profiles", "profile changed", "input"),
    ("chain.label_definition", "label changed", "input"),
    ("chain.detector", "detector code changed", "provenance"),
    ("chain.cohort", "cohort changed", "input"),
    ("chain.scored.packets", "detector code changed", "provenance"),
    ("chain.packets", "detector code changed", "provenance"),
    ("chain.evaluation.digest", "evaluation changed", "provenance"),
    ("chain.strength.digest", "strength changed", "provenance"),
    ("chain.reports", "presentation only", "presentation"),
    ("semantic.decisions_digest", "decision changed", "semantic"),
    ("semantic.observations_digest", "evidence changed", "semantic"),
    ("semantic.seals_digest", "evidence changed", "semantic"),
    ("semantic.eligibility_digest", "eligibility changed", "semantic"),
    ("semantic.evaluation_statistics", "evaluation changed", "semantic"),
    ("semantic.strength_estimates", "strength changed", "semantic"),
    ("semantic.split", "selection changed", "semantic"),
    ("semantic.demo", "decision changed", "semantic"),
    ("semantic.fixtures", "decision changed", "semantic"),
    ("semantic.week", "decision changed", "semantic"),
    ("semantic.nightly", "decision changed", "semantic"),
    ("semantic.qualification", "qualification changed", "semantic"),
    ("semantic.eligibility_states", "eligibility changed", "semantic"),
    ("semantic.challenge", "qualification changed", "semantic"),
    ("semantic.auth", "qualification changed", "semantic"),
    ("semantic.demo_failures", "qualification changed", "semantic"),
    ("semantic.fixture_failures", "qualification changed", "semantic"),
)


def _get(obj: Mapping, path: str):
    node = obj
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def compare(result: Mapping, expected: Mapping | None) -> dict:
    """The result against what was pinned: a status, and every category that explains it."""
    if expected is None:
        return {"status": "NOT_RUN", "categories": [], "changes": [{"detail": "nothing is pinned for this dataset"}]}
    changes = []
    environment_only = []
    for path, category, kind in FIELDS:
        want, got = _get(expected, path), _get(result, path)
        if want is None and got is None:
            continue
        if path == "semantic.auth" and want != got and "not_run" in ((got or {}).get("status"), (want or {}).get("status")):
            environment_only.append({"field": path, "detail": "external-signature scenarios need the optional cryptography package"})
            continue
        if want != got:
            changes.append({"field": path, "category": category, "kind": kind})
    kinds = {change["kind"] for change in changes}
    if "input" in kinds:
        status = "INPUT_CHANGED"
    elif "semantic" in kinds:
        status = "DRIFT"
    elif "provenance" in kinds:
        status = "PROVENANCE_ONLY"
    elif "presentation" in kinds:
        status = "PRESENTATION_ONLY"
    elif environment_only or (expected.get("environment") or {}).get("python") != (result.get("environment") or {}).get("python"):
        status = "ENVIRONMENT_ONLY"
    else:
        status = "MATCH"
    categories = sorted({change["category"] for change in changes})
    if status == "PROVENANCE_ONLY" and categories == ["detector code changed", "evaluation changed", "strength changed"]:
        categories = ["detector code changed"]  # the artifact digests bind the detector; their numbers did not move
    return {"status": status, "categories": categories, "changes": changes + environment_only}


def expected_entry(result: Mapping) -> dict:
    """What a pin records: the result's identity, with the environment it was pinned on."""
    return {**{key: result[key] for key in IDENTITY if key in result}, "digest": result["digest"], "environment": result["environment"]}


def load_expected(root: Path = ROOT) -> dict:
    path = root / definition(root)["expected"]
    if not path.exists():
        return {"format": EXPECTED, "benchmark": BENCHMARK, "datasets": {}}
    found = read_json(path)
    if found.get("format") != EXPECTED:
        raise BenchmarkError(f"{path} is not {EXPECTED}")
    return found


def pin(results: Iterable[Mapping], root: Path = ROOT) -> dict:
    """Write each result's identity as the expectation for its dataset. A curator's act, never automatic."""
    expected = load_expected(root)
    for result in results:
        expected["datasets"][result["dataset"]["id"]] = expected_entry(result)
    expected["datasets"] = dict(sorted(expected["datasets"].items()))
    write_json(root / definition(root)["expected"], expected)
    return expected


# Verification without scoring.


def verify(root: Path = ROOT, data: Mapping[str, Path] | None = None) -> list[dict]:
    """Check every committed artifact and its chain without re-scoring. With prepared data, also check the
    run's case packets. Each check: what was checked, ok, and why not."""
    from .calibration import verify_artifact
    from .strength import verify_strength

    checks: list[dict] = []

    def check(name: str, problems: list[str]) -> None:
        checks.append({"check": name, "ok": not problems, "problems": problems})

    found_definition = definition(root)
    expected = load_expected(root)
    for entry in found_definition["datasets"]:
        found = read_json(root / entry["manifest"])
        check(f"{entry['id']}: manifest", check_manifest(found))
        result_path = root / found["artifacts"]["result"]
        if not result_path.exists():
            check(f"{entry['id']}: result", [f"{found['artifacts']['result']} is missing"])
            continue
        result = read_json(result_path)
        problems = []
        if result.get("digest") != result_digest(result):
            problems.append("the result digest does not match its contents")
        if result["dataset"]["manifest"] != manifest_digest(found):
            problems.append("the result was not produced from this manifest")
        check(f"{entry['id']}: result", problems)
        pinned = expected["datasets"].get(found["id"])
        comparison = compare(result, pinned)
        check(f"{entry['id']}: expected", [] if comparison["status"] in ("MATCH", "ENVIRONMENT_ONLY") else [f"{comparison['status']}: {', '.join(comparison['categories'])}"])
        if found["class"] == "synthetic":
            current = {"code": code_identity(found["code"], root), "profiles": {name: profile_identity(path, root) for name, path in found["profiles"].items()}}
            check(f"{entry['id']}: code", [f"{path} changed since the pin" for path in current["code"] if current["code"][path] != result["chain"]["code"].get(path)]
                  + [f"profile {name} changed since the pin" for name in current["profiles"] if current["profiles"][name] != result["chain"]["profiles"].get(name)])
            continue
        check(f"{entry['id']}: selection", [f"{change['input']}: {change['detail']}" for change in selection_changes(found, root)])
        problems = [f"{change['input']}: {change['detail']}" for change in input_changes(found, result["chain"]["inputs"])]
        check(f"{entry['id']}: inputs bound", problems)
        evaluation = read_json(root / found["artifacts"]["evaluation"])
        estimated = read_json(root / found["artifacts"]["strength"])
        split = read_json(root / found["artifacts"]["split"])
        check(f"{entry['id']}: evaluation", verify_artifact(evaluation) + (
            [] if evaluation["digest"] == result["chain"]["evaluation"]["digest"] else ["the committed evaluation is not the one the result binds"]))
        check(f"{entry['id']}: strength", verify_strength(estimated, evaluation) + (
            [] if estimated["digest"] == result["chain"]["strength"]["digest"] else ["the committed strength file is not the one the result binds"]))
        problems = []
        if split.get("digest") != digest("split", {key: value for key, value in split.items() if key != "digest"}):
            problems.append("the split manifest digest does not match its contents")
        if split.get("digest") != result["chain"]["split"]["digest"]:
            problems.append("the committed split manifest is not the one the result binds")
        rows = {row["player"]: row for row in evaluation["rows"]}
        from .strength import _unit, split_of

        wrong = [player for player, _label, unit, membership in split["subjects"] if player in rows and membership != split_of(_unit(rows[player], evaluation["dataset"]))]
        if wrong:
            problems.append(f"{len(wrong)} scored subjects are not in the half their unit hashes to, first {wrong[0]}")
        if not problems and Counter(row["label"] for row in evaluation["rows"]) != Counter(
                label for _player, label, _unit, membership in split["subjects"] if membership in ("development", "evaluation")):
            problems.append("the scored subjects in the split manifest are not the evaluation's rows")
        check(f"{entry['id']}: split", problems)
        counts = found["expected_counts"]
        labelled = Counter(label for _player, label, _unit, _membership in split["subjects"] if label is not None)
        problems = []
        if result["semantic"]["cases"] != counts["scored_players"]:
            problems.append(f"{result['semantic']['cases']} scored players, the manifest expects {counts['scored_players']}")
        if sum(split["counts"].get("baseline_only", {}).values()) != counts["baseline_players"]:
            problems.append(f"the baseline holds {sum(split['counts'].get('baseline_only', {}).values())} players, the manifest expects {counts['baseline_players']}")
        if dict(labelled) != counts["labelled"]:
            problems.append(f"labels {dict(labelled)}, the manifest expects {counts['labelled']}")
        if result["chain"]["inputs"]["events"].get("events", counts["events"]) != counts["events"]:
            problems.append("the scored event count is not the manifest's")
        check(f"{entry['id']}: counts", problems)
        place = (data or {}).get(found["id"])
        if place:
            cases_path = Path(place) / "cases.jsonl"
            if cases_path.exists():
                from .calibration import load_cases

                cases = load_cases(cases_path)
                verified = _verify_cases(cases)
                ids = case_identities(cases)
                problems = list(verified["problems"])
                if ids["packets"] != result["chain"]["scored"]["packets"]:
                    problems.append("these case packets are not the ones the result binds")
                check(f"{entry['id']}: case packets", problems)
    return checks


# The report.


def context(root: Path = ROOT) -> dict:
    """Everything the report is generated from, read from committed artifacts only."""
    found_definition = definition(root)
    out: dict = {"definition": found_definition, "manifests": {}, "results": {}, "evaluations": {}, "strengths": {}, "splits": {}}
    for entry in found_definition["datasets"]:
        found = read_json(root / entry["manifest"])
        out["manifests"][found["id"]] = found
        artifacts = found["artifacts"]
        if (root / artifacts["result"]).exists():
            out["results"][found["id"]] = read_json(root / artifacts["result"])
        for name, store in (("evaluation", "evaluations"), ("strength", "strengths"), ("split", "splits")):
            if name in artifacts and (root / artifacts[name]).exists():
                out[store][found["id"]] = read_json(root / artifacts[name])
    out["capabilities"] = read_json(root / found_definition["capabilities"])
    return out


def coverage_matrix(ctx: Mapping) -> list[dict]:
    """For every native detector: its controlled proof, and what each real dataset can and did show.
    A detector a dataset cannot observe is "not observable", never a zero."""
    synthetic = ctx["results"].get("synthetic-v1", {}).get("semantic", {})
    rows = []
    for kind in NATIVE_KINDS:
        qualification = synthetic.get("qualification", {}).get(kind, {})
        row = {
            "detector": kind,
            "family": KINDS[kind][0],
            "fixture": qualification.get("planted", []),
            "fixture_fires": qualification.get("planted_fired"),
            "twins": qualification.get("twins", []),
            "twins_quiet": qualification.get("twins_quiet"),
            "states": synthetic.get("eligibility_states", {}).get(kind, []),
        }
        for dataset, evaluation in ctx["evaluations"].items():
            entry = next(item for item in evaluation["statistics"]["detectors"] if item["kind"] == kind)
            strength_entry = next((item for item in ctx["strengths"].get(dataset, {}).get("detectors", []) if item["kind"] == kind), {})
            row[dataset] = {
                "observable": entry["observability"]["observable"],
                "status": entry["status"],
                "why": ", ".join(entry["observability"]["reasons"]),
                "strength": strength_entry.get("evaluation", {}).get("stability") or strength_entry.get("status"),
            }
        telemetry = {field for found in ctx["manifests"].values() if found["class"] == "real"
                     for field in read_json(ROOT / found["label_semantics"]["definition"])["telemetry"]}
        row["real_challenge_telemetry"] = bool({"challenge_track_ms", "private_track_ms"} & telemetry) if kind == "occluded_motion_replay" else None
        rows.append(row)
    return rows


def capability_matrix(ctx: Mapping, root: Path = ROOT) -> tuple[list[dict], list[str]]:
    """Each technique with its claim, the evidence that stands behind it (worked out, not declared), and
    every reference checked. Returns the rows and the problems found."""
    import ast

    synthetic = ctx["results"].get("synthetic-v1", {}).get("semantic", {})
    qualification = synthetic.get("qualification", {})
    challenge = {row["scenario"]: row for row in synthetic.get("challenge", [])}
    capabilities = ctx["capabilities"]
    problems: list[str] = []
    if capabilities.get("format") != CAPABILITIES:
        problems.append(f"benchmark/capabilities.json is not {CAPABILITIES}")
    rows = []
    for technique in capabilities["techniques"]:
        name = technique["id"]
        detectors = technique["detectors"]
        unknown = [kind for kind in detectors if kind not in NATIVE_KINDS]
        problems += [f"{name}: {kind} is not a native detector" for kind in unknown]
        caught_ok, through_ok = [], []
        for ref in technique.get("caught", []):
            world, _, item = ref.partition(":")
            if world == "challenge":
                ok = challenge.get(item, {}).get("observed") == "caught"
            else:
                ok = _fired_in(ref, detectors, qualification)
            caught_ok.append(ok)
            if not ok:
                problems.append(f"{name}: {ref} is claimed caught, and is not")
        for ref in technique.get("through", []):
            item = ref.partition(":")[2]
            ok = challenge.get(item, {}).get("observed") == "not_caught"
            through_ok.append(ok)
            if not ok:
                problems.append(f"{name}: {ref} is claimed to get through, and does not")
        for ref in technique.get("quiet", []):
            if _twin_fired(ref, detectors, qualification):
                problems.append(f"{name}: honest twin {ref} tripped a detector of this technique")
        for test in technique.get("tests", []):
            path, _, rest = test.partition("::")
            cls = rest.split("::")[0]
            source = root / path
            if not source.exists() or cls not in {node.name for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))) if isinstance(node, ast.ClassDef)}:
                problems.append(f"{name}: {test} does not exist")
        claim = technique["claim"]
        if claim not in capabilities["claims"]:
            problems.append(f"{name}: {claim!r} is not a claim this matrix knows")
        if claim == "detectable" and not (caught_ok and all(caught_ok) and technique.get("quiet")):
            problems.append(f"{name}: detectable needs a caught fixture and an honest twin")
        if claim == "partially_detectable" and not technique.get("gets_through"):
            problems.append(f"{name}: partially detectable must say what gets through")
        if claim == "not_detectable" and not (technique.get("architecture") or through_ok):
            problems.append(f"{name}: not detectable needs an architecture statement or a scenario that gets through")
        real = {}
        for dataset, evaluation in ctx["evaluations"].items():
            statuses = {entry["kind"]: entry["status"] for entry in evaluation["statistics"]["detectors"] if entry["kind"] in detectors}
            measured = sorted(kind for kind, status in statuses.items() if status in ("measured", "descriptive_only"))
            stable = sorted(entry["kind"] for entry in ctx["strengths"].get(dataset, {}).get("detectors", [])
                            if entry["kind"] in detectors and entry.get("evaluation", {}).get("stability") == "replicated")
            real[dataset] = {"measured": measured, "replicated": stable}
        if claim == "not_detectable":
            evidence = "architecture statement" + (" and controlled scenarios" if through_ok else "")
        elif any(item["replicated"] for item in real.values()):
            evidence = "real-world measured, replicated on one split"
        elif any(item["measured"] for item in real.values()):
            evidence = "real-world measured"
        elif caught_ok and all(caught_ok):
            evidence = "controlled only"
        else:
            evidence = "architecture statement"
        rows.append({
            "id": name, "name": technique["name"], "behaviour": technique["behaviour"], "claim": claim, "evidence": evidence,
            "detectors": detectors, "requires": technique.get("requires", []), "caught": technique.get("caught", []),
            "through": technique.get("through", []), "quiet": technique.get("quiet", []), "real": real,
            "architecture": technique.get("architecture"), "gets_through": technique.get("gets_through"), "tests": technique.get("tests", []),
        })
    for limit in capabilities.get("limits", []):
        if not (root / limit["reference"]).exists():
            problems.append(f"limit {limit['id']}: {limit['reference']} does not exist")
    return rows, problems


def _fired_in(ref: str, detectors: list[str], qualification: Mapping) -> bool:
    """Did this planted player fire any of these detectors, by the qualification table?"""
    return any(ref in qualification.get(kind, {}).get("planted", []) and qualification[kind].get("planted_fired") for kind in detectors)


def _twin_fired(ref: str, detectors: list[str], qualification: Mapping) -> bool:
    return any(ref in qualification.get(kind, {}).get("twins", []) and not qualification[kind].get("twins_quiet") for kind in detectors)


def limitations(ctx: Mapping) -> list[str]:
    """What still gets through, generated: every technique not or only partly detectable says what gets
    through, then the limits of data and trust the benchmark shows."""
    rows, _problems = capability_matrix(ctx)
    out = [row["gets_through"] for row in rows if row["claim"] in ("not_detectable", "partially_detectable") and row["gets_through"]]
    for dataset, evaluation in ctx["evaluations"].items():
        unseen = sum(entry["status"] == "not_observable" for entry in evaluation["statistics"]["detectors"])
        title = evaluation["dataset"]["title"]
        out.append(f"Anything its telemetry cannot show: on {title}, {unseen} of {len(NATIVE_KINDS)} detectors are not observable at all, which is not the same as finding nothing.")
    out.append("A number with too few humans behind it: a detector waits for a thick enough baseline, and says so, before it compares.")
    out += [limit["text"] for limit in ctx["capabilities"].get("limits", [])]
    return out


README_BEGIN = "<!-- benchmark:limits:begin (generated by fpsdet benchmark report; do not edit) -->"
README_END = "<!-- benchmark:limits:end -->"


def readme_block(ctx: Mapping) -> str:
    return "\n".join([README_BEGIN, "", *[f"- {line}" for line in limitations(ctx)], "", README_END])


def _md(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _yes(value) -> str:
    return {True: "yes", False: "**no**", None: "-"}.get(value, str(value))


def render_report(ctx: Mapping) -> str:
    """docs/benchmark.md, from the committed artifacts alone."""
    from .calibration import _pct, _ratio

    found_definition = ctx["definition"]
    manifests, results = ctx["manifests"], ctx["results"]
    out: list[str] = []
    add = out.append
    add("# The fpsdet benchmark")
    add("")
    add(f"Generated by `fpsdet benchmark report` from the committed artifacts ({found_definition['format']}). Do not edit by hand: change the artifacts, then regenerate.")
    add("")
    add("## What this benchmark can prove")
    add("")
    for line in found_definition["can_prove"]:
        add(f"- {line}")
    add("")
    add("## What this benchmark cannot prove")
    add("")
    for line in found_definition["cannot_prove"]:
        add(f"- {line}")
    add("")
    add("There is no overall score. Each section answers its own question, and a detector a dataset cannot observe is shown as not observable, never as zero.")
    add("")
    add("## 1. Benchmark datasets")
    add("")
    add("| Dataset | Class | Question | Result | Status against the pin |")
    add("| --- | --- | --- | --- | --- |")
    expected = load_expected()
    for dataset_id_, found in manifests.items():
        result = results.get(dataset_id_)
        status = compare(result, expected["datasets"].get(dataset_id_))["status"] if result else "NOT_RUN"
        add(f"| `{dataset_id_}` | {found['class']} | {_md(found['question'])} | `{(result or {}).get('digest', '-')[:23]}…` | {status} |")
    add("")
    for dataset_id_, found in manifests.items():
        if found["class"] != "real":
            continue
        add(f"**{found['title']}** (`{dataset_id_}`).")
        add(f"- Source: " + "; ".join(f"[{origin['name']}]({origin['url']}): {_md(origin['content'])}" for origin in found["source"]["origins"]) + f". Fetched {found['source']['fetched']}. {found['source']['mutable']}")
        add(f"- Licence and use: {found['license']['terms']} {found['license']['committed']} {found['license']['attribution']}")
        add(f"- Pseudonyms: {found['pseudonymization']['method']}")
        add("- Selection, frozen (`" + found["selection"]["recipe"] + "`): " + " ".join(found["selection"]["rules"]))
        counts = found["expected_counts"]
        add("- Counts: " + ", ".join(f"{key.replace('_', ' ')} {value:,}" for key, value in counts.items() if isinstance(value, int))
            + "; labelled " + ", ".join(f"{label} {n:,}" for label, n in counts["labelled"].items()) + ".")
        add("")
    add("## 2. Label semantics")
    add("")
    for dataset_id_, evaluation in ctx["evaluations"].items():
        add(f"**{evaluation['dataset']['title']}.**")
        add("")
        add("| Label | Role | Who | What it does not mean |")
        add("| --- | --- | --- | --- |")
        for group in evaluation["dataset"]["labels"]:
            add(f"| {_md(group['name'])} | {group['role']} | {_md(group['meaning'])} | {_md(group['not_meaning'])} |")
        add("")
    synthetic = results.get("synthetic-v1", {})
    semantic = synthetic.get("semantic", {})
    add("## 3. Controlled detector qualification")
    add("")
    add("Controlled synthetic qualification: planted by construction, never a real-world rate. Each detector must trip on its planted player and stay quiet on every honest twin; the states are every eligibility status its fixtures show.")
    add("")
    add("| Detector | Planted (fires) | Honest twins (quiet) | Eligibility states shown |")
    add("| --- | --- | --- | --- |")
    for kind in NATIVE_KINDS:
        entry = semantic.get("qualification", {}).get(kind, {})
        add(f"| {kind} | {', '.join(entry.get('planted', [])) or '-'} ({_yes(entry.get('planted_fired'))}) | {', '.join(entry.get('twins', [])) or '-'} ({_yes(entry.get('twins_quiet'))}) | {', '.join(semantic.get('eligibility_states', {}).get(kind, []))} |")
    add("")
    published = synthetic.get("published", {})
    add(f"{published.get('qualification_passes', 0)} of {published.get('detectors', 0)} native detectors pass. The demo's {semantic.get('honest_demo_fixtures_clean', 0)} honest players have no finding at all. Demo self-checks failing: {len(semantic.get('demo_failures', []))}; fixture self-checks failing: {len(semantic.get('fixture_failures', []))}.")
    add("")
    add("## 4. Real-data results")
    add("")
    add("Measured directly, from the committed evaluations. Every rate is among players fpsdet could run on, with a two-sided 95% Wilson interval; under 20 players a rate is not shown.")
    add("")
    for dataset_id_, evaluation in ctx["evaluations"].items():
        stats = evaluation["statistics"]
        groups = evaluation["dataset"]["labels"]
        names = {group["label"]: group["name"] for group in groups}
        positive = stats["population"]["positive"]
        add(f"**{evaluation['dataset']['title']}.**")
        add("")
        add("| Group | Scored | Review | Watch | Review or watch, of compared | Of all scored |")
        add("| --- | ---: | ---: | ---: | --- | --- |")
        for group in groups:
            cell = stats["decisions"]["by_group"][group["label"]]
            add(f"| {_md(group['name'])} | {cell['scored']:,} | {cell['counts']['review']} | {cell['counts']['watch']} | {_pct(cell['of_evaluated']['review_or_watch'])} | {_pct(cell['of_scored']['review_or_watch'])} |")
        add("")
        queue = stats["decisions"]["queues"]["review_or_watch"]
        add(f"Queue: {queue['size']} players, {queue['by_label'].get(positive, 0)} of them {names[positive]} ({_pct(queue['positive_share'])}); the chance level among compared players is {queue['prevalence']:.1%}.")
        for comparison, cell in stats["decisions"]["enrichment"].items():
            add(f"Ratio against {names[comparison]}: {_ratio(cell['review_or_watch'])}.")
        if evaluation["dataset"].get("split_unit") != "match":
            equal = next(cell for cell in stats["evidence_amount"] if cell["matches"] == "15-20")["groups"]
            add("At equal evidence (15–20 matches): " + "; ".join(f"{names[label]} {_pct(cell['review_or_watch'])}" for label, cell in equal.items() if cell["scored"]) + ".")
        else:
            add("Every player is one match, so there is no equal-evidence comparison to make here.")
        add("")
    add("## 5. Eligibility and coverage")
    add("")
    add("For every native detector: whether a controlled fixture proves it, and what each real dataset can show. Not observable is not the same as not working.")
    add("")
    real_ids = list(ctx["evaluations"])
    add("| Detector | Family | Fixture | Honest twin | " + " | ".join(f"{dataset} observable | {dataset} status" for dataset in real_ids) + " | Real challenge telemetry | Strength |")
    add("| --- | --- | --- | --- |" + " --- | --- |" * len(real_ids) + " --- | --- |")
    for row in coverage_matrix(ctx):
        cells = [f"{_yes(row[dataset]['observable'])} | {row[dataset]['status']}" for dataset in real_ids]
        strength_cells = "; ".join(f"{dataset}: {row[dataset]['strength']}" for dataset in real_ids)
        challenge_cell = {True: "yes", False: "**none**", None: "-"}[row["real_challenge_telemetry"]]
        add(f"| {row['detector']} | {row['family']} | {_yes(bool(row['fixture']) and row['fixture_fires'])} | {_yes(bool(row['twins']) and row['twins_quiet'])} | " + " | ".join(cells) + f" | {challenge_cell} | {strength_cells} |")
    add("")
    add("No real dataset here carries external provider records either, so external evidence has no real-world measurement.")
    add("")
    add("## 6. Strength and stability")
    add("")
    add("Offline research, never used by the scorer: the label-conditioned evidence ratio fitted on the development half, checked on the untouched evaluation half. Only detectors with an estimate are listed.")
    add("")
    add("| Dataset | Detector | Ratio (95% credible) | Stability |")
    add("| --- | --- | --- | --- |")
    from .strength import _ratio as strength_ratio

    for dataset_id_, estimated in ctx["strengths"].items():
        for entry in estimated["detectors"]:
            if "evidence_ratio" in entry.get("development", {}):
                add(f"| {dataset_id_} | {entry['kind']} | {strength_ratio(entry['development']['evidence_ratio'])} | {entry['evaluation']['stability']} |")
    add("")
    add("## 7. Challenge qualification")
    add("")
    add("Controlled synthetic challenge qualification, not real-world calibration: event-level stand-ins for cheats that know challenges exist, against four challenges in one match.")
    add("")
    add("| Scenario | Expected | Observed | Findings | Decision |")
    add("| --- | --- | --- | ---: | --- |")
    for row in semantic.get("challenge", []):
        add(f"| {row['scenario'].replace('_', ' ')} | {row['expected'].replace('_', ' ')} | {row['observed'].replace('_', ' ')} | {row['findings']} | {row['decision']} |")
    add("")
    auth = semantic.get("auth", {})
    add("## 8. External-authentication protocol qualification")
    add("")
    add("This proves what fpsdet does with a provider's record under each signature state. It says nothing about any vendor detector's accuracy.")
    add("")
    if auth.get("status") != "run":
        add("Not run here: the optional cryptography package was not installed.")
    else:
        add("| Scenario | Expected | Observed | Read as evidence |")
        add("| --- | --- | --- | --- |")
        for row in auth["scenarios"]:
            add(f"| {row['scenario'].replace('_', ' ')} | {row['expected']} | {row['observed']} | {_yes(row['read'])} |")
    add("")
    add("## 9. Capability matrix")
    add("")
    add("What each technique looks like to the server, what fpsdet claims about it, and the evidence behind the claim, worked out from the fixtures, scenarios and real datasets, not declared. Controlled-only means no public dataset here can show it.")
    add("")
    add("| Technique | Claim | Evidence | Detectors | Needs |")
    add("| --- | --- | --- | --- | --- |")
    rows, _problems = capability_matrix(ctx)
    for row in rows:
        add(f"| {row['name']} | {row['claim'].replace('_', ' ')} | {row['evidence']} | {', '.join(row['detectors'])} | {_md('; '.join(row['requires'])) or '-'} |")
    add("")
    add("## 10. Known blind spots")
    add("")
    for line in limitations(ctx):
        add(f"- {line}")
    add("")
    add("## 11. Reproduce it")
    add("")
    for line in found_definition["reproduce"]:
        add(line)
    add("")
    add("## Third-party systems")
    add("")
    for line in found_definition["third_party"]:
        add(line)
    add("")
    add("## Provenance")
    add("")
    for dataset_id_, result in results.items():
        chain = result["chain"]
        add(f"- `{dataset_id_}`: result `{result['digest']}`, manifest `{chain['manifest']}`, detector `{chain.get('detector')}`")
        if "evaluation" in chain:
            add(f"  - inputs: events `{chain['inputs']['events']['sha256']}`, labels `{chain['inputs']['labels']['digest']}`, cohort `{chain['inputs']['cohort']['digest']}`; packets `{chain['scored']['packets']}`; evaluation `{chain['evaluation']['digest']}`; strength `{chain['strength']['digest']}`; split `{chain['split']['digest']}`")
        environment_ = result.get("environment", {})
        timing = ", ".join(f"{name} {value:g}s" for name, value in result.get("timings_s", {}).items())
        add(f"  - pinned on Python {environment_.get('python')} ({environment_.get('platform')}, {environment_.get('machine')}); timings, not part of the identity: {timing}")
    add("")
    return "\n".join(out)
