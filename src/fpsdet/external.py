"""External evidence: what another integrity system said about a player, kept exactly as it said it.

fpsdet does not own or understand client anti-cheat, platform attestation, account systems or human
adjudication. It records what they report, says where each record came from, and keeps it apart from
its own server evidence. A provider's confidence is preserved with the meaning the provider gave it,
never turned into an fpsdet probability. A record's digest is its identity, not proof of who wrote it:
a record is authenticated only when a registered provider key signed it (fpsdet.auth), and even then that
says who made the claim, not that it is true.

Records arrive in one of two ways:

- the native format, ``fpsdet.external/1``, one JSON object per line, which needs no adapter;
- a provider's own format, mapped by a data-only adapter (``fpsdet.external-adapter/1``): field paths,
  constants and an allowlist. An adapter cannot run code, evaluate expressions, read files or
  transform values. It is untrusted configuration, and it is validated as strictly as the records.

Every string is untrusted data. Bounds keep a hostile file from becoming a memory or CPU problem, and
errors name the file, line and field, never the value.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .auth import REJECTED, UNSIGNED_RECORD, VERIFIED, Authentication, AuthError, Registry, is_envelope, parse_envelope, signed_bytes, verify
from .evidence import FLOAT_DIGITS, FORBIDDEN_KEYS, canonical_json

# The native record format, and the recipe of a record's identity.
RECORD_RECIPE = "fpsdet.external/1"
# An adapter file, and the recipe of its digest.
ADAPTER_FORMAT = "fpsdet.external-adapter/1"

SOURCE_CLASSES = (
    "client_integrity",  # software on the player's machine: memory, process, driver, input integrity
    "platform_attestation",  # the device or platform vouching, or failing to vouch, for itself
    "account_status",  # standing on an account: past bans, restrictions, history. Not about these matches
    "tournament_finding",  # a league or tournament administration's ruling
    "human_review",  # a person's finding outside fpsdet: a studio reviewer, a moderator
    "custom_detector",  # a studio's own automated detector
)
# What the provider asserts about the player.
DIRECTIONS = (
    "adverse",  # something was wrong
    "favorable",  # something was checked and was fine
    "context",  # neither
)
# What the provider's signal was measured from. Two providers on one domain are not independent.
TELEMETRY_DOMAINS = (
    "endpoint_memory",
    "endpoint_process",
    "endpoint_input",
    "platform_attestation",
    "server_behavior",
    "account_history",
    "human_review",
    "unspecified",
)
# What the provider says its confidence is. fpsdet never converts one into another.
CONFIDENCE_SCALES = (
    "probability",  # the provider defines it as a probability between 0 and 1
    "score",  # a number on the provider's own scale, not a probability
    "label",  # a word on the provider's own scale, such as "high"
    "unspecified",  # the provider did not say
)

# Bounds. External input is untrusted.
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ADAPTER_BYTES = 64 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_RECORDS = 1_000_000  # per run, after duplicates are dropped
MAX_RECORDS_PER_SUBJECT = 64
MAX_DEPTH = 8  # nesting of one provider record
MAX_ID = 128  # ids, kinds and labels
MAX_TEXT = 256  # free text: a confidence meaning, a metadata value
MAX_METADATA_FIELDS = 16
MAX_METADATA_BYTES = 4096
MAX_MS = 10**10  # match time
MAX_NUMBER = 1e12

PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
PATH_PART = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_PATH_PARTS = 6
OBSERVED_AT = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2}))?")
# Control characters, and the bidirectional overrides that make text read differently from its bytes.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩﻿]")

NATIVE_FIELDS = frozenset({
    "format", "provider", "provider_group", "source_class", "telemetry_domain", "kind", "direction",
    "subject_id", "match_id", "started_ms", "ended_ms", "observed_at", "confidence", "confidence_scale",
    "confidence_meaning", "provider_record_id", "metadata",
})
# What an adapter may take from a provider record, and what it may set as a constant instead.
MAPPABLE = ("subject_id", "match_id", "kind", "direction", "started_ms", "ended_ms", "observed_at",
            "confidence", "confidence_scale", "confidence_meaning", "provider_record_id")
CONSTANT = ("kind", "direction", "confidence_scale", "confidence_meaning")
ADAPTER_FIELDS = frozenset({
    "format", "name", "version", "provider", "provider_group", "source_class", "telemetry_domain",
    "fields", "constants", "directions", "metadata",
})


class ExternalError(ValueError):
    """An external record, adapter or file fpsdet will not read. The message never quotes the input."""


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _text(value, name: str, limit: int, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ExternalError(f"{name} is required")
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ExternalError(f"{name} must be a string of 1 to {limit} characters")
    if _UNSAFE.search(value):
        raise ExternalError(f"{name} contains control or direction-override characters")
    return value


def _choice(value, name: str, choices: tuple[str, ...], default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if value not in choices:
        raise ExternalError(f"{name} must be one of {', '.join(choices)}")
    return value


def _ms(value, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_MS:
        raise ExternalError(f"{name} must be an integer from 0 to {MAX_MS}")
    return value


def _number(value, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > MAX_NUMBER:
        raise ExternalError(f"{name} must be a finite number no larger than {MAX_NUMBER:g}")
    if isinstance(value, float) and float(f"{value:.{FLOAT_DIGITS}g}") != value:
        # Evidence keeps 12 significant digits. A value it would round is refused, never silently changed.
        raise ExternalError(f"{name} has more than {FLOAT_DIGITS} significant digits")
    return value


def _observed_at(value) -> str | None:
    text = _text(value, "observed_at", 40, required=False)
    if text is None:
        return None
    if not OBSERVED_AT.fullmatch(text):
        raise ExternalError("observed_at must be an ISO 8601 date or UTC date and time")
    try:
        dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ExternalError("observed_at is not a real date") from None
    return text


def _confidence(value, scale, meaning) -> tuple[int | float | str | None, str, str | None]:
    """The confidence exactly as given, the scale the provider says it is on, and what the provider says it
    means. A probability must be a number from 0 to 1; a score a number; a label a word."""
    meaning = _text(meaning, "confidence_meaning", MAX_TEXT, required=False)
    if value is None:
        if scale is not None or meaning is not None:
            raise ExternalError("confidence_scale and confidence_meaning need a confidence")
        return None, "unspecified", None
    scale = _choice(scale, "confidence_scale", CONFIDENCE_SCALES, default="unspecified")
    if isinstance(value, str):
        if scale not in ("label", "unspecified"):
            raise ExternalError(f"a {scale} confidence must be a number")
        return _text(value, "confidence", 64, required=True), scale, meaning
    value = _number(value, "confidence")
    if scale == "label":
        raise ExternalError("a label confidence must be a word")
    if scale == "probability" and not 0 <= value <= 1:
        raise ExternalError("a probability confidence must be from 0 to 1")
    return value, scale, meaning


def _metadata(value) -> tuple[tuple[str, object], ...]:
    """Flat, small and plain: at most 16 keys, each a string, number, true, false or null."""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ExternalError("metadata must be an object")
    if len(value) > MAX_METADATA_FIELDS:
        raise ExternalError(f"metadata can have at most {MAX_METADATA_FIELDS} fields")
    out = []
    for key, item in value.items():
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ExternalError("metadata keys must be 1 to 64 letters, digits or underscores, starting with a letter")
        if key in FORBIDDEN_KEYS:
            raise ExternalError("metadata cannot carry an action on an account")
        if isinstance(item, str):
            item = _text(item, f"metadata.{key}", MAX_TEXT, required=True)
        elif item is not None and not isinstance(item, bool):
            item = _number(item, f"metadata.{key}")
        out.append((key, item))
    out.sort()
    if len(canonical_json(dict(out)).encode("utf-8")) > MAX_METADATA_BYTES:
        raise ExternalError(f"metadata is larger than {MAX_METADATA_BYTES} bytes")
    return tuple(out)


@dataclass(frozen=True)
class ExternalRecord:
    """One provider's claim about one player, normalized. Nothing in it was reinterpreted."""

    provider: str
    provider_group: str  # who stands behind the provider; two products of one vendor share it
    source_class: str
    telemetry_domain: str
    kind: str  # the provider's own label, verbatim
    direction: str
    subject_id: str
    match_id: str | None
    started_ms: int | None  # match time, when the provider scoped the record inside a match
    ended_ms: int | None
    observed_at: str | None  # the provider's own date or time for the record, verbatim
    confidence: int | float | str | None
    confidence_scale: str
    confidence_meaning: str | None
    provider_record_id: str | None  # the provider's own id for the record, when it has one
    metadata: tuple[tuple[str, object], ...] = ()
    # Who signed it (fpsdet.auth). Not part of the claim or its identity: the same claim signed or not is one record.
    auth: Authentication = field(default=UNSIGNED_RECORD, compare=False)
    digest: str = field(default="", init=False)  # the whole SHA-256 of the claim
    external_id: str = field(default="", init=False)  # its first 24 hex digits, as the record's name

    def __post_init__(self) -> None:
        object.__setattr__(self, "digest", record_digest(self.material()))
        object.__setattr__(self, "external_id", "ext-" + self.digest[len("sha256:"):][:24])

    def material(self) -> dict:
        """Everything the record claims. Its identity covers all of it, and nothing about where or in what
        order it was read."""
        return {
            "provider": self.provider,
            "provider_group": self.provider_group,
            "source_class": self.source_class,
            "telemetry_domain": self.telemetry_domain,
            "kind": self.kind,
            "direction": self.direction,
            "subject_id": self.subject_id,
            "scope": {"match_id": self.match_id, "started_ms": self.started_ms, "ended_ms": self.ended_ms, "observed_at": self.observed_at},
            "confidence": {"value": self.confidence, "scale": self.confidence_scale, "meaning": self.confidence_meaning},
            "provider_record_id": self.provider_record_id,
            "metadata": dict(self.metadata),
        }


