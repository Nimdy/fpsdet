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
    SOURCE_CHANGED     the upstream download is not the frozen source the manifest pins (prepare)
    NOT_RUN            the prepared data is not on this machine

with the categories that explain it: code changed, source changed, selection changed, identity changed,
and what moved downstream. A dataset with baseline draws (``tf2-rgl-v2``) runs every predeclared draw, and
its result is the spread across them (``fpsdet.benchmark-sensitivity/2``), never one draw. It is a
non-detection module: the scorer never imports it, and nothing it pins is ever read as configuration.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
import re
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
SENSITIVITY = "fpsdet.benchmark-sensitivity/1"
DRAWS = "fpsdet.benchmark-sensitivity/2"
STATISTICS = "fpsdet.benchmark-statistics/1"
RELEASE = "fpsdet.benchmark-release/1"
CLAIMS = "fpsdet.benchmark-claims/1"
SOURCES = "fpsdet.benchmark-sources/1"
# Alternative pseudonym keys for the baseline-draw check: public on purpose, so anyone with the same downloads
# rebuilds the same draws. Only aggregate numbers from them are ever published.
ALTERNATIVE_KEY = "fpsdet.benchmark-alternative-pseudonym-key/{n}"
CAPABILITIES = "fpsdet.benchmark-capabilities/1"
KEY_COMMITMENT = "fpsdet.pseudonym-key-commitment/1"
ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "benchmark" / "benchmark.json"
# The short names are the current release's datasets; the historical ones keep a -v1 name.
ALIASES = {"synthetic": "synthetic-v1", "tf2": "tf2-rgl-v2", "cs2": "cs2cd-v2", "tf2-v1": "tf2-rgl-v1", "cs2-v1": "cs2cd-v1"}
STATUSES = ("MATCH", "ENVIRONMENT_ONLY", "PROVENANCE_ONLY", "PRESENTATION_ONLY", "DRIFT", "INPUT_CHANGED", "SOURCE_CHANGED", "NOT_RUN")
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


def current(root: Path = ROOT) -> list[str]:
    """The datasets of the current release, without the historical ones."""
    return [entry["id"] for entry in definition(root)["datasets"] if not entry.get("historical")]


def draws_of(found: Mapping) -> list[int]:
    """A dataset's predeclared baseline draws, or none: most datasets have one fixed population."""
    return list(found["draws"]["ids"]) if "draws" in found else []


def draw_folder(found: Mapping, data: Path, draw: int) -> Path:
    return data / found["draws"]["folder"].format(draw=draw)


def expected_key(dataset: str, draw: int | None = None) -> str:
    return dataset if draw is None else f"{dataset}/draw-{draw}"


def draw_artifact(found: Mapping, draw: int, name: str, root: Path = ROOT) -> Path:
    return root / found["artifacts"]["draw"].format(draw=draw) / name


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
                    found[kind].add(name if name != "eligible" else ("eligible (fired)" if (kind, unit) in fired else "eligible (quiet)"))
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


def run_steps(steps: list, data: Path, python: str, fill: Mapping[str, str] | None = None) -> list[str]:
    """Run a manifest's commands in order. Each is an argv with {data}, {python} and {fpsdet} filled in (and
    {draw} and {prepared}, for a draw), or a concatenation of prepared files. Stops at the first that fails."""
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
                for name, value in {"data": str(data), "python": python, **(fill or {})}.items():
                    part = part.replace("{" + name + "}", value)
                argv.append(part)
        completed = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
        log.append(" ".join(step["run"]) + (f"  [draw {fill['draw']}]" if fill and "draw" in fill else "") + f"  (exit {completed.returncode})")
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
    the manifest: the sources first, then every draw's prepared inputs. A difference is reported, never
    accepted silently: SOURCE_CHANGED when the download is not the frozen source, INPUT_CHANGED when the
    sources match and the prepared inputs do not."""
    if found["class"] != "real":
        raise BenchmarkError(f"{found['id']} is built by fpsdet's own code; there is nothing to prepare")
    log = run_steps(found["prepare"]["steps"], data, python)
    changes = source_changes(found, data)
    if draws_of(found):
        identity = found["source"]["identity"]
        now: dict = {"source": read_json(data / identity["file"]) if (data / identity["file"]).exists() else None, "draws": {}}
        for draw in draws_of(found):
            place = draw_folder(found, data, draw)
            place.mkdir(parents=True, exist_ok=True)
            log += run_steps(found["prepare"]["draw_steps"], data, python, {"draw": str(draw), "prepared": str(place)})
            now["draws"][str(draw)] = prepared_identity(found, place)
            pinned = ((found.get("inputs") or {}).get("draws") or {}).get(str(draw))
            changes += [{**change, "draw": draw} for change in input_changes(found, now["draws"][str(draw)], pinned)]
    else:
        now = prepared_identity(found, data)
        now["source"] = source_identity(found, data)
        changes += input_changes(found, now)
    changes += selection_changes(found)
    status = "SOURCE_CHANGED" if any(change.get("upstream") for change in changes) else ("INPUT_CHANGED" if changes else "MATCH")
    pinned = (found.get("inputs") or {}).get("source")
    return {"dataset": found["id"], "inputs": now, "changes": changes, "status": status, "log": log,
            "frozen_source": frozen_source(changes) if pinned else "the manifest pins no source yet"}


def frozen_source(changes: list[dict]) -> str:
    moved = sorted({change["input"] for change in changes if change.get("upstream")})
    if not moved:
        return "this download is the frozen source the manifest pins"
    reached = any(not change.get("upstream") and change["category"] not in ("code changed",) for change in changes)
    return (f"this download is not the frozen source ({', '.join(moved)} differ), and is reported as changed, never as the frozen source. "
            + ("Its prepared inputs differ too: see each change above. " if reached else
               "Every prepared input it gives still matches its pinned digest: the change does not reach this dataset's data. ")
            + "The pinned digests still verify a cached copy of the frozen source, and the committed results verify without any download.")


def source_changes(found: Mapping, data: Path) -> list[dict]:
    """The download against the frozen source the manifest pins, part by part: what was pinned, what was
    found, and what each part feeds. A source file a prepare step writes (fpsdet.tf2-sources/1), or the
    pinned fetcher's receipts (fpsdet.cs2cd-fetch-receipt/1). Older manifests compare a digest of the files
    in input_changes instead."""
    identity = found["source"]["identity"]
    pinned = (found.get("inputs") or {}).get("source")
    changes: list[dict] = []
    if "file" in identity:
        path = data / identity["file"]
        got = read_json(path) if path.exists() else None
        if got is None:
            return [{"category": "source changed", "input": identity["file"], "detail": "the source digests were not written: the download is incomplete", "upstream": True, "pinned": None, "found": None}]
        if pinned is None:
            return []
        for part, meaning in identity["parts"].items():
            want, have = pinned.get(part), got.get(part)
            if want != have:
                changes.append({"category": meaning["category"], "input": part, "detail": meaning["detail"], "upstream": meaning["upstream"],
                                "pinned": (want or {}).get("digest", want), "found": (have or {}).get("digest", have),
                                "counts": {"pinned": {key: value for key, value in (want or {}).items() if key != "digest"}, "found": {key: value for key, value in (have or {}).items() if key != "digest"}}})
    if "receipts" in identity:
        sources = read_json(ROOT / identity["sources"])
        if pinned is not None and sources_digest(sources) != identity.get("sources_digest"):
            changes.append({"category": "code changed", "input": identity["sources"], "detail": "the committed source list is not the one the manifest pins", "pinned": identity.get("sources_digest"), "found": sources_digest(sources)})
        for split in sources["selection"]:
            path = data / identity["receipts"].format(split=split)
            receipt = read_json(path) if path.exists() else None
            if receipt is None:
                changes.append({"category": "source changed", "input": split, "detail": "no receipt from the pinned fetcher: fetch with fpsdet benchmark fetch, which never falls back to the latest revision", "upstream": True, "pinned": sources["revision"], "found": None})
                continue
            listed = {row["file"] for row in receipt["files"] if row["found"] == row["expected"]}
            wanted = {name for name in sources["files"][split] if name.endswith(".json")}
            if receipt["revision"] != sources["revision"] or receipt["status"] != "MATCH" or not wanted <= listed:
                changes.append({"category": "source changed", "input": split, "detail": f"the fetch receipt says {receipt['status']} at revision {receipt['revision']}", "upstream": True,
                                "pinned": sources["revision"], "found": receipt["revision"], "files": receipt.get("changed", [])[:10]})
    return changes


def sources_digest(sources: Mapping) -> str:
    return digest("sources", sources)


def input_changes(found: Mapping, now: Mapping, pinned: Mapping | None = None) -> list[dict]:
    """How the prepared inputs differ from the manifest, with the category and what it means."""
    pinned = pinned if pinned is not None else found.get("inputs")
    if not pinned:
        return [{"category": "input changed", "input": "manifest", "detail": "the manifest pins no inputs yet", "pinned": None, "found": None}]
    changes = []
    meaning = {
        "events": ("input changed", "the scored events differ"),
        "baseline": ("input changed", "the baseline events differ"),
        "labels": ("label changed", "the labels differ"),
        "cohort": ("cohort changed", "the baseline cohort differs"),
        "key": ("identity changed", "the pseudonym key is not the curator's: every player id, and any split by pseudonym, differs"),
    }
    want, got = pinned.get("source"), now.get("source")
    if want and got and want != got and "digest" in want:
        changes.append({"category": "source changed", "input": "source", "detail": "the downloaded source data differs from what was pinned: the upstream data changed, or a different selection was fetched", "upstream": True, "pinned": want["digest"], "found": got["digest"]})
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
            changes.append({"category": "code changed", "input": path, "detail": "the converter that selects and builds the data changed", "pinned": want, "found": got})
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


def run_real(found: Mapping, data: Path, out: Path, draw: int | None = None) -> tuple[dict, dict]:
    """Score one real dataset's prepared inputs offline (one draw's, for a dataset with draws), and evaluate,
    estimate and bind every step. Returns the result and the artifacts it wrote to ``out``."""
    from .calibration import EvaluationError, PublishedMismatch, census, dumps, evaluate, load_dataset, render_markdown
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
    found_census = census(data / files["events"])
    mismatch = None
    try:
        evaluation = evaluate(cases, labels, dataset, profile, telemetry_census=found_census)
    except PublishedMismatch as error:
        # Another population (another pseudonym key, another download) need not give the published decisions.
        # Say exactly how they differ, and evaluate what this run has.
        mismatch = str(error)
        evaluation = evaluate(cases, labels, {key: value for key, value in dataset.items() if key != "published"}, profile, telemetry_census=found_census)
    except EvaluationError as error:
        raise BenchmarkError(f"{found['id']}: {error}") from error
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
        "where": (evaluation.get("published") or {}).get("where") or (dataset.get("published") or {}).get("where"),
        **({"mismatch": mismatch} if mismatch else {}),
        "decisions": {label: cell["counts"] for label, cell in evaluation["statistics"]["decisions"]["by_group"].items()},
    }
    result = assemble(found, chain, semantic, published, timings, draw=draw)
    write_json(out / "result.json", result)
    return result, {"evaluation": evaluation, "strength": estimated, "split": split, "reports": reports}


# How much a real result depends on its random baseline draw.


def draw_summary(evaluation: Mapping, estimated: Mapping) -> dict:
    """The numbers a draw changes: who was flagged in each main group, the queue, the ratio, equal evidence,
    and each strength estimate's ratio and stability."""
    stats = evaluation["statistics"]
    positive, comparison = stats["population"]["positive"], stats["population"]["primary_comparison"]
    groups = stats["decisions"]["by_group"]
    queue = stats["decisions"]["queues"]["review_or_watch"]
    equal = next((cell for cell in stats["evidence_amount"] if cell["matches"] == "15-20"), None)
    flagged = lambda label: {"flagged": groups[label]["counts"]["review"] + groups[label]["counts"]["watch"], "compared": groups[label]["evaluated"],
                             "rate": groups[label]["of_evaluated"]["review_or_watch"]["rate"]}
    return {
        "positive": flagged(positive),
        "comparison": flagged(comparison),
        "queue": {"size": queue["size"], "positive": queue["by_label"].get(positive, 0)},
        "ratio": stats["decisions"]["enrichment"][comparison]["review_or_watch"]["ratio"],
        "equal_evidence_comparison_rate": None if equal is None else equal["groups"][comparison]["review_or_watch"]["rate"],
        "strength": {entry["kind"]: {"ratio": entry["development"]["evidence_ratio"]["ratio"], "stability": entry["evaluation"]["stability"]}
                     for entry in estimated["detectors"] if "evidence_ratio" in entry.get("development", {})},
    }


