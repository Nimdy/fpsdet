"""Planning challenges with the server secret. The only module of fpsdet that reads it.

Scoring never imports this module (the provenance manifest test fails if detection code does), so a
reviewer's machine that scores and verifies cases never needs the secret.

The construction is HMAC-SHA256 (RFC 2104) used as a pseudorandom function, keyed with the server
secret: at least 32 random bytes that never leave the game server. Nothing here is new cryptography.

Recipe ``fpsdet.challenge/1``. Every input is UTF-8 text, framed as

    field(x) = the byte length of x as 4 bytes, big-endian, then x

and a message is

    field("fpsdet.challenge/1") field(purpose) field(game_id) field(match_id) field(subject_id)
    field(nonce) field(challenge_type) field(version) field(index)

with ``version`` and ``index`` as decimal text. The purposes:

- ``id``: the challenge id is ``ch-`` and the first 24 hex digits of HMAC(secret, message), with
  index the challenge's counter. Public.
- ``realization``: the realization material is HMAC(secret, message), with index the counter. 32
  bytes. Secret: the game server derives it, fpsdet never writes it.
- ``schedule``: draw i is HMAC(secret, message) read as a big-endian integer, with index i. The
  windows of every challenge one subject gets in one match come from these draws.

Each parameter of a realization is ``low + P mod (high - low + 1)``, where P is
HMAC(material, field("fpsdet.challenge/1") field("parameter") field(name)) read as a big-endian
integer. The modulo bias is below 2**-200.

The commitment is ``sha256:`` and the hex SHA-256 of

    field("fpsdet.challenge-commitment/1") field(canonical JSON of the plan's other public fields) field(material)

What the commitment proves, and what it does not, is in docs/challenges.md.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .challenge import (
    COMMITMENT_RECIPE,
    CURRENT,
    DERIVATION_RECIPE,
    NONCE,
    Budget,
    ChallengeError,
    ChallengePlan,
    ChallengeSpec,
    PlanFile,
    undefeated,
)
from .evidence import canonical_json

MIN_SECRET_BYTES = 32
SECRET_ENV = "FPSDET_CHALLENGE_SECRET"


class SecretError(ValueError):
    """The secret could not be read. The message never contains any of it."""


class ServerSecret:
    """The server's challenge key. It prints, formats and logs as redacted, and it is never pickled."""

    __slots__ = ("_key",)

    def __init__(self, key: bytes):
        if not isinstance(key, (bytes, bytearray)) or len(key) < MIN_SECRET_BYTES:
            raise SecretError(f"the challenge secret must be at least {MIN_SECRET_BYTES} random bytes")
        self._key = bytes(key)

    def mac(self, message: bytes) -> bytes:
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def __repr__(self) -> str:
        return "ServerSecret(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a ServerSecret is not pickled or copied out of the process")


def secret_from_hex(text: str) -> ServerSecret:
    cleaned = "".join(str(text).split())
    try:
        key = bytes.fromhex(cleaned)
    except ValueError:
        raise SecretError("the challenge secret must be written as hex digits") from None
    return ServerSecret(key)


def load_secret(path: str | Path | None = None, environ: Mapping[str, str] | None = None) -> tuple[ServerSecret, list[str]]:
    """The secret from a file the operator names (``/dev/fd/N`` works), or from ``FPSDET_CHALLENGE_SECRET``.
    Either way it is hex. Returns the secret and any warnings; neither ever contains any of it."""
    environ = os.environ if environ is None else environ
    from_env = environ.get(SECRET_ENV)
    if path is not None and from_env:
        raise SecretError(f"pass the secret in a file or in {SECRET_ENV}, not both")
    warnings = []
    if path is not None:
        target = Path(path)
        try:
            mode = target.stat().st_mode
            text = target.read_text(encoding="ascii")
        except (OSError, UnicodeDecodeError):
            raise SecretError(f"cannot read the challenge secret file {target}") from None
        if stat.S_ISREG(mode) and mode & (stat.S_IRWXG | stat.S_IRWXO):
            warnings.append(f"{target} can be read by other users; chmod 600 it")
        return secret_from_hex(text), warnings
    if from_env:
        return secret_from_hex(from_env), warnings
    raise SecretError(f"no challenge secret: pass --secret-file or set {SECRET_ENV}")


