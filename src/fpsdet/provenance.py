"""Provenance: which detector code and which parsed game profile produced a case's evidence.

Four fingerprints, each a full SHA-256 written ``sha256:<64 hex>``:

- ``profile``: the GameProfile as the scorer sees it after parsing, not the file. The parser turns
  some explicit values into defaults, and this describes what actually ran.
- ``detector``: the source of every module whose code can change a finding or the evidence written
  for it, read from the package that is running. Presentation code is not in it, so a dashboard or
  page change cannot move it.
- ``cohort``: every (band, key, metric, player, value) of the baseline the run compared players
  with, and separately the advisory integrity stamp that travels with it.
- ``inputs``: the subject player's own parsed events, in the order the scorer received them.
- ``history``: the account-history rows the scorer could read for the subject, when history was given.

The first three are computed once per run and shared by every case in it; ``inputs`` and ``history``
are per player. ``packet`` then binds the subject, the decision, the observations, the eligibility
and all of this provenance into one evidence-packet digest. A digest proves content identity, not
origin: anyone who edits a case can recompute it. Signing it with a server key is a later step.
Observation ids do not include provenance, and the case seal does not change: provenance says what
produced an observation, the id says which observation it is.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import math
import operator
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROVENANCE_VERSION = 1
# The canonicalization recipes. Bump one when the bytes it hashes would be built differently.
PROFILE_RECIPE = "fpsdet.profile/1"
DETECTOR_RECIPE = "fpsdet.detector/1"
COHORT_RECIPE = "fpsdet.cohort/1"
INTEGRITY_RECIPE = "fpsdet.cohort-integrity/1"
INPUTS_RECIPE = "fpsdet.player-events/1"
HISTORY_RECIPE = "fpsdet.history/1"
PACKET_RECIPE = "fpsdet.packet/1"

PACKAGE = "fpsdet"
PACKAGE_DIR = Path(__file__).resolve().parent

# Every module whose code can change a finding, the evidence written for it, or how an event,
# profile, cohort or history is read. Tests derive the same set from the imports of DETECTOR_ROOTS
# and fail when the two disagree, so a new detection module cannot fall outside the fingerprint.
DETECTOR_MODULES = (
    "fpsdet",  # __init__.py runs on every import of the package, so it is part of what runs
    "fpsdet.baseline",
    "fpsdet.evidence",
    "fpsdet.models",
    "fpsdet.parse",
    "fpsdet.persist",
    "fpsdet.pipeline",
    "fpsdet.provenance",
    "fpsdet.score",
    "fpsdet.signals",
    "fpsdet.statsutil",
    "fpsdet.summarize",
    "fpsdet.timeline",
)
# Where detection starts: the package's own __init__ (it runs before any module), scoring a batch,
# reading events and profiles, reading cohorts and history and writing the case and its evidence.
DETECTOR_ROOTS = ("fpsdet", "fpsdet.pipeline", "fpsdet.parse", "fpsdet.persist")
# Every other module of the package, and why it is not detection. A test fails when a module is in
# neither list, or when detection code imports one of these.
NOT_DETECTOR = {
    "fpsdet.__main__": "starts the command line",
    "fpsdet.cli": "the command line: reads files, prints, writes outputs",
    "fpsdet.casefile": "writes a finished case as JSON and an HTML page",
    "fpsdet.priority": "queue order for people, after the decision",
    "fpsdet.lake": "raw event storage; which events were scored is input provenance, not code",
    "fpsdet.ai_triage": "AI prose about a finished case",
    "fpsdet.ops": "the dashboard payload",
    "fpsdet.opsview": "dashboard HTML, CSS and JS",
    "fpsdet.board": "the review desk page",
    "fpsdet.pages": "the public site",
    "fpsdet.synthetic": "the planted demo players",
    "fpsdet.week": "the synthetic week",
}
# GameProfile fields the scorer never reads. Editing them changes no detection.
PROFILE_NOT_MATERIAL = frozenset({"notes"})
# Sequences whose order the scorer ignores. A recoil floor's mods are matched as a sorted build key.
UNORDERED_FIELDS = frozenset({("RecoilFloor", "mod_set")})

_MODULE_NAME = re.compile(r"fpsdet(\.[a-z_][a-z0-9_]*)?")


def _json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value):
    """Plain JSON data for a parsed profile value, the same on every Python version."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        kind = type(value).__name__
        out = {}
        for spec in dataclasses.fields(value):
            item = _canonical(getattr(value, spec.name))
            if (kind, spec.name) in UNORDERED_FIELDS:
                item = sorted(item, key=_json)
            out[spec.name] = item
        return out
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"non_finite": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(item) for item in value), key=_json)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("profile mappings must have string keys")
        return {key: _canonical(item) for key, item in value.items()}
    raise TypeError(f"{type(value).__name__} has no canonical profile form")