def record_digest(material: Mapping) -> str:
    """``fpsdet.external/1``: SHA-256 over the recipe, a zero byte, and the canonical JSON of the record's
    material. The record's id is ``ext-`` and the first 24 hex digits of it."""
    return _sha256(RECORD_RECIPE.encode("ascii") + b"\0" + canonical_json(dict(material)).encode("utf-8"))


def build_record(values: Mapping) -> ExternalRecord:
    """A record from already-separated values, checked field by field."""
    provider = _text(values.get("provider"), "provider", 64, required=True)
    if not PROVIDER.fullmatch(provider):
        raise ExternalError("provider must be 1 to 64 lowercase letters, digits, '.', '_' or '-'")
    group = _text(values.get("provider_group"), "provider_group", 64, required=False) or provider
    if not PROVIDER.fullmatch(group):
        raise ExternalError("provider_group must be 1 to 64 lowercase letters, digits, '.', '_' or '-'")
    match_id = _text(values.get("match_id"), "match_id", MAX_ID, required=False)
    started = _ms(values.get("started_ms"), "started_ms")
    ended = _ms(values.get("ended_ms"), "ended_ms")
    if (started is not None or ended is not None) and match_id is None:
        raise ExternalError("started_ms and ended_ms are match time, so they need a match_id")
    if started is not None and ended is not None and ended < started:
        raise ExternalError("ended_ms is before started_ms")
    confidence, scale, meaning = _confidence(values.get("confidence"), values.get("confidence_scale"), values.get("confidence_meaning"))
    return ExternalRecord(
        provider=provider,
        provider_group=group,
        source_class=_choice(values.get("source_class"), "source_class", SOURCE_CLASSES),
        telemetry_domain=_choice(values.get("telemetry_domain"), "telemetry_domain", TELEMETRY_DOMAINS, default="unspecified"),
        kind=_text(values.get("kind"), "kind", MAX_ID, required=True),
        direction=_choice(values.get("direction"), "direction", DIRECTIONS),
        subject_id=_text(values.get("subject_id"), "subject_id", MAX_ID, required=True),
        match_id=match_id,
        started_ms=started,
        ended_ms=ended,
        observed_at=_observed_at(values.get("observed_at")),
        confidence=confidence,
        confidence_scale=scale,
        confidence_meaning=meaning,
        provider_record_id=_text(values.get("provider_record_id"), "provider_record_id", MAX_ID, required=False),
        metadata=_metadata(values.get("metadata")),
    )


