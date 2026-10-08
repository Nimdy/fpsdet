"""Active challenges: probes the server plans, for information the legitimate client cannot have.

Passive checks ask whether a player behaved strangely, and an elite human can. A challenge asks a
different question: did the player react to something the server made sure this client could not
know? The server places a probe that the stock client never draws or plays, records whether the aim
followed it, and fpsdet judges the record.

This module is the public half. It holds what each challenge type is (``ChallengeSpec``), the record
of one planned challenge (``ChallengePlan``), how that record is digested and checked, and the
knowledge a challenge target has. It never sees the server secret. Planning with the secret is
``fpsdet.challenge_plan``, which scoring does not import.

Three things are kept apart:

- the **spec** of a type, public and in this file;
- the **realization** of one challenge (where, which route, which heading), derived from the server
  secret, held by the game server and never written by fpsdet;
- the **plan**: public identifiers, the window, and a commitment to the realization.

A plan holds no key and no realization. Before its match its windows still say when each challenge
runs, so it stays on the server until the match is over. After that it can go into evidence.
"""

from __future__ import annotations

import bisect
import hashlib
import random
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .evidence import canonical_json
from .knowledge import (
    CHANNEL_ABSENT,
    CHANNEL_KNOWN,
    CHANNEL_NOT_APPLICABLE,
    KNOWN,
    UNKNOWABLE,
    KnowledgeState,
    body_knowledge,
    challenge_channels,
    resolve,
    shot_knowledge,
)
from .statsutil import exact_sum

# The domain prefix of every keyed derivation (fpsdet.challenge_plan). A new prefix is a new recipe.
DERIVATION_RECIPE = "fpsdet.challenge/1"
# The commitment to one realization.
COMMITMENT_RECIPE = "fpsdet.challenge-commitment/1"
# The digest of one public plan record.
PLAN_RECIPE = "fpsdet.challenge-plan/1"
# A plan file: one match, its challenges, and the budget they were scheduled under.
PLAN_FILE_FORMAT = "fpsdet.challenge-plans/1"

# How a challenge type knows its body was hidden. "plan": by the type's own requirements, which the server
# promised to keep (version 1). "per_sample": by the server's own vision and audio verdict for the body,
# reported on every event that names the challenge (version 2).
BY_PLAN = "plan"
PER_SAMPLE = "per_sample"
# Version 3: the per-moment verdict of version 2, and the finding is whether the aim turned with the body's
# secret turns, against the same aim scored at shuffled times.
PER_TURN = "per_turn"

# The budget's hard ceiling. A player is not a target range: more probes make a challenge-aware cheat's
# job easier, and add nothing a person needs to review a case.
MAX_PER_MATCH = 4

CHALLENGE_ID = re.compile(r"ch-[0-9a-f]{24}")
# What an event may carry in challenge_id. Planned ids are narrower; anything else is reported as unplanned.
EVENT_CHALLENGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
NONCE = re.compile(r"[0-9a-f]{16,64}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

PLAN_NOTICE = (
    "Public plan: challenge ids, windows and commitments. It holds no key and no realization. Keep it on "
    "the server until the match is over, because its windows say when each challenge runs."
)


class ChallengeError(ValueError):
    """A plan, plan file or challenge type that cannot be trusted as written."""


@dataclass(frozen=True)
class TurnRule:
    """Secret turns: how many, how far apart, the reaction window, and the bar. Public, and fixed before any
    data, so the bar cannot be tuned to the result.

    A turn's response is how much the aim's yaw rate changed in the turn's direction: the yaw moved over
    ``react_from_ms``..``react_to_ms`` after the turn, minus the yaw moved over the same length of time
    just before it, times the turn's sign. A holder's aim does not change with a turn it was never sent; a
    reader of server state follows the body.
    """

    count: int = 6
    min_gap_ms: int = 1_500
    react_from_ms: int = 150
    react_to_ms: int = 600
    # The aim trace may skip at most this long between two samples where a response is read.
    max_trace_gap_ms: int = 200
    shuffles: int = 1_000
    alpha: float = 0.01
    # A turn is followed when the aim's yaw rate changed by more than this, in the turn's own direction, over
    # the reaction window: far above a held aim's tremor. A fixed floor, not one read from the shuffles: with
    # turns this dense, every shuffled time sits near some real turn and a follower's aim moves there too.
    min_turn_response_deg: float = 2.0
    min_followed: int = 4
    # Fewer turns than this, checked and measurable, and the challenge abstains.
    min_counted: int = 4

    @property
    def lead_ms(self) -> int:
        """The aim before a turn that its response is measured against."""
        return self.react_to_ms - self.react_from_ms

    @property
    def min_window_ms(self) -> int:
        """The shortest window that holds ``count`` turns ``min_gap_ms`` apart, each with its lead and reaction."""
        return self.lead_ms + self.react_to_ms + self.count * self.min_gap_ms

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "min_gap_ms": self.min_gap_ms,
            "react_from_ms": self.react_from_ms,
            "react_to_ms": self.react_to_ms,
            "max_trace_gap_ms": self.max_trace_gap_ms,
            "shuffles": self.shuffles,
            "alpha": self.alpha,
            "min_turn_response_deg": self.min_turn_response_deg,
            "min_followed": self.min_followed,
            "min_counted": self.min_counted,
        }


@dataclass(frozen=True)
class ChallengeSpec:
    """What one challenge type is. Public: the source can be read by anyone, cheat authors included."""

    challenge_type: str
    version: int
    # Legal channels the challenge is built to defeat, and the server checks for the whole window.
    defeats: tuple[str, ...]
    # Channels that cannot carry this target at all.
    not_applicable: tuple[str, ...]
    # The realization's parameters, in game-neutral units: name, lowest, highest (inclusive). Each
    # challenge's values come from the server secret; the game maps them onto its own world.
    parameters: tuple[tuple[str, int, int], ...]
    # What the game server must keep true, so the challenge never touches a legitimate player's game.
    requirements: tuple[str, ...]
    # Machine-readable entry for the capability matrix: what this catches, how well, and what beats it.
    capability: Mapping
    # How the body's hiddenness is known: BY_PLAN, PER_SAMPLE or PER_TURN.
    verification: str = BY_PLAN
    # PER_TURN only: the secret turns and the bar, fixed before any data.
    turns: TurnRule | None = None

    @property
    def name(self) -> str:
        return f"{self.challenge_type}/{self.version}"

    def to_dict(self) -> dict:
        return {
            "challenge_type": self.challenge_type,
            "version": self.version,
            "defeats": list(self.defeats),
            "not_applicable": list(self.not_applicable),
            "parameters": [{"name": name, "low": low, "high": high} for name, low, high in self.parameters],
            "requirements": list(self.requirements),
            "capability": dict(self.capability),
            **({"verification": self.verification} if self.verification != BY_PLAN else {}),
            **({"turns": self.turns.to_dict()} if self.turns is not None else {}),
        }