def new_secret_file(path: str | Path) -> None:
    """Write a new random secret, as hex, to a file that does not exist yet, readable by its owner only."""
    target = Path(path)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(os.urandom(MIN_SECRET_BYTES).hex() + "\n")


def _field(value: str | bytes) -> bytes:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return len(data).to_bytes(4, "big") + data


def message(purpose: str, game_id: str, match_id: str, subject_id: str, nonce: str, spec: ChallengeSpec, index: int) -> bytes:
    """The canonical input of one keyed derivation (``fpsdet.challenge/1``)."""
    parts = (DERIVATION_RECIPE, purpose, game_id, match_id, subject_id, nonce, spec.challenge_type, str(spec.version), str(index))
    return b"".join(_field(part) for part in parts)


@dataclass(frozen=True)
class Realization:
    """What the game server needs to run one challenge. Secret until the challenge is over, and never
    written by fpsdet: it has no to_dict, and it prints without its values."""

    challenge_id: str
    material: bytes = field(repr=False)
    parameters: tuple[tuple[str, int], ...] = field(repr=False)

    def parameter(self, name: str) -> int:
        for key, value in self.parameters:
            if key == name:
                return value
        raise KeyError(name)

    def __repr__(self) -> str:
        return f"Realization(challenge_id={self.challenge_id!r}, <secret>)"


@dataclass(frozen=True)
class _Context:
    game_id: str
    match_id: str
    subject_id: str
    nonce: str
    spec: ChallengeSpec

    def mac(self, secret: ServerSecret, purpose: str, index: int) -> bytes:
        return secret.mac(message(purpose, self.game_id, self.match_id, self.subject_id, self.nonce, self.spec, index))


def _parameters(spec: ChallengeSpec, material: bytes) -> tuple[tuple[str, int], ...]:
    out = []
    for name, low, high in spec.parameters:
        draw = hmac.new(material, _field(DERIVATION_RECIPE) + _field("parameter") + _field(name), hashlib.sha256).digest()
        out.append((name, low + int.from_bytes(draw, "big") % (high - low + 1)))
    return tuple(out)


def commitment(committed_fields: Mapping, material: bytes) -> str:
    body = _field(COMMITMENT_RECIPE) + _field(canonical_json(dict(committed_fields))) + _field(material)
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _windows(secret: ServerSecret, context: _Context, budget: Budget) -> list[tuple[int, int]]:
    """``budget.count`` windows inside the play window, at least ``cooldown_ms`` apart, with lengths and
    gaps drawn from the secret. The leftover time is cut at sorted random points, so each gap is as
    likely as any other and nothing about the next window follows from the last."""
    draws = (int.from_bytes(context.mac(secret, "schedule", index), "big") for index in range(2 * budget.count))
    span = budget.max_duration_ms - budget.min_duration_ms + 1
    lengths = [budget.min_duration_ms + next(draws) % span for _ in range(budget.count)]
    slack = budget.to_ms - budget.from_ms - sum(lengths) - (budget.count - 1) * budget.cooldown_ms
    cuts = sorted(next(draws) % (slack + 1) for _ in range(budget.count))
    windows: list[tuple[int, int]] = []
    previous_cut = 0
    for length, cut in zip(lengths, cuts):
        start = (budget.from_ms if not windows else windows[-1][1] + budget.cooldown_ms) + cut - previous_cut
        windows.append((start, start + length))
        previous_cut = cut
    return windows


def derive(secret: ServerSecret, game_id: str, match_id: str, subject_id: str, nonce: str, spec: ChallengeSpec, budget: Budget) -> list[tuple[ChallengePlan, Realization]]:
    """Every challenge one subject gets in one match: the public plan and the secret realization of each."""
    context = _Context(game_id, match_id, subject_id, nonce, spec)
    out = []
    for counter, (start, end) in enumerate(_windows(secret, context, budget)):
        challenge_id = "ch-" + context.mac(secret, "id", counter).hex()[:24]
        material = context.mac(secret, "realization", counter)
        public = ChallengePlan(challenge_id, spec.challenge_type, spec.version, game_id, match_id, subject_id, counter, nonce, start, end, "")
        plan = ChallengePlan(**{**public.committed_fields(), "commitment": commitment(public.committed_fields(), material)})
        out.append((plan, Realization(challenge_id, material, _parameters(spec, material))))
    return out


