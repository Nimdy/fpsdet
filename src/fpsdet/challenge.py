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

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .evidence import canonical_json
from .knowledge import CHANNEL_ABSENT, CHANNEL_NOT_APPLICABLE, UNKNOWABLE, KnowledgeState, resolve, shot_knowledge
from .statsutil import exact_sum

# The domain prefix of every keyed derivation (fpsdet.challenge_plan). A new prefix is a new recipe.
DERIVATION_RECIPE = "fpsdet.challenge/1"
# The commitment to one realization.
COMMITMENT_RECIPE = "fpsdet.challenge-commitment/1"
# The digest of one public plan record.
PLAN_RECIPE = "fpsdet.challenge-plan/1"
# A plan file: one match, its challenges, and the budget they were scheduled under.
PLAN_FILE_FORMAT = "fpsdet.challenge-plans/1"

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

SPECS: dict[tuple[str, int], ChallengeSpec] = {(OCCLUDED_MOTION_REPLAY.challenge_type, OCCLUDED_MOTION_REPLAY.version): OCCLUDED_MOTION_REPLAY}
# The version a new plan uses, by type.
CURRENT: dict[str, ChallengeSpec] = {OCCLUDED_MOTION_REPLAY.challenge_type: OCCLUDED_MOTION_REPLAY}


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
    """Every event that names a challenge or carries challenge time, in the order given (a timeline)."""
    return [
        ChallengeSample(event.challenge_id, event.match_id, event.t_ms, event.challenge_track_ms, _competing(event, profile))
        for event in events
        if event.challenge_id is not None or event.challenge_track_ms is not None
    ]


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
        return out


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
            unlinked += 1
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
    return notes