OCCLUDED_MOTION_REPLAY = ChallengeSpec(
    challenge_type="occluded_motion_replay",
    version=1,
    defeats=("vision", "audio"),
    not_applicable=("recent_perception",),
    parameters=(
        # The replayed route is turned by this much, so it never runs on its source's heading.
        ("heading_offset_deg", 30, 330),
        # How far back in time the source movement is taken from.
        ("replay_delay_ms", 2_000, 20_000),
        # Which recorded route, and which valid occluded spot: an index the game takes modulo its own list.
        ("route_pick", 0, 2**32 - 1),
        ("placement_pick", 0, 2**32 - 1),
    ),
    requirements=(
        "Place the body only where this client's line-of-sight and audio queries both fail, for the whole window; end it early if either would succeed.",
        "Send it to this client only.",
        "Never draw it in the stock client, and never show it in the scoreboard, kill feed, radar, minimap or any other UI.",
        "It makes no sound.",
        "It has no collision: it blocks no player, projectile or trace.",
        "It cannot be hit, damaged or killed, and deals no damage.",
        "It changes no movement, score, objective, economy or match result.",
        "Send it with the same fields as a real player: no decoy bit.",
        "Keep it away from any enemy this client can see or hear, so the aim on a real enemy does not cross it.",
    ),
    capability={
        "technique": "packet or memory reader that shows, or aims at, players the client was sent but does not draw",
        "evidence": "challenge.occluded_motion_replay",
        "strength": "strong when every knowledge channel the game declares is one the challenge defeats, and the plan, window and linkage all check out",
        "needs": [
            "challenge_id and challenge_track_ms on the subject's events",
            "the public plan file when the events are scored",
            "knowledge_channels no wider than vision and audio",
        ],
        "limits": [
            "pixel-only aimbots: they react to the rendered frame, and the body is never rendered",
            "challenge-aware software that drops players it cannot verify, or never aims at hidden ones",
            "software that has learned a fixed pattern in how a game places or routes its challenges",
            "information use that never turns into measurable aim toward the probe, such as callouts and map awareness",
            "following a probe too briefly, or too rarely, to clear the bar on any one challenge",
            "following a probe only while a real enemy the client can see or hear is in the same aim cone",
        ],
    },
)

# Version 2: the same probe, but the plan's word that the body was hidden is not enough. The server checks
# this client's line-of-sight and audio queries against the body at every moment of the window and reports
# the verdict on each event that names the challenge. A moment counts only when the verdict says both were
# absent; a moment where the body was seen or heard, or where the verdicts contradict each other, voids the
# whole challenge. Version 1 is kept unchanged, so plans and benchmarks made with it reproduce.
OCCLUDED_MOTION_REPLAY_V2 = ChallengeSpec(
    challenge_type="occluded_motion_replay",
    version=2,
    defeats=OCCLUDED_MOTION_REPLAY.defeats,
    not_applicable=OCCLUDED_MOTION_REPLAY.not_applicable,
    parameters=OCCLUDED_MOTION_REPLAY.parameters,
    requirements=(
        *OCCLUDED_MOTION_REPLAY.requirements,
        "Query this client's line-of-sight and audio against the body at every server tick of the window, and send the verdict over the time each event covers on every event that names the challenge: challenge_vision_state and challenge_audio_state, known if the query succeeded at any tick, unchecked if it did not run at every tick, absent otherwise.",
    ),
    capability={
        **OCCLUDED_MOTION_REPLAY.capability,
        "strength": "strong when every knowledge channel the game declares is one the challenge defeats, the server's own per-moment verdict says the body was neither seen nor heard, and the plan, window and linkage all check out",
        "needs": [
            "challenge_id, challenge_track_ms, challenge_vision_state and challenge_audio_state on the subject's events",
            "the public plan file when the events are scored",
            "knowledge_channels no wider than vision and audio",
        ],
    },
    verification=PER_SAMPLE,
)

# Version 3: the same probe and per-moment verdict as version 2, but it no longer asks how long the aim stayed
# on the body. A player holding an angle the body happens to pass behind can reach that bar without knowing
# anything (the human pilot's dry run). Version 3 turns the body at secret moments and asks whether the aim
# turned with it, compared with the same aim at shuffled times. Versions 1 and 2 are kept unchanged.
OCCLUDED_MOTION_REPLAY_V3 = ChallengeSpec(
    challenge_type="occluded_motion_replay",
    version=3,
    defeats=OCCLUDED_MOTION_REPLAY.defeats,
    not_applicable=OCCLUDED_MOTION_REPLAY.not_applicable,
    parameters=(
        *OCCLUDED_MOTION_REPLAY.parameters,
        # Where in its slot each turn falls, and which way each turns: challenge_plan.turn_schedule.
        *((f"turn_{index}_pick", 0, 2**32 - 1) for index in range(TurnRule().count)),
        ("turn_signs", 0, 2 ** TurnRule().count - 1),
    ),
    requirements=(
        *OCCLUDED_MOTION_REPLAY_V2.requirements,
        "Turn the body at the realization's turn times, each way its sign says, and at no other time; a turn is a change in the rate at which the body's bearing from this client moves.",
        "A turn makes no sound and changes nothing this client can see or hear.",
        "On one event at each turn's server time, send challenge_turn_index and challenge_turn_sign with the challenge's id and verdict.",
        "Through the window, send this client's server-authoritative view yaw as view_yaw_deg on events naming the challenge, at least every max_trace_gap_ms.",
    ),
    capability={
        **OCCLUDED_MOTION_REPLAY_V2.capability,
        "strength": "strong when every knowledge channel the game declares is one the challenge defeats, the server's own per-moment verdict says the body was neither seen nor heard, and the aim turned with the body's secret turns far more than at shuffled times",
        "needs": [
            "challenge_id, challenge_vision_state and challenge_audio_state on the subject's events",
            "view_yaw_deg through the window, and challenge_turn_index and challenge_turn_sign at each turn",
            "the public plan file when the events are scored",
            "knowledge_channels no wider than vision and audio",
        ],
        "limits": [
            *OCCLUDED_MOTION_REPLAY_V2.capability["limits"],
            "software that follows the body but smooths its aim so much that no turn reaches the aim within the reaction window",
        ],
    },
    verification=PER_TURN,
    turns=TurnRule(),
)

SPECS: dict[tuple[str, int], ChallengeSpec] = {
    (spec.challenge_type, spec.version): spec for spec in (OCCLUDED_MOTION_REPLAY, OCCLUDED_MOTION_REPLAY_V2, OCCLUDED_MOTION_REPLAY_V3)
}
# The version a new plan uses, by type: what fpsdet challenge plan writes.
CURRENT: dict[str, ChallengeSpec] = {OCCLUDED_MOTION_REPLAY_V2.challenge_type: OCCLUDED_MOTION_REPLAY_V2}
# What challenge_plan.plan_match plans when its caller names no type: version 1, so code written before
# version 2, the benchmark's controlled scenarios among it, plans exactly what it always did.
DEFAULT_SPEC = OCCLUDED_MOTION_REPLAY