def sensitivity(found: Mapping, data: Path, draws: int, python: str, curator: Mapping | None = None) -> dict:
    """Rebuild a keyed dataset under ``draws`` public alternative pseudonym keys, from the same downloads in
    ``data``, and summarise each. Every other choice is the manifest's; only the random draw of which
    never-banned half builds the baseline (and every id) changes. Network-free, and about two minutes a draw."""
    import shutil

    key_file = found["pseudonymization"].get("key_file")
    if not key_file:
        raise BenchmarkError(f"{found['id']} has no pseudonym key, so its baseline draw is fixed")
    outputs = set(found["prepare"]["outputs"].values()) | {key_file, "benchmark", "draws"}
    out: dict = {
        "format": SENSITIVITY,
        "dataset": found["id"],
        "manifest": manifest_digest(found),
        "question": "How much does the result depend on which random half of the never-banned players builds the baseline?",
        "key_recipe": "SHA-256 of " + ALTERNATIVE_KEY + ", as hex, for draw n",
        "draws": [],
    }
    if curator:
        out["draws"].append({"draw": "curator", "summary": curator})
    for n in range(1, draws + 1):
        place = data / "draws" / str(n)
        if place.exists():
            shutil.rmtree(place)
        place.mkdir(parents=True)
        for item in data.iterdir():
            if item.name not in outputs:
                (place / item.name).symlink_to(item.resolve(), target_is_directory=item.is_dir())
        (place / key_file).write_text(hashlib.sha256(ALTERNATIVE_KEY.format(n=n).encode("ascii")).hexdigest(), encoding="utf-8")
        run_steps(found["prepare"]["steps"], place, python)
        result, artifacts = run_real(found, place, place / "benchmark")
        out["draws"].append({"draw": n, "result": result["digest"], "summary": draw_summary(artifacts["evaluation"], artifacts["strength"])})
        for name in set(found["prepare"]["outputs"].values()) | {"benchmark/cases.jsonl"}:
            target = place / name
            if target.exists() and target.stat().st_size > 10_000_000:
                target.unlink()  # the large rebuilt files; the result binds their digests
    out = json.loads(canonical_json(out))
    out["digest"] = digest("sensitivity", out)
    return out


# Every predeclared baseline draw. A dataset with draws has no one result: its result is their spread.


def _group(statistics: Mapping, label: str) -> dict:
    population = next(group for group in statistics["population"]["groups"] if group["label"] == label)
    cell = statistics["decisions"]["by_group"][label]
    flagged = cell["of_evaluated"]["review_or_watch"]
    return {"labelled": population["labelled"], "scored": population["scored"], "eligible": cell["evaluated"],
            "review": cell["counts"]["review"], "watch": cell["counts"]["watch"], "flagged": flagged["count"],
            "rate": flagged["rate"], "ci95": flagged["ci95"]}


def draw_numbers(statistics: Mapping, estimated: Mapping) -> dict:
    """What one draw says, from its aggregate statistics and strength alone, so anyone can recompute it from
    the committed files. Each main group: labelled, scored, eligible (fpsdet could run on them), flagged
    (review or watch), review, watch. The queue, the enrichment, equal evidence. Per observable detector, its
    fires among eligible players in each group; per strength estimate, its ratio and stability."""
    positive, comparison = statistics["population"]["positive"], statistics["population"]["primary_comparison"]
    queues = statistics["decisions"]["queues"]
    enrichment = statistics["decisions"]["enrichment"][comparison]["review_or_watch"]
    equal = next((cell for cell in statistics["evidence_amount"] if cell["matches"] == "15-20"), None)
    detectors = {}
    for entry in statistics["detectors"]:
        if entry["status"] == "not_observable":
            continue
        rates = entry.get("rates") or {}
        side = lambda label: {"fired": (rates.get(label) or {}).get("count"), "eligible": (rates.get(label) or {}).get("denominator")}
        detectors[entry["kind"]] = {"status": entry["status"], "positive": side(positive), "comparison": side(comparison),
                                    "ratio": ((entry.get("enrichment") or {}).get(comparison) or {}).get("ratio")}
    estimates = {}
    for entry in estimated["detectors"]:
        if "evidence_ratio" in entry.get("development", {}):
            estimates[entry["kind"]] = _rounded({
                "ratio": entry["development"]["evidence_ratio"]["ratio"],
                "credible95": entry["development"]["evidence_ratio"]["credible95"],
                "held_out_ratio": entry["evaluation"]["evidence_ratio"]["ratio"],
                "stability": entry["evaluation"]["stability"],
            })
    return {
        "positive": _group(statistics, positive),
        "comparison": _group(statistics, comparison),
        "review": queues["review"]["size"],
        "watch": queues["watch"]["size"],
        "queue": {"size": queues["review_or_watch"]["size"], "positive": queues["review_or_watch"]["by_label"].get(positive, 0)},
        "ratio": enrichment["ratio"],
        "ratio_range": enrichment["range"],
        "equal_evidence_comparison_rate": None if equal is None else equal["groups"][comparison]["review_or_watch"]["rate"],
        "detectors": detectors,
        "strength": estimates,
    }


def spread(values: list) -> dict:
    """One number over the draws: the median, the lowest and the highest, and every value in draw order.
    Nothing is averaged, so a ratio is never a mean of ratios."""
    known = sorted(value for value in values if value is not None)
    if not known:
        return {"median": None, "min": None, "max": None, "values": values, "missing": len(values)}
    middle = len(known) // 2
    median = known[middle] if len(known) % 2 else round((known[middle - 1] + known[middle]) / 2, 6)
    return {"median": median, "min": known[0], "max": known[-1], "values": values, **({"missing": len(values) - len(known)} if len(known) < len(values) else {})}


def _path(node, path: str):
    for key in path.split("."):
        node = None if node is None else node.get(key)
    return node


def across_draws(records: list[Mapping]) -> dict:
    """The spread of every number across the draws, and per detector how its fires and its strength move."""
    numbers = [record["numbers"] for record in records]
    pick = lambda path: [_path(entry, path) for entry in numbers]
    out: dict = {name: spread(pick(path)) for name, path in (
        ("ratio", "ratio"), ("positive_rate", "positive.rate"), ("comparison_rate", "comparison.rate"),
        ("positive_flagged", "positive.flagged"), ("positive_eligible", "positive.eligible"),
        ("comparison_flagged", "comparison.flagged"), ("comparison_eligible", "comparison.eligible"),
        ("review", "review"), ("watch", "watch"), ("queue", "queue.size"), ("queue_positive", "queue.positive"),
        ("equal_evidence_comparison_rate", "equal_evidence_comparison_rate"))}
    rate = lambda cell, side: None if not cell or not cell[side]["eligible"] else round(cell[side]["fired"] / cell[side]["eligible"], 6)
    out["detectors"] = {}
    for kind in sorted({kind for entry in numbers for kind in entry["detectors"]}, key=NATIVE_KINDS.index):
        cells = [entry["detectors"].get(kind) for entry in numbers]
        out["detectors"][kind] = {
            "status": dict(sorted(Counter(cell["status"] if cell else "not_observable" for cell in cells).items())),
            "positive_rate": spread([rate(cell, "positive") for cell in cells]),
            "comparison_rate": spread([rate(cell, "comparison") for cell in cells]),
            "positive_eligible": spread([cell["positive"]["eligible"] if cell else None for cell in cells]),
            "comparison_eligible": spread([cell["comparison"]["eligible"] if cell else None for cell in cells]),
            "ratio": spread([cell["ratio"] if cell else None for cell in cells]),
            "fired_on_positive_in": sum(bool(cell and cell["positive"]["fired"]) for cell in cells),
        }
    out["strength"] = {}
    for kind in sorted({kind for entry in numbers for kind in entry["strength"]}, key=NATIVE_KINDS.index):
        cells = [entry["strength"].get(kind) for entry in numbers]
        out["strength"][kind] = {
            "ratio": spread([cell["ratio"] if cell else None for cell in cells]),
            "stability": dict(sorted(Counter(cell["stability"] if cell else "no estimate" for cell in cells).items())),
            "replicated_in": sum(bool(cell and cell["stability"] == "replicated") for cell in cells),
        }
    out["draws"] = len(records)
    return out


def statistics_file(found: Mapping, draw: int, evaluation: Mapping) -> dict:
    """One draw's evaluation without its rows: the aggregate statistics, bound to the evaluation by digest."""
    return {
        "format": STATISTICS,
        "dataset": found["id"],
        "draw": draw,
        "evaluation": {"schema": evaluation["schema"], "digest": evaluation["digest"]},
        "withheld": "the per-player rows: " + found["publish"]["why"],
        "statistics": evaluation["statistics"],
    }


def draw_record(result: Mapping, statistics: Mapping, estimated: Mapping) -> dict:
    """One draw in the spread: its own inputs and cohort, every output identity, and its numbers."""
    chain, semantic, inputs = result["chain"], result["semantic"], result["chain"]["inputs"]
    return json.loads(canonical_json({
        "draw": result["dataset"]["draw"],
        "result": result["digest"],
        "inputs": {"events": inputs["events"]["sha256"], "events_count": inputs["events"].get("events"), "baseline": inputs["baseline"]["sha256"],
                   "labels": inputs["labels"]["digest"], "labelled": inputs["labels"]["count"], "cohort": inputs["cohort"]["digest"],
                   "cohort_players": inputs["cohort"]["players"]},
        "outputs": {"decisions": semantic["decisions_digest"], "observations": semantic["observations_digest"], "eligibility": semantic["eligibility_digest"],
                    "packets": chain["scored"]["packets"], "evaluation": chain["evaluation"]["digest"], "statistics": chain["evaluation"]["statistics"],
                    "strength": chain["strength"]["digest"], "estimates": chain["strength"]["estimates"], "split": chain["split"]["digest"]},
        "numbers": draw_numbers(statistics, estimated),
    }))


def draws_artifact(found: Mapping, records: list[Mapping]) -> dict:
    """fpsdet.benchmark-sensitivity/2: every predeclared draw, in order, and the spread across them."""
    spec = found["draws"]
    body = {
        "format": DRAWS,
        "dataset": found["id"],
        "manifest": manifest_digest(found),
        "question": "How much does the result depend on which never-banned players build the baseline?",
        **{key: spec[key] for key in ("recipe", "rule", "ids", "why", "headline", "fixed")},
        "draws": list(records),
        "across": across_draws(records),
    }
    body = json.loads(canonical_json(body))
    body["digest"] = digest("draws", body)
    return body


