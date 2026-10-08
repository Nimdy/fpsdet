"""Provenance: which detector code and which parsed game profile produced a case's evidence.

Four fingerprints, each a full SHA-256 written ``sha256:<64 hex>``:

- ``profile``: the GameProfile as the scorer sees it after parsing, not the file. The parser turns
  some explicit values into defaults, and this describes what actually ran.
- ``detector``: the source of every module whose code can change a finding or the evidence written
  for it, read from the package that is running. Presentation code is not in it, so a dashboard or
  page change cannot move it.
- ``cohort``: every (band, key, metric, player, value) of the baseline the run compared players
  with, and separately the advisory integrity stamp that travels with it.
- ``inputs``: the subject player's own parsed events, in the canonical timeline order the scorer reads.
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

from .timeline import player_timelines, timeline

PROVENANCE_VERSION = 1
# The canonicalization recipes. Bump one when the bytes it hashes would be built differently.
PROFILE_RECIPE = "fpsdet.profile/1"
DETECTOR_RECIPE = "fpsdet.detector/1"
COHORT_RECIPE = "fpsdet.cohort/1"
INTEGRITY_RECIPE = "fpsdet.cohort-integrity/1"
INPUTS_RECIPE = "fpsdet.player-events/2"
# The same construction over a timeline in which some event reports the server's verdict on a challenge's
# body (challenge_vision_state, challenge_audio_state). Those fields did not exist when /2 was defined, so a
# timeline that sets them is digested under its own recipe, and /2 keeps meaning exactly what it meant.
INPUTS_RECIPE_V3 = "fpsdet.player-events/3"
_V3_FIELDS = ("challenge_vision_state", "challenge_audio_state")
# The same again over a timeline that carries secret-turn telemetry (occluded_motion_replay/3): the view yaw
# trace and the turn markers. /3 keeps meaning exactly what it meant.
INPUTS_RECIPE_V4 = "fpsdet.player-events/4"
_V4_FIELDS = ("view_yaw_deg", "challenge_turn_index", "challenge_turn_sign")
# What fpsdet wrote before event normalization: the events in the order they arrived. Packets that
# carry it still verify; nothing writes it any more.
ARRIVAL_INPUTS_RECIPE = "fpsdet.player-events/1"
HISTORY_RECIPE = "fpsdet.history/1"
EXTERNAL_INPUT_RECIPE = "fpsdet.external-input/1"
PACKET_V1 = "fpsdet.packet/1"
# packet/1 plus the external input and the fusion state.
PACKET_V2 = "fpsdet.packet/2"
# packet/2 plus the evidence graph's recipe and digest.
PACKET_V3 = "fpsdet.packet/3"
# packet/3 plus the provider-key registry and signature policy external records were read under. Every
# new case is written with it.
PACKET_V4 = "fpsdet.packet/4"
# packet/4 plus why each native detector could or could not run (evidence.detector_eligibility). Every
# new case is written with it.
PACKET_V5 = "fpsdet.packet/5"
PACKET_RECIPE = PACKET_V5

PACKAGE = "fpsdet"
PACKAGE_DIR = Path(__file__).resolve().parent

# Every module whose code can change a finding, the evidence written for it, or how an event,
# profile, cohort or history is read. Tests derive the same set from the imports of DETECTOR_ROOTS
# and fail when the two disagree, so a new detection module cannot fall outside the fingerprint.
DETECTOR_MODULES = (
    "fpsdet",  # __init__.py runs on every import of the package, so it is part of what runs
    "fpsdet.auth",
    "fpsdet.baseline",
    "fpsdet.challenge",
    "fpsdet.evidence",
    "fpsdet.external",
    "fpsdet.graph",
    "fpsdet.knowledge",
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
    "fpsdet.fixtures": "controlled fixtures beside the planted demo: plants, honest twins and eligibility probes",
    "fpsdet.strength": "offline research: label-conditioned evidence ratios from a frozen split; detection never imports it, so no estimate can reach a decision",
    "fpsdet.benchmark": "the reproducible benchmark: runs, compares and reports; detection never imports it, and nothing it pins is configuration",
    "fpsdet.challenge_plan": "plans challenges with the server secret; detection never imports it, so scoring never needs the secret",
    "fpsdet.calibration": "measures detectors against labels after scoring; detection never imports it, so no rate can reach a decision",
}
# GameProfile fields the scorer never reads. Editing them changes no detection.
PROFILE_NOT_MATERIAL = frozenset({"notes"})
# Opt-in GameProfile fields added after profile/1 was pinned. Unset, the scorer behaves as before they
# existed, so they are left out of the digest and every older profile keeps its digest. Set, they count.
PROFILE_OPT_IN = frozenset({"speed_min_run_ms", "movement_clock", "shot_clock"})
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
        and not (spec.name in PROFILE_OPT_IN and getattr(profile, spec.name) is None)
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
    recipe: str = INPUTS_RECIPE

    def to_dict(self) -> dict:
        return {"recipe": self.recipe, "digest": self.digest, "events": self.events, "matches": self.matches}


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


def _events_digest(recipe: str, events: list) -> str:
    """The recipe, the event count, then for each Event field in name order that any event sets: the name
    and a JSON array of that field on every event, in the order given, null where it is not set. Each
    occurrence counts, so a duplicated event is a different input."""
    hasher = hashlib.sha256(recipe.encode("ascii") + b"\0" + str(len(events)).encode("ascii") + b"\0")
    for name, column in _event_columns(events):
        hasher.update(name.encode("ascii") + b"\0" + _VALUES.encode(column).encode("utf-8") + b"\0")
    return "sha256:" + hasher.hexdigest()


def arrival_digest(events: list) -> str:
    """``fpsdet.player-events/1``: one player's events in the order they arrived.

    What fpsdet wrote before event normalization, when the scorer still read arrival order. Kept so its
    meaning never changes; the scorer no longer writes it.
    """
    return _events_digest(ARRIVAL_INPUTS_RECIPE, list(events))


def timeline_recipe(events: list) -> str:
    """``fpsdet.player-events/4`` when any event carries secret-turn telemetry, ``/3`` when any reports on a
    challenge's body, ``/2`` otherwise."""
    if any(getattr(event, name) is not None for event in events for name in _V4_FIELDS):
        return INPUTS_RECIPE_V4
    return INPUTS_RECIPE_V3 if any(getattr(event, name) is not None for event in events for name in _V3_FIELDS) else INPUTS_RECIPE