def canonical_profile(profile) -> dict:
    """Every field of the parsed profile the scorer can read, as plain JSON data."""
    return {
        spec.name: _canonical(getattr(profile, spec.name))
        for spec in dataclasses.fields(profile)
        if spec.name not in PROFILE_NOT_MATERIAL
    }


def profile_digest(profile) -> str:
    return _sha256(_json({"recipe": PROFILE_RECIPE, "profile": canonical_profile(profile)}).encode("utf-8"))


def normalized_source(raw: bytes) -> bytes:
    """Source bytes as stored, minus what a checkout can change: a UTF-8 BOM and CRLF line ends."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n")


def detector_digest(sources: Mapping[str, bytes]) -> str:
    """SHA-256 over each module's name, length and normalized source, in module-name order."""
    hasher = hashlib.sha256(DETECTOR_RECIPE.encode("ascii") + b"\0")
    for name in sorted(sources):
        source = normalized_source(sources[name])
        hasher.update(f"{name}\0{len(source)}\0".encode("ascii"))
        hasher.update(source)
    return "sha256:" + hasher.hexdigest()


def _module_file(package_dir: Path, module: str) -> Path:
    """The file of one package module. Names come from this module's own lists, never from input."""
    if not _MODULE_NAME.fullmatch(module):
        raise ValueError(f"{module!r} is not a module of {PACKAGE}")
    root = package_dir.resolve()
    path = (root / f"{module.partition('.')[2] or '__init__'}.py").resolve()
    if path.parent != root:
        raise ValueError(f"{module!r} resolves outside the package")
    return path


@dataclass(frozen=True)
class DetectorFingerprint:
    digest: str | None  # None when a module's source could not be read
    modules: tuple[str, ...]
    missing: tuple[str, ...] = ()


def _fingerprint_dir(package_dir: Path) -> DetectorFingerprint:
    sources: dict[str, bytes] = {}
    missing: list[str] = []
    for module in DETECTOR_MODULES:
        try:
            sources[module] = _module_file(package_dir, module).read_bytes()
        except OSError:
            missing.append(module)
    digest = None if missing else detector_digest(sources)
    return DetectorFingerprint(digest, DETECTOR_MODULES, tuple(missing))


@lru_cache(maxsize=1)
def detector_fingerprint() -> DetectorFingerprint:
    """The running package's detector fingerprint. Read once per process; the code that runs is what was imported."""
    return _fingerprint_dir(PACKAGE_DIR)


# Values are hashed as JSON with Python's shortest round-trip float spelling, so two different floats
# never share a spelling and nothing is rounded. NaN and the infinities are written as the tokens NaN,
# Infinity and -Infinity, which plain JSON does not have; they are only ever hashed, never emitted.
_VALUES = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=True)


class ProvenanceMismatch(ValueError):
    """A file claims a digest that its own contents do not have."""


def cohort_digest(values: Mapping[tuple[str, str, str], Iterable[tuple[str, float]]]) -> str:
    """Every (band, key, metric, player, value) a cohort holds, sorted, each occurrence once.

    The player id is part of it: scoring leaves the subject out of the distribution by id, so the same
    numbers held by other players are a different baseline.
    """
    rows = sorted(
        (band, key, metric, player_id, _VALUES.encode(value))
        for (band, key, metric), pairs in values.items()
        for player_id, value in pairs
    )
    hasher = hashlib.sha256(COHORT_RECIPE.encode("ascii") + b"\0")
    for band, key, metric, player_id, value in rows:
        hasher.update(_VALUES.encode([band, key, metric, player_id]).encode("utf-8") + b"\0" + value.encode("ascii") + b"\n")
    return "sha256:" + hasher.hexdigest()