def native_record(obj) -> ExternalRecord:
    """One ``fpsdet.external/1`` record. Every key must be one the format defines."""
    if not isinstance(obj, Mapping):
        raise ExternalError("a record must be a JSON object")
    if obj.get("format") != RECORD_RECIPE:
        raise ExternalError(f"format must be {RECORD_RECIPE}; a provider's own format needs an adapter")
    unknown = sorted(set(obj) - NATIVE_FIELDS)
    if unknown:
        raise ExternalError(f"{len(unknown)} fields that {RECORD_RECIPE} does not define")
    return build_record(obj)


def _path(text, where: str) -> tuple[str, ...]:
    if not isinstance(text, str):
        raise ExternalError(f"{where} must be a dotted path")
    parts = tuple(text.split("."))
    if not 1 <= len(parts) <= MAX_PATH_PARTS or not all(PATH_PART.fullmatch(part) for part in parts):
        raise ExternalError(f"{where} must be 1 to {MAX_PATH_PARTS} dot-separated names of letters, digits, '_' or '-'")
    return parts


@dataclass(frozen=True)
class Adapter:
    """A provider's record format, mapped onto fpsdet's, as data. Paths look values up by key; nothing else
    happens to them."""

    name: str
    version: int
    provider: str
    provider_group: str
    source_class: str
    telemetry_domain: str
    fields: tuple[tuple[str, tuple[str, ...]], ...]
    constants: tuple[tuple[str, str], ...]
    directions: tuple[tuple[str, str], ...]
    metadata: tuple[tuple[str, tuple[str, ...]], ...]
    digest: str

    def map(self, raw) -> ExternalRecord:
        if not isinstance(raw, Mapping):
            raise ExternalError("a record must be a JSON object")
        values: dict = {
            "provider": self.provider,
            "provider_group": self.provider_group,
            "source_class": self.source_class,
            "telemetry_domain": self.telemetry_domain,
            **dict(self.constants),
        }
        for name, path in self.fields:
            values[name] = _lookup(raw, path)
        if values.get("confidence") is None and "confidence_scale" not in dict(self.fields):
            # The adapter's scale and meaning describe the provider's confidence. A record without one has neither.
            values.pop("confidence_scale", None)
            values.pop("confidence_meaning", None)
        if self.directions:
            kind = values.get("kind")
            table = dict(self.directions)
            if kind not in table:
                raise ExternalError("the record's kind has no direction in the adapter")
            values["direction"] = table[kind]
        found = {}
        for key, path in self.metadata:
            item = _lookup(raw, path)
            if item is not None:
                found[key] = item
        values["metadata"] = found or None
        return build_record(values)