def timeline_digest(events: list) -> str:
    """``fpsdet.player-events/2`` (or ``/3``, by ``timeline_recipe``) over events already in canonical
    timeline order (fpsdet.timeline)."""
    return _events_digest(timeline_recipe(events), events)


def player_digest(events: Iterable) -> str:
    """``fpsdet.player-events/2``: one player's events in the timeline order the scorer reads, whatever
    order they arrived in. The same events in any order have one digest; a changed, added or removed
    event, a duplicate included, has another."""
    return timeline_digest(timeline(events))


def timeline_inputs(timelines: Mapping[str, list]) -> dict[str, PlayerInputs]:
    """Each player's input identity from the timelines a run already built for scoring."""
    return {
        player_id: PlayerInputs(timeline_digest(rows), len(rows), len({event.match_id for event in rows}), timeline_recipe(rows))
        for player_id, rows in timelines.items()
    }


def player_inputs(events: Iterable) -> dict[str, PlayerInputs]:
    """Each player's input identity, from the run's events."""
    return timeline_inputs(player_timelines(events))


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
class ExternalProvenance:
    """The external records a run attached to one player, and the files the run read them from.

    ``none`` means the run was given no external input. ``supplied`` means it was, even when none of it
    was about this player: then ``records`` is 0 and the digest is that of no records. The sources are the
    run's, the same on every case: each file's SHA-256, its adapter, and how many records it added,
    repeated, or could not be read.
    """

    mode: str
    digest: str | None = None
    records: int = 0
    sources: tuple = ()
    registry: Mapping | None = None  # the provider-key registry signatures were checked against
    policy: Mapping | None = None  # {"require_signed": bool}

    def to_dict(self) -> dict:
        if self.mode == "none":
            return {"mode": "none"}
        return {
            "mode": self.mode,
            "recipe": EXTERNAL_INPUT_RECIPE,
            "digest": self.digest,
            "records": self.records,
            "sources": [dict(source) for source in self.sources],
            "registry": None if self.registry is None else dict(self.registry),
            "policy": dict(self.policy or {}),
        }


def external_digest(record_digests: Iterable[str]) -> str:
    """``fpsdet.external-input/1``: SHA-256 over the recipe, the record count, and each record's own whole
    ``fpsdet.external/1`` digest, sorted, each followed by a zero byte. File order does not show; a changed
    record, an added one or a removed one does."""
    rows = sorted(record_digests)
    hasher = hashlib.sha256(EXTERNAL_INPUT_RECIPE.encode("ascii") + b"\0" + str(len(rows)).encode("ascii") + b"\0")
    for row in rows:
        hasher.update(row.encode("ascii") + b"\0")
    return "sha256:" + hasher.hexdigest()