def integrity_digest(integrity: Mapping) -> str:
    """The advisory stamp a cohort carries (status, alarms, matches left out). Scoring never reads it."""
    return _sha256(INTEGRITY_RECIPE.encode("ascii") + b"\0" + _json(integrity).encode("utf-8"))


def cohort_seal(table) -> dict:
    """What ``fpsdet baseline`` writes into a cohort file, so a later load can check the file."""
    return {
        "cohort": {"recipe": COHORT_RECIPE, "digest": cohort_digest(table._values)},
        "integrity": {"recipe": INTEGRITY_RECIPE, "digest": integrity_digest(table.integrity)},
    }


def check_cohort_seal(table, stored) -> None:
    """Refuse a cohort file whose stored digests do not match what it holds. No digest is trusted unchecked."""
    if not isinstance(stored, Mapping):
        raise ProvenanceMismatch("the cohort file's provenance is not an object")
    expected = cohort_seal(table)
    for part in ("cohort", "integrity"):
        claim = stored.get(part)
        if not isinstance(claim, Mapping) or claim.get("recipe") != expected[part]["recipe"]:
            raise ProvenanceMismatch(f"the cohort file's {part} provenance is missing or uses a recipe this version cannot check")
        if claim.get("digest") != expected[part]["digest"]:
            what = "values" if part == "cohort" else "integrity stamp"
            raise ProvenanceMismatch(
                f"the cohort file's {what} do not match the digest it carries. It was edited after "
                "fpsdet baseline wrote it; rebuild it rather than trust it"
            )


@dataclass(frozen=True)
class CohortFingerprint:
    mode: str  # "external": a baseline given to the run; "in_file": fitted on the very events being scored
    digest: str
    stored_digest: str  # "matched": the file carried a digest and it checked; "absent": an older file; "not_from_file"
    integrity_status: str
    integrity_digest: str

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "recipe": COHORT_RECIPE,
            "digest": self.digest,
            "stored_digest": self.stored_digest,
            "integrity": {"recipe": INTEGRITY_RECIPE, "status": self.integrity_status, "digest": self.integrity_digest},
        }


def cohort_fingerprint(table, mode: str) -> CohortFingerprint:
    if mode not in ("external", "in_file"):
        raise ValueError(f"unknown cohort mode {mode!r}")
    return CohortFingerprint(
        mode=mode,
        digest=cohort_digest(table._values),
        stored_digest=getattr(table, "stored_digest", "not_from_file"),
        integrity_status=str(table.integrity.get("status", "unchecked")),
        integrity_digest=integrity_digest(table.integrity),
    )


@dataclass(frozen=True)
class PlayerInputs:
    digest: str
    events: int
    matches: int

    def to_dict(self) -> dict:
        return {"recipe": INPUTS_RECIPE, "digest": self.digest, "events": self.events, "matches": self.matches}


_EVENT_FIELDS: tuple[str, ...] = ()
_EVENT_VALUES = None
# Empty containers are how an Event says a field was not sent.
_EMPTY_IS_ABSENT = frozenset({"mod_set", "extras"})


def _event_columns(events: list) -> list[tuple[str, list]]:
    global _EVENT_FIELDS, _EVENT_VALUES
    if _EVENT_VALUES is None:
        from .models import Event

        _EVENT_FIELDS = tuple(sorted(spec.name for spec in dataclasses.fields(Event)))
        _EVENT_VALUES = operator.attrgetter(*_EVENT_FIELDS)
    count = len(events)
    columns = []
    for name, column in zip(_EVENT_FIELDS, zip(*map(_EVENT_VALUES, events))):
        if name in _EMPTY_IS_ABSENT:
            column = tuple(value or None for value in column)
        if column.count(None) < count:
            columns.append((name, column))
    return columns