def plan_match(
    secret: ServerSecret,
    profile,
    match_id: str,
    subjects: Iterable[str],
    budget: Budget,
    *,
    nonce: str | None = None,
    spec: ChallengeSpec | None = None,
) -> PlanFile:
    """The public plan for one match. Without a nonce a fresh random one is drawn and written into the
    plan, so the same match id used twice never repeats its challenges; with one, the plan is reproducible."""
    spec = spec or CURRENT["occluded_motion_replay"]
    subjects = list(subjects)
    if not subjects or any(not isinstance(subject, str) or not subject for subject in subjects):
        raise ChallengeError("name at least one player, each a non-empty id")
    if len(set(subjects)) != len(subjects):
        raise ChallengeError("a player is named twice")
    if not isinstance(match_id, str) or not match_id:
        raise ChallengeError("match_id must be a non-empty string")
    problems = budget.problems()
    if problems:
        raise ChallengeError(problems[0])
    missing = undefeated(spec, profile)
    if missing:
        raise ChallengeError(
            f"{spec.name} does not defeat {', '.join(missing)}, which the profile declares; its results could never count, so it is not planned"
        )
    if budget.min_duration_ms < profile.hidden_track_min_ms:
        raise ChallengeError(f"a window shorter than {profile.hidden_track_min_ms:g} ms could never reach the review bar")
    nonce = os.urandom(16).hex() if nonce is None else nonce
    if not NONCE.fullmatch(nonce):
        raise ChallengeError("nonce must be 16 to 64 lowercase hex digits")
    plans: list[ChallengePlan] = []
    materials: set[bytes] = set()
    for subject in subjects:
        for plan, realization in derive(secret, profile.game_id, match_id, subject, nonce, spec, budget):
            if realization.material in materials:
                # HMAC-SHA256 outputs for distinct inputs do not collide in practice. If they did, stop.
                raise ChallengeError("two challenges derived the same realization; stop and check the secret and the recipe")
            materials.add(realization.material)
            plans.append(plan)
    return PlanFile(profile.game_id, match_id, nonce, spec, budget, tuple(plans))


def realize(secret: ServerSecret, plan: ChallengePlan, budget: Budget) -> Realization:
    """For the game server, in its own trusted process: the realization behind one public plan record.
    Raises if the record is not what this secret plans."""
    for derived, realization in derive(secret, plan.game_id, plan.match_id, plan.subject_id, plan.nonce, plan.spec, budget):
        if derived == plan:
            return realization
    raise ChallengeError(f"{plan.challenge_id} is not a challenge this secret planned")


def reproduce(secret: ServerSecret, plan_file: PlanFile) -> list[str]:
    """Privileged check: does the secret plan exactly this file? Each record's id, window and commitment
    is derived again and compared. Returns the records that differ, by id and field; never a value
    that came from the secret."""
    problems = []
    subjects = sorted({plan.subject_id for plan in plan_file.plans})
    expected: dict[tuple[str, int], ChallengePlan] = {}
    for subject in subjects:
        for plan, _realization in derive(secret, plan_file.game_id, plan_file.match_id, subject, plan_file.nonce, plan_file.spec, plan_file.budget):
            expected[(subject, plan.counter)] = plan
    seen = set()
    for plan in plan_file.plans:
        seen.add((plan.subject_id, plan.counter))
        want = expected.get((plan.subject_id, plan.counter))
        if want is None:
            problems.append(f"{plan.challenge_id}: this secret plans no challenge {plan.counter} for {plan.subject_id}")
            continue
        for name in ("challenge_id", "start_ms", "end_ms", "commitment"):
            if getattr(plan, name) != getattr(want, name):
                problems.append(f"{plan.challenge_id}: {name} is not what this secret plans")
    for key in sorted(set(expected) - seen):
        problems.append(f"{key[0]}: challenge {key[1]} is planned by this secret but missing from the file")
    return problems