def external_provenance(external, subject_id: str) -> ExternalProvenance:
    if external is None:
        return ExternalProvenance("none")
    records = external.for_subject(subject_id)
    sources = tuple(source.to_dict() for source in external.sources)
    registry = None if external.registry is None else external.registry.summary()
    return ExternalProvenance(
        "supplied", external_digest(record.digest for record in records), len(records), sources, registry, external.policy(),
    )


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
    external: ExternalProvenance | None = None

    def to_dict(self) -> dict:
        return {
            **self.run.to_dict(),
            "inputs": None if self.inputs is None else self.inputs.to_dict(),
            "history": None if self.history is None else self.history.to_dict(),
            "external": None if self.external is None else self.external.to_dict(),
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
    timelines: Mapping[str, list] | None = None,
    history: Mapping[str, list] | None = None,
    external=None,
) -> RunProvenance:
    """Give every case of one run its provenance. The run's part is computed once; inputs and history per player.

    ``history`` maps each player to the history rows the scorer could read for them (``score.history_for``),
    or is None when the run was given no history. ``external`` is the run's external input
    (fpsdet.external.ExternalInput), or None when it was given none.
    """
    run = run_provenance(profile, cohort, cohort_mode)
    if timelines is not None:
        inputs = timeline_inputs(timelines)  # the very timelines the scorer read
    else:
        inputs = {} if events is None else player_inputs(events)
    for case in cases:
        rows = None if history is None else history.get(case.player_id, [])
        case.provenance = CaseProvenance(run, inputs.get(case.player_id), history_provenance(rows), external_provenance(external, case.player_id))
    return run


# The evidence packet. One digest over what the evidence is and what produced it, read from the
# serialized case, so building it and checking it are the same code.