def player_digest(events: list) -> str:
    """One player's parsed events, in the order the scorer received them.

    Order is kept because it is not neutral: the scorer lists findings, metrics and context lines in the
    order a player's weapons first appear, and events at the same time keep their arrival order. Each
    occurrence counts, so a duplicated event is a different input. The bytes are the recipe, the event
    count, then for each Event field in name order that any event sets: the name and a JSON array of
    that field on every event, null where it is not set.
    """
    hasher = hashlib.sha256(INPUTS_RECIPE.encode("ascii") + b"\0" + str(len(events)).encode("ascii") + b"\0")
    for name, column in _event_columns(events):
        hasher.update(name.encode("ascii") + b"\0" + _VALUES.encode(column).encode("utf-8") + b"\0")
    return "sha256:" + hasher.hexdigest()


def player_inputs(events: Iterable) -> dict[str, PlayerInputs]:
    """Each player's input identity, from one pass over the run's events."""
    grouped: dict[str, list] = {}
    for event in events:
        grouped.setdefault(event.player_id, []).append(event)
    return {
        player_id: PlayerInputs(player_digest(rows), len(rows), len({event.match_id for event in rows}))
        for player_id, rows in grouped.items()
    }


@dataclass(frozen=True)
class HistoryProvenance:
    mode: str  # "none": the run was given no history; "external": it was
    digest: str | None = None
    windows: int = 0

    def to_dict(self) -> dict:
        if self.mode == "none":
            return {"mode": "none"}
        return {"mode": self.mode, "recipe": HISTORY_RECIPE, "digest": self.digest, "windows": self.windows}


def history_digest(rows: Iterable) -> str:
    """The history windows the scorer could read for one player, as a sorted multiset.

    The account check adds up shots and hits over the matching windows, so their order cannot matter
    and a repeated window counts twice. Each window is the JSON array [player_id, weapon_key,
    skill_band, shots, hits]; the windows are sorted by that text, one per line.
    """
    lines = sorted(
        _VALUES.encode([row.player_id, row.weapon_key, row.skill_band, row.shots, row.hits]) for row in rows
    )
    payload = HISTORY_RECIPE.encode("ascii") + b"\0" + str(len(lines)).encode("ascii") + b"\0"
    return _sha256(payload + "".join(line + "\n" for line in lines).encode("utf-8"))


def history_provenance(rows: list | None) -> HistoryProvenance:
    """``rows`` is None when the run was given no history, else the subject's readable rows (maybe none)."""
    if rows is None:
        return HistoryProvenance("none")
    return HistoryProvenance("external", history_digest(rows), len(rows))


@dataclass(frozen=True)
class RunProvenance:
    """What produced every case in one scoring run. One object, shared by every case."""

    profile: str
    detector: DetectorFingerprint
    cohort: CohortFingerprint | None = None

    def to_dict(self) -> dict:
        detector: dict = {"recipe": DETECTOR_RECIPE, "digest": self.detector.digest, "modules": list(self.detector.modules)}
        if self.detector.missing:
            detector["missing"] = list(self.detector.missing)
        return {
            "version": PROVENANCE_VERSION,
            "profile": {"recipe": PROFILE_RECIPE, "digest": self.profile},
            "detector": detector,
            "cohort": None if self.cohort is None else self.cohort.to_dict(),
        }


@dataclass(frozen=True)
class CaseProvenance:
    """The run's provenance, shared, and the subject player's own inputs and history."""

    run: RunProvenance
    inputs: PlayerInputs | None = None
    history: HistoryProvenance | None = None

    def to_dict(self) -> dict:
        return {
            **self.run.to_dict(),
            "inputs": None if self.inputs is None else self.inputs.to_dict(),
            "history": None if self.history is None else self.history.to_dict(),
        }


def run_provenance(profile, cohort=None, cohort_mode: str | None = None) -> RunProvenance:
    fingerprint = None if cohort is None else cohort_fingerprint(cohort, cohort_mode or "external")
    return RunProvenance(profile_digest(profile), detector_fingerprint(), fingerprint)