def spec_for(challenge_type: str, version: int) -> ChallengeSpec:
    spec = SPECS.get((challenge_type, version))
    if spec is None:
        raise ChallengeError(f"no challenge type {challenge_type}/{version}")
    return spec


def challenge_knowledge(spec: ChallengeSpec, profile) -> KnowledgeState:
    """Could this client know the challenge target? Every channel the type defeats is absent by the
    type's own requirements, and the server checks them for the whole window. A channel the game
    declares that the type does not defeat was not checked, so the answer is unknown and the
    challenge cannot count."""
    channels = {name: CHANNEL_ABSENT for name in spec.defeats}
    channels.update({name: CHANNEL_NOT_APPLICABLE for name in spec.not_applicable})
    return resolve(channels, profile.knowledge_channels)


def undefeated(spec: ChallengeSpec, profile) -> list[str]:
    """The channels the game declares that this type neither defeats nor rules out."""
    covered = set(spec.defeats) | set(spec.not_applicable)
    return [name for name in profile.knowledge_channels if name not in covered]


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ChallengePlan:
    """One planned challenge, as fpsdet writes it and evidence cites it. No key and no realization."""

    challenge_id: str
    challenge_type: str
    version: int
    game_id: str
    match_id: str
    subject_id: str
    counter: int  # this challenge's place among the subject's challenges in this match, from 0
    nonce: str  # the plan's public nonce; with the secret, it reproduces every challenge in the plan
    start_ms: int
    end_ms: int
    commitment: str

    def committed_fields(self) -> dict:
        """What the commitment covers alongside the realization: every public field but the commitment."""
        return {
            "challenge_id": self.challenge_id,
            "challenge_type": self.challenge_type,
            "version": self.version,
            "game_id": self.game_id,
            "match_id": self.match_id,
            "subject_id": self.subject_id,
            "counter": self.counter,
            "nonce": self.nonce,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }

    @property
    def digest(self) -> str:
        """``fpsdet.challenge-plan/1``: SHA-256 of the recipe, a zero byte, and the canonical JSON of every
        public field, the commitment included. It shows a record was not edited after it was digested.
        It is not a signature: anyone can edit a record and digest it again."""
        body = {**self.committed_fields(), "commitment": self.commitment}
        return _sha256(PLAN_RECIPE.encode("ascii") + b"\0" + canonical_json(body).encode("utf-8"))

    @property
    def spec(self) -> ChallengeSpec:
        return spec_for(self.challenge_type, self.version)

    def to_dict(self) -> dict:
        return {**self.committed_fields(), "commitment": self.commitment, "plan": self.digest}


def _field(obj: Mapping, key: str, kind: type, where: str):
    value = obj.get(key)
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ChallengeError(f"{where}: {key} must be an integer")
        return value
    if not isinstance(value, str) or not value:
        raise ChallengeError(f"{where}: {key} must be a non-empty string")
    return value


def plan_from_dict(obj: Mapping, where: str = "plan") -> ChallengePlan:
    """One plan record, checked: every field present and well formed, the type known, and the stored
    digest equal to the digest of the fields."""
    if not isinstance(obj, Mapping):
        raise ChallengeError(f"{where} must be an object")
    plan = ChallengePlan(
        challenge_id=_field(obj, "challenge_id", str, where),
        challenge_type=_field(obj, "challenge_type", str, where),
        version=_field(obj, "version", int, where),
        game_id=_field(obj, "game_id", str, where),
        match_id=_field(obj, "match_id", str, where),
        subject_id=_field(obj, "subject_id", str, where),
        counter=_field(obj, "counter", int, where),
        nonce=_field(obj, "nonce", str, where),
        start_ms=_field(obj, "start_ms", int, where),
        end_ms=_field(obj, "end_ms", int, where),
        commitment=_field(obj, "commitment", str, where),
    )
    if not CHALLENGE_ID.fullmatch(plan.challenge_id):
        raise ChallengeError(f"{where}: challenge_id is not a planned challenge id")
    spec_for(plan.challenge_type, plan.version)
    if plan.counter < 0 or plan.counter >= MAX_PER_MATCH:
        raise ChallengeError(f"{where}: counter must be 0 to {MAX_PER_MATCH - 1}")
    if not NONCE.fullmatch(plan.nonce):
        raise ChallengeError(f"{where}: nonce must be 16 to 64 lowercase hex digits")
    if not 0 <= plan.start_ms < plan.end_ms:
        raise ChallengeError(f"{where}: the window must start at 0 or later and end after it starts")
    if not _DIGEST.fullmatch(plan.commitment):
        raise ChallengeError(f"{where}: commitment must be a sha256 digest")
    if obj.get("plan") != plan.digest:
        raise ChallengeError(f"{where}: the plan digest does not match its fields")
    return plan


@dataclass(frozen=True)
class Budget:
    """How many challenges a subject gets in one match, and how they are spread. Public: it is in the plan file."""

    to_ms: int  # the latest a window may end, in match time
    count: int = 1
    from_ms: int = 60_000  # no challenge before this: the player has to be in the game first
    cooldown_ms: int = 60_000  # at least this long between one window's end and the next one's start
    min_duration_ms: int = 8_000
    max_duration_ms: int = 16_000

    def problems(self) -> list[str]:
        out = []
        if not 1 <= self.count <= MAX_PER_MATCH:
            out.append(f"count must be 1 to {MAX_PER_MATCH} per player per match")
        if self.from_ms < 0 or self.to_ms <= self.from_ms:
            out.append("the play window must start at 0 or later and end after it starts")
        if self.cooldown_ms < 0:
            out.append("cooldown_ms cannot be negative")
        if not 0 < self.min_duration_ms <= self.max_duration_ms:
            out.append("min_duration_ms must be above 0 and at most max_duration_ms")
        if not out and self.count * self.max_duration_ms + (self.count - 1) * self.cooldown_ms > self.to_ms - self.from_ms:
            fits = (self.to_ms - self.from_ms + self.cooldown_ms) // (self.max_duration_ms + self.cooldown_ms)
            out.append(f"the play window fits at most {fits} challenges of up to {self.max_duration_ms} ms with {self.cooldown_ms} ms between them")
        return out

    def to_dict(self) -> dict:
        return {
            "count": self.count,
            "from_ms": self.from_ms,
            "to_ms": self.to_ms,
            "cooldown_ms": self.cooldown_ms,
            "min_duration_ms": self.min_duration_ms,
            "max_duration_ms": self.max_duration_ms,
        }


def budget_from_dict(obj: Mapping, where: str = "schedule") -> Budget:
    if not isinstance(obj, Mapping):
        raise ChallengeError(f"{where} must be an object")
    budget = Budget(**{key: _field(obj, key, int, where) for key in ("to_ms", "count", "from_ms", "cooldown_ms", "min_duration_ms", "max_duration_ms")})
    problems = budget.problems()
    if problems:
        raise ChallengeError(f"{where}: {problems[0]}")
    return budget


