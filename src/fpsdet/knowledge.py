"""Client knowledge: could this legitimate client know this target at this moment?

Every information check asks the same question, and this module is the one place that answers it,
with one of three statuses:

- ``known``: at least one legal channel exposed the target to this client.
- ``unknowable``: every channel the game declares (``GameProfile.knowledge_channels``) was checked by
  the server and none exposed it.
- ``unknown``: anything else. A declared channel nobody checked, or telemetry that contradicts itself.

Unchecked is not absent. A channel the server did not report is ``unchecked``, and one unchecked
declared channel makes the answer ``unknown``. Information checks act only on ``unknowable``, so
missing telemetry can only make them abstain, never make them stronger.

Channels with telemetry today are ``vision`` and ``audio`` (from ``information_state``, or per channel
from ``vision_state`` and ``audio_state``) and ``recent_perception`` (from ``since_perceived_ms`` and the
profile's ``hidden_grace_ms``). Recent perception only ever adds knowledge: inside the grace window the
target is known; outside it, or when not sent, it decides nothing unless the profile declares it.
``FUTURE_CHANNELS`` can be declared by a game that has them, but no event field reports them yet, so
declaring one makes every information check abstain until it is reported.

The wire and the picture are a second axis, not a status: what the server sent this client's software
against what its official client drew for a person to see. ``presentation`` describes that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

KNOWN = "known"
UNKNOWN = "unknown"
UNKNOWABLE = "unknowable"
STATUSES = (KNOWN, UNKNOWN, UNKNOWABLE)

# What one channel said about the target.
CHANNEL_KNOWN = "known"
CHANNEL_ABSENT = "absent"
CHANNEL_UNCHECKED = "unchecked"
CHANNEL_NOT_APPLICABLE = "not_applicable"
CHANNEL_STATES = (CHANNEL_KNOWN, CHANNEL_ABSENT, CHANNEL_UNCHECKED, CHANNEL_NOT_APPLICABLE)

REPORTED_CHANNELS = ("vision", "audio", "recent_perception")
FUTURE_CHANNELS = ("team_share", "radar", "objective", "ability", "spectator")
CHANNELS = REPORTED_CHANNELS + FUTURE_CHANNELS
# What information_state "unknowable" has always meant: the server's line-of-sight and audio queries both failed.
DEFAULT_CHANNELS = ("vision", "audio")
# The values an emitter may send in vision_state and audio_state.
EVENT_CHANNEL_STATES = (CHANNEL_KNOWN, CHANNEL_ABSENT, CHANNEL_UNCHECKED)

# The legacy information_state, as channels. "visible" and "audio" each say one channel exposed the
# enemy and nothing about the other; "unknowable" says both were checked and failed.
LEGACY_STATES: dict[str, dict[str, str]] = {
    "visible": {"vision": CHANNEL_KNOWN},
    "audio": {"audio": CHANNEL_KNOWN},
    "unknowable": {"vision": CHANNEL_ABSENT, "audio": CHANNEL_ABSENT},
}


@dataclass(frozen=True, slots=True)
class KnowledgeState:
    """What this client could know about one target at one moment, and why."""

    status: str
    channels: tuple[tuple[str, str], ...]  # every channel considered, by name
    required: tuple[str, ...]  # the channels the game declares
    conflict: str = ""  # set when the telemetry contradicts itself; the status is then unknown

    def channel(self, name: str) -> str:
        return dict(self.channels).get(name, CHANNEL_UNCHECKED)

    @property
    def perceived_now(self) -> bool:
        """Known because the client could see or hear the target at this moment, not only from memory."""
        return self.channel("vision") == CHANNEL_KNOWN or self.channel("audio") == CHANNEL_KNOWN

    @property
    def cause(self) -> str:
        """One word for why: seen, heard, recent, unknowable, conflict, or unchecked."""
        if self.conflict:
            return "conflict"
        if self.status == KNOWN:
            if self.channel("vision") == CHANNEL_KNOWN:
                return "seen"
            return "heard" if self.channel("audio") == CHANNEL_KNOWN else "recent"
        return UNKNOWABLE if self.status == UNKNOWABLE else "unchecked"

    def to_dict(self) -> dict:
        out = {"status": self.status, "channels": dict(self.channels), "required": list(self.required)}
        if self.conflict:
            out["conflict"] = self.conflict
        return out


def resolve(channels: Mapping[str, str], required: Iterable[str], conflict: str = "") -> KnowledgeState:
    """The status the channels imply. Known if any channel exposed the target; unknowable only if every
    declared channel was checked and absent (or cannot carry this target); unknown otherwise."""
    required = tuple(required)
    states = {name: channels.get(name, CHANNEL_UNCHECKED) for name in (*channels, *required)}
    for name, state in states.items():
        if state not in CHANNEL_STATES:
            raise ValueError(f"channel {name} cannot be {state!r}")
    ordered = tuple(sorted(states.items()))
    if conflict:
        return KnowledgeState(UNKNOWN, ordered, required, conflict)
    if any(state == CHANNEL_KNOWN for state in states.values()):
        return KnowledgeState(KNOWN, ordered, required)
    if required and all(states[name] in (CHANNEL_ABSENT, CHANNEL_NOT_APPLICABLE) for name in required):
        return KnowledgeState(UNKNOWABLE, ordered, required)
    return KnowledgeState(UNKNOWN, ordered, required)


def shot_channels(event) -> tuple[dict[str, str], str]:
    """What the server said about the enemy this shot was about (``enemy_id``), and any contradiction.

    ``information_state`` gives the legacy channels; ``vision_state`` and ``audio_state``, when sent,
    give one channel each. Where both speak about a channel and disagree, nothing says which is right.
    """
    channels = dict(LEGACY_STATES.get(event.information_state, {}))
    conflict = ""
    for name, said in (("vision", event.vision_state), ("audio", event.audio_state)):
        if said is None:
            continue
        legacy = channels.get(name)
        if legacy is not None and legacy != said and not conflict:
            conflict = f"information_state {event.information_state} has {name} {legacy}, but {name}_state is {said}"
        channels[name] = said
    return channels, conflict


def recent_perception(event, grace_ms: float) -> str:
    """Known inside the grace window after this client last saw or heard the target; absent after it."""
    if event.since_perceived_ms is None:
        return CHANNEL_UNCHECKED
    return CHANNEL_KNOWN if event.since_perceived_ms < grace_ms else CHANNEL_ABSENT


def shot_knowledge(event, profile) -> KnowledgeState:
    """Could this client know the enemy this shot was about, at the time of the shot?"""
    channels, conflict = shot_channels(event)
    channels["recent_perception"] = recent_perception(event, profile.hidden_grace_ms)
    return resolve(channels, profile.knowledge_channels, conflict)


def tracked_knowledge(event, profile) -> KnowledgeState:
    """Could this client know the enemy ``hidden_track_ms`` says the aim stayed on?

    By that field's definition the server's line-of-sight and audio queries both failed for it, so it
    supplies vision and audio where the shot says nothing. Where the shot says the enemy was seen or
    heard, the enemy was known. Where the shot says a channel was not checked, the field's claim that
    it was cannot both be true, and the answer is unknown.
    """
    channels, conflict = shot_channels(event)
    for name in ("vision", "audio"):
        said = channels.get(name)
        if said is None:
            channels[name] = CHANNEL_ABSENT
        elif said == CHANNEL_UNCHECKED and not conflict:
            conflict = f"hidden_track_ms says {name} was checked, but {name}_state is unchecked"
    channels["recent_perception"] = recent_perception(event, profile.hidden_grace_ms)
    return resolve(channels, profile.knowledge_channels, conflict)


def private_knowledge(profile) -> KnowledgeState:
    """Could this client know the server's private replay body? By ``private_track_ms``'s definition the
    server placed it where this client's line-of-sight and audio queries both fail, and it was never
    perceivable, so recent perception cannot apply. The shot's own labels are about another enemy."""
    channels = {"vision": CHANNEL_ABSENT, "audio": CHANNEL_ABSENT, "recent_perception": CHANNEL_NOT_APPLICABLE}
    return resolve(channels, profile.knowledge_channels)