def _lookup(raw: Mapping, path: tuple[str, ...]):
    value = raw
    for part in path:
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def adapter_from_dict(obj) -> Adapter:
    """An adapter, checked: only known keys, only fields fpsdet defines, paths that are only key lookups, and
    every record's direction declared."""
    if not isinstance(obj, Mapping) or obj.get("format") != ADAPTER_FORMAT:
        raise ExternalError(f"an adapter must be a {ADAPTER_FORMAT} object")
    unknown = sorted(set(obj) - ADAPTER_FIELDS)
    if unknown:
        raise ExternalError(f"the adapter has {len(unknown)} keys {ADAPTER_FORMAT} does not define")
    name = _text(obj.get("name"), "name", 64, required=True)
    if not PROVIDER.fullmatch(name):
        raise ExternalError("the adapter name must be 1 to 64 lowercase letters, digits, '.', '_' or '-'")
    version = obj.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 10**6:
        raise ExternalError("the adapter version must be a positive integer")
    fields = obj.get("fields")
    constants = obj.get("constants") or {}
    directions = obj.get("directions") or {}
    metadata = obj.get("metadata") or {}
    if not isinstance(fields, Mapping) or not isinstance(constants, Mapping) or not isinstance(directions, Mapping) or not isinstance(metadata, Mapping):
        raise ExternalError("fields, constants, directions and metadata must be objects")
    if set(fields) - set(MAPPABLE):
        raise ExternalError(f"fields can map only {', '.join(MAPPABLE)}")
    if set(constants) - set(CONSTANT):
        raise ExternalError(f"constants can set only {', '.join(CONSTANT)}")
    if set(fields) & set(constants):
        raise ExternalError("a field cannot be both mapped and constant")
    if "subject_id" not in fields or ("kind" not in fields and "kind" not in constants):
        raise ExternalError("the adapter must map subject_id, and map or set kind")
    if ("direction" in fields) + ("direction" in constants) + bool(directions) != 1:
        raise ExternalError("the adapter must declare direction exactly one way: a mapped field, a constant, or a table by kind")
    for kind, direction in directions.items():
        _text(kind, "a directions key", MAX_ID, required=True)
        _choice(direction, "a direction", DIRECTIONS)
    if len(directions) > 256 or len(metadata) > MAX_METADATA_FIELDS:
        raise ExternalError("the adapter maps too many kinds or metadata fields")
    for key in metadata:
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ExternalError("metadata keys must be 1 to 64 letters, digits or underscores, starting with a letter")
        if key in FORBIDDEN_KEYS:
            raise ExternalError("metadata cannot carry an action on an account")
    for key, value in constants.items():
        if key == "direction":
            _choice(value, "direction", DIRECTIONS)
        elif key == "confidence_scale":
            _choice(value, "confidence_scale", CONFIDENCE_SCALES)
        else:
            _text(value, key, MAX_TEXT, required=True)
    provider = _text(obj.get("provider"), "provider", 64, required=True)
    group = _text(obj.get("provider_group"), "provider_group", 64, required=False) or provider
    for value, label in ((provider, "provider"), (group, "provider_group")):
        if not PROVIDER.fullmatch(value):
            raise ExternalError(f"{label} must be 1 to 64 lowercase letters, digits, '.', '_' or '-'")
    return Adapter(
        name=name,
        version=version,
        provider=provider,
        provider_group=group,
        source_class=_choice(obj.get("source_class"), "source_class", SOURCE_CLASSES),
        telemetry_domain=_choice(obj.get("telemetry_domain"), "telemetry_domain", TELEMETRY_DOMAINS, default="unspecified"),
        fields=tuple(sorted((key, _path(value, f"fields.{key}")) for key, value in fields.items())),
        constants=tuple(sorted(constants.items())),
        directions=tuple(sorted(directions.items())),
        metadata=tuple(sorted((key, _path(value, f"metadata.{key}")) for key, value in metadata.items())),
        digest=_sha256(ADAPTER_FORMAT.encode("ascii") + b"\0" + canonical_json(dict(obj)).encode("utf-8")),
    )