@dataclass(frozen=True)
class PlanFile:
    """One match's plan, as ``fpsdet challenge plan`` writes it."""

    game_id: str
    match_id: str
    nonce: str
    spec: ChallengeSpec
    budget: Budget
    plans: tuple[ChallengePlan, ...]

    def to_dict(self) -> dict:
        return {
            "format": PLAN_FILE_FORMAT,
            "notice": PLAN_NOTICE,
            "game_id": self.game_id,
            "match_id": self.match_id,
            "nonce": self.nonce,
            "challenge_type": self.spec.challenge_type,
            "version": self.spec.version,
            "schedule": self.budget.to_dict(),
            "requirements": list(self.spec.requirements),
            "challenges": [plan.to_dict() for plan in self.plans],
        }


def schedule_problems(plans: Iterable[ChallengePlan], budget: Budget) -> list[str]:
    """Where a set of plans breaks its budget: windows outside the play window, too short or long, too
    many for one player, overlapping or closer than the cooldown, or a counter out of time order."""
    problems = []
    by_subject: dict[tuple[str, str], list[ChallengePlan]] = {}
    for plan in plans:
        by_subject.setdefault((plan.match_id, plan.subject_id), []).append(plan)
        length = plan.end_ms - plan.start_ms
        if plan.start_ms < budget.from_ms or plan.end_ms > budget.to_ms:
            problems.append(f"{plan.challenge_id}: window outside the play window")
        if not budget.min_duration_ms <= length <= budget.max_duration_ms:
            problems.append(f"{plan.challenge_id}: window of {length} ms is outside the budget's durations")
        rule = plan.spec.turns
        if rule is not None and length < rule.min_window_ms:
            problems.append(f"{plan.challenge_id}: window of {length} ms cannot hold {rule.count} turns; {plan.spec.name} needs {rule.min_window_ms} ms")
    for (_match, subject), rows in sorted(by_subject.items()):
        rows.sort(key=lambda plan: plan.start_ms)
        if len(rows) > budget.count:
            problems.append(f"{subject}: {len(rows)} challenges, the budget allows {budget.count}")
        if [plan.counter for plan in rows] != list(range(len(rows))):
            problems.append(f"{subject}: counters are not 0, 1, 2... in time order")
        for before, after in zip(rows, rows[1:]):
            if after.start_ms < before.end_ms + budget.cooldown_ms:
                problems.append(f"{subject}: {after.challenge_id} starts less than {budget.cooldown_ms} ms after {before.challenge_id} ends")
    return problems


def plan_file_from_dict(obj: Mapping, where: str = "plan file") -> PlanFile:
    """A plan file, checked without the secret: format, type, budget, every record's digest, and the
    schedule. It shows the file is consistent, not who wrote it: a plan file is not signed."""
    if not isinstance(obj, Mapping) or obj.get("format") != PLAN_FILE_FORMAT:
        raise ChallengeError(f"{where} is not a {PLAN_FILE_FORMAT} file")
    spec = spec_for(_field(obj, "challenge_type", str, where), _field(obj, "version", int, where))
    budget = budget_from_dict(obj.get("schedule"), f"{where} schedule")
    head = {key: _field(obj, key, str, where) for key in ("game_id", "match_id", "nonce")}
    rows = obj.get("challenges")
    if not isinstance(rows, list):
        raise ChallengeError(f"{where}: challenges must be a list")
    plans = tuple(plan_from_dict(row, f"{where} challenge {index}") for index, row in enumerate(rows))
    for plan in plans:
        if (plan.game_id, plan.match_id, plan.nonce) != (head["game_id"], head["match_id"], head["nonce"]):
            raise ChallengeError(f"{where}: {plan.challenge_id} is for another game, match or nonce than the file")
        if (plan.challenge_type, plan.version) != (spec.challenge_type, spec.version):
            raise ChallengeError(f"{where}: {plan.challenge_id} is another challenge type than the file")
    problems = schedule_problems(plans, budget)
    if problems:
        raise ChallengeError(f"{where}: {problems[0]}")
    _unique(plans, where)
    return PlanFile(head["game_id"], head["match_id"], head["nonce"], spec, budget, plans)


def _unique(plans: Iterable[ChallengePlan], where: str) -> None:
    seen: set[str] = set()
    for plan in plans:
        if plan.challenge_id in seen:
            raise ChallengeError(f"{where}: challenge id {plan.challenge_id} appears twice")
        seen.add(plan.challenge_id)


class ChallengeRegistry:
    """Every plan a scoring run knows, by challenge id. Built from public plan files; no secret."""

    def __init__(self, plans: Iterable[ChallengePlan] = ()):
        plans = list(plans)
        _unique(plans, "the challenge plans")
        self._plans = {plan.challenge_id: plan for plan in plans}
        self._subjects: dict[str, list[ChallengePlan]] = {}
        for plan in sorted(plans, key=lambda plan: plan.challenge_id):
            self._subjects.setdefault(plan.subject_id, []).append(plan)

    @classmethod
    def from_files(cls, files: Iterable[PlanFile]) -> ChallengeRegistry:
        return cls(plan for file in files for plan in file.plans)

    def get(self, challenge_id: str) -> ChallengePlan | None:
        return self._plans.get(challenge_id)

    def for_subject(self, subject_id: str) -> list[ChallengePlan]:
        return list(self._subjects.get(subject_id, ()))

    def __len__(self) -> int:
        return len(self._plans)


# Linking responses to challenges. An event answers a challenge when it carries challenge_id. Its
# challenge_track_ms is the time, since the previous event that named the same challenge (or since the
# window opened), that the aim cone contained that challenge's body. Nothing else is read as a response:
# private_track_ms on an event that names a challenge is not, and challenge time with no id is not.


@dataclass(frozen=True, slots=True)
class ChallengeSample:
    """One event's claim about one challenge, read from the player's timeline."""

    challenge_id: str | None
    match_id: str
    t_ms: int
    track_ms: float | None
    # When the event's own enemy could explain the aim, why: seen, heard, recent, unchecked or conflict.
    # Empty when the event names no enemy, or that enemy was unknowable too.
    competing: str
    # The server's verdict on the challenge's body (challenge_vision_state, challenge_audio_state), or None.
    channels: tuple[tuple[str, str], ...] | None = None
    # Secret-turn telemetry (version 3): this client's view yaw, and the turn this event marks.
    yaw_deg: float | None = None
    turn_index: int | None = None
    turn_sign: int | None = None


def _competing(event, profile) -> str:
    """Could a real enemy on this same event explain the aim? A challenge target is never one the client
    could know. If the event's own enemy was seen, heard or recently perceived, aim on it explains the
    sample; if the server named an enemy without saying whether this client could know it, nothing rules
    that out either."""
    if (
        event.enemy_id is None
        and event.information_state is None
        and event.vision_state is None
        and event.audio_state is None
        and event.since_perceived_ms is None
    ):
        return ""
    known = shot_knowledge(event, profile)
    return "" if known.status == UNKNOWABLE else known.cause


