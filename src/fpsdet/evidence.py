"""Structured evidence: what fired, the role it played in the decision, and the numbers behind it.

The scorer has always known which check fired and on what numbers, then kept only a sentence.
An ``Observation`` keeps the rest: the detector (``kind``), its evidence ``family``, the ``role``
the scorer gave it, and the measurements, bounds, cohort lines and thresholds it compared.

This module is data only. It imports nothing from the scorer, and the scorer does not read it
back: decisions still come from ``score.decide``. ``implied_decision`` recomputes a decision
from the roles alone, so tests can prove the evidence explains every decision. It is a check,
not an authority.

Identity. ``observation_id`` is ``obs-`` and the first 24 hex digits of the SHA-256 of the
canonical JSON of the observation's material fields: the identity version, ``source``,
``family``, ``kind``, ``role``, ``subject_id``, ``key``, ``match_ids``, ``depends_on`` and
``evidence``. ``context`` is not material and is left out, so rewording an explanation does not
move an id. Nothing random, counted or timed goes in. The id is not the case seal and does not
change it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# The shape of case["evidence"]. Bump it when a consumer would have to read it differently.
EVIDENCE_VERSION = 1
# The recipe for observation_id. Bump it when the material fields change.
IDENTITY_VERSION = 1
# Native observations: computed by fpsdet from events a game server wrote.
SOURCE = "fpsdet"

FAMILIES = ("physics", "weapon_rules", "human_baseline", "information", "relationship", "account_history")

# The role the scorer gives a finding. Together these are exactly the inputs of score.decide,
# plus the batch passes:
#   review          a review on its own
#   past_human      one is a watch; two of different metrics, or one with an account_change or a
#                   supporting observation, is a review
#   account_change  a watch on its own; with a past_human, a review
#   supporting      two from different families are a watch; with a past_human, a review
#   watch           a watch on its own; never part of a review
ROLES = ("review", "past_human", "account_change", "supporting", "watch")

# kind -> (family, the case check id it fires, the role the scorer gives it)
KINDS: dict[str, tuple[str, str, str]] = {
    "speed": ("physics", "speed", "review"),
    "fire_rate": ("weapon_rules", "fire_rate", "review"),
    "metronome": ("weapon_rules", "metronome", "review"),
    "recoil_floor": ("weapon_rules", "recoil_floor", "review"),
    "mirror": ("weapon_rules", "mirror", "review"),
    "recoil_learned": ("human_baseline", "recoil_learned", "review"),
    "accuracy": ("human_baseline", "accuracy", "past_human"),
    "headshot_rate": ("human_baseline", "headshot_rate", "past_human"),
    "median_distance": ("human_baseline", "median_distance", "past_human"),
    "geometry_rate": ("human_baseline", "geometry_rate", "past_human"),
    "extra": ("human_baseline", "extra", "past_human"),
    "rank_tail": ("human_baseline", "rank_tail", "watch"),
    "view_snaps": ("human_baseline", "supporting", "supporting"),
    "acquire_timing": ("human_baseline", "supporting", "supporting"),
    "supporting_extra": ("human_baseline", "supporting", "supporting"),
    "account_jump": ("account_history", "account_jump", "account_change"),
    "hidden": ("information", "hidden", "review"),
    "quiet_aim": ("information", "quiet_aim", "review"),
    "wire": ("information", "wire", "review"),
    "private_replay": ("information", "private_replay", "review"),
    "leftover": ("relationship", "leftover", "watch"),
    "voice": ("relationship", "voice", "watch"),
}

# An observation describes evidence. It never carries an instruction to act on an account.
FORBIDDEN_KEYS = frozenset({"action", "automated_action", "recommended_action"})


# Python 3.12 rounds a float sum() differently from 3.11 in the last bit. Twelve significant
# digits is far past any meaningful precision and keeps one id for one finding on both.
FLOAT_DIGITS = 12


def _clean(value, where: str):
    """A JSON-safe copy of ``value``, or TypeError. Non-finite floats become explicit strings."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return float(f"{value:.{FLOAT_DIGITS}g}")
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, (list, tuple)):
        return [_clean(item, f"{where}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{where}: keys must be strings, not {type(key).__name__}")
            if key in FORBIDDEN_KEYS:
                raise ValueError(f"{where}.{key}: an observation cannot carry an action on an account")
            out[key] = _clean(item, f"{where}.{key}")
        return out
    raise TypeError(f"{where}: {type(value).__name__} is not evidence data (use str, number, bool, None, list, dict)")


def canonical_json(obj) -> str:
    """One spelling for one value: sorted keys, no spaces, no NaN."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class Observation:
    """One finding, as data. ``evidence`` holds what the detector compared; ``context`` what it did not."""

    family: str
    kind: str
    role: str
    subject_id: str
    key: str = ""  # the weapon key, build key or declared-metric group; "" for the whole account
    match_ids: tuple[str, ...] = ()
    evidence: Mapping = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()  # ids of observations, on this or another case, this one rests on
    context: Mapping = field(default_factory=dict)
    source: str = SOURCE
    observation_id: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"unknown evidence family {self.family!r}")
        if self.kind not in KINDS:
            raise ValueError(f"unknown observation kind {self.kind!r}")
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")
        family, _check, role = KINDS[self.kind]
        if self.family != family:
            raise ValueError(f"{self.kind} belongs to {family}, not {self.family}")
        if self.role != role:
            raise ValueError(f"the scorer gives {self.kind} the role {role}, not {self.role}")
        if self.source != SOURCE:
            raise ValueError(f"only native fpsdet observations exist yet, not {self.source!r}")
        if not isinstance(self.subject_id, str) or not self.subject_id:
            raise ValueError("subject_id must be a non-empty string")
        if not isinstance(self.key, str):
            raise TypeError("key must be a string")
        for name in ("match_ids", "depends_on"):
            items = getattr(self, name)
            if isinstance(items, (str, set, frozenset)) or not all(isinstance(item, str) for item in items):
                raise TypeError(f"{name} must be a sequence of strings in a fixed order")
            object.__setattr__(self, name, tuple(items))
        if not isinstance(self.evidence, Mapping) or not isinstance(self.context, Mapping):
            raise TypeError("evidence and context must be mappings")
        object.__setattr__(self, "evidence", _clean(self.evidence, "evidence"))
        object.__setattr__(self, "context", _clean(self.context, "context"))
        object.__setattr__(self, "observation_id", observation_id(self))

    @property
    def check(self) -> str:
        """The id this observation adds to ``Case.checks``."""
        return KINDS[self.kind][1]

    def material(self) -> dict:
        return {
            "v": IDENTITY_VERSION,
            "source": self.source,
            "family": self.family,
            "kind": self.kind,
            "role": self.role,
            "subject_id": self.subject_id,
            "key": self.key,
            "match_ids": list(self.match_ids),
            "depends_on": list(self.depends_on),
            "evidence": self.evidence,
        }

    def to_dict(self) -> dict:
        return json.loads(canonical_json({
            "observation_id": self.observation_id,
            "source": self.source,
            "family": self.family,
            "kind": self.kind,
            "role": self.role,
            "subject_id": self.subject_id,
            "key": self.key,
            "match_ids": list(self.match_ids),
            "depends_on": list(self.depends_on),
            "evidence": self.evidence,
            "context": self.context,
        }))


def observation_id(obs: Observation) -> str:
    digest = hashlib.sha256(canonical_json(obs.material()).encode("utf-8")).hexdigest()
    return f"obs-{digest[:24]}"


def evidence_block(observations: Iterable[Observation], compared: Iterable[tuple[str, str]]) -> dict:
    """``case["evidence"]``. ``compared`` lists each (metric, key) the scorer measured against a thick cohort.

    It is there because no observation, on its own, tells clean from insufficient data: a player
    nobody could compare and a player who was compared and passed both have no finding.
    """
    return {
        "version": EVIDENCE_VERSION,
        "observations": [obs.to_dict() for obs in observations],
        "eligibility": {"compared": [{"metric": metric, "key": key} for metric, key in sorted(set(compared))]},
    }


def _unit(obs: dict) -> str:
    """What counts once. The same metric on several weapons is one; each declared metric is its own."""
    kind = obs["kind"]
    if kind in ("extra", "supporting_extra"):
        return f"extra:{obs['evidence']['metric']}"
    return {"view_snaps": "view", "acquire_timing": "acquire"}.get(kind, kind)


def implied_decision(block: Mapping) -> str:
    """The decision the roles in ``case["evidence"]`` imply, by the rules of ``score.decide``.

    A test helper and a consumer's check. The scorer does not call it.
    """
    observations = block["observations"]
    roles = {role: [obs for obs in observations if obs["role"] == role] for role in ROLES}
    past = {_unit(obs) for obs in roles["past_human"]}
    supporting = {_unit(obs) for obs in roles["supporting"]}
    changed = bool(roles["account_change"])
    if roles["review"] or len(past) >= 2 or (past and (changed or supporting)):
        return "review"
    if roles["watch"] or past or changed or len(supporting) >= 2:
        return "watch"
    return "clean" if block["eligibility"]["compared"] else "insufficient_data"