def load_adapter(path: str | Path) -> Adapter:
    target = Path(path)
    try:
        if target.stat().st_size > MAX_ADAPTER_BYTES:
            raise ExternalError(f"{target}: an adapter is at most {MAX_ADAPTER_BYTES} bytes")
        return adapter_from_dict(_loads(target.read_bytes()))
    except OSError:
        raise ExternalError(f"{target}: cannot be read") from None
    except ExternalError as error:
        raise ExternalError(f"{target}: {error}") from None


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ExternalError("a key appears twice in one object")
        seen[key] = value
    return seen


def _no_constant(name: str):
    raise ExternalError("NaN and Infinity are not JSON numbers")


def _depth(value, limit: int = MAX_DEPTH) -> None:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            raise ExternalError(f"nested more than {limit} levels deep")
        if isinstance(item, Mapping):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _loads(data: bytes):
    """Strict JSON: UTF-8, no repeated keys, no NaN or Infinity, nested at most MAX_DEPTH."""
    try:
        text = data.decode("utf-8")
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except UnicodeDecodeError:
        raise ExternalError("not UTF-8") from None
    except RecursionError:
        raise ExternalError(f"nested more than {MAX_DEPTH} levels deep") from None
    except json.JSONDecodeError as error:
        raise ExternalError(f"not JSON ({error.msg})") from None
    _depth(value)
    return value