@dataclass(frozen=True, slots=True)
class Presentation:
    """The wire and the picture for one shot's enemy.

    The wire is the snapshot the server sent: data the client's software holds. The picture is where
    the official client draws that enemy, one interpolation delay earlier: what a person can see. Data
    in memory is not information a person perceived, and the wire check is the gap between the two.
    """

    wire_error_deg: float
    picture_error_deg: float
    interp_delay_ms: float

    def to_dict(self) -> dict:
        return {
            "client_data": "the snapshot the server sent (wire)",
            "human_perception": "the position the official client draws (picture), one interpolation delay earlier",
        }


def presentation(event) -> Presentation | None:
    """The shot's wire and picture, or None unless the server sent all three numbers. The wire check
    still skips a delay that has not started and a negative error, as it always has."""
    if event.wire_error_deg is None or event.picture_error_deg is None or event.interp_delay_ms is None:
        return None
    return Presentation(event.wire_error_deg, event.picture_error_deg, event.interp_delay_ms)


def profile_channels(names) -> tuple[str, ...]:
    """The declared channels of a profile, checked. At least one; each one a channel fpsdet knows."""
    channels = tuple(dict.fromkeys(str(name) for name in names))
    if not channels:
        raise ValueError("knowledge_channels needs at least one channel")
    unknown = [name for name in channels if name not in CHANNELS]
    if unknown:
        raise ValueError(f"unknown knowledge channel {unknown[0]!r}; the channels are {', '.join(CHANNELS)}")
    return channels