def challenge_samples(events: Iterable, profile) -> list[ChallengeSample]:
    """Every event that names a challenge, carries challenge time, or reports on a challenge's body, in
    the order given (a timeline)."""
    out = []
    for event in events:
        channels = challenge_channels(event)
        if event.challenge_id is None and event.challenge_track_ms is None and channels is None:
            continue
        out.append(ChallengeSample(
            event.challenge_id, event.match_id, event.t_ms, event.challenge_track_ms, _competing(event, profile), channels,
            event.view_yaw_deg, event.challenge_turn_index, event.challenge_turn_sign,
        ))
    return out


FOLLOWED = "followed"
NOT_FOLLOWED = "not_followed"
ABSTAINED = "abstained"
NO_SAMPLES = "no_samples"
UNPLANNED = "unplanned"


@dataclass
class ChallengeResult:
    """What one challenge showed for one player. Kept per challenge: two challenges are two results."""

    challenge_id: str
    plan: ChallengePlan | None
    status: str = NO_SAMPLES
    cause: str = ""  # why it abstained
    eligible: int = 0  # moments in the window with one agreed measurement, counted or not
    tracked: list[float] = field(default_factory=list)  # the time each counted moment added
    not_counted: dict[str, int] = field(default_factory=dict)
    knowledge: KnowledgeState | None = None
    verification: str = BY_PLAN
    verified: int = 0  # eligible moments whose own verdict made the body unknowable (PER_SAMPLE only)
    # PER_TURN only: each counted turn, the shuffle test, and the bar it was read against.
    turns: list[dict] = field(default_factory=list)
    p_value: float | None = None
    turn_bar_deg: float | None = None

    @property
    def total_ms(self) -> float:
        return exact_sum(self.tracked)

    def to_dict(self) -> dict:
        out = {
            "challenge_id": self.challenge_id,
            "plan": None if self.plan is None else self.plan.digest,
            "match_id": None if self.plan is None else self.plan.match_id,
            "status": self.status,
            "eligible_samples": self.eligible,
            "tracked_samples": len(self.tracked),
            "total_ms": self.total_ms,
            "not_counted": dict(sorted(self.not_counted.items())),
        }
        if self.cause:
            out["cause"] = self.cause
        if self.verification != BY_PLAN:
            out["verification"] = self.verification
            out["verified_samples"] = self.verified
        if self.verification == PER_TURN:
            out["turns"] = [dict(turn) for turn in self.turns]
            out["followed_turns"] = self.followed_turns
            out["p_value"] = self.p_value
            out["turn_bar_deg"] = self.turn_bar_deg
        return out

    @property
    def followed_turns(self) -> int:
        return sum(1 for turn in self.turns if turn["followed"])


def _skip(result: ChallengeResult, cause: str, n: int = 1) -> None:
    result.not_counted[cause] = result.not_counted.get(cause, 0) + n


def _moments(rows: list[ChallengeSample]):
    start = 0
    while start < len(rows):
        end = start + 1
        while end < len(rows) and (rows[end].match_id, rows[end].t_ms) == (rows[start].match_id, rows[start].t_ms):
            end += 1
        yield rows[start:end]
        start = end


def _judge(result: ChallengeResult, rows: list[ChallengeSample], plan: ChallengePlan, profile) -> None:
    if plan.spec.verification == PER_TURN:
        _judge_turns(result, rows, plan, profile)
        return
    if plan.spec.verification == PER_SAMPLE or any(sample.channels is not None for sample in rows):
        _judge_verified(result, rows, plan, profile)
        return
    state = challenge_knowledge(plan.spec, profile)
    result.knowledge = state
    previous = plan.start_ms
    for moment in _moments(rows):
        first = moment[0]
        if first.match_id != plan.match_id:
            _skip(result, "other_match", len(moment))
            continue
        if not plan.start_ms <= first.t_ms <= plan.end_ms:
            _skip(result, "outside_window", len(moment))
            continue
        # Every moment in the window that names the challenge restarts the clock, measured or not, so a
        # value can only be cut by it, never stretched.
        window = first.t_ms - previous
        previous = first.t_ms
        claims = [sample for sample in moment if sample.track_ms is not None]
        if not claims:
            _skip(result, "no_measurement", len(moment))
            continue
        if len({(sample.track_ms, sample.competing) for sample in claims}) != 1:
            # Events at one moment are one aim. When they disagree, nothing says which is right.
            _skip(result, "disagreed")
            continue
        result.eligible += 1
        value = min(claims[0].track_ms, window)
        if not value > 0:
            continue
        if claims[0].competing:
            _skip(result, claims[0].competing)
        elif state.status != UNKNOWABLE:
            _skip(result, state.cause)
        else:
            result.tracked.append(value)
    if state.status != UNKNOWABLE:
        result.status, result.cause = ABSTAINED, state.cause
    elif len(result.tracked) >= profile.hidden_track_min_samples and result.total_ms >= profile.hidden_track_min_ms:
        result.status = FOLLOWED
    else:
        result.status = NOT_FOLLOWED if result.eligible else NO_SAMPLES


def _judge_verified(result: ChallengeResult, rows: list[ChallengeSample], plan: ChallengePlan, profile) -> None:
    """Per-moment proof: a moment counts only when the server's own verdict for that moment says every
    declared channel was absent for the body. Used for every version 2 challenge, and for a version 1
    challenge whose events carry a verdict, which can then only lose samples.

    One moment where the body was seen or heard voids the challenge: from then on the client could know
    it, and the server should have ended it. Events at one moment that disagree about the body void it
    too: nothing says which is right. A moment with a declared channel unchecked does not count; with no
    moment verified at all, the challenge abstains.
    """
    spec = plan.spec
    required = profile.knowledge_channels
    result.verification = PER_SAMPLE
    previous = plan.start_ms
    exposed: KnowledgeState | None = None
    conflict = ""
    unverified = 0
    for moment in _moments(rows):
        first = moment[0]
        if first.match_id != plan.match_id:
            _skip(result, "other_match", len(moment))
            continue
        if not plan.start_ms <= first.t_ms <= plan.end_ms:
            _skip(result, "outside_window", len(moment))
            continue
        window = first.t_ms - previous
        previous = first.t_ms
        verdicts = {sample.channels for sample in moment}
        if len(verdicts) != 1:
            # One aim, two accounts of what this client could know about the body.
            conflict = conflict or f"events at {first.match_id} {first.t_ms} ms disagree about the challenge's body: " + " / ".join(
                sorted(", ".join(f"{name} {state}" for name, state in verdict) if verdict else "no verdict" for verdict in verdicts))
            _skip(result, "conflict", len(moment))
            continue
        state = body_knowledge(first.channels, spec.not_applicable, required)
        if state.status == KNOWN:
            exposed = exposed or state
            _skip(result, state.cause)
            continue
        claims = [sample for sample in moment if sample.track_ms is not None]
        if not claims:
            _skip(result, "no_measurement", len(moment))
            continue
        if len({(sample.track_ms, sample.competing) for sample in claims}) != 1:
            _skip(result, "disagreed")
            continue
        result.eligible += 1
        if state.status != UNKNOWABLE:
            unverified += 1
            _skip(result, state.cause)
            continue
        result.verified += 1
        value = min(claims[0].track_ms, window)
        if not value > 0:
            continue
        if claims[0].competing:
            _skip(result, claims[0].competing)
        else:
            result.tracked.append(value)
    absent = {name: CHANNEL_ABSENT for name in spec.defeats}
    absent.update({name: CHANNEL_NOT_APPLICABLE for name in spec.not_applicable})
    if conflict:
        result.knowledge = resolve({}, required, conflict)
        result.status, result.cause = ABSTAINED, "conflict"
    elif exposed is not None:
        result.knowledge = exposed
        result.status, result.cause = ABSTAINED, exposed.cause
    elif not result.verified and unverified:
        result.knowledge = resolve({name: state for name, state in absent.items() if name in spec.not_applicable}, required)
        result.status, result.cause = ABSTAINED, result.knowledge.cause
    else:
        result.knowledge = resolve(absent, required)
        if result.knowledge.status != UNKNOWABLE:
            result.status, result.cause = ABSTAINED, result.knowledge.cause
        elif len(result.tracked) >= profile.hidden_track_min_samples and result.total_ms >= profile.hidden_track_min_ms:
            result.status = FOLLOWED
        else:
            result.status = NOT_FOLLOWED if result.eligible else NO_SAMPLES


