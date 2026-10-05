"""Provenance: which detector code and which parsed game profile produced a case's evidence.

Two fingerprints, each a full SHA-256 written ``sha256:<64 hex>``:

- ``profile``: the GameProfile as the scorer sees it after parsing, not the file. The parser turns
  some explicit values into defaults, and this describes what actually ran.
- ``detector``: the source of every module whose code can change a finding or the evidence written
  for it, read from the package that is running. Presentation code is not in it, so a dashboard or
  page change cannot move it.

Both are computed once per scoring run and shared by every case in it. Neither yet binds the events
scored, the cohort, external evidence or challenge material. Observation ids do not include
provenance, and the case seal does not change: provenance says what produced an observation, the id
says which observation it is.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROVENANCE_VERSION = 1
# The canonicalization recipes. Bump one when the bytes it hashes would be built differently.
PROFILE_RECIPE = "fpsdet.profile/1"
DETECTOR_RECIPE = "fpsdet.detector/1"

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


@dataclass(frozen=True)
class RunProvenance:
    """What produced every case in one scoring run. One object, shared by every case."""

    profile: str
    detector: DetectorFingerprint

    def to_dict(self) -> dict:
        detector: dict = {"recipe": DETECTOR_RECIPE, "digest": self.detector.digest, "modules": list(self.detector.modules)}
        if self.detector.missing:
            detector["missing"] = list(self.detector.missing)
        return {
            "version": PROVENANCE_VERSION,
            "profile": {"recipe": PROFILE_RECIPE, "digest": self.profile},
            "detector": detector,
        }


def run_provenance(profile) -> RunProvenance:
    return RunProvenance(profile_digest(profile), detector_fingerprint())


def stamp(cases: Iterable, profile) -> RunProvenance:
    """Give every case of one run the same provenance. Computed once, not per player."""
    provenance = run_provenance(profile)
    for case in cases:
        case.provenance = provenance
    return provenance


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