@dataclass
class ExternalSource:
    """One file read: its bytes' digest, how it was mapped, and what came of it. Not the path: a path is local."""

    artifact: str  # sha256 of the file as read
    adapter: str  # "fpsdet.external/1", or the adapter's digest
    adapter_name: str | None
    records: int = 0  # new records it added
    duplicates: int = 0  # records already read, from it or an earlier file
    errors: int = 0  # lines not read: malformed, or a signature that was refused
    excluded: int = 0  # readable records left out because the run required verified signatures
    authentication: dict[str, int] = field(default_factory=dict)  # every readable line's signature state

    def to_dict(self) -> dict:
        return {
            "artifact": self.artifact,
            "adapter": self.adapter,
            "adapter_name": self.adapter_name,
            "records": self.records,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "excluded": self.excluded,
            "authentication": dict(sorted(self.authentication.items())),
        }


@dataclass
class ExternalInput:
    """Every external record a run was given, without duplicates, indexed by player."""

    sources: list[ExternalSource] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    records: dict[str, ExternalRecord] = field(default_factory=dict)  # by external_id
    by_subject: dict[str, list[ExternalRecord]] = field(default_factory=dict)
    registry: Registry | None = None  # the provider keys signatures were checked against, if any
    require_signed: bool = False  # only records with a verified signature were read

    def for_subject(self, subject_id: str) -> list[ExternalRecord]:
        """This player's records, in external id order: never in file order."""
        return self.by_subject.get(subject_id, [])

    def add(self, record: ExternalRecord, source: ExternalSource) -> str | None:
        """Keep a record once. Returns why it was not kept, or None. When the same claim arrives again with a
        stronger signature, the claim is kept once with the stronger one, whatever the order it came in."""
        known = self.records.get(record.external_id)
        if known is not None:
            source.duplicates += 1
            if _stronger(record.auth, known.auth):
                self.records[record.external_id] = record
                rows = self.by_subject[record.subject_id]
                rows[rows.index(known)] = record
            return None
        rows = self.by_subject.setdefault(record.subject_id, [])
        if len(rows) >= MAX_RECORDS_PER_SUBJECT:
            return f"more than {MAX_RECORDS_PER_SUBJECT} external records for one player"
        if len(self.records) >= MAX_RECORDS:
            return f"more than {MAX_RECORDS} external records in one run"
        self.records[record.external_id] = record
        rows.append(record)
        source.records += 1
        return None

    def policy(self) -> dict:
        return {"require_signed": self.require_signed}

    def finish(self) -> ExternalInput:
        for rows in self.by_subject.values():
            rows.sort(key=lambda record: record.external_id)
        return self


def _stronger(new: Authentication, old: Authentication) -> bool:
    """Is ``new`` the better signature for a claim already read? Verified over anything; then a fixed order,
    so which one is kept never depends on file order."""
    if new.rank != old.rank:
        return new.rank > old.rank
    return (new.provider or "", new.key_id or "", new.status) < (old.provider or "", old.key_id or "", old.status)