def _wrap(degrees: float) -> float:
    """An angle difference in [-180, 180)."""
    return (degrees + 180.0) % 360.0 - 180.0


class _Trace:
    """One client's view yaw through a window, unwrapped so a turn past 180 degrees is not a jump."""

    def __init__(self, points: list[tuple[int, float]], max_gap_ms: int):
        self.times: list[int] = []
        self.yaws: list[float] = []
        previous = None  # the last raw yaw
        for t_ms, yaw in points:
            self.yaws.append(yaw if previous is None else self.yaws[-1] + _wrap(yaw - previous))
            self.times.append(t_ms)
            previous = yaw
        self.max_gap_ms = max_gap_ms

    def at(self, t_ms: int) -> float | None:
        """The yaw at ``t_ms``, linear between the two samples around it; None if they are too far apart."""
        index = bisect.bisect_left(self.times, t_ms)
        if index < len(self.times) and self.times[index] == t_ms:
            return self.yaws[index]
        if index == 0 or index == len(self.times):
            return None
        before, after = self.times[index - 1], self.times[index]
        if after - before > self.max_gap_ms:
            return None
        share = (t_ms - before) / (after - before)
        return self.yaws[index - 1] + share * (self.yaws[index] - self.yaws[index - 1])

    def response(self, t_ms: int, sign: int, rule: TurnRule) -> float | None:
        """How much the yaw rate changed in the turn's direction at ``t_ms``, in degrees over the reaction window."""
        points = [self.at(t_ms - rule.lead_ms), self.at(t_ms), self.at(t_ms + rule.react_from_ms), self.at(t_ms + rule.react_to_ms)]
        if any(point is None for point in points):
            return None
        before, turn, start, end = points
        return sign * ((end - start) - (turn - before))


def _shuffle_seed(challenge_id: str) -> int:
    """The shuffles are drawn from the public challenge id, so anyone re-running the case draws the same ones."""
    return int.from_bytes(hashlib.sha256(b"fpsdet.turn-shuffle/1\0" + challenge_id.encode("utf-8")).digest()[:8], "big")


def _judge_turns(result: ChallengeResult, rows: list[ChallengeSample], plan: ChallengePlan, profile) -> None:
    """Version 3: did the aim turn with the body's secret turns?

    The per-moment verdict is read as in version 2: one moment where the body was seen or heard, or where
    events disagree about it, voids the challenge. A turn counts when its own verdict and every aim sample
    from its lead to the end of its reaction window say the body was unknowable, no real enemy on those
    events explains the aim, and the trace has no gap there. Then each counted turn's response is compared
    with responses at shuffled times in the same window, drawn from the challenge id. The challenge is
    followed when the summed response beats the shuffles at ``alpha`` and at least ``min_followed`` turns
    moved the aim more than ``min_turn_response_deg`` their own way.
    """
    spec = plan.spec
    rule = spec.turns
    required = profile.knowledge_channels
    result.verification = PER_TURN
    exposed: KnowledgeState | None = None
    conflict = ""
    aim: dict[int, tuple[float, bool]] = {}  # t_ms -> (yaw, clean): clean when the body was unknowable and no enemy explains it
    marks: dict[int, list[tuple[int, int, bool]]] = {}  # turn index -> (t_ms, sign, verified)
    for moment in _moments(rows):
        first = moment[0]
        if first.match_id != plan.match_id:
            _skip(result, "other_match", len(moment))
            continue
        if not plan.start_ms <= first.t_ms <= plan.end_ms:
            _skip(result, "outside_window", len(moment))
            continue
        verdicts = {sample.channels for sample in moment}
        if len(verdicts) != 1:
            conflict = conflict or f"events at {first.match_id} {first.t_ms} ms disagree about the challenge's body: " + " / ".join(
                sorted(", ".join(f"{name} {state}" for name, state in verdict) if verdict else "no verdict" for verdict in verdicts))
            _skip(result, "conflict", len(moment))
            continue
        state = body_knowledge(first.channels, spec.not_applicable, required)
        if state.status == KNOWN:
            exposed = exposed or state
            _skip(result, state.cause)
            continue
        verified = state.status == UNKNOWABLE
        result.verified += verified
        yaws = {sample.yaw_deg for sample in moment if sample.yaw_deg is not None}
        if len(yaws) > 1:
            _skip(result, "disagreed")
        elif yaws:
            aim[first.t_ms] = (yaws.pop(), verified and not any(sample.competing for sample in moment))
        for sample in moment:
            if sample.turn_index is not None and sample.turn_sign is not None:
                marks.setdefault(sample.turn_index, []).append((first.t_ms, sample.turn_sign, verified))
    absent = {name: CHANNEL_ABSENT for name in spec.defeats}
    absent.update({name: CHANNEL_NOT_APPLICABLE for name in spec.not_applicable})
    if conflict:
        result.knowledge = resolve({}, required, conflict)
        result.status, result.cause = ABSTAINED, "conflict"
        return
    if exposed is not None:
        result.knowledge = exposed
        result.status, result.cause = ABSTAINED, exposed.cause
        return
    result.knowledge = resolve(absent, required)
    if result.knowledge.status != UNKNOWABLE:
        result.status, result.cause = ABSTAINED, result.knowledge.cause
        return
    times = sorted(aim)
    trace = _Trace([(t_ms, aim[t_ms][0]) for t_ms in times], rule.max_trace_gap_ms)

    def clean(t_ms: int) -> bool:
        low = bisect.bisect_left(times, t_ms - rule.lead_ms)
        high = bisect.bisect_right(times, t_ms + rule.react_to_ms)
        return all(aim[t][1] for t in times[low:high])

    counted: list[tuple[int, int, int]] = []  # (index, t_ms, sign)
    for index in sorted(marks):
        if len(set(marks[index])) != 1:
            _skip(result, "turn_disagreed")
            continue
        t_ms, sign, verified = marks[index][0]
        if not verified:
            _skip(result, "unchecked")
        elif not plan.start_ms + rule.lead_ms <= t_ms <= plan.end_ms - rule.react_to_ms:
            _skip(result, "turn_outside_window")
        elif not clean(t_ms):
            _skip(result, "turn_not_clean")
        elif trace.response(t_ms, sign, rule) is None:
            _skip(result, "no_trace")
        else:
            counted.append((index, t_ms, sign))
    result.eligible = len(counted)
    if not marks and not aim:
        result.status = NO_SAMPLES
        return
    if len(counted) < rule.min_counted:
        result.status, result.cause = ABSTAINED, "too_few_turns" if result.verified else "unchecked"
        return
    # Each shuffle is a whole schedule drawn the way the secret draws one: a time in each counted turn's own
    # slot, and a fair direction. The aim of a player who cannot know the secret is independent of it, so the
    # real schedule is one more draw among these; and a reaction near a real turn gets a random direction here,
    # so it cancels instead of raising the null.
    rng = random.Random(_shuffle_seed(plan.challenge_id))
    first = plan.start_ms + rule.lead_ms
    slot = (plan.end_ms - rule.react_to_ms - first) // rule.count
    null_sums: list[float] = []
    for _ in range(rule.shuffles):
        total = 0.0
        for index, _t_ms, _sign in counted:
            value = None
            for _attempt in range(64):
                at = first + min(index, rule.count - 1) * slot + rng.randint(0, max(0, slot - rule.min_gap_ms))
                sign = rng.choice((1, -1))
                if clean(at):
                    value = trace.response(at, sign, rule)
                    if value is not None:
                        break
            if value is None:
                result.status, result.cause = ABSTAINED, "no_trace"
                return
            total += value
        null_sums.append(total)
    responses = [(index, t_ms, sign, trace.response(t_ms, sign, rule)) for index, t_ms, sign in counted]
    observed = exact_sum([value for *_rest, value in responses])
    result.p_value = (1 + sum(1 for value in null_sums if value >= observed)) / (1 + rule.shuffles)
    result.turn_bar_deg = rule.min_turn_response_deg
    result.turns = [
        {"index": index, "t_ms": t_ms, "sign": sign, "response_deg": round(value, 6), "followed": value > result.turn_bar_deg}
        for index, t_ms, sign, value in responses
    ]
    if result.p_value <= rule.alpha and result.followed_turns >= rule.min_followed:
        result.status = FOLLOWED
    else:
        result.status = NOT_FOLLOWED