# The provenance fields packet/1 binds, by part. A key added to a part later is not bound by packet/1;
# packet/2 binds the same, and the external input.
PACKET_PROVENANCE = {
    "detector": ("recipe", "digest", "modules"),
    "profile": ("recipe", "digest"),
    "cohort": ("mode", "recipe", "digest", "stored_digest", "integrity"),
    "inputs": ("recipe", "digest", "events", "matches"),
    "history": ("mode", "recipe", "digest", "windows"),
}
PACKET_V2_PROVENANCE = {**PACKET_PROVENANCE, "external": ("mode", "recipe", "digest", "records", "sources")}
PACKET_V4_PROVENANCE = {**PACKET_PROVENANCE, "external": ("mode", "recipe", "digest", "records", "sources", "registry", "policy")}
PACKET_PARTS = {
    PACKET_V1: PACKET_PROVENANCE, PACKET_V2: PACKET_V2_PROVENANCE, PACKET_V3: PACKET_V2_PROVENANCE,
    PACKET_V4: PACKET_V4_PROVENANCE, PACKET_V5: PACKET_V4_PROVENANCE,
}
# Recipes that bind the evidence graph, and so are checked with verify_graph.
WITH_GRAPH = frozenset({PACKET_V3, PACKET_V4, PACKET_V5})
# Recipes that bind detector eligibility, and so are checked with evidence.eligibility_problems.
WITH_ELIGIBILITY = frozenset({PACKET_V5})
# The recipes this fpsdet can read in a packet, by part. Old ones stay: a recipe never changes meaning.
RECIPES = {
    "detector": (DETECTOR_RECIPE,),
    "profile": (PROFILE_RECIPE,),
    "cohort": (COHORT_RECIPE,),
    "inputs": (INPUTS_RECIPE_V4, INPUTS_RECIPE_V3, INPUTS_RECIPE, ARRIVAL_INPUTS_RECIPE),
    "history": (HISTORY_RECIPE,),
    "external": (EXTERNAL_INPUT_RECIPE,),
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _missing(provenance, recipe: str = PACKET_RECIPE, evidence: Mapping | None = None) -> list[str]:
    """What a complete packet needs and this case's provenance, or for packet/3 its graph, lacks."""
    if recipe in WITH_ELIGIBILITY and evidence is not None and not isinstance(evidence.get("detector_eligibility"), Mapping):
        return ["detector eligibility"] + ([] if isinstance(provenance, Mapping) else ["provenance"])
    if recipe in WITH_GRAPH and evidence is not None and not isinstance(evidence.get("graph"), Mapping):
        return ["graph"] + ([] if isinstance(provenance, Mapping) else ["provenance"])
    if not isinstance(provenance, Mapping):
        return ["provenance"]
    missing = [part for part in PACKET_PARTS[recipe] if not isinstance(provenance.get(part), Mapping)]
    detector = provenance.get("detector")
    if isinstance(detector, Mapping) and detector.get("digest") is None:
        missing.append("detector source")
    return missing


def packet_material(case: Mapping, recipe: str = PACKET_RECIPE) -> dict:
    """The canonical content of the evidence packet. Reason text, context, reports and the brief are not in it.

    ``fpsdet.packet/1`` is exactly what it always was. ``fpsdet.packet/2`` adds the external input's
    provenance and ``evidence.fusion`` (null when the run had no external input). External observations
    are bound in both, as every observation is, by id. ``fpsdet.packet/3`` adds the evidence graph's
    recipe and digest; ``verify_packet`` checks the graph itself (fpsdet.graph.verify_graph). ``fpsdet.packet/4``
    adds the provider-key registry and the signature policy external records were read under. ``fpsdet.packet/5``
    adds ``evidence.detector_eligibility``; ``verify_packet`` checks every native finding names a unit its
    detector could run on.
    """
    evidence = case["evidence"]
    provenance = evidence["provenance"]
    material = {
        "recipe": recipe,
        "evidence_version": evidence["version"],
        "provenance_version": provenance["version"],
        "subject": case["player_id"],
        "game": case["game_id"],
        "decision": case["decision"],
        "eligibility": evidence["eligibility"],
        "observations": sorted(obs["observation_id"] for obs in evidence["observations"]),
        "provenance": {
            part: {key: provenance[part].get(key) for key in keys} for part, keys in PACKET_PARTS[recipe].items()
        },
    }
    if recipe in (PACKET_V2, PACKET_V3, PACKET_V4, PACKET_V5):
        material["fusion"] = evidence.get("fusion")
    if recipe in WITH_ELIGIBILITY:
        material["detector_eligibility"] = evidence["detector_eligibility"]
    if recipe in WITH_GRAPH:
        graph = evidence["graph"]
        material["graph"] = {"recipe": graph["recipe"], "digest": graph["digest"]}
    return material


def packet_block(case: Mapping, recipe: str = PACKET_RECIPE) -> dict:
    """``case["evidence"]["packet"]``: the digest of a complete packet, or what keeps it from being complete.
    New cases are written with ``fpsdet.packet/5``; a packet is checked with the recipe it names."""
    missing = _missing(case["evidence"].get("provenance"), recipe, case["evidence"])
    if missing:
        return {"recipe": recipe, "status": "incomplete", "missing": missing}
    digest = _sha256(_json(packet_material(case, recipe)).encode("utf-8"))
    return {"recipe": recipe, "status": "complete", "digest": digest}


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
        for part, recipes in RECIPES.items():
            block = provenance.get(part)
            if not isinstance(block, Mapping) or (part in ("history", "external") and block.get("mode") == "none"):
                continue
            if block.get("recipe") not in recipes:
                problems.append(f"provenance {part} uses recipe {block.get('recipe')!r}, not one of {', '.join(recipes)}")
            if block.get("digest") is not None and not _DIGEST.fullmatch(str(block.get("digest"))):
                problems.append(f"provenance {part} digest is not a sha256 digest")
    packet = evidence.get("packet")
    if not isinstance(packet, Mapping):
        return problems + ["the evidence has no packet"]
    if packet.get("recipe") not in PACKET_PARTS:
        return problems + [f"the packet uses recipe {packet.get('recipe')!r}, not one of {', '.join(PACKET_PARTS)}"]
    try:
        expected = packet_block(case, packet["recipe"])
    except (KeyError, TypeError) as error:
        return problems + [f"the packet cannot be rebuilt: {error!r}"]
    if packet["recipe"] in WITH_GRAPH and isinstance(evidence.get("graph"), Mapping):
        from .graph import verify_graph

        problems += [f"graph: {problem}" for problem in verify_graph(case)]
    if packet["recipe"] in WITH_ELIGIBILITY and isinstance(evidence.get("detector_eligibility"), Mapping):
        from .evidence import eligibility_problems

        problems += [f"eligibility: {problem}" for problem in eligibility_problems(evidence)]
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