def read_external(
    sources: Iterable[tuple[str | Path, Adapter | None]],
    *,
    strict: bool = False,
    registry: Registry | None = None,
    require_signed: bool = False,
) -> ExternalInput:
    """Read external files, each in the native format (adapter None) or through its adapter.

    A line fpsdet will not read is skipped and named in ``errors``, by file, line and reason, and counted
    on its source; native scoring goes on without it. With ``strict``, the first one raises instead.

    A line may be a signed envelope (fpsdet.auth). Its signature is checked against ``registry`` over the
    exact claim, before the claim is mapped or read, as the provider the record will be read as: the
    claim's own, or the adapter's. A signature that is invalid, from a revoked key or in an algorithm
    fpsdet does not verify is refused like a malformed line. With ``require_signed``, only records with a
    verified signature are read; the rest are counted as excluded.
    """
    out = ExternalInput(registry=registry, require_signed=require_signed)
    # The same signature over the same bytes, for the same provider, is checked once per run.
    checked: dict[tuple, Authentication] = {}
    for path, adapter in sources:
        target = Path(path)

        def fail(message: str, source: ExternalSource | None = None) -> None:
            if strict:
                raise ExternalError(message)
            out.errors.append(message)
            if source is not None:
                source.errors += 1

        try:
            size = target.stat().st_size
        except OSError:
            fail(f"{target}: cannot be read")
            continue
        if size > MAX_FILE_BYTES:
            fail(f"{target}: larger than {MAX_FILE_BYTES} bytes; not read")
            continue
        hasher = hashlib.sha256()
        source = ExternalSource("", RECORD_RECIPE if adapter is None else adapter.digest, None if adapter is None else adapter.name)
        out.sources.append(source)
        with target.open("rb") as handle:
            number = 0
            while True:
                # At most one line's worth in memory: a longer line is read past in pieces and skipped.
                line = handle.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                number += 1
                hasher.update(line)
                if len(line) > MAX_LINE_BYTES:
                    while not line.endswith(b"\n"):
                        line = handle.readline(MAX_LINE_BYTES + 1)
                        if not line:
                            break
                        hasher.update(line)
                    fail(f"{target} line {number}: longer than {MAX_LINE_BYTES} bytes", source)
                    continue
                if not line.strip():
                    continue
                try:
                    raw = _loads(line)
                    auth = UNSIGNED_RECORD
                    if is_envelope(raw):
                        raw, signature = parse_envelope(raw)
                        claimed = adapter.provider if adapter is not None else raw.get("provider")
                        claimed = claimed if isinstance(claimed, str) else ""
                        seen = (claimed, signature.algorithm, signature.provider, signature.key_id, signature.value,
                                hashlib.sha256(signed_bytes(raw)).digest())
                        auth = checked.get(seen)
                        if auth is None:
                            auth = checked[seen] = verify(raw, signature, claimed, registry)
                    source.authentication[auth.status] = source.authentication.get(auth.status, 0) + 1
                    if auth.status in REJECTED:
                        raise ExternalError(f"signature {auth.status.replace('_', ' ')}; not read")
                    # The signature was checked over this exact claim. Only now is it mapped and read.
                    record = native_record(raw) if adapter is None else adapter.map(raw)
                except (ExternalError, AuthError) as error:
                    fail(f"{target} line {number}: {error}", source)
                    continue
                if require_signed and auth.status != VERIFIED:
                    source.excluded += 1
                    continue
                record = dataclasses.replace(record, auth=auth)
                refused = out.add(record, source)
                if refused:
                    fail(f"{target} line {number}: {refused}", source)
        source.artifact = "sha256:" + hasher.hexdigest()
    return out.finish()



# Fusion: external evidence next to fpsdet's own, by explicit rules. No score, no weights.
#
#   native clean or insufficient_data + a qualifying external signal -> watch     (rule A)
#   native watch                      + external                     -> watch     (rule B)
#   native review                     + external                     -> review    (rule C)
#
# Any number of signals from any number of providers is still at most a watch (rules D and E). Account
# status is never a signal (rule F). A person's finding outside fpsdet is kept as theirs, never relabelled
# as fpsdet's review (rule G). Reports are never read.

FUSION_POLICY = "fpsdet.fusion/1"
# Classes whose adverse record, scoped to a match fpsdet scored, can make a watch. Account status is about
# the account's past, not these matches, so it is context only.
SIGNAL_CLASSES = frozenset({"client_integrity", "platform_attestation", "tournament_finding", "human_review", "custom_detector"})
ADJUDICATION_CLASSES = frozenset({"tournament_finding", "human_review"})
NO_FINDING = ("clean", "insufficient_data")