def evaluate_challenges(
    subject_id: str,
    match_ids: Iterable[str],
    samples: list[ChallengeSample],
    registry: ChallengeRegistry | None,
    profile,
) -> tuple[list[ChallengeResult], int]:
    """One result per challenge this player named or was planned in the matches scored, by id; and how
    many events carried challenge time with no challenge id.

    A sample counts only when its challenge is planned for this player, in this match, inside its
    window; it is the one agreed measurement at its moment; no real enemy on the same event explains
    it; and the challenge's target was unknowable to this client. The bar is the hidden-mover bar, on
    each challenge alone: samples from different challenges never add up.
    """
    rows: dict[str, list[ChallengeSample]] = {}
    unlinked = 0
    for sample in samples:
        if sample.challenge_id is None:
            unlinked += sample.track_ms is not None  # a verdict on a body with no challenge named is reported by challenge_notes' caller
        else:
            rows.setdefault(sample.challenge_id, []).append(sample)
    if registry is not None:
        scored = set(match_ids)
        for plan in registry.for_subject(subject_id):
            if plan.match_id in scored:
                rows.setdefault(plan.challenge_id, [])
    results = []
    for challenge_id in sorted(rows):
        plan = None if registry is None else registry.get(challenge_id)
        result = ChallengeResult(challenge_id, plan)
        if plan is None:
            result.status = UNPLANNED
            _skip(result, "unplanned", len(rows[challenge_id]))
        elif plan.subject_id != subject_id:
            result.status, result.cause = ABSTAINED, "other_subject"
            _skip(result, "other_subject", len(rows[challenge_id]))
        else:
            _judge(result, rows[challenge_id], plan, profile)
        results.append(result)
    return results, unlinked


def challenge_notes(results: list[ChallengeResult], unlinked: int) -> list[str]:
    """Challenge telemetry fpsdet could not read, said once, so the operator can fix the emitter or the plans."""
    notes = []
    if unlinked:
        notes.append(f"{unlinked} events carried challenge_track_ms with no challenge_id. They were not read; check the emitter.")
    unplanned = [result for result in results if result.status == UNPLANNED]
    if unplanned:
        events = sum(result.not_counted.get("unplanned", 0) for result in unplanned)
        notes.append(
            f"{events} events named {len(unplanned)} challenges this run has no plan for. They were not read; "
            "score with the plan files (--challenges)."
        )
    foreign = sum(1 for result in results if result.cause == "other_subject")
    if foreign:
        notes.append(f"{foreign} challenges named on this player's events were planned for another player. They were not read.")
    for result in results:
        if result.verification not in (PER_SAMPLE, PER_TURN) or result.plan is None:
            continue
        name = result.challenge_id
        if result.cause == "conflict":
            notes.append(f"Challenge {name}: {result.knowledge.conflict}. It does not count; check the emitter.")
        elif result.status == ABSTAINED and result.cause in ("seen", "heard"):
            moments = result.not_counted.get(result.cause, 0)
            notes.append(f"Challenge {name}: the server reported its body {result.cause} at {moments} moments, so this client could know it. It does not count; check the placement.")
        unverified = result.not_counted.get("unchecked", 0)
        if unverified and result.verification == PER_SAMPLE:
            notes.append(f"Challenge {name}: {unverified} moments had no complete vision and audio verdict for its body. They were not counted.")
        if result.verification == PER_TURN and result.cause == "too_few_turns":
            notes.append(f"Challenge {name}: {result.eligible} of its turns could be read, and {result.plan.spec.turns.min_counted} are needed. It does not count; check the turn markers and the view yaw trace.")
        elif result.verification == PER_TURN and result.cause == "no_trace":
            notes.append(f"Challenge {name}: the view yaw trace had too many gaps to draw shuffled times. It does not count; check the emitter.")
    return notes


# Findings. A followed challenge is a review, by the bar above, on that challenge alone. The legacy
# private replay keeps its own bar and reason, and becomes the same kind of observation.