def run_draws(found: Mapping, data: Path, out: Path) -> tuple[list[dict], list[dict], dict]:
    """Run every predeclared draw from its own prepared inputs and cohort, in order. Returns each draw's result,
    the aggregate files a pin commits for it (statistics and strength, no rows), and the spread."""
    results, files, records = [], [], []
    for draw in draws_of(found):
        place = draw_folder(found, data, draw)
        result, made = run_real(found, place, out / f"draw-{draw}", draw=draw)
        statistics = statistics_file(found, draw, made["evaluation"])
        results.append(result)
        files.append({"statistics": statistics, "strength": made["strength"]})
        records.append(draw_record(result, statistics["statistics"], made["strength"]))
        print(f"{found['id']} draw {draw}: ratio {records[-1]['numbers']['ratio']}, result {result['digest']}", flush=True)
    return results, files, draws_artifact(found, records)


def publish_draws(found: Mapping, results: list[Mapping], files: list[Mapping], artifact: Mapping, root: Path = ROOT) -> None:
    """Write what a pin commits for a dataset with draws: per draw its result, aggregate statistics and
    strength, and the spread. Never a row, a case or a split list."""
    for result, made in zip(results, files):
        draw = result["dataset"]["draw"]
        write_json(draw_artifact(found, draw, "result.json", root), result)
        target = draw_artifact(found, draw, "statistics.json", root)
        target.write_text(json.dumps(made["statistics"], ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        write_json(draw_artifact(found, draw, "strength.json", root), made["strength"])
    write_json(root / found["artifacts"]["sensitivity"], artifact)


# The result, and its comparison with what was pinned.


def assemble(found: Mapping, chain: Mapping, semantic: Mapping, published: Mapping, timings: Mapping, draw: int | None = None) -> dict:
    artifact = {
        "format": RESULT,
        "benchmark": BENCHMARK,
        "dataset": {"id": found["id"], "class": found["class"], "manifest": manifest_digest(found), **({"draw": draw} if draw is not None else {})},
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


# Each field a comparison reads, the category a difference in it falls under, and its kind. Causes: an input
# or the detector code. Effects: a semantic number. Derived: a digest that binds the causes and moves with
# them, reported only when nothing explains it. Presentation: a generated report's text.
FIELDS = (
    ("chain.inputs.events.sha256", "input changed", "input"),
    ("chain.inputs.baseline.sha256", "input changed", "input"),
    ("chain.inputs.labels.digest", "label changed", "input"),
    ("chain.inputs.cohort.digest", "cohort changed", "input"),
    ("chain.inputs.key", "identity changed", "input"),
    ("chain.selection.code", "code changed", "input"),
    ("chain.selection.parameters", "selection changed", "input"),
    ("chain.code", "input changed", "input"),
    ("chain.profile", "profile changed", "input"),
    ("chain.profiles", "profile changed", "input"),
    ("chain.label_definition", "label changed", "input"),
    ("chain.detector", "detector code changed", "detector"),
    ("chain.cohort", "cohort changed", "input"),
    ("chain.scored.packets", "provenance changed", "derived"),
    ("chain.packets", "provenance changed", "derived"),
    ("chain.evaluation.digest", "provenance changed", "derived"),
    ("chain.strength.digest", "provenance changed", "derived"),
    ("chain.reports", "presentation only", "presentation"),
    ("semantic.decisions_digest", "decision changed", "semantic"),
    ("semantic.observations_digest", "evidence changed", "semantic"),
    ("semantic.seals_digest", "evidence changed", "semantic"),
    ("semantic.eligibility_digest", "eligibility changed", "semantic"),
    ("semantic.evaluation_statistics", "evaluation changed", "semantic"),
    ("semantic.strength_estimates", "strength changed", "semantic"),
    ("semantic.split", "split changed", "semantic"),
    ("published.decisions_reproduce", "decision changed", "semantic"),
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
    elif kinds & {"detector", "derived"}:
        status = "PROVENANCE_ONLY"
    elif "presentation" in kinds:
        status = "PRESENTATION_ONLY"
    elif environment_only or (expected.get("environment") or {}).get("python") != (result.get("environment") or {}).get("python"):
        status = "ENVIRONMENT_ONLY"
    else:
        status = "MATCH"
    # Causes and their effects. A derived digest moves with its causes, and a report's text with its numbers, so
    # each is named only when nothing else explains it.
    categories = {change["category"] for change in changes if change["kind"] in ("input", "detector", "semantic")}
    if "derived" in kinds and not categories:
        categories.add("provenance changed")
    if "presentation" in kinds and not categories:
        categories.add("presentation only")
    return {"status": status, "categories": sorted(categories), "changes": changes + environment_only}


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
        expected["datasets"][expected_key(result["dataset"]["id"], result["dataset"].get("draw"))] = expected_entry(result)
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
        if "draws" in found:
            verify_draws(found, expected, root, check)
            continue
        if "receipts" in found.get("source", {}).get("identity", {}):
            identity = found["source"]["identity"]
            check(f"{entry['id']}: source list", [] if sources_digest(read_json(root / identity["sources"])) == identity["sources_digest"]
                  else [f"{identity['sources']} is not the source list the manifest pins"])
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
        sensitivity_path = found_definition.get("sensitivity", {}).get(found["id"])
        if sensitivity_path and (root / sensitivity_path).exists():
            draws = read_json(root / sensitivity_path)
            problems = [] if draws.get("digest") == digest("sensitivity", {key: value for key, value in draws.items() if key != "digest"}) else ["the sensitivity digest does not match its contents"]
            curator = next((row for row in draws["draws"] if row["draw"] == "curator"), None)
            if curator is None or curator["summary"] != draw_summary(evaluation, estimated):
                problems.append("the curator's draw is not the committed evaluation")
            check(f"{entry['id']}: baseline draws", problems)
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
    for path in sorted((root / ADDENDA).glob("*.json")):
        check(f"addendum {path.stem}", addendum_problems(read_json(path), root))
    return checks


def draws_problems(found: Mapping, artifact: Mapping, records: Mapping[int, Mapping]) -> list[str]:
    """What is wrong with a committed spread, given each draw's record as its committed files say: it must
    be exactly the predeclared draws in order, each as its files say, and the spread must recompute."""
    problems = []
    if artifact.get("format") != DRAWS or artifact.get("digest") != digest("draws", {key: value for key, value in artifact.items() if key != "digest"}):
        problems.append("the spread's digest does not match its contents")
    if artifact.get("manifest") != manifest_digest(found) or any(artifact.get(key) != found["draws"][key] for key in ("recipe", "rule", "ids", "headline")):
        problems.append("the spread is not of this manifest's predeclared draws")
    if [row["draw"] for row in artifact["draws"]] != draws_of(found):
        problems.append("the spread does not hold exactly the predeclared draws, in order: none may be dropped or added")
    for row in artifact["draws"]:
        if row["draw"] in records and canonical_json(row) != canonical_json(records[row["draw"]]):
            problems.append(f"draw {row['draw']} in the spread is not what its committed result, statistics and strength say")
    if canonical_json(artifact["across"]) != canonical_json(json.loads(canonical_json(across_draws(artifact["draws"])))):
        problems.append("the spread across draws does not recompute from the draws")
    return problems


def verify_draws(found: Mapping, expected: Mapping, root: Path, check) -> None:
    """A dataset with draws: every predeclared draw's committed result, statistics and strength bind each
    other and the manifest, the spread is exactly those draws (none dropped, none added) recomputed from
    them, and nothing per player is committed."""
    from .strength import strength_digest

    name = found["id"]
    inputs = found.get("inputs") or {}
    if not inputs.get("draws"):
        check(f"{name}: inputs", ["the manifest pins no inputs"])
        return
    check(f"{name}: selection", [f"{change['input']}: {change['detail']}" for change in selection_changes(found, root)])
    source, counts = inputs.get("source") or {}, found.get("expected_counts") or {}
    found_counts = {"bans": (source.get("bans") or {}).get("count"), "labelled_accounts": (source.get("cheaters") or {}).get("accounts"),
                    "extra_never_banned": (source.get("honest") or {}).get("accounts"), "selected_logs": (source.get("logs") or {}).get("count")}
    check(f"{name}: counts", [] if counts == found_counts else [f"the pinned source holds {found_counts}, the manifest expects {counts}"])
    records: dict[int, dict] = {}
    for draw in draws_of(found):
        paths = {key: draw_artifact(found, draw, f"{key}.json", root) for key in ("result", "statistics", "strength")}
        missing = [str(path.relative_to(root)) for path in paths.values() if not path.exists()]
        if missing:
            check(f"{name}: draw {draw}", [f"{path} is missing" for path in missing])
            continue
        result, statistics, estimated = (read_json(paths[key]) for key in ("result", "statistics", "strength"))
        problems = []
        if result.get("digest") != result_digest(result):
            problems.append("the result digest does not match its contents")
        if result["dataset"].get("draw") != draw or result["dataset"]["manifest"] != manifest_digest(found):
            problems.append("the result is not this draw of this manifest")
        comparison = compare(result, expected["datasets"].get(expected_key(name, draw)))
        if comparison["status"] not in ("MATCH", "ENVIRONMENT_ONLY"):
            problems.append(f"{comparison['status']}: {', '.join(comparison['categories'])}")
        problems += [f"{change['input']}: {change['detail']}" for change in input_changes(found, result["chain"]["inputs"], inputs["draws"].get(str(draw)))]
        if (statistics.get("format") != STATISTICS or statistics.get("draw") != draw or "rows" in statistics
                or digest("statistics", statistics["statistics"]) != result["chain"]["evaluation"]["statistics"]
                or statistics["evaluation"]["digest"] != result["chain"]["evaluation"]["digest"]):
            problems.append("the committed statistics are not the ones the result binds")
        if (estimated.get("digest") != strength_digest(estimated) or estimated["digest"] != result["chain"]["strength"]["digest"]
                or digest("estimates", _strength_estimates(estimated)) != result["chain"]["strength"]["estimates"]
                or estimated["evaluation"]["digest"] != statistics["evaluation"]["digest"]):
            problems.append("the committed strength file is not the one the result binds")
        check(f"{name}: draw {draw}", problems)
        records[draw] = draw_record(result, statistics["statistics"], estimated)
    path = root / found["artifacts"]["sensitivity"]
    problems = draws_problems(found, read_json(path), records) if path.exists() else [f"{found['artifacts']['sensitivity']} is missing"]
    check(f"{name}: draws", problems)
    leaks = []
    for item in [root / found["artifacts"]["sensitivity"], *(draw_artifact(found, draw, "", root) for draw in draws_of(found))]:
        for target in ([item] if item.is_file() else sorted(item.glob("*.json")) if item.exists() else []):
            text = target.read_text(encoding="utf-8")
            if re.search(r"tfb-[0-9a-f]{12}|\[U:1:\d+\]|7656119\d{10}", text):
                leaks.append(f"{target.relative_to(root)} names a player")
    check(f"{name}: nothing per player", leaks)


# The report: docs/benchmark.md, the README's generated blocks, the claims and the release manifest, all
# generated from the committed artifacts alone.

# Every statement the report and the README make belongs to exactly one class, and says which.
CLASSES = {
    "controlled_fixture": "controlled fixture: planted by construction in synthetic fixtures and scenarios; it qualifies code, never a rate",
    "benchmark_v1_real_data": "benchmark v1 real data: measured on a current dataset of this release, rebuildable from public sources without any private key",
    "historical_real_data": "historical real data: a run published before this release, still verifiable, superseded, and never the benchmark result",
    "architecture_limit": "architecture limit: what no fpsdet check can see, by design or by a controlled scenario that gets through",
    "unmeasured": "unmeasured: what the benchmark's data cannot show at all, which is not the same as finding nothing",
}
TAGS = {name: text.split(":")[0] for name, text in CLASSES.items()}


def context(root: Path = ROOT) -> dict:
    """Everything the report is generated from, read from committed artifacts only."""
    found_definition = definition(root)
    out: dict = {"definition": found_definition, "manifests": {}, "current": [], "historical": [], "results": {}, "evaluations": {},
                 "strengths": {}, "splits": {}, "draws": {}, "draw_statistics": {}, "draw_strengths": {}, "draw_results": {}, "sensitivity_v1": {}}
    for entry in found_definition["datasets"]:
        found = read_json(root / entry["manifest"])
        out["manifests"][found["id"]] = found
        out["historical" if entry.get("historical") else "current"].append(found["id"])
        artifacts = found["artifacts"]
        if "draws" in found:
            if (root / artifacts["sensitivity"]).exists():
                out["draws"][found["id"]] = read_json(root / artifacts["sensitivity"])
            for name, store in (("statistics", "draw_statistics"), ("strength", "draw_strengths"), ("result", "draw_results")):
                out[store][found["id"]] = {draw: read_json(path) for draw in draws_of(found) if (path := draw_artifact(found, draw, f"{name}.json", root)).exists()}
            out["draw_statistics"][found["id"]] = {draw: file_["statistics"] for draw, file_ in out["draw_statistics"][found["id"]].items()}
            continue
        if (root / artifacts["result"]).exists():
            out["results"][found["id"]] = read_json(root / artifacts["result"])
        for name, store in (("evaluation", "evaluations"), ("strength", "strengths"), ("split", "splits")):
            if name in artifacts and (root / artifacts[name]).exists():
                out[store][found["id"]] = read_json(root / artifacts[name])
    for name, path in found_definition.get("sensitivity", {}).items():
        if (root / path).exists():
            out["sensitivity_v1"][name] = read_json(root / path)
    out["capabilities"] = read_json(root / found_definition["capabilities"])
    return out


def _counted(counter: Counter, total: int) -> str:
    if len(counter) == 1:
        return next(iter(counter))
    return "; ".join(f"{name} in {count} of {total} draws" for name, count in counter.most_common())


def real_views(ctx: Mapping) -> dict[str, dict]:
    """Each current real dataset, per native detector: observable, status, why, and strength stability, over
    every draw for a dataset with draws. A detector a dataset cannot observe is "not observable", never a zero."""
    views: dict[str, dict] = {}
    for dataset in ctx["current"]:
        found = ctx["manifests"][dataset]
        if found["class"] != "real":
            continue
        if "draws" in found:
            statistics = list(ctx["draw_statistics"].get(dataset, {}).values())
            strengths = list(ctx["draw_strengths"].get(dataset, {}).values())
        else:
            statistics = [ctx["evaluations"][dataset]["statistics"]] if dataset in ctx["evaluations"] else []
            strengths = [ctx["strengths"][dataset]] if dataset in ctx["strengths"] else []
        if not statistics:
            continue
        kinds = {}
        for kind in NATIVE_KINDS:
            entries = [next(item for item in stats["detectors"] if item["kind"] == kind) for stats in statistics]
            estimates = [next((item for item in found_strength["detectors"] if item["kind"] == kind), {}) for found_strength in strengths]
            stabilities = Counter(item.get("evaluation", {}).get("stability") or item.get("status") for item in estimates)
            kinds[kind] = {
                "observable": entries[0]["observability"]["observable"],
                "status": _counted(Counter(item["status"] for item in entries), len(entries)),
                "why": ", ".join(entries[0]["observability"]["reasons"]),
                "strength": _counted(stabilities, len(estimates)),
                "measured": any(item["status"] in ("measured", "descriptive_only") for item in entries),
                "replicated_in": stabilities.get("replicated", 0),
            }
        views[dataset] = {"title": found["title"], "short": found["title"].split(" ")[0], "draws": len(statistics) if "draws" in found else None,
                          "kinds": kinds, "not_observable": sum(not cell["observable"] for cell in kinds.values())}
    return views


def coverage_matrix(ctx: Mapping) -> list[dict]:
    """For every native detector: its controlled proof, and what each current real dataset can and did show."""
    synthetic = ctx["results"].get("synthetic-v1", {}).get("semantic", {})
    views = real_views(ctx)
    telemetry = {field for dataset in views for field in read_json(ROOT / ctx["manifests"][dataset]["label_semantics"]["definition"])["telemetry"]}
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
        for dataset, view in views.items():
            cell = view["kinds"][kind]
            row[dataset] = {key: cell[key] for key in ("observable", "status", "why", "strength")}
        row["real_challenge_telemetry"] = bool({"challenge_track_ms", "private_track_ms"} & telemetry) if kind == "occluded_motion_replay" else None
        rows.append(row)
    return rows


def capability_matrix(ctx: Mapping, root: Path = ROOT) -> tuple[list[dict], list[str]]:
    """Each technique with its claim, the evidence that stands behind it (worked out, not declared), and
    every reference checked. Real-data evidence comes from the current datasets, with how many baseline
    draws it held in. Returns the rows and the problems found."""
    import ast

    synthetic = ctx["results"].get("synthetic-v1", {}).get("semantic", {})
    qualification = synthetic.get("qualification", {})
    challenge = {row["scenario"]: row for row in synthetic.get("challenge", [])}
    capabilities = ctx["capabilities"]
    views = real_views(ctx)
    problems: list[str] = []
    if capabilities.get("format") != CAPABILITIES:
        problems.append(f"benchmark/capabilities.json is not {CAPABILITIES}")
    rows = []
    for technique in capabilities["techniques"]:
        name = technique["id"]
        detectors = technique["detectors"]
        problems += [f"{name}: {kind} is not a native detector" for kind in detectors if kind not in NATIVE_KINDS]
        caught_ok, through_ok = [], []
        for ref in technique.get("caught", []):
            world, _, item = ref.partition(":")
            ok = challenge.get(item, {}).get("observed") == "caught" if world == "challenge" else _fired_in(ref, detectors, qualification)
            caught_ok.append(ok)
            if not ok:
                problems.append(f"{name}: {ref} is claimed caught, and is not")
        for ref in technique.get("through", []):
            ok = challenge.get(ref.partition(":")[2], {}).get("observed") == "not_caught"
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
        for dataset, view in views.items():
            cells = {kind: view["kinds"][kind] for kind in detectors if kind in view["kinds"]}
            real[dataset] = {
                "measured": sorted((kind for kind, cell in cells.items() if cell["measured"]), key=NATIVE_KINDS.index),
                "replicated": sorted((kind for kind, cell in cells.items() if cell["replicated_in"]), key=NATIVE_KINDS.index),
                "replicated_in": {kind: cell["replicated_in"] for kind, cell in cells.items() if cell["replicated_in"]},
                "draws": view["draws"],
            }
        controlled = "controlled" if caught_ok and all(caught_ok) else ("architecture statement" if technique.get("architecture") else "no proof")
        if claim == "not_detectable":
            evidence = "architecture statement" + (", and controlled scenarios that get through" if through_ok else "")
        elif any(item["replicated"] for item in real.values()):
            # Labels say who was banned or judged a cheater, not which cheat they used: this is the detectors, not the technique.
            parts = []
            for dataset, item in real.items():
                if not item["replicated"]:
                    continue
                if item["draws"]:
                    ranked = sorted(item["replicated"], key=lambda kind: (-item["replicated_in"][kind], NATIVE_KINDS.index(kind)))
                    held = ", ".join(f"{kind} in {item['replicated_in'][kind]} of {item['draws']}" for kind in ranked)
                    parts.append(f"{views[dataset]['short']} labelled cheaters fire {', '.join(ranked)} more, replicated on one split per baseline draw: {held}")
                else:
                    parts.append(f"{views[dataset]['short']} labelled cheaters fire {', '.join(item['replicated'])} more, replicated on one split")
            evidence = f"{controlled}; real data: " + "; ".join(parts) + " (which cheat they used is unknown)"
        elif any(item["measured"] for item in real.values()):
            evidence = f"{controlled}; real data observes " + "; ".join(
                f"{', '.join(item['measured'])} on {views[dataset]['short']}" for dataset, item in real.items() if item["measured"]
            ) + ", with no separation shown"
        else:
            evidence = f"{controlled} only" if controlled == "controlled" else controlled
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


def limitations(ctx: Mapping) -> list[tuple[str, str]]:
    """What still gets through, generated, each with its class: every technique not or only partly
    detectable says what gets through, then the limits of data and trust the benchmark shows."""
    rows, _problems = capability_matrix(ctx)
    out = [("architecture_limit", row["gets_through"]) for row in rows if row["claim"] in ("not_detectable", "partially_detectable") and row["gets_through"]]
    views = real_views(ctx)
    unseen = [f"{view['not_observable']} of {len(NATIVE_KINDS)} detectors on {view['short']}" for view in views.values()]
    if unseen:
        out.append(("unmeasured", f"Anything a game's telemetry cannot show. In the benchmark's real data, {' and '.join(unseen)} are not observable at all, which is not the same as finding nothing."))
    out.append(("architecture_limit", "A number with too few humans behind it: a detector waits for a thick enough baseline, and says so, before it compares."))
    out += [("architecture_limit", limit["text"]) for limit in ctx["capabilities"].get("limits", [])]
    return out


def _pc(value) -> str:
    return "-" if value is None else f"{value:.1%}"


def _x(value) -> str:
    return "-" if value is None else f"{value:.1f}x"


def _span(found: Mapping, show=_pc) -> str:
    """A spread as the report writes it: the median, then the lowest and highest. Never the median alone."""
    return f"{show(found['median'])} (lowest {show(found['min'])}, highest {show(found['max'])})"


def _v1_published(ctx: Mapping, dataset: str) -> dict | None:
    """The historical published draw of a dataset with draws: its ratio and where it ranked among its own draws."""
    found = ctx["manifests"][dataset]
    old = (found.get("supersedes") or {}).get("id")
    draws = ctx["sensitivity_v1"].get(old)
    if not draws:
        return None
    ratios = [row["summary"]["ratio"] for row in draws["draws"]]
    published = next(row for row in draws["draws"] if row["draw"] == "curator")["summary"]
    return {"id": old, "ratio": published["ratio"], "rank": sorted(ratios, reverse=True).index(published["ratio"]) + 1, "of": len(ratios), "min": min(ratios), "max": max(ratios),
            "comparison_rate": published["comparison"]["rate"], "positive_rate": published["positive"]["rate"], "digest": draws["digest"],
            "path": ctx["definition"]["sensitivity"][old]}


def claims(ctx: Mapping) -> dict:
    """fpsdet.benchmark-claims/1: every public statement of this release, with its class and the artifact
    it is generated from. The README and the report print these, never anything looser."""
    out: list[dict] = []
    add = lambda claim_id, cls, text, path, bound: out.append({"id": claim_id, "class": cls, "text": text, "source": {"path": path, "digest": bound}})
    synthetic = ctx["results"].get("synthetic-v1", {})
    if synthetic:
        published = synthetic["published"]
        add("synthetic.qualification", "controlled_fixture",
            f"All {published['qualification_passes']} of {published['detectors']} native detectors trip on a player planted for them and stay quiet on every honest twin, "
            f"and {published['challenge_as_expected']} of {published['challenge_scenarios']} challenge and {published['auth_as_expected']} of {published['auth_scenarios']} signature scenarios behave as documented. "
            "Controlled synthetic fixtures: they qualify code, never a rate.",
            ctx["manifests"]["synthetic-v1"]["artifacts"]["result"], synthetic["digest"])
    for dataset in ctx["current"]:
        found = ctx["manifests"][dataset]
        if dataset in ctx["draws"]:
            draws = ctx["draws"][dataset]
            across = draws["across"]
            path = found["artifacts"]["sensitivity"]
            names = {group["label"]: group["name"] for group in next(iter(ctx["draw_statistics"][dataset].values()))["population"]["groups"]}
            add(f"{dataset}.headline", "benchmark_v1_real_data",
                f"Across all {across['draws']} predeclared baseline draws of {dataset}, fpsdet flagged (review or watch) a median {_pc(across['positive_rate']['median'])} of the "
                f"{names['cheater']} accounts it could compare (lowest {_pc(across['positive_rate']['min'])}, highest {_pc(across['positive_rate']['max'])}) and a median "
                f"{_pc(across['comparison_rate']['median'])} of the {names['not banned']} (lowest {_pc(across['comparison_rate']['min'])}, highest {_pc(across['comparison_rate']['max'])}). "
                f"The ratio between them was {_x(across['ratio']['median'])} at the median, from {_x(across['ratio']['min'])} to {_x(across['ratio']['max'])}. "
                "Every draw is listed in docs/benchmark.md, and none is preferred.",
                path, draws["digest"])
            for kind, cell in sorted(across["strength"].items(), key=lambda item: (-item[1]["replicated_in"], NATIVE_KINDS.index(item[0]))):
                rates = across["detectors"].get(kind, {})
                add(f"{dataset}.{kind}", "benchmark_v1_real_data",
                    f"{kind} on {dataset}: it fired on a median {_pc(rates['positive_rate']['median'])} of the {names['cheater']} accounts it could run on "
                    f"(lowest {_pc(rates['positive_rate']['min'])}, highest {_pc(rates['positive_rate']['max'])}) and {_pc(rates['comparison_rate']['median'])} of the {names['not banned']} "
                    f"({_pc(rates['comparison_rate']['min'])} to {_pc(rates['comparison_rate']['max'])}); its strength estimate replicated on the held-out split in "
                    f"{cell['replicated_in']} of {across['draws']} draws.",
                    path, draws["digest"])
            add(f"{dataset}.population", "unmeasured",
                f"Which {found['selection']['parameters']['extra_never_banned']} extra never-banned players join the comparison is fixed by a public order, not drawn: "
                f"the {across['draws']} draws vary only which half of the never-banned players builds the baseline, so they do not show how much another set of extra players would move the result.",
                path, draws["digest"])
            unseen = sum(1 for kind in NATIVE_KINDS if kind not in across["detectors"])
            add(f"{dataset}.unobservable", "unmeasured",
                f"{unseen} of {len(NATIVE_KINDS)} detectors cannot be observed on {dataset}'s per-match totals at all, and are reported that way, never as 0%.",
                path, draws["digest"])
            old = _v1_published(ctx, dataset)
            if old:
                place = "the most favourable" if old["rank"] == 1 else f"ranked {old['rank']}"
                beyond = " and higher than every draw of this release" if old["ratio"] > across["ratio"]["max"] else ""
                add(f"{old['id']}.published_draw", "historical_real_data",
                    f"The historical published TF2 result ({old['id']}) was one baseline draw, chosen by the curator's private key: its ratio, {_x(old['ratio'])}, was {place} of "
                    f"{old['of']} draws of that data{beyond}. It is not the benchmark result.",
                    old["path"], old["digest"])
                add(f"{old['id']}.draws", "historical_real_data",
                    f"{old['id']}'s own {old['of']} draws ran from {_x(old['min'])} to {_x(old['max'])}. Its extra never-banned players were chosen by the curator's private key, not by this release's public order, "
                    "so its population differs from this release's, not only its baseline draw.",
                    old["path"], old["digest"])
        elif dataset in ctx["evaluations"] and found["class"] == "real":
            evaluation = ctx["evaluations"][dataset]
            stats = evaluation["statistics"]
            queue = stats["decisions"]["queues"]["review_or_watch"]
            positive = stats["population"]["positive"]
            names = {group["label"]: group["name"] for group in evaluation["dataset"]["labels"]}
            add(f"{dataset}.headline", "benchmark_v1_real_data",
                f"On {dataset}, fpsdet picked {queue['size']} of {queue['scored_pool']:,} scored players for review or watch, and {queue['by_label'].get(positive, 0)} of them are {names[positive]}s. "
                "Each player is one match here, so there is no equal-evidence comparison to make.",
                found["artifacts"]["evaluation"], evaluation["digest"])
    for cls, text in limitations(ctx):
        add(f"limit.{len(out)}", cls, text, ctx["definition"]["capabilities"], sha256_file(ROOT / ctx["definition"]["capabilities"]))
    body = {"format": CLAIMS, "release": RELEASE_TITLE, "classes": CLASSES, "claims": out}
    body = json.loads(canonical_json(body))
    body["digest"] = digest("claims", body)
    return body


RELEASE_TITLE = "FPSDET Benchmark v1"
# Every format the benchmark writes or reads, for anyone checking it without this code.
FORMATS = {
    BENCHMARK: "the definition: datasets, which are historical, archival classes, what the benchmark can and cannot prove",
    DATASET: "one dataset's manifest: source, licence, ids, labels, fetch and prepare commands, frozen selection, pinned inputs, and draws where it has them",
    SOURCES: "a pinned upstream source list: repository, revision, selection and each file's digest (cs2cd-v2)",
    "fpsdet.tf2-sources/1": "tf2-rgl-v2's frozen sources as digests and counts: bans, both selections, every selected log; names nobody",
    "fpsdet.cs2cd-fetch-receipt/1": "what the pinned CS2 fetcher checked, file by file, at which revision",
    RESULT: "one run (one draw, for a dataset with draws), every step bound by digest",
    STATISTICS: "one draw's evaluation statistics without its rows, bound to the evaluation by digest",
    DRAWS: "every predeclared draw of a dataset, in order, with its own inputs, outputs and numbers, and the median and range across them",
    SENSITIVITY: "historical: tf2-rgl-v1's published draw beside five public alternative-key draws",
    EXPECTED: "the pinned identity of every result, per draw for a dataset with draws",
    SPLIT: "every subject's development, evaluation or baseline membership (never committed for a dataset with public ids)",
    CAPABILITIES: "each technique's claim and the references its evidence is worked out from",
    CLAIMS: "every public statement of the release, with its class and the artifact it comes from",
    RELEASE: "the release itself: every dataset version, the expectations, spreads, capabilities, claims and report, by digest",
}
README_BEGIN = "<!-- benchmark:limits:begin (generated by fpsdet benchmark report; do not edit) -->"
README_END = "<!-- benchmark:limits:end -->"


def _markers(name: str) -> tuple[str, str]:
    return f"<!-- benchmark:{name}:begin (generated by fpsdet benchmark report; do not edit) -->", f"<!-- benchmark:{name}:end -->"


def _tagged(claim: Mapping) -> str:
    return f"- **[{TAGS[claim['class']]}]** {claim['text']}"


def readme_block(ctx: Mapping, found_claims: Mapping | None = None) -> str:
    found_claims = found_claims or claims(ctx)
    lines = [_tagged(claim) for claim in found_claims["claims"] if claim["id"].startswith("limit.")]
    return "\n".join([README_BEGIN, "", *lines, "", README_END])


def readme_blocks(ctx: Mapping) -> dict[str, str]:
    """Every generated README block: the TF2 result, the detectors' real-data numbers and the limits."""
    found_claims = claims(ctx)
    by_id = {claim["id"]: claim for claim in found_claims["claims"]}
    blocks = {"limits": readme_block(ctx, found_claims)}
    tf2 = [claim for claim in found_claims["claims"] if claim["id"].startswith("tf2") and claim["id"].endswith((".headline", ".population"))]
    tf2 += [claim for claim in found_claims["claims"] if claim["class"] == "historical_real_data"]
    detectors = [claim for claim in found_claims["claims"] if claim["class"] == "benchmark_v1_real_data" and not claim["id"].endswith(".headline")]
    detectors += [claim for claim in found_claims["claims"] if claim["id"].endswith(".unobservable")]
    for name, chosen in (("tf2", tf2), ("detectors", detectors)):
        begin, end = _markers(name)
        blocks[name] = "\n".join([begin, "", *[_tagged(claim) for claim in chosen], "", end])
    return blocks


def apply_readme(readme: str, blocks: Mapping[str, str]) -> str:
    for name, block in blocks.items():
        begin, end = _markers(name) if name != "limits" else (README_BEGIN, README_END)
        if begin not in readme or end not in readme:
            raise BenchmarkError(f"the README has no generated {name} block ({begin})")
        start, stop = readme.index(begin), readme.index(end) + len(end)
        readme = readme[:start] + block + readme[stop:]
    return readme


def _md(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _yes(value) -> str:
    return {True: "yes", False: "**no**", None: "-"}.get(value, str(value))


def _status(ctx: Mapping, dataset: str, expected: Mapping) -> tuple[str, str]:
    """A dataset's result identity and its status against the pin, as the dataset table shows them."""
    if dataset in ctx["draw_results"]:
        results = ctx["draw_results"][dataset]
        statuses = Counter(compare(result, expected["datasets"].get(expected_key(dataset, draw)))["status"] for draw, result in results.items())
        spread_digest = ctx["draws"].get(dataset, {}).get("digest", "-")
        return f"`{spread_digest[:23]}…` (spread of {len(results)} draws)", _counted(statuses, len(results)) if statuses else "NOT_RUN"
    result = ctx["results"].get(dataset)
    if not result:
        return "-", "NOT_RUN"
    return f"`{result['digest'][:23]}…`", compare(result, expected["datasets"].get(dataset))["status"]


def _dataset_paragraph(add, found: Mapping) -> None:
    add(f"**{found['title']}** (`{found['id']}`).")
    add("- Source: " + "; ".join(f"[{origin['name']}]({origin['url']}): {_md(origin['content'])}" for origin in found["source"]["origins"])
        + f". Fetched {found['source']['fetched']}" + (f", frozen at {found['source']['freeze']}" if found["source"].get("freeze") else "")
        + (f", revision `{found['source']['revision']}`" if found["source"].get("revision") else "") + f". {found['source']['mutable']}")
    add(f"- Licence and use: {found['license']['terms']} {found['license']['committed']} {found['license']['attribution']}")
    if found["license"].get("archival"):
        add(f"- Archive: {found['license']['archival']}")
    add(f"- Ids: {found['pseudonymization']['method']}" + (f" {found['pseudonymization']['not_for_production']}" if found["pseudonymization"].get("not_for_production") else ""))
    add("- Selection, frozen (`" + found["selection"]["recipe"] + "`): " + " ".join(found["selection"]["rules"]))
    counts = found.get("expected_counts") or {}
    if counts:
        add("- Counts: " + ", ".join(f"{key.replace('_', ' ')} {value:,}" for key, value in counts.items() if isinstance(value, int))
            + ("; labelled " + ", ".join(f"{label} {n:,}" for label, n in counts["labelled"].items()) if "labelled" in counts else "") + ".")
    add("")


def _tf2_section(add, ctx: Mapping, dataset: str, found_claims: Mapping) -> None:
    """A dataset with draws, in the order that keeps one draw from being quoted alone: what the labels are,
    every draw, the median and range, the historical published draw, each detector across draws, then what
    the data cannot show."""
    found = ctx["manifests"][dataset]
    draws = ctx["draws"][dataset]
    across = draws["across"]
    first = next(iter(ctx["draw_statistics"][dataset].values()))
    names = {group["label"]: group["name"] for group in first["population"]["groups"]}
    positive, comparison = first["population"]["positive"], first["population"]["primary_comparison"]
    by_id = {claim["id"]: claim for claim in found_claims["claims"]}
    definition_ = read_json(ROOT / found["label_semantics"]["definition"])
    add(f"### {found['title']} (`{dataset}`)")
    add("")
    add(f"**[{TAGS['benchmark_v1_real_data']}]** {len(draws['draws'])} predeclared baseline draws, fixed {draws['fixed']}.")
    add("")
    not_meaning = next(group["not_meaning"] for group in definition_["labels"] if group["role"] == "positive")
    add(f"**What the labels are.** {found['label_semantics']['statement']} The positive label ({names[positive]}) does not mean {not_meaning[0].lower() + not_meaning[1:]}")
    add("")
    add("**How much the result depends on the baseline draw.** " + draws["question"] + " " + draws["rule"] + " " + draws["why"])
    add("")
    add(f"| Draw | {_md(names[positive])} flagged | {_md(names[comparison])} flagged | Review | Watch | Queue ({_md(names[positive])}) | Ratio (95% range) | Equal evidence, {_md(names[comparison])} | Baseline cohort |")
    add("| ---: | --- | --- | ---: | ---: | --- | --- | ---: | ---: |")
    for row in draws["draws"]:
        numbers = row["numbers"]
        lo, hi = numbers["ratio_range"]
        add(f"| {row['draw']} | {numbers['positive']['flagged']}/{numbers['positive']['eligible']} = {_pc(numbers['positive']['rate'])} | "
            f"{numbers['comparison']['flagged']}/{numbers['comparison']['eligible']:,} = {_pc(numbers['comparison']['rate'])} | {numbers['review']} | {numbers['watch']} | "
            f"{numbers['queue']['size']} ({numbers['queue']['positive']}) | {_x(numbers['ratio'])} ({lo:.1f}–{hi:.1f}) | {_pc(numbers['equal_evidence_comparison_rate'])} | "
            f"{row['inputs']['cohort_players']:,} players, `{row['inputs']['cohort'][7:19]}` |")
    add("")
    add(f"**The result: the median and the range over all {across['draws']} draws.** " + by_id[f"{dataset}.headline"]["text"])
    add("")
    add("| Across the draws | Median | Lowest | Highest |")
    add("| --- | ---: | ---: | ---: |")
    for label, key, show in ((f"{names[positive]} flagged", "positive_rate", _pc), (f"{names[comparison]} flagged", "comparison_rate", _pc), ("Ratio", "ratio", _x),
                             ("Review decisions", "review", str), ("Watch decisions", "watch", str), ("Queue", "queue", str),
                             (f"Equal evidence, {names[comparison]}", "equal_evidence_comparison_rate", _pc), (f"{names[comparison]} compared", "comparison_eligible", lambda value: f"{value:,}")):
        cell = across[key]
        add(f"| {_md(label)} | {show(cell['median'])} | {show(cell['min'])} | {show(cell['max'])} |")
    add("")
    add(f"**What the draws do not vary.** **[{TAGS['unmeasured']}]** " + by_id[f"{dataset}.population"]["text"])
    add("")
    old = _v1_published(ctx, dataset)
    if old:
        add(f"**The historical published draw: one draw, not the result.** **[{TAGS['historical_real_data']}]** " + by_id[f"{old['id']}.published_draw"]["text"]
            + " " + by_id[f"{old['id']}.draws"]["text"])
        add("")
        add("| Run | What it is | Ratio |")
        add("| --- | --- | ---: |")
        place = "the most favourable" if old["rank"] == 1 else f"number {old['rank']}"
        add(f"| `{old['id']}`, published | historical: 1 draw, chosen by a private key, {place} of {old['of']} | {_x(old['ratio'])} (one draw) |")
        add(f"| `{dataset}` | this release: median of {across['draws']} public draws | {_x(across['ratio']['median'])} (range {_x(across['ratio']['min'])}–{_x(across['ratio']['max'])}) |")
        add("")
    add("**Each detector across the draws.** Fires among the players each detector could run on, per draw; a detector's strength estimate (fitted on the development half, checked on the held-out half) can replicate in some draws and not others. Stable decisions do not make a detector robust: this table is per detector.")
    add("")
    add(f"| Detector | Status | {_md(names[positive])} fired (median, range) | {_md(names[comparison])} fired (median, range) | Eligible {_md(names[comparison])} | Strength ratio (median, range) | Replicated in |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for kind, cell in sorted(across["detectors"].items(), key=lambda item: NATIVE_KINDS.index(item[0])):
        estimate = across["strength"].get(kind)
        add(f"| {kind} | {_counted(Counter(cell['status']), across['draws']) if len(cell['status']) > 1 else next(iter(cell['status']))} | {_span(cell['positive_rate'])} | {_span(cell['comparison_rate'])} | "
            f"{cell['comparison_eligible']['min']:,}–{cell['comparison_eligible']['max']:,} | "
            + (f"{_span(estimate['ratio'], lambda v: '-' if v is None else f'{v:.3g}')} | {estimate['replicated_in']} of {across['draws']} draws" if estimate else "no estimate | -") + " |")
    add("")
    unseen = [kind for kind in NATIVE_KINDS if kind not in across["detectors"]]
    add(f"**What this data cannot show.** **[{TAGS['unmeasured']}]** {len(unseen)} of {len(NATIVE_KINDS)} detectors are not observable on it at all: {', '.join(unseen)}. "
        + " ".join(definition_["caveats"][2:4]))
    add("")


def render_report(ctx: Mapping) -> str:
    """docs/benchmark.md, from the committed artifacts alone."""
    from .calibration import _pct, _ratio
    from .strength import _ratio as strength_ratio

    found_definition = ctx["definition"]
    manifests = ctx["manifests"]
    found_claims = claims(ctx)
    expected = load_expected()
    out: list[str] = []
    add = out.append
    add(f"# {RELEASE_TITLE}")
    add("")
    add(f"Generated by `fpsdet benchmark report` from the committed artifacts ({found_definition['format']}; release manifest `{found_definition['release']}`, {RELEASE}). Do not edit by hand: change the artifacts, then regenerate.")
    add("")
    add(found_definition["version_note"])
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
    add("Every statement below carries its class, and no sentence mixes two:")
    add("")
    for name, text in CLASSES.items():
        add(f"- **[{TAGS[name]}]** {text.split(': ', 1)[1]}.")
    add("")
    add("## 1. Benchmark datasets")
    add("")
    add("| Dataset | Role | Class | Question | Result | Status against the pin |")
    add("| --- | --- | --- | --- | --- | --- |")
    for dataset in [*ctx["current"], *ctx["historical"]]:
        found = manifests[dataset]
        identity, status = _status(ctx, dataset, expected)
        role = "this release" if dataset in ctx["current"] else f"historical, superseded by `{next(entry['superseded_by'] for entry in found_definition['datasets'] if entry['id'] == dataset)}`"
        add(f"| `{dataset}` | {role} | {found['class']} | {_md(found['question'])} | {identity} | {status} |")
    add("")
    for dataset in ctx["current"]:
        if manifests[dataset]["class"] == "real":
            _dataset_paragraph(add, manifests[dataset])
    add("## 2. Label semantics")
    add("")
    for dataset in ctx["current"]:
        found = manifests[dataset]
        if found["class"] != "real":
            continue
        labels = read_json(ROOT / found["label_semantics"]["definition"])
        add(f"**{labels['title']}** (`{dataset}`).")
        add("")
        add("| Label | Role | Who | What it does not mean |")
        add("| --- | --- | --- | --- |")
        for group in labels["labels"]:
            add(f"| {_md(group['name'])} | {group['role']} | {_md(group['meaning'])} | {_md(group['not_meaning'])} |")
        add("")
    synthetic = ctx["results"].get("synthetic-v1", {})
    semantic = synthetic.get("semantic", {})
    add("## 3. Controlled detector qualification")
    add("")
    add(f"**[{TAGS['controlled_fixture']}]** Controlled synthetic qualification: planted by construction, never a real-world rate. Each detector must trip on its planted player and stay quiet on every honest twin; the states are every eligibility status its fixtures show.")
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
    add("Measured directly, from the committed artifacts. Every rate is among players fpsdet could run on, with a two-sided 95% Wilson interval; under 20 players a rate is not shown.")
    add("")
    for dataset in ctx["current"]:
        if dataset in ctx["draws"]:
            _tf2_section(add, ctx, dataset, found_claims)
    for dataset in ctx["current"]:
        if dataset not in ctx["evaluations"] or manifests[dataset]["class"] != "real":
            continue
        evaluation = ctx["evaluations"][dataset]
        stats = evaluation["statistics"]
        groups = evaluation["dataset"]["labels"]
        names = {group["label"]: group["name"] for group in groups}
        positive = stats["population"]["positive"]
        add(f"### {evaluation['dataset']['title']} (`{dataset}`)")
        add("")
        add(f"**[{TAGS['benchmark_v1_real_data']}]** One population: the selection has no random draw, and the ids are the dataset's own, so a rebuild gets these exact rows.")
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
        add("Every player is one match, so there is no equal-evidence comparison to make here." if evaluation["dataset"].get("split_unit") == "match" else "")
        add("")
    add("## 5. Eligibility and coverage")
    add("")
    add("For every native detector: whether a controlled fixture proves it, and what each dataset of this release can show (over every draw, for a dataset with draws). Not observable is not the same as not working.")
    add("")
    views = real_views(ctx)
    add("| Detector | Family | Fixture | Honest twin | " + " | ".join(f"{dataset} observable | {dataset} status" for dataset in views) + " | Real challenge telemetry | Strength |")
    add("| --- | --- | --- | --- |" + " --- | --- |" * len(views) + " --- | --- |")
    for row in coverage_matrix(ctx):
        cells = [f"{_yes(row[dataset]['observable'])} | {row[dataset]['status']}" for dataset in views]
        strength_cells = "; ".join(f"{dataset}: {row[dataset]['strength']}" for dataset in views)
        challenge_cell = {True: "yes", False: "**none**", None: "-"}[row["real_challenge_telemetry"]]
        add(f"| {row['detector']} | {row['family']} | {_yes(bool(row['fixture']) and row['fixture_fires'])} | {_yes(bool(row['twins']) and row['twins_quiet'])} | " + " | ".join(cells) + f" | {challenge_cell} | {strength_cells} |")
    add("")
    add(f"**[{TAGS['unmeasured']}]** No real dataset here carries external provider records either, so external evidence has no real-world measurement.")
    add("")
    add("## 6. Strength and stability")
    add("")
    add("Offline research, never used by the scorer: the label-conditioned evidence ratio fitted on the development half, checked on the untouched evaluation half. For a dataset with draws, see each detector across the draws in section 4; a single draw's estimate is never quoted alone.")
    add("")
    add("| Dataset | Detector | Ratio (95% credible) | Stability |")
    add("| --- | --- | --- | --- |")
    for dataset in ctx["current"]:
        for entry in ctx["strengths"].get(dataset, {}).get("detectors", []):
            if "evidence_ratio" in entry.get("development", {}):
                add(f"| {dataset} | {entry['kind']} | {strength_ratio(entry['development']['evidence_ratio'])} | {entry['evaluation']['stability']} |")
        if dataset in ctx["draws"]:
            across = ctx["draws"][dataset]["across"]
            for kind, cell in sorted(across["strength"].items(), key=lambda item: NATIVE_KINDS.index(item[0])):
                stability = ", ".join(f"{name} in {count}" for name, count in cell["stability"].items())
                add(f"| {dataset} | {kind} | median {cell['ratio']['median']:.3g}, from {cell['ratio']['min']:.3g} to {cell['ratio']['max']:.3g} over {across['draws']} draws | {stability} of {across['draws']} |")
    add("")
    add("## 7. Challenge qualification")
    add("")
    add(f"**[{TAGS['controlled_fixture']}]** Controlled synthetic challenge qualification, not real-world calibration: event-level stand-ins for cheats that know challenges exist, against four challenges in one match.")
    add("")
    add("| Scenario | Expected | Observed | Findings | Decision |")
    add("| --- | --- | --- | ---: | --- |")
    for row in semantic.get("challenge", []):
        add(f"| {row['scenario'].replace('_', ' ')} | {row['expected'].replace('_', ' ')} | {row['observed'].replace('_', ' ')} | {row['findings']} | {row['decision']} |")
    add("")
    auth = semantic.get("auth", {})
    add("## 8. External-authentication protocol qualification")
    add("")
    add(f"**[{TAGS['controlled_fixture']}]** This proves what fpsdet does with a provider's record under each signature state. It says nothing about any vendor detector's accuracy.")
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
    add("What each technique looks like to the server, what fpsdet claims about it, and the evidence behind the claim, worked out from the fixtures, scenarios and this release's datasets, not declared. Real-data evidence says in how many baseline draws it held. Controlled-only means no public dataset here can show it.")
    add("")
    add("| Technique | Claim | Evidence | Detectors | Needs |")
    add("| --- | --- | --- | --- | --- |")
    rows, _problems = capability_matrix(ctx)
    for row in rows:
        add(f"| {row['name']} | {row['claim'].replace('_', ' ')} | {row['evidence']} | {', '.join(row['detectors'])} | {_md('; '.join(row['requires'])) or '-'} |")
    add("")
    add("## 10. Known blind spots")
    add("")
    for claim in found_claims["claims"]:
        if claim["id"].startswith("limit."):
            add(_tagged(claim))
    add("")
    add("## 11. Reproduce it")
    add("")
    for line in found_definition["reproduce"]:
        add(line)
    add("")
    add("**Formats.** Everything above is plain JSON with a declared format:")
    add("")
    for name, text in FORMATS.items():
        add(f"- `{name}`: {text}.")
    add("")
    add("## 12. The historical record")
    add("")
    add(f"**[{TAGS['historical_real_data']}]** Superseded runs, kept so that what was published can still be checked. Their artifacts verify by digest; none of their numbers is this release's result.")
    add("")
    for dataset in ctx["historical"]:
        found = manifests[dataset]
        successor = next(entry["superseded_by"] for entry in found_definition["datasets"] if entry["id"] == dataset)
        why = manifests[successor].get("supersedes", {}).get("why", "")
        add(f"- `{dataset}`, superseded by `{successor}`: {why}")
    add("")
    for dataset, draws in ctx["sensitivity_v1"].items():
        evaluation = ctx["evaluations"][dataset]
        names = {group["label"]: group["name"] for group in evaluation["dataset"]["labels"]}
        positive, comparison = evaluation["statistics"]["population"]["positive"], evaluation["statistics"]["population"]["primary_comparison"]
        add(f"**How much `{dataset}` depended on its baseline draw.** {draws['question']} The curator's draw is the published one; each other draw uses a public alternative key ({draws['key_recipe']}). Only the draw changes.")
        add("")
        kinds = sorted({kind for row in draws["draws"] for kind in row["summary"]["strength"]}, key=NATIVE_KINDS.index)
        add(f"| Draw | {_md(names[positive])} flagged | {_md(names[comparison])} flagged | Queue ({_md(names[positive])}) | Ratio | Equal evidence, {_md(names[comparison])} | " + " | ".join(f"{kind} strength" for kind in kinds) + " |")
        add("| --- | --- | --- | --- | ---: | ---: |" + " --- |" * len(kinds))
        for row in draws["draws"]:
            summary = row["summary"]
            strength_cells = [f"{summary['strength'][kind]['ratio']:.3g} ({summary['strength'][kind]['stability']})" if kind in summary["strength"] else "-" for kind in kinds]
            equal = summary["equal_evidence_comparison_rate"]
            label = "curator (published, one draw)" if row["draw"] == "curator" else row["draw"]
            add(f"| {label} | {summary['positive']['flagged']}/{summary['positive']['compared']} = {summary['positive']['rate']:.1%} | {summary['comparison']['flagged']}/{summary['comparison']['compared']:,} = {summary['comparison']['rate']:.1%} | {summary['queue']['size']} ({summary['queue']['positive']}) | {summary['ratio']:.1f}x | {'-' if equal is None else f'{equal:.1%}'} | " + " | ".join(strength_cells) + " |")
        ratios = [row["summary"]["ratio"] for row in draws["draws"]]
        published_ratio = next(row for row in draws["draws"] if row["draw"] == "curator")["summary"]["ratio"]
        rank = sorted(ratios, reverse=True).index(published_ratio) + 1
        add("")
        add(f"The published draw's ratio, {published_ratio:.1f}x, ranks {rank} of {len(ratios)} draws (from {min(ratios):.1f}x to {max(ratios):.1f}x): one draw of a random split, not a fixed property of fpsdet, and not this release's result.")
        add("")
    add("## Third-party systems")
    add("")
    for line in found_definition["third_party"]:
        add(line)
    add("")
    add("## Provenance")
    add("")
    for dataset in [*ctx["current"], *ctx["historical"]]:
        if dataset in ctx["draw_results"]:
            draws = ctx["draws"].get(dataset, {})
            add(f"- `{dataset}`: spread `{draws.get('digest')}` over {len(ctx['draw_results'][dataset])} draws, manifest `{draws.get('manifest')}`")
            for draw, result in ctx["draw_results"][dataset].items():
                chain = result["chain"]
                add(f"  - draw {draw}: result `{result['digest']}`; inputs: events `{chain['inputs']['events']['sha256']}`, labels `{chain['inputs']['labels']['digest']}`, cohort `{chain['inputs']['cohort']['digest']}`; evaluation `{chain['evaluation']['digest']}`; strength `{chain['strength']['digest']}`")
            continue
        result = ctx["results"].get(dataset)
        if not result:
            continue
        chain = result["chain"]
        add(f"- `{dataset}`: result `{result['digest']}`, manifest `{chain['manifest']}`, detector `{chain.get('detector')}`")
        if "evaluation" in chain:
            add(f"  - inputs: events `{chain['inputs']['events']['sha256']}`, labels `{chain['inputs']['labels']['digest']}`, cohort `{chain['inputs']['cohort']['digest']}`; packets `{chain['scored']['packets']}`; evaluation `{chain['evaluation']['digest']}`; strength `{chain['strength']['digest']}`; split `{chain['split']['digest']}`")
        environment_ = result.get("environment", {})
        timing = ", ".join(f"{name} {value:g}s" for name, value in result.get("timings_s", {}).items())
        add(f"  - pinned on Python {environment_.get('python')} ({environment_.get('platform')}, {environment_.get('machine')}); timings, not part of the identity: {timing}")
    add("")
    return "\n".join(out)


def release(ctx: Mapping, report: str, found_claims: Mapping, root: Path = ROOT) -> dict:
    """fpsdet.benchmark-release/1: FPSDET Benchmark v1 as one record. It names every dataset version, the
    pinned expectations, each spread, the capability matrix, the claims and the report, by digest."""
    found_definition = ctx["definition"]
    datasets = []
    for entry in found_definition["datasets"]:
        found = ctx["manifests"][entry["id"]]
        item = {"id": found["id"], "role": "historical" if entry.get("historical") else "release", "class": found["class"],
                "manifest": {"path": entry["manifest"], "digest": manifest_digest(found)},
                "archival": found_definition["archival"][found["id"]]}
        if entry.get("historical"):
            item["superseded_by"] = entry["superseded_by"]
        if "draws" in found:
            item["draws"] = {"ids": found["draws"]["ids"], "spread": {"path": found["artifacts"]["sensitivity"], "digest": ctx["draws"].get(found["id"], {}).get("digest")},
                             "results": {str(draw): result["digest"] for draw, result in ctx["draw_results"][found["id"]].items()}}
        elif found["id"] in ctx["results"]:
            item["result"] = {"path": found["artifacts"]["result"], "digest": ctx["results"][found["id"]]["digest"]}
        datasets.append(item)
    file_ = lambda path: {"path": path, "sha256": sha256_file(root / path)}
    body = {
        "format": RELEASE,
        "title": RELEASE_TITLE,
        "benchmark": found_definition["format"],
        "version_note": found_definition["version_note"],
        "release": {"synthetic": "synthetic-v1", "tf2": ALIASES["tf2"], "cs2": ALIASES["cs2"]},
        "datasets": datasets,
        "expected": file_(found_definition["expected"]),
        "capabilities": file_(found_definition["capabilities"]),
        "claims": {"path": found_definition["claims"], "digest": found_claims["digest"]},
        "report": {"path": found_definition["report"], "sha256": "sha256:" + hashlib.sha256(report.encode("utf-8")).hexdigest()},
        "historical_sensitivity": {name: {"path": path, "digest": ctx["sensitivity_v1"][name]["digest"]} for name, path in found_definition.get("sensitivity", {}).items() if name in ctx["sensitivity_v1"]},
    }
    body = json.loads(canonical_json(body))
    body["digest"] = digest("release", body)
    return body


# After v1: addenda. Benchmark v1 stays frozen; evidence of a new kind is added beside it, with its own class
# and its own digest, bound to the v1 release it extends. Nothing here changes a v1 file.

ADDENDUM = "fpsdet.benchmark-addendum/1"
ADDENDA = Path("benchmark") / "addenda"
PILOT_RESULT = Path("examples") / "pilot" / "result.json"
PILOT_CLASS = "live_controlled_pilot"
PILOT_CLASS_TEXT = ("live controlled pilot: a real game server and stock client, run on purpose with scripted players and controlled "
                    "stand-ins; it qualifies one integration, never a detection rate")
PILOT_BEGIN = "<!-- pilot:results:begin (generated by fpsdet benchmark report from examples/pilot/result.json; do not edit) -->"
PILOT_END = "<!-- pilot:results:end -->"


def _scenario(result: Mapping, name: str) -> Mapping:
    return next(row for row in result["scenarios"] if row["id"] == name)


def pilot_addendum(root: Path = ROOT) -> dict | None:
    """benchmark/addenda/p12-challenge-pilot.json: the live challenge pilot as evidence after Benchmark v1,
    generated from examples/pilot/result.json. None when there is no pilot result."""
    if not (root / PILOT_RESULT).exists():
        return None
    release = read_json(root / definition(root)["release"])
    result = read_json(root / PILOT_RESULT)
    capabilities = read_json(root / definition(root)["capabilities"])
    follower, exposed = _scenario(result, "illicit_follower"), _scenario(result, "exposed_vision")
    quiet = [row["id"] for row in result["scenarios"] if not row["observed"]["evidence"] and row["observed"]["status"] != "abstained"]
    abstained = {row["id"]: row["observed"]["cause"] for row in result["scenarios"] if row["observed"]["status"] == "abstained"}
    seen_client = follower["client"]["subject"]
    exposed_client = exposed["client"]["subject"]
    source = {"path": PILOT_RESULT.as_posix(), "digest": result["digest"]}
    engine = f"{result['engine']['name']} {result['engine']['version'].split('.official')[0]}"
    claims = [
        {"id": "pilot.follower", "text": (
            f"In a live {engine} dedicated server with stock clients, a server-side stand-in for software reading the probe it was sent produced challenge evidence "
            f"({follower['observed']['decision']}) from a secret-derived occluded_motion_replay/{follower['plan']['challenges'][0]['version']} plan: "
            f"{follower['observed']['tracked_samples']} counted moments, {follower['observed']['total_ms']:.0f} ms, each with the server's own vision and audio verdict.")},
        {"id": "pilot.quiet", "text": (
            f"In the same server, {', '.join(name.replace('_', ' ') for name in quiet)} produced no challenge evidence; "
            + "; ".join(f"{name.replace('_', ' ')} abstained ({cause})" for name, cause in abstained.items()) + ".")},
        {"id": "pilot.client", "text": (
            f"The subject's stock client received the probe ({seen_client['probe_updates']} updates, {seen_client['probe_bytes']} bytes) and drew no pixel of it in "
            f"{seen_client['probe_checks_drawn']} checks while the server reported it hidden; the enemy's client never received it. When the server was made to place it "
            f"in the open, the client drew it (up to {exposed_client['probe_pixels_max']} pixels) and the server reported it seen.")},
        {"id": "pilot.gameplay", "text": (
            f"Gameplay was identical, tick for tick over {result['non_interference']['ticks']} ticks, with and without the probe running: every position, hit, damage and score.")},
        {"id": "pilot.replay", "text": (
            f"Scoring the captured server telemetry again offline gave the same decisions, observation ids, graphs and packets in all {len(result['scenarios'])} scenarios.")},
    ]
    claims = [{**claim, "class": PILOT_CLASS, "source": source} for claim in claims]
    rows = []
    for technique in capabilities["techniques"]:
        if "occluded_motion_replay" not in technique["detectors"]:
            continue
        if technique["id"] == "packet_reader":
            live = "yes: a server-side stand-in for a reader of the probe it was sent was caught, and the honest scenarios were not"
        elif technique["id"] == "challenge_aware_cheat":
            live = "partly: following the probe only while a visible enemy covers it was run live and gets through, as in the controlled scenario; nothing else challenge-aware was run live"
        else:
            live = "not exercised live"
        rows.append({"technique": technique["id"], "name": technique["name"], "controlled_synthetic": "yes", "live_engine_pilot": live,
                     "real_adversarial_population": "no"})
    body = {
        "format": ADDENDUM,
        "id": "p12-challenge-pilot",
        "extends": {"release": RELEASE_TITLE, "path": definition(root)["release"], "digest": release["digest"]},
        "frozen": "Benchmark v1 is unchanged by this addendum: its release manifest, results, claims and report verify as they did. This is evidence added after it, with its own class.",
        "class": {PILOT_CLASS: PILOT_CLASS_TEXT},
        "pilot": {**source, "engine": result["engine"], "scenarios": len(result["scenarios"]),
                  "as_expected": sum(1 for row in result["scenarios"] if row["as_expected"])},
        "capabilities": rows,
        "claims": claims,
        "not": "No real adversarial population: nothing here is a detection rate or a calibration.",
    }
    body = json.loads(canonical_json(body))
    body["digest"] = digest("addendum", body)
    return body


def pilot_block(root: Path = ROOT) -> str:
    """The generated results section of docs/pilot.md."""
    result = read_json(root / PILOT_RESULT)
    out = [PILOT_BEGIN, ""]
    add = out.append
    add(f"**[{PILOT_CLASS.replace('_', ' ')}]** `{PILOT_RESULT.as_posix()}` (`{result['format']}`), digest `{result['digest']}`.")
    add("")
    add("| Scenario | Declared | Observed | Counted moments | Tracked | Left out | Live = offline | Realization | Leaks |")
    add("| --- | --- | --- | ---: | ---: | --- | --- | --- | --- |")
    for row in result["scenarios"]:
        expected, observed = row["expected"], row["observed"]
        declared = expected["status"] + (f" ({expected['cause']})" if expected.get("cause") else "")
        found = observed["status"] + (f" ({observed['cause']})" if observed.get("cause") else "") + (", evidence" if observed["evidence"] else "")
        left = ", ".join(f"{name} {count}" for name, count in sorted(observed["not_counted"].items())) or "-"
        add(f"| {row['id'].replace('_', ' ')} | {declared} | {found} | {observed['tracked_samples']} | {observed['total_ms']:.0f} ms | {left} | "
            f"{row['live_vs_offline']} | {row['realization']['status']} | {len(row['secret_leaks']) or 'none'} |")
    add("")
    add("| Stock client (subject) | Probe updates received | Probe sounds | Pixel checks with the probe drawn | Probe pixels, most | Enemy's client got the probe |")
    add("| --- | ---: | ---: | ---: | ---: | --- |")
    for row in result["scenarios"]:
        client = row["client"].get("subject", {})
        drawn = client.get("probe_checks_drawn")
        add(f"| {row['id'].replace('_', ' ')} | {client.get('probe_updates', 0)} | {client.get('probe_sounds', 0)} | {'-' if drawn is None else drawn} | "
            f"{'-' if drawn is None else client.get('probe_pixels_max', 0)} | {'yes' if row['client'].get('enemy', {}).get('probe_updates') else 'no'} |")
    add("")
    add("Damaged captures of the follower, scored again (none may be stronger than the undamaged capture):")
    add("")
    add("| Damage | Status | Counted | Evidence | Stronger |")
    add("| --- | --- | ---: | --- | --- |")
    for row in result["telemetry_failures"]:
        add(f"| {row['failure'].replace('_', ' ')} | {row['status']}{' (' + row['cause'] + ')' if row.get('cause') else ''} | {row['tracked_samples']} | {'yes' if row['evidence'] else 'no'} | {'**yes**' if row['stronger_than_undamaged'] else 'no'} |")
    add("")
    add("The server's own refusals (a server-only run each):")
    add("")
    add("| Failure | Server | Why | Events naming a challenge |")
    add("| --- | --- | --- | ---: |")
    for row in result["server_failures"]:
        detail = row["detail"] if isinstance(row["detail"], str) else "; ".join(row["detail"])
        add(f"| {row['failure'].replace('_', ' ')} | {row['server']} | {_md(detail)} | {row['events_naming_a_challenge']} |")
    add("")
    ab = result["non_interference"]
    add(f"Non-interference: {ab['ticks']} server ticks of the same scripted match with the probe running ({ab['probe_ticks']} ticks) and without it. "
        f"Gameplay digest {'identical' if ab['identical'] else '**different**'} (`{ab['gameplay_digest_with'][:16]}…`); mean tick {ab['tick_us_mean_with']} µs with, {ab['tick_us_mean_without']} µs without.")
    add("")
    timing = _scenario(result, "illicit_follower")["timing"]
    add(f"Timing (illicit follower): server tick {timing['server_tick_hz']} Hz, mean {timing['tick_us_mean']} µs, most {timing['tick_us_max']} µs; "
        f"challenge update {timing['challenge_us_per_probe_tick']} µs and knowledge queries {timing['knowledge_us_per_probe_tick']} µs "
        f"({timing['rays_per_probe_tick']} rays) per probe tick; telemetry {timing['telemetry_us_per_tick']} µs per tick, {timing['event_bytes_per_s']} bytes/s; "
        f"movement events every {', '.join(str(step) for step in timing['movement_event_spacing_ms'])} ms; every t_ms on the tick grid: {'yes' if timing['t_ms_on_tick_grid'] else 'no'}; "
        f"challenge_track_ms resolution {timing['track_ms_resolution_ms']} ms, emitted {timing['track_ms_emitted']} ms against the server's {timing['track_ms_server']} ms.")
    add("")
    add(PILOT_END)
    return "\n".join(out)


def addendum_problems(found: Mapping, root: Path = ROOT) -> list[str]:
    problems = []
    if found.get("format") != ADDENDUM or found.get("digest") != digest("addendum", {key: value for key, value in found.items() if key != "digest"}):
        problems.append("the addendum's digest does not match its contents")
    release = read_json(root / definition(root)["release"])
    if found["extends"]["digest"] != release["digest"]:
        problems.append("the Benchmark v1 release it extends has changed")
    pilot = root / found["pilot"]["path"]
    result = read_json(pilot) if pilot.exists() else {}
    if result.get("digest") != found["pilot"]["digest"]:
        problems.append("the pilot result it binds is not the committed one")
    if "as_expected" in found["pilot"] and found["pilot"]["as_expected"] != found["pilot"]["scenarios"]:
        problems.append("not every pilot scenario came out as declared")
    if HUMAN_CLASS in found.get("class", {}) and result.get("kind") != "human":
        problems.append("a consented_human_pilot addendum must bind a result of people")
    return problems


HUMAN_RESULT = Path("examples") / "human-pilot" / "result.json"
HUMAN_CLASS = "consented_human_pilot"
HUMAN_CLASS_TEXT = ("consented human pilot: a small group of consenting people playing the repository's own pilot honestly; a behavioural "
                    "baseline with its counts, never a false-positive rate, a calibration or a validation")


def human_addendum_from(result: Mapping, release_digest: str, path: str = HUMAN_RESULT.as_posix()) -> dict:
    """benchmark/addenda/p13-consented-human-pilot.json from a fpsdet.human-pilot/1 result of people. A result
    of machine stand-ins is refused: it qualifies instruments and is never evidence about people."""
    if result.get("format") != "fpsdet.human-pilot/1" or result.get("kind") != "human":
        raise BenchmarkError("only a fpsdet.human-pilot/1 result of people becomes the consented_human_pilot addendum; machine stand-ins never do")
    review = result["review_grade"]
    units = result["units"]
    claims = [{"id": "human.review_grade", "class": HUMAN_CLASS, "source": {"path": path, "digest": result["digest"]}, "text": (
        f"{len(review['participants'])} of {units['participants']} consenting participants, {review['sessions']} of {units['sessions']} sessions and "
        f"{review['challenges']} of {units['challenges']} challenges produced review-grade challenge evidence while playing honestly. "
        "Counts, with dependence between a participant's sessions; not a false-positive rate.")}]
    body = {
        "format": ADDENDUM,
        "id": "p13-consented-human-pilot",
        "extends": {"release": RELEASE_TITLE, "digest": release_digest},
        "frozen": "Benchmark v1 is unchanged by this addendum.",
        "class": {HUMAN_CLASS: HUMAN_CLASS_TEXT},
        "pilot": {"path": path, "digest": result["digest"], "units": units},
        "claims": claims,
    }
    body = json.loads(canonical_json(body))
    body["digest"] = digest("addendum", body)
    return body


def generated(root: Path = ROOT) -> dict[Path, str]:
    """Every file the report command writes, with its content: the report, the claims, the release manifest
    and the README with its generated blocks."""
    ctx = context(root)
    found_definition = ctx["definition"]
    report = render_report(ctx)
    found_claims = claims(ctx)
    readme = apply_readme((root / "README.md").read_text(encoding="utf-8"), readme_blocks(ctx))
    out = {
        root / found_definition["report"]: report,
        root / found_definition["claims"]: json.dumps(found_claims, indent=1, ensure_ascii=False) + "\n",
        root / "README.md": readme,
    }
    out[root / found_definition["release"]] = json.dumps(release(ctx, report, found_claims, root), indent=1, ensure_ascii=False) + "\n"
    if (root / HUMAN_RESULT).exists():
        human = human_addendum_from(read_json(root / HUMAN_RESULT), read_json(root / found_definition["release"])["digest"])
        out[root / ADDENDA / f"{human['id']}.json"] = json.dumps(human, indent=1, ensure_ascii=False) + "\n"
    addendum = pilot_addendum(root)
    if addendum is not None:
        out[root / ADDENDA / f"{addendum['id']}.json"] = json.dumps(addendum, indent=1, ensure_ascii=False) + "\n"
        doc = root / "docs" / "pilot.md"
        if doc.exists() and PILOT_BEGIN in doc.read_text(encoding="utf-8"):
            text = doc.read_text(encoding="utf-8")
            start, stop = text.index(PILOT_BEGIN), text.index(PILOT_END) + len(PILOT_END)
            out[doc] = text[:start] + pilot_block(root) + text[stop:]
    return out