def stamp(
    cases: Iterable,
    profile,
    *,
    cohort=None,
    cohort_mode: str | None = None,
    events: Iterable | None = None,
    history: Mapping[str, list] | None = None,
) -> RunProvenance:
    """Give every case of one run its provenance. The run's part is computed once; inputs and history per player.

    ``history`` maps each player to the history rows the scorer could read for them (``score.history_for``),
    or is None when the run was given no history.
    """
    run = run_provenance(profile, cohort, cohort_mode)
    inputs = {} if events is None else player_inputs(events)
    for case in cases:
        rows = None if history is None else history.get(case.player_id, [])
        case.provenance = CaseProvenance(run, inputs.get(case.player_id), history_provenance(rows))
    return run


# The evidence packet. One digest over what the evidence is and what produced it, read from the
# serialized case, so building it and checking it are the same code.

# The provenance fields the packet binds, by part. A key added to a part later is not bound by packet/1.
PACKET_PROVENANCE = {
    "detector": ("recipe", "digest", "modules"),
    "profile": ("recipe", "digest"),
    "cohort": ("mode", "recipe", "digest", "stored_digest", "integrity"),
    "inputs": ("recipe", "digest", "events", "matches"),
    "history": ("mode", "recipe", "digest", "windows"),
}
RECIPES = {
    "detector": DETECTOR_RECIPE,
    "profile": PROFILE_RECIPE,
    "cohort": COHORT_RECIPE,
    "inputs": INPUTS_RECIPE,
    "history": HISTORY_RECIPE,
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _missing(provenance) -> list[str]:
    """What a complete packet needs and this case's provenance lacks."""
    if not isinstance(provenance, Mapping):
        return ["provenance"]
    missing = [part for part in PACKET_PROVENANCE if not isinstance(provenance.get(part), Mapping)]
    detector = provenance.get("detector")
    if isinstance(detector, Mapping) and detector.get("digest") is None:
        missing.append("detector source")
    return missing


def packet_material(case: Mapping) -> dict:
    """The canonical content of the evidence packet. Reason text, context, reports and the brief are not in it."""
    evidence = case["evidence"]
    provenance = evidence["provenance"]
    return {
        "recipe": PACKET_RECIPE,
        "evidence_version": evidence["version"],
        "provenance_version": provenance["version"],
        "subject": case["player_id"],
        "game": case["game_id"],
        "decision": case["decision"],
        "eligibility": evidence["eligibility"],
        "observations": sorted(obs["observation_id"] for obs in evidence["observations"]),
        "provenance": {
            part: {key: provenance[part].get(key) for key in keys} for part, keys in PACKET_PROVENANCE.items()
        },
    }


def packet_block(case: Mapping) -> dict:
    """``case["evidence"]["packet"]``: the digest of a complete packet, or what keeps it from being complete."""
    missing = _missing(case["evidence"].get("provenance"))
    if missing:
        return {"recipe": PACKET_RECIPE, "status": "incomplete", "missing": missing}
    digest = _sha256(_json(packet_material(case)).encode("utf-8"))
    return {"recipe": PACKET_RECIPE, "status": "complete", "digest": digest}


def verify_packet(case: Mapping) -> list[str]:
    """Is a serialized case internally consistent? Empty when it is.

    Every observation id is recomputed from the observation's own fields first, so an edited value
    with a copied id is caught. Then the provenance recipes are checked and the packet digest is
    recomputed. This needs no events, cohort or code: it says the packet is the packet it claims to
    be, not that the sources would produce it again, and not who produced it.
    """
    from .evidence import EVIDENCE_VERSION, Observation

    evidence = case.get("evidence") if isinstance(case, Mapping) else None
    if not isinstance(evidence, Mapping):
        return ["the case has no evidence block"]
    problems: list[str] = []
    if evidence.get("version") != EVIDENCE_VERSION:
        problems.append(f"evidence version {evidence.get('version')!r} is not one this fpsdet reads")
    for obs in evidence.get("observations") or []:
        name = obs.get("observation_id") if isinstance(obs, Mapping) else None
        try:
            rebuilt = Observation(
                family=obs["family"],
                kind=obs["kind"],
                role=obs["role"],
                subject_id=obs["subject_id"],
                key=obs["key"],
                match_ids=tuple(obs["match_ids"]),
                evidence=obs["evidence"],
                depends_on=tuple(obs["depends_on"]),
                context=obs.get("context") or {},
                source=obs["source"],
            )
        except (KeyError, TypeError, ValueError) as error:
            problems.append(f"observation {name}: {error}")
            continue
        if rebuilt.observation_id != name:
            problems.append(f"observation {name} does not match its own contents")
        if obs["subject_id"] != case.get("player_id"):
            problems.append(f"observation {name} is about {obs['subject_id']}, not this case's player")
    provenance = evidence.get("provenance")
    if isinstance(provenance, Mapping):
        for part, recipe in RECIPES.items():
            block = provenance.get(part)
            if not isinstance(block, Mapping) or (part == "history" and block.get("mode") == "none"):
                continue
            if block.get("recipe") != recipe:
                problems.append(f"provenance {part} uses recipe {block.get('recipe')!r}, not {recipe}")
            if block.get("digest") is not None and not _DIGEST.fullmatch(str(block.get("digest"))):
                problems.append(f"provenance {part} digest is not a sha256 digest")
    packet = evidence.get("packet")
    if not isinstance(packet, Mapping):
        return problems + ["the evidence has no packet"]
    try:
        expected = packet_block(case)
    except (KeyError, TypeError) as error:
        return problems + [f"the packet cannot be rebuilt: {error!r}"]
    if packet.get("status") != expected["status"]:
        problems.append(f"the packet says {packet.get('status')!r} but its provenance makes it {expected['status']!r}")
    elif expected["status"] == "complete" and packet.get("digest") != expected["digest"]:
        problems.append("the packet digest does not match the evidence and provenance it covers")
    return problems


# The guard. Tests run these over the package's own sources; scoring never does.


def local_imports(source: str, modules: Iterable[str]) -> set[str]:
    """The package modules a source file imports, anywhere in it, including inside functions."""
    known = set(modules)
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level > 1:
                raise ValueError(f"{PACKAGE} is one flat package; a parent-relative import has nowhere to go")
            base = node.module or ""
            if node.level == 0:
                if base != PACKAGE and not base.startswith(PACKAGE + "."):
                    continue
                base = base[len(PACKAGE):].lstrip(".")
            if base:
                found.add(f"{PACKAGE}.{base.split('.')[0]}")
                continue
            for alias in node.names:
                name = f"{PACKAGE}.{alias.name}"
                found.add(name if name in known else PACKAGE)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE:
                    found.add(PACKAGE)
                elif alias.name.startswith(PACKAGE + "."):
                    found.add(f"{PACKAGE}.{alias.name.split('.')[1]}")
    return found


def detector_closure(sources: Mapping[str, str], roots: Iterable[str] = DETECTOR_ROOTS) -> list[str]:
    """Every package module reachable by import from the detection roots."""
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        if name not in sources:
            raise ValueError(f"{name} is imported by detection code but is not a module of the package")
        seen.add(name)
        todo.extend(local_imports(sources[name], sources) - seen)
    return sorted(seen)


def manifest_problems(
    sources: Mapping[str, str],
    manifest: Iterable[str] = DETECTOR_MODULES,
    outside: Mapping[str, str] = NOT_DETECTOR,
    roots: Iterable[str] = DETECTOR_ROOTS,
) -> list[str]:
    """Where DETECTOR_MODULES and NOT_DETECTOR disagree with the imports. Empty when they agree."""
    closure = set(detector_closure(sources, roots))
    listed = set(manifest)
    problems = [f"{name} is imported by detection code but is not in DETECTOR_MODULES" for name in sorted(closure - listed)]
    problems += [f"{name} is in DETECTOR_MODULES but detection code does not import it" for name in sorted(listed - closure)]
    problems += [f"{name} is listed as not detector code, but detection code imports it" for name in sorted(closure & set(outside))]
    problems += [
        f"{name} is not classified: add it to DETECTOR_MODULES or NOT_DETECTOR"
        for name in sorted(set(sources) - closure - set(outside))
    ]
    return problems