LEGACY_ORIGIN = "legacy_private_replay"
PLANNED_ORIGIN = "planned"
# What private_track_ms always described: a replayed body placed where this client's line-of-sight and
# audio queries fail. The adapter reads it as that type, with no plan, window or commitment.
LEGACY_SPEC = OCCLUDED_MOTION_REPLAY


def legacy_challenge_id(aim_key: str) -> str:
    """The identity the legacy adapter gives one aim key's private replay samples. It is visibly not a
    planned id: no plan, window or commitment ever existed for it."""
    return f"{LEGACY_ORIGIN}:{aim_key}"


def _knowledge(spec: ChallengeSpec, profile) -> dict:
    return {"required": list(profile.knowledge_channels), "defeated": list(spec.defeats), "not_applicable": list(spec.not_applicable)}


def legacy_evidence(aim_key: str, numbers: Mapping, profile) -> dict:
    """The legacy private replay finding (signals.private_finding) as challenge evidence."""
    return {
        "challenge": {
            "challenge_id": legacy_challenge_id(aim_key),
            "origin": LEGACY_ORIGIN,
            "type": LEGACY_SPEC.challenge_type,
            "version": None,
            "commitment": None,
            "plan": None,
            "window": None,
        },
        "linkage": "private_track_ms",
        "scope": "aim_key",  # every match in the window on one aim key, as the legacy check always added them
        "eligible_samples": None,
        "tracked_samples": numbers["shots"],
        "total_ms": numbers["total_ms"],
        "knowledge": _knowledge(LEGACY_SPEC, profile),
        "thresholds": {"min_samples": numbers["thresholds"]["min_shots"], "min_total_ms": numbers["thresholds"]["min_total_ms"]},
    }


def legacy_context(state: KnowledgeState, not_counted: Mapping) -> dict:
    return {
        "knowledge": {
            **state.to_dict(),
            "basis": "private_track_ms: placed where this client's line-of-sight and audio queries fail",
            "not_counted": dict(sorted(not_counted.items())),
        },
        "legacy": "private_track_ms names no challenge: there is no plan, window or commitment, and time from every match on this aim key adds up",
    }


def challenge_line(result: ChallengeResult) -> str:
    plan = result.plan
    if result.verification == PER_TURN:
        rule = plan.spec.turns
        return (
            f"aim turned with challenge {result.challenge_id}'s secret turns (occluded motion replay in {plan.match_id}): "
            f"{result.followed_turns} of {len(result.turns)} turns followed, p = {result.p_value:.4f} against {rule.shuffles} shuffled timings"
        )
    return (
        f"aim stayed on challenge {result.challenge_id} (occluded motion replay in {plan.match_id}) "
        f"for {result.total_ms:.0f} ms across {len(result.tracked)} samples"
    )


def challenge_evidence(result: ChallengeResult, profile) -> dict:
    """A followed planned challenge, as evidence: the challenge it binds to, by id, plan digest and
    commitment; its window; what was counted; the knowledge it needed, and how it was known; the bar."""
    plan = result.plan
    knowledge = _knowledge(plan.spec, profile)
    if result.verification in (PER_SAMPLE, PER_TURN):
        knowledge = {**knowledge, "verification": result.verification, "verified_samples": result.verified}
    if result.verification == PER_TURN:
        return {
            "challenge": {
                "challenge_id": plan.challenge_id,
                "origin": PLANNED_ORIGIN,
                "type": plan.challenge_type,
                "version": plan.version,
                "commitment": plan.commitment,
                "plan": plan.digest,
                "window": {"start_ms": plan.start_ms, "end_ms": plan.end_ms},
            },
            "linkage": "challenge_id",
            "scope": "challenge",
            "turns": [dict(turn) for turn in result.turns],
            "followed_turns": result.followed_turns,
            "p_value": result.p_value,
            "turn_bar_deg": result.turn_bar_deg,
            "knowledge": knowledge,
            "thresholds": plan.spec.turns.to_dict(),
        }
    return {
        "challenge": {
            "challenge_id": plan.challenge_id,
            "origin": PLANNED_ORIGIN,
            "type": plan.challenge_type,
            "version": plan.version,
            "commitment": plan.commitment,
            "plan": plan.digest,
            "window": {"start_ms": plan.start_ms, "end_ms": plan.end_ms},
        },
        "linkage": "challenge_id",
        "scope": "challenge",  # this challenge's own samples; never added to another challenge's
        "eligible_samples": result.eligible,
        "tracked_samples": len(result.tracked),
        "total_ms": result.total_ms,
        "knowledge": knowledge,
        "thresholds": {"min_samples": profile.hidden_track_min_samples, "min_total_ms": profile.hidden_track_min_ms},
    }


def challenge_context(result: ChallengeResult, results: Iterable[ChallengeResult]) -> dict:
    """Why it counted, what was left out, and the player's other challenges in this run. Not identity."""
    series: dict[str, int] = {}
    for other in results:
        if other.plan is not None and other.plan.subject_id == result.plan.subject_id:
            series[other.status] = series.get(other.status, 0) + 1
    return {
        "knowledge": {
            **result.knowledge.to_dict(),
            "basis": (f"challenge_track_ms on events naming {result.challenge_id}; {result.plan.spec.name} defeats {', '.join(result.plan.spec.defeats)}"
                      + ("; the server's own vision and audio verdict for the body at every counted moment" if result.verification in (PER_SAMPLE, PER_TURN) else "")
                      + ("; view_yaw_deg against the body's secret turns, and against shuffled times drawn from the challenge id" if result.verification == PER_TURN else "")),
            "not_counted": dict(sorted(result.not_counted.items())),
        },
        "series": {"planned": sum(series.values()), **dict(sorted(series.items()))},
    }


def case_problems(case: Mapping, registry: ChallengeRegistry) -> list[str]:
    """Does each planned challenge finding in a serialized case match its public plan? Checks the id,
    plan digest, commitment, type, version, window, match and player. Needs no secret."""
    problems = []
    for obs in (case.get("evidence") or {}).get("observations") or []:
        if obs.get("family") != "challenge":
            continue
        claim = (obs.get("evidence") or {}).get("challenge") or {}
        if claim.get("origin") != PLANNED_ORIGIN:
            continue
        name = claim.get("challenge_id")
        plan = registry.get(name) if isinstance(name, str) else None
        if plan is None:
            problems.append(f"{case.get('player_id')}: {name} is in no plan given")
            continue
        expected = {
            "commitment": plan.commitment,
            "plan": plan.digest,
            "type": plan.challenge_type,
            "version": plan.version,
            "window": {"start_ms": plan.start_ms, "end_ms": plan.end_ms},
        }
        for key, value in expected.items():
            if claim.get(key) != value:
                problems.append(f"{case.get('player_id')}: {name} {key} does not match its plan")
        if obs.get("subject_id") != plan.subject_id or case.get("player_id") != plan.subject_id:
            problems.append(f"{case.get('player_id')}: {name} was planned for another player")
        if list(obs.get("match_ids") or []) != [plan.match_id]:
            problems.append(f"{case.get('player_id')}: {name} is cited for other matches than its plan's")
    return problems
