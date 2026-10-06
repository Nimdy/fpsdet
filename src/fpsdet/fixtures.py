"""Controlled fixtures beside the planted demo: plants for the six detectors it never plants, honest twins
for three that had none, and probes for the eligibility statuses it never reaches.

Planted by construction. They qualify code: does a planted behaviour trip the detector built for it, does
an honest twin built to look like it stay quiet, and does detector eligibility say what each probe was
built to show? They are never a rate, never mixed into a real-world measurement, and never calibration.

The world is its own: a profile with a fire-interval rule, a server-paced gun, recoil learned from humans
(one other gun has a designer floor) and two declared metrics, scored against a baseline frozen from 40 humans. Every
other player differs from a human in one way, on purpose. Standard library only, and the same on every run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .baseline import build_cohorts
from .models import Case, Event, GameProfile, HistoryWindow
from .parse import profile_from_dict
from .pipeline import run_score
from .summarize import summarize

GAME = "fixtures"
PROFILE = {
    "game_id": GAME,
    "notes": "Controlled fixtures (fpsdet.fixtures). Not a real game.",
    "min_shots": 40,
    "min_hits_for_headshot": 25,
    "min_cohort_players": 30,
    "aim_group": "weapon_class",
    "recoil_pattern": "learnable",
    "recoil_min_run": 10,
    "recoil_min_spray_index": 3,
    "weapons": {
        "rifle": {"min_shot_interval_ms": 90, "interval_slack_ms": 15, "min_intervals": 20, "min_violations": 10, "min_violation_rate": 0.3},
        "smg": {"min_shot_interval_ms": 60, "server_paced": True},
    },
    "recoil_floors": [{"weapon_id": "fx-floored", "mod_set": [], "min_pitch_deg": 1.0}],
    "extra_metrics": [
        {"name": "kills_per_min", "source": "kills_per_min", "kind": "primary", "direction": "high", "min_samples": 10, "group_by": ["weapon_class"]},
        {"name": "reaction_ms", "source": "reaction_ms", "kind": "supporting", "direction": "low", "min_samples": 10, "group_by": ["weapon_class"]},
    ],
}
HUMANS = 40
ALL_FIELDS = frozenset({"hitbox", "distance", "geometry", "view", "acquire", "recoil", "extras"})

# Each planted player, and the detectors it was built to trip.
PLANTED = {
    "fx-geometry": ("geometry_rate",),
    "fx-snaps": ("view_snaps",),
    "fx-acquire": ("acquire_timing",),
    "fx-kills": ("extra",),
    "fx-reaction": ("supporting_extra",),
    "fx-recoil": ("recoil_learned",),
}
# Honest players built to look like a plant without the cheat, by the detector they must not trip.
TWINS = {
    "geometry_rate": ("fx-geometry-twin",),
    "view_snaps": ("fx-snaps-twin",),
    "acquire_timing": ("fx-acquire-twin",),
    "extra": ("fx-kills-twin",),
    "supporting_extra": ("fx-reaction-twin",),
    "recoil_learned": ("fx-recoil-twin",),
    "fire_rate": ("fx-trigger-twin", "fx-held-trigger"),
    "metronome": ("fx-steady-twin", "fx-held-trigger"),
    "account_jump": ("fx-improved",),
}
# Probes: (player, detector, unit) -> the eligibility status each was built to show.
PROBES = {
    ("fx-pistol", "accuracy", "pistol"): "baseline_too_thin",
    ("fx-pistol", "geometry_rate", "pistol"): "baseline_too_thin",
    ("fx-pistol", "view_snaps", "pistol"): "baseline_too_thin",
    ("fx-pistol", "acquire_timing", "pistol"): "baseline_too_thin",
    ("fx-pistol", "extra", "kills_per_min:pistol"): "baseline_too_thin",
    ("fx-pistol", "supporting_extra", "reaction_ms:pistol"): "baseline_too_thin",
    ("fx-pistol", "recoil_learned", "fx-p|"): "baseline_too_thin",
    ("fx-pistol", "fire_rate", "pistol"): "disabled",
    ("fx-pistol", "metronome", "pistol"): "disabled",
    ("fx-pistol", "account_jump", ""): "telemetry_unavailable",
    ("fx-thin-history", "account_jump", "pistol"): "telemetry_unavailable",
    ("fx-few", "geometry_rate", "rifle"): "insufficient_samples",
    ("fx-few", "view_snaps", "rifle"): "insufficient_samples",
    ("fx-few", "acquire_timing", "rifle"): "insufficient_samples",
    ("fx-few", "extra", "kills_per_min:rifle"): "insufficient_samples",
    ("fx-few", "supporting_extra", "reaction_ms:rifle"): "insufficient_samples",
    ("fx-few", "recoil_learned", "fx-ar|"): "insufficient_samples",
    ("fx-few", "recoil_floor", "fx-ar|"): "disabled",
    ("fx-few", "fire_rate", "rifle"): "insufficient_samples",
    ("fx-few", "metronome", "rifle"): "insufficient_samples",
    ("fx-bare", "headshot_rate", "rifle"): "telemetry_unavailable",
    ("fx-bare", "median_distance", "rifle"): "telemetry_unavailable",
    ("fx-bare", "geometry_rate", "rifle"): "telemetry_unavailable",
    ("fx-bare", "view_snaps", "rifle"): "telemetry_unavailable",
    ("fx-bare", "acquire_timing", "rifle"): "telemetry_unavailable",
    ("fx-bare", "recoil_learned", ""): "telemetry_unavailable",
    ("fx-bare", "extra", "kills_per_min"): "telemetry_unavailable",
    ("fx-bare", "supporting_extra", "reaction_ms"): "telemetry_unavailable",
    ("fx-capless", "speed", ""): "disabled",
    ("fx-floor-few", "recoil_floor", "fx-floored|"): "insufficient_samples",
    ("fx-floor-few", "recoil_learned", "fx-floored|"): "not_applicable",
    ("fx-mirror-few", "mirror", "fx-ar|"): "insufficient_samples",
    ("fx-mirror-few", "leftover", "rifle"): "insufficient_samples",
    ("fx-mirror-conflict", "mirror", "fx-ar|"): "conflict",
    ("fx-lone-script", "leftover", "smg"): "baseline_too_thin",
    ("fx-lone-script", "mirror", "fx-smg|"): "eligible",
    ("fx-lone-script", "metronome", "smg"): "not_applicable",
    ("fx-hidden-few", "hidden", "rifle"): "insufficient_samples",
    ("fx-hidden-conflict", "hidden", "rifle"): "conflict",
    ("fx-quiet-smooth", "quiet_aim", "rifle"): "not_applicable",
    ("fx-quiet-conflict", "quiet_aim", "rifle"): "conflict",
    ("fx-wire-few", "wire", "rifle"): "insufficient_samples",
    ("fx-replay-few", "occluded_motion_replay", "legacy_private_replay:rifle"): "insufficient_samples",
    ("fx-thin-history", "account_jump", "rifle"): "insufficient_samples",
    ("fx-improved", "account_jump", "rifle"): "eligible",
    ("fx-mate-quiet", "voice", "fx-stack"): "telemetry_unavailable",
    ("fx-mate-few", "voice", "fx-stack"): "insufficient_samples",
    ("fx-held-trigger", "metronome", "rifle"): "not_applicable",
}


@dataclass
class Fixtures:
    profile: GameProfile
    cases: list[Case]
    events: list[Event] = field(default_factory=list)
    history: list[HistoryWindow] = field(default_factory=list)


def _event(player_id: str, match_id: str, t_ms: int, **values) -> Event:
    base = dict(game_id=GAME, match_id=match_id, player_id=player_id, t_ms=t_ms, event_type="shot", skill_band="average",
                weapon_class="rifle", weapon_id="fx-ar")
    base.update(values)
    return Event(**base)


def _shooter(
    player_id: str,
    rng: random.Random,
    *,
    shots: int = 200,
    matches: int = 4,
    accuracy: float = 0.25,
    head: float = 0.3,
    geometry: float = 0.05,
    view: float = 10.0,
    acquire: float = 250.0,
    recoil: float = 1.5,
    kills: float = 1.0,
    reaction: float = 260.0,
    cadence: tuple[int, int] = (110, 150),
    weapon_class: str = "rifle",
    weapon_id: str = "fx-ar",
    fields: frozenset[str] = ALL_FIELDS,
    party_id: str | None = None,
) -> list[Event]:
    """A player's shots: bursts of ten at ``cadence`` ms, a pause between bursts, ``matches`` matches.
    Each measurement is drawn around the player's own level, so one level can be set out of human range."""
    events: list[Event] = []
    per_match = max(1, shots // matches)
    for match in range(matches):
        t = 1000
        for index in range(per_match if match < matches - 1 else shots - per_match * (matches - 1)):
            spray = index % 10
            if index and spray == 0:
                t += 1500
            elif index:
                t += rng.randint(*cadence)
            hit = rng.random() < accuracy
            values: dict = {"hit": hit, "weapon_class": weapon_class, "weapon_id": weapon_id, "party_id": party_id, "spray_index": spray}
            if hit and "hitbox" in fields:
                values["hitbox"] = "head" if rng.random() < head else "chest"
            if hit and "distance" in fields:
                values["distance_m"] = 20.0 + 20.0 * rng.random()
            if hit and "geometry" in fields:
                values["through_geometry"] = rng.random() < geometry
            if "view" in fields:
                values["view_delta_deg"] = view * rng.random() ** 2
            if "acquire" in fields:
                values["acquire_ms"] = acquire + 60.0 * (rng.random() - 0.5)
            if "recoil" in fields:
                values["recoil_pitch_deg"] = recoil * (0.85 + 0.3 * rng.random())
            if "extras" in fields and spray == 0:
                values["extras"] = {"kills_per_min": kills + 0.2 * (rng.random() - 0.5), "reaction_ms": reaction + 40.0 * (rng.random() - 0.5)}
            events.append(_event(player_id, f"{player_id}-m{match}", t, **values))
    return events


def _humans(rng: random.Random) -> list[Event]:
    events: list[Event] = []
    for index in range(HUMANS):
        u = [rng.random() for _ in range(8)]
        events += _shooter(
            f"fx-human-{index:02d}", rng,
            accuracy=0.22 + 0.06 * u[0], head=0.25 + 0.1 * u[1], geometry=0.03 + 0.05 * u[2], view=8.0 + 4.0 * u[3],
            acquire=210.0 + 80.0 * u[4], recoil=1.2 + 0.6 * u[5], kills=0.8 + 0.4 * u[6], reaction=220.0 + 80.0 * u[7],
        )
    return events


def _plants_and_twins(rng: random.Random) -> list[Event]:
    return (
        _shooter("fx-geometry", rng, geometry=0.6)
        + _shooter("fx-snaps", rng, view=60.0)
        + _shooter("fx-acquire", rng, acquire=90.0)
        + _shooter("fx-kills", rng, kills=3.0)
        + _shooter("fx-reaction", rng, reaction=80.0)
        + _shooter("fx-recoil", rng, recoil=0.15)
        # The twins sit high or low inside the humans: good players, not cheats.
        + _shooter("fx-geometry-twin", rng, geometry=0.07)
        + _shooter("fx-snaps-twin", rng, view=11.5)
        + _shooter("fx-acquire-twin", rng, acquire=215.0)
        + _shooter("fx-kills-twin", rng, kills=1.15)
        + _shooter("fx-reaction-twin", rng, reaction=225.0)
        + _shooter("fx-recoil-twin", rng, recoil=1.25)
        # A fast but legal trigger, a steady hand that still varies, and a held full-auto at the gun's cycle.
        + _shooter("fx-trigger-twin", rng, cadence=(76, 95))
        + _shooter("fx-steady-twin", rng, cadence=(117, 123))
        + _shooter("fx-held-trigger", rng, cadence=(91, 93))
        # Better than last month, inside what the account has shown.
        + _shooter("fx-improved", rng, accuracy=0.27)
    )


def _kicks(player_id: str, rng: random.Random, sprays: int, length: int, *, weapon_class: str = "rifle", weapon_id: str = "fx-ar",
           unordered: bool = False) -> list[Event]:
    """Recoil kicks and the player's own command, sprays of ``length`` shots; the command does not follow the kick."""
    events: list[Event] = []
    t = 1000
    for _spray in range(sprays):
        for index in range(length):
            t += 100
            values = dict(weapon_class=weapon_class, weapon_id=weapon_id, spray_index=index, hit=False,
                          applied_recoil_pitch_deg=1.0 + rng.random(), compensation_pitch_deg=rng.random())
            events.append(_event(player_id, f"{player_id}-m0", t, **values))
            if unordered:
                # A second kick at the same moment and spray index: the server gave the two no order.
                events.append(_event(player_id, f"{player_id}-m0", t, **{**values, "applied_recoil_pitch_deg": values["applied_recoil_pitch_deg"] + 0.5}))
        t += 1500
    return events


def _probes(rng: random.Random) -> list[Event]:
    events = _shooter("fx-pistol", rng, accuracy=0.3, weapon_class="pistol", weapon_id="fx-p")
    few = _shooter("fx-few", rng, shots=20, matches=1)
    # Recoil on its first six shots only: too few eligible pitches to learn from.
    events += [ev if index < 6 else _strip(ev, "recoil_pitch_deg") for index, ev in enumerate(few)]
    events += _shooter("fx-bare", rng, fields=frozenset())
    events += [
        Event(game_id=GAME, match_id="fx-capless-m0", player_id="fx-capless", t_ms=1000 + 100 * index, event_type="movement",
              skill_band="average", weapon_class="unknown", weapon_id=None, speed_mps=5.0, on_ground=True)
        for index in range(40)
    ]
    events += _shooter("fx-floor-few", rng, shots=6, matches=1, weapon_id="fx-floored")
    events += _kicks("fx-mirror-few", rng, 1, 10)
    events += _kicks("fx-mirror-conflict", rng, 2, 10, unordered=True)
    events += _kicks("fx-lone-script", rng, 4, 15, weapon_class="smg", weapon_id="fx-smg")
    events += [_event("fx-hidden-few", "fx-hidden-few-m0", 1000 + 300 * index, hidden_track_ms=0.0) for index in range(3)]
    events += [
        _event("fx-hidden-conflict", "fx-hidden-conflict-m0", 1000 + 300 * index, hidden_track_ms=track)
        for index in range(10) for track in (100.0, 50.0)
    ]
    events += [
        _event("fx-quiet-smooth", "fx-quiet-smooth-m0", 1000 + 300 * index, information_state=state, aim_jitter_deg=0.01, enemy_id="e9")
        for index, state in enumerate(["visible"] * 15 + ["unknowable"] * 15)
    ]
    events += [
        _event("fx-quiet-conflict", "fx-quiet-conflict-m0", 1000 + 300 * index, information_state="visible", vision_state="absent", aim_jitter_deg=0.5)
        for index in range(15)
    ]
    events += [
        _event("fx-wire-few", "fx-wire-few-m0", 1000 + 300 * index, wire_error_deg=0.5, picture_error_deg=2.0, interp_delay_ms=50.0)
        for index in range(3)
    ]
    events += [_event("fx-replay-few", "fx-replay-few-m0", 1000 + 300 * index, private_track_ms=50.0) for index in range(3)]
    # History on the rifle, too little of it; none on the pistol it also fires.
    events += _shooter("fx-thin-history", rng) + _shooter("fx-thin-history", rng, shots=60, matches=1, weapon_class="pistol", weapon_id="fx-p")
    # A wallhacker and two teammates: one who sends no enemy ids, one with too few swings to time.
    events += [
        _event("fx-wall", "fx-stack-m0", 1000 + 300 * index, hidden_track_ms=150.0, information_state="unknowable", enemy_id="e1", party_id="fx-stack")
        for index in range(12)
    ]
    events += _shooter("fx-mate-quiet", rng, shots=50, matches=1, party_id="fx-stack")
    events += [
        _event("fx-mate-few", "fx-stack-m0", 1100 + 300 * index, information_state="unknowable", enemy_id="e1", party_id="fx-stack", aim_jitter_deg=0.4)
        for index in range(2)
    ]
    return events


def _strip(event: Event, name: str) -> Event:
    from dataclasses import replace

    return replace(event, **{name: None})


def build_fixtures(seed: int = 11) -> Fixtures:
    """Score the fixture world against a baseline frozen from its humans."""
    rng = random.Random(seed)
    profile = profile_from_dict(PROFILE)
    humans = _humans(rng)
    others = _plants_and_twins(rng) + _probes(rng)
    cohort = build_cohorts(summarize(humans, profile), profile)
    history = [
        HistoryWindow("fx-improved", "rifle", "average", 400, 96),
        HistoryWindow("fx-thin-history", "rifle", "average", 20, 5),
        HistoryWindow("fx-pistol", "rifle", "average", 400, 100),
    ]
    events = humans + others
    cases = run_score(events, profile, cohort, history)
    return Fixtures(profile, cases, events, history)


def fixture_failures(fixtures: Fixtures) -> list[str]:
    """Where the fixture world does not show what it was built to show. Empty when it does."""
    by_id = {case.player_id: case for case in fixtures.cases}
    failures = []
    for player, kinds in PLANTED.items():
        fired = {obs.kind for obs in by_id[player].evidence}
        failures += [f"{player} was planted to trip {kind}, and did not" for kind in kinds if kind not in fired]
    for kind, players in TWINS.items():
        failures += [f"{player} is an honest twin for {kind}, and tripped it" for player in players if any(obs.kind == kind for obs in by_id[player].evidence)]
    for (player, kind, unit), status in PROBES.items():
        found = by_id[player].detector_eligibility.get(kind, {}).get(unit)
        if found != status:
            failures.append(f"{player} {kind} {unit!r}: eligibility {found}, built to show {status}")
    humans_flagged = [pid for pid, case in by_id.items() if pid.startswith("fx-human-") and case.decision == "review"]
    failures += [f"{pid}: a background human went to review" for pid in humans_flagged]
    return failures