def context_reason(record: ExternalRecord, match_ids: set[str]) -> str:
    """Why a record cannot change a decision, or "" when it is a qualifying signal."""
    if record.source_class not in SIGNAL_CLASSES:
        return record.source_class  # account_status
    if record.direction != "adverse":
        return record.direction  # favorable or context
    if record.match_id is None:
        return "no_match"
    if record.match_id not in match_ids:
        return "other_match"
    return ""


def _line(record: ExternalRecord, why: str) -> str:
    """fpsdet's own sentence about a record. It names the record's id and class, never a provider string, so
    nothing the provider wrote reaches the case text, the command line or an AI prompt."""
    name = f"External {record.source_class} record {record.external_id}"
    if not why:
        if record.source_class in ADJUDICATION_CLASSES:
            return f"{name} is a person's finding about a scored match, made outside fpsdet. It is theirs, not an fpsdet review. External evidence can make a watch, never a review."
        return f"{name} reports something wrong in a scored match. fpsdet did not verify it. External evidence can make a watch, never a review."
    return {
        "account_status": f"{name} is about the account's history, not these matches. It does not change the decision.",
        "favorable": f"{name} reports a check that passed. It does not change the decision.",
        "context": f"{name} is context. It does not change the decision.",
        "no_match": f"{name} names no match. It does not change the decision.",
        "other_match": f"{name} is about a match outside this window. It does not change the decision.",
    }[why]


def observation_for(record: ExternalRecord, subject_id: str, match_ids: set[str], printed_in: str):
    """One record as an observation of the external family, with its source and every claim kept."""
    from .evidence import Observation

    why = context_reason(record, match_ids)
    return Observation(
        family="external",
        kind="external_context" if why else "external_signal",
        role="external_context" if why else "external_watch",
        subject_id=subject_id,
        key=record.external_id,
        match_ids=(record.match_id,) if record.match_id else (),
        evidence={
            "external_id": record.external_id,
            "provider": record.provider,
            "provider_group": record.provider_group,
            "source_class": record.source_class,
            "telemetry_domain": record.telemetry_domain,
            "kind": record.kind,
            "direction": record.direction,
            "scope": {"match_id": record.match_id, "started_ms": record.started_ms, "ended_ms": record.ended_ms, "observed_at": record.observed_at},
            "confidence": {"value": record.confidence, "scale": record.confidence_scale, "meaning": record.confidence_meaning},
            "provider_record_id": record.provider_record_id,
            "metadata": dict(record.metadata),
            "authenticity": record.auth.to_dict(),
            "decision_effect": "watch_at_most" if not why else "none",
            "context_because": why or None,
        },
        context={"line": _line(record, why), "printed_in": printed_in},
        source="external",
    )


def fuse(cases: Iterable, external: ExternalInput | None) -> None:
    """Add each player's external records to their case, by the rules above. Runs after every native pass,
    and reads only the case's decision and matches: never its reports. Without external input it does nothing."""
    if external is None:
        return
    from .models import ACTIONS
    from .signals import evidence_seal

    for case in cases:
        native = case.decision
        matches = set(case.match_ids)
        records = external.for_subject(case.player_id)
        signals = [record for record in records if not context_reason(record, matches)]
        decision = "watch" if signals and native in NO_FINDING else native
        rule = "none" if not signals else {"watch": "B", "review": "C"}.get(native, "A")
        for record in records:
            signal = not context_reason(record, matches)
            printed_in = "reasons" if signal and decision != native else "observations"
            observation = observation_for(record, case.player_id, matches, printed_in)
            case.evidence.append(observation)
            (case.reasons if printed_in == "reasons" else case.observations).append(observation.context["line"])
        case.fusion = {
            "policy": FUSION_POLICY,
            "native_decision": native,
            "decision": decision,
            "rule": rule,
            "signals": len(signals),
            "context": len(records) - len(signals),
            # Recorded for later independence rules. Nothing escalates on them yet.
            "provider_groups": sorted({record.provider_group for record in signals}),
            "telemetry_domains": sorted({record.telemetry_domain for record in signals}),
        }
        if decision != native:
            case.decision = decision
            case.recommended_action = ACTIONS[decision]
            case.seal = evidence_seal(case)
