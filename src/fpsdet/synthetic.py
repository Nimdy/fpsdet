"""Planted matches so a demo can show the bars without a live game."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from .baseline import build_cohorts
from .models import Event, GameProfile, HistoryWindow
from .parse import load_profile
from .persist import case_to_dict
from .priority import review_order, scan_order
from .score import assess_player
from .statsutil import wilson_lower, wilson_upper
from .summarize import summarize

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "profiles" / "example-loadout.json"

EXPECT = {
    "elite-human": "clean",
    "weak-human": "clean",
    "legal-heavy": "clean",
    "blasted": "clean",
    "glitch": "clean",
    "adrenaline": "clean",
    "modded-recoil": "clean",
    "new-gun": "clean",
    "reported-streamer": "clean",
    "rank-outlier": "watch",
    "account-changed": "watch",
    "small-sample": "insufficient_data",
    "rage": "review",
    "weight-cheat": "review",
    "no-recoil": "review",
    "fire-rate": "review",
    "mirror-script": "review",
    "metronome": "review",
    "wall-eye": "review",
    "late-compensate": "clean",
    "angle-holder": "clean",
    "quiet-radar": "review",
    "steady-hands": "clean",
    "listened": "clean",
    "clone-source": "review",
    "clone-buyer": "watch",
    "radar-friend": "watch",
    "callout-friend": "clean",
    "replay-lock": "review",
    "real-fight": "clean",
    "wire-lock": "review",
    "picture-track": "clean",
}


@dataclass
class Demo:
    profile: GameProfile
    cases: list
    failures: list[str]
    events: list[Event] = field(default_factory=list)
    anchors: dict = field(default_factory=dict)


def _event(**kwargs) -> Event:
    base = dict(
        game_id="example-loadout",
        match_id="m1",
        event_type="shot",
        skill_band="average",
        weapon_class="rifle",
        weapon_id="ak",
        mod_set=(),
        displacement_cause="none",
    )
    base.update(kwargs)
    return Event(**base)


def _gap_jitter(player_id: str, index: int) -> int:
    """±18 ms, deterministic. A flat gap is a metronome plant, not a person."""
    salt = sum(ord(ch) for ch in player_id) % 17
    return ((index * 13 + salt * 7) % 37) - 18


def _kick_curve(count: int) -> list[float]:
    """Pseudo-random camera kicks. Lag-1 correlation stays near zero."""
    state = 7
    kicks: list[float] = []
    for _ in range(count):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        kicks.append(0.8 + (state % 1000) / 1000 * 1.6)
    return kicks


def _noise_seq(count: int, salt: int = 99) -> list[float]:
    state = salt
    values: list[float] = []
    for _ in range(count):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        values.append((state % 2000) / 1000 - 1.0)
    return values


def _at(value, index: int):
    if isinstance(value, list):
        return value[index]
    return value


def _shots(
    player_id: str,
    shots: int,
    hits: int,
    head_hits: int,
    distance: float,
    band: str,
    *,
    interval: int = 140,
    party_id: str | None = None,
    match_id: str = "m1",
    recoil: float | None = None,
    pitches: list[float] | None = None,
    mods: tuple[str, ...] = (),
    weapon_id: str = "ak",
    spray: bool = False,
    jitter: bool = True,
    applied: list[float] | None = None,
    compensation: list[float] | None = None,
    hidden_ms: float | None = None,
    private_ms: float | list[float] | None = None,
    wire_error: float | list[float] | None = None,
    picture_error: float | list[float] | None = None,
    interp_delay: float | list[float] | None = None,
    acquire: float | None = None,
    information: str | list[str] | None = None,
    aim_jitter: float | list[float] | None = None,
    enemy_id: str | None = None,
) -> list[Event]:
    rows: list[Event] = []
    hits_left = hits
    heads_left = head_hits
    t_ms = 0
    for index in range(shots):
        if index:
            delta = 0 if not jitter else _gap_jitter(player_id, index)
            t_ms += max(1, interval + delta)
        hit = hits_left > 0
        if hit:
            hits_left -= 1
        head = hit and heads_left > 0
        if head:
            heads_left -= 1
        pitch = recoil if pitches is None else pitches[index]
        rows.append(
            _event(
                player_id=player_id,
                t_ms=t_ms,
                skill_band=band,
                match_id=match_id,
                party_id=party_id,
                hit=hit,
                hitbox=("head" if head else "upper_torso") if hit else None,
                distance_m=distance,
                weapon_id=weapon_id,
                mod_set=mods,
                recoil_pitch_deg=pitch,
                spray_index=index if spray else None,
                applied_recoil_pitch_deg=None if applied is None else applied[index],
                compensation_pitch_deg=None if compensation is None else compensation[index],
                hidden_track_ms=hidden_ms,
                private_track_ms=_at(private_ms, index),
                wire_error_deg=_at(wire_error, index),
                picture_error_deg=_at(picture_error, index),
                interp_delay_ms=_at(interp_delay, index),
                acquire_ms=acquire,
                information_state=_at(information, index),
                aim_jitter_deg=_at(aim_jitter, index),
                enemy_id=enemy_id,
            )
        )
    return rows


def _move(
    player_id: str,
    samples: int,
    speed: float,
    weight: float,
    *,
    cause: str = "none",
    on_ground: bool | None = True,
    expected: float | None = None,
    band: str = "average",
    t0: int = 0,
) -> list[Event]:
    rows = []
    for index in range(samples):
        rows.append(
            _event(
                player_id=player_id,
                event_type="movement",
                t_ms=t0 + index * 100,
                skill_band=band,
                weapon_class="unknown",
                weapon_id=None,
                speed_mps=speed,
                loadout_weight_kg=weight,
                on_ground=on_ground,
                displacement_cause=cause,
                expected_max_ground_speed_mps=expected,
            )
        )
    return rows


def _background(rng: random.Random) -> list[Event]:
    bands = {
        "developing": (0.12, 0.20, 22.0),
        "average": (0.18, 0.28, 28.0),
        "advanced": (0.26, 0.36, 34.0),
        "elite": (0.34, 0.46, 40.0),
    }
    events: list[Event] = []
    for band, (acc, hs, dist) in bands.items():
        for index in range(36):
            player = f"pop-{band}-{index:02d}"
            player_acc = min(0.48, max(0.05, rng.gauss(acc, 0.015)))
            player_hs = min(0.7, max(0.05, rng.gauss(hs, 0.03)))
            player_dist = max(8.0, rng.gauss(dist, 3.0))
            shots = 200
            hit_count = round(player_acc * shots)
            head_count = round(player_hs * hit_count)
            events.extend(
                _shots(player, shots, hit_count, head_count, player_dist, band, match_id=f"pop-{index}")
            )
    return events


# The desk draws these. The scorer reads the same millisecond lists and nothing spatial.
REPLAY_LOCK_PRIVATE_MS = [80.0] * 20 + [0.0] * 28
REAL_FIGHT_PRIVATE_MS = [0.0] * 22 + [40.0] * 4 + [0.0] * 22
REPLAY_HEADING_DEG = 137.0
REPLAY_DELAY = 4
REPLAY_ORIGIN = (58.0, 4.0)


def _live_route(count: int) -> list[list[float]]:
    points = []
    for index in range(count):
        x = index * 0.62
        y = 3.2 * math.sin(index / 7.5) + 0.45 * math.sin(index / 2.2)
        points.append([round(x, 3), round(y, 3)])
    return points


def _turn(point: list[float], heading_deg: float, origin: tuple[float, float]) -> list[float]:
    theta = math.radians(heading_deg)
    cosine, sine = math.cos(theta), math.sin(theta)
    x, y = point
    return [
        origin[0] + cosine * x - sine * y,
        origin[1] + sine * x + cosine * y,
    ]


def _round_point(point: list[float]) -> list[float]:
    return [round(point[0], 3), round(point[1], 3)]


def replay_scene() -> dict:
    """Worked picture of the private-replay plant.

    One live route, the same steps turned onto another heading and delayed,
    placed where this client has no sight and no audio. The score never reads
    these coordinates. It reads ``private_track_ms``.
    """
    count = len(REPLAY_LOCK_PRIVATE_MS)
    if len(REAL_FIGHT_PRIVATE_MS) != count:
        raise ValueError("private-replay plants must be the same length")
    source = _live_route(count)
    raw_replay = []
    for index in range(count):
        sample = source[0] if index < REPLAY_DELAY else source[index - REPLAY_DELAY]
        raw_replay.append(_turn(sample, REPLAY_HEADING_DEG, REPLAY_ORIGIN))
    enemy = []
    for index in range(count):
        enemy.append([
            round(4.0 + (index % 12) * 0.45, 3),
            round(34.0 + 0.8 * math.sin(index / 5.0), 3),
        ])
    replay = [_round_point(point) for point in raw_replay]
    xs = [point[0] for point in replay]
    ys = [point[1] for point in replay]
    pad = 1.6
    wall = {
        "x": round(min(xs) - pad, 3),
        "y": round(min(ys) - pad, 3),
        "w": round(max(xs) - min(xs) + 2 * pad, 3),
        "h": round(max(ys) - min(ys) + 2 * pad, 3),
    }
    shift = 0.85 / math.sqrt(2.0)

    def _on_replay(index: int, clipped: bool) -> list[float]:
        point = raw_replay[index]
        if not clipped:
            return _round_point(point)
        return _round_point([point[0] + shift, point[1] + shift])

    def _aim(mask: list[float], clipped: bool) -> list[list[float]]:
        return [
            _on_replay(index, clipped) if ms > 0 else [enemy[index][0], enemy[index][1]]
            for index, ms in enumerate(mask)
        ]

    scene = {
        "heading_deg": REPLAY_HEADING_DEG,
        "delay": REPLAY_DELAY,
        "origin": [REPLAY_ORIGIN[0], REPLAY_ORIGIN[1]],
        "source_id": "live-route",
        "enemy_id": "seen-1",
        "source": source,
        "replay": replay,
        "enemy": enemy,
        "wall": wall,
        "aims": {
            "replay-lock": _aim(REPLAY_LOCK_PRIVATE_MS, clipped=False),
            "real-fight": _aim(REAL_FIGHT_PRIVATE_MS, clipped=True),
        },
        "scored_by": "private_track_ms",
    }
    _reject_lying_scene(scene, raw_replay)
    return scene


def _inside_wall(point: list[float], wall: dict) -> bool:
    return (
        wall["x"] <= point[0] <= wall["x"] + wall["w"]
        and wall["y"] <= point[1] <= wall["y"] + wall["h"]
    )


def _reject_lying_scene(scene: dict, raw_replay: list[list[float]]) -> None:
    """The picture is a lie if the live route or the visible enemy sits in the blind volume."""
    wall = scene["wall"]
    for point in scene["source"] + scene["enemy"]:
        if _inside_wall(point, wall):
            raise ValueError("private-replay picture covers a body this client could perceive")
    theta = math.radians(scene["heading_deg"])
    cosine, sine = math.cos(theta), math.sin(theta)
    origin = scene["origin"]
    delay = scene["delay"]
    for index in range(delay, len(raw_replay)):
        dx = raw_replay[index][0] - origin[0]
        dy = raw_replay[index][1] - origin[1]
        back_x = cosine * dx + sine * dy
        back_y = -sine * dx + cosine * dy
        source = scene["source"][index - delay]
        if abs(back_x - source[0]) > 1e-9 or abs(back_y - source[1]) > 1e-9:
            raise ValueError("turned route does not undo to the delayed live route")


def _hits_for_bound(shots: int, low: float, high: float) -> int | None:
    found = [hits for hits in range(shots + 1) if low < wilson_lower(hits, shots) < high]
    if not found:
        return None
    return found[len(found) // 2]


def build_demo(seed: int = 1) -> Demo:
    profile = load_profile(PROFILE_PATH)
    rng = random.Random(seed)
    background = _background(rng)
    pop_records = summarize(background, profile)
    cohorts = build_cohorts(pop_records, profile)
    elite_acc = cohorts.dist("elite", "rifle", "accuracy", None)
    avg_acc = cohorts.dist("average", "rifle", "accuracy", None)
    elite_hs = cohorts.dist("elite", "rifle", "headshot_rate", None)
    avg_hs = cohorts.dist("average", "rifle", "headshot_rate", None)
    avg_dist = cohorts.dist("average", "rifle", "median_distance", None)
    failures: list[str] = []
    if not elite_acc or not avg_acc or not elite_hs or not avg_hs or not avg_dist:
        return Demo(profile, [], ["background cohort did not form"])

    def around(rate: float, shots: int = 200) -> tuple[int, int]:
        hits = max(0, min(shots, round(rate * shots)))
        return shots, hits

    elite_shots, elite_hits = around(elite_acc.mid)
    elite_heads = round(elite_hs.mid * elite_hits)
    avg_shots, avg_hits = around(avg_acc.mid)
    avg_heads = round(avg_hs.mid * avg_hits)
    rank_hits = _hits_for_bound(200, avg_acc.p95, elite_acc.maximum)
    if rank_hits is None:
        failures.append(
            f"no accuracy sits between average p95 {avg_acc.p95:.3f} and elite max {elite_acc.maximum:.3f}"
        )
        rank_hits = avg_hits

    history_upper = wilson_upper(8, 200)
    jump_hits = _hits_for_bound(200, history_upper + profile.self_jump_gap, avg_acc.p95)
    if jump_hits is None:
        failures.append(
            f"no self-jump rate between {history_upper + profile.self_jump_gap:.3f} and p95 {avg_acc.p95:.3f}"
        )
        jump_hits = avg_hits

    subjects: list[Event] = []
    subjects += _shots("elite-human", elite_shots, elite_hits, elite_heads, 40, "elite")
    subjects += _shots("weak-human", 200, 16, 2, 12, "average")
    subjects += _shots("legal-heavy", avg_shots, avg_hits, avg_heads, avg_dist.mid, "average")
    subjects += _move("legal-heavy", 30, 5.0, 18)
    subjects += _shots("blasted", avg_shots, avg_hits, avg_heads, avg_dist.mid, "average")
    subjects += _move("blasted", 30, 7.1, 10, cause="explosion")
    subjects += _shots("glitch", avg_shots, avg_hits, avg_heads, avg_dist.mid, "average")
    subjects += _move("glitch", 2, 15.0, 10)
    subjects += _move("glitch", 30, 5.0, 10, t0=5000)
    subjects += _shots("adrenaline", avg_shots, avg_hits, avg_heads, avg_dist.mid, "average")
    subjects += _move("adrenaline", 30, 7.0, 10, expected=7.2)
    subjects += _shots(
        "modded-recoil",
        avg_shots,
        avg_hits,
        avg_heads,
        avg_dist.mid,
        "average",
        mods=("vertical_grip", "compensator"),
        recoil=0.8,
        spray=True,
    )
    subjects += _shots(
        "new-gun",
        avg_shots,
        avg_hits,
        avg_heads,
        avg_dist.mid,
        "average",
        weapon_id="newgun",
        recoil=0.02,
        spray=True,
    )
    subjects += _shots("reported-streamer", avg_shots, avg_hits, avg_heads, avg_dist.mid, "average")
    rank_heads = round(avg_hs.mid * rank_hits)
    subjects += _shots("rank-outlier", 200, rank_hits, rank_heads, avg_dist.mid, "average")
    jump_heads = round(avg_hs.mid * jump_hits)
    subjects += _shots("account-changed", 200, jump_hits, jump_heads, avg_dist.mid, "average")
    subjects += _shots("small-sample", 10, 10, 8, 30, "average")
    subjects += _shots("rage", 200, 185, 160, 80, "average", party_id="stack-1", match_id="raid-9")
    subjects += _shots("weight-cheat", avg_shots, avg_hits, avg_heads, avg_dist.mid, "average", party_id="stack-1")
    subjects += _move("weight-cheat", 30, 7.1, 10)
    subjects += _shots(
        "no-recoil",
        avg_shots,
        avg_hits,
        avg_heads,
        avg_dist.mid,
        "average",
        recoil=0.05,
        spray=True,
    )
    subjects += _shots("fire-rate", 60, round(avg_acc.mid * 60), round(avg_hs.mid * round(avg_acc.mid * 60)), avg_dist.mid, "average", interval=40)
    mirror_n = 48
    mirror_hits = round(avg_acc.mid * mirror_n)
    mirror_heads = round(avg_hs.mid * mirror_hits)
    kicks = _kick_curve(mirror_n)
    script_command = [-kick + 1.35 + ((index % 5) - 2) * 0.02 for index, kick in enumerate(kicks)]
    script_net = [kick + command for kick, command in zip(kicks, script_command)]
    subjects += _shots(
        "mirror-script",
        mirror_n,
        mirror_hits,
        mirror_heads,
        avg_dist.mid,
        "average",
        pitches=script_net,
        spray=True,
        applied=kicks,
        compensation=script_command,
    )
    late_command = []
    late_net = []
    # Independent of the mirror plant's five-step paint. The same paint, after
    # the kick is regressed out, is the same leftover and would false-match.
    late_wiggle = _noise_seq(mirror_n, salt=3)
    for index, kick in enumerate(kicks):
        previous = kicks[0] if index == 0 else kicks[index - 1]
        command = -0.45 * previous + 0.05 * late_wiggle[index]
        late_command.append(command)
        late_net.append(kick + command)
    subjects += _shots(
        "late-compensate",
        mirror_n,
        mirror_hits,
        mirror_heads,
        avg_dist.mid,
        "average",
        pitches=late_net,
        spray=True,
        applied=kicks,
        compensation=late_command,
    )
    metronome_hits = round(avg_acc.mid * 60)
    subjects += _shots(
        "metronome",
        60,
        metronome_hits,
        round(avg_hs.mid * metronome_hits),
        avg_dist.mid,
        "average",
        interval=140,
        jitter=False,
    )
    wall = _shots(
        "wall-eye",
        20,
        round(avg_acc.mid * 20),
        1,
        avg_dist.mid,
        "average",
        hidden_ms=80,
        party_id="stack-radar",
        enemy_id="mover-1",
        information="unknowable",
    )
    subjects += wall
    for event in wall[:8]:
        subjects.append(
            _event(
                player_id="radar-friend",
                t_ms=event.t_ms + 40,
                skill_band="average",
                party_id="stack-radar",
                match_id="radar-window",
                enemy_id="mover-1",
                information_state="unknowable",
                hidden_track_ms=40,
            )
        )
    subjects += _shots(
        "callout-friend",
        mirror_n,
        mirror_hits,
        mirror_heads,
        avg_dist.mid,
        "average",
        party_id="stack-radar",
    )
    burst_end = wall[-1].t_ms
    for index in range(8):
        subjects.append(
            _event(
                player_id="callout-friend",
                t_ms=burst_end + 800 + index * 60,
                skill_band="average",
                party_id="stack-radar",
                match_id="callout-window",
                enemy_id="mover-1",
                information_state="unknowable",
                hidden_track_ms=40,
            )
        )
    half = 24
    window_hits = round(avg_acc.mid * 48)
    window_heads = round(avg_hs.mid * window_hits)
    subjects += _shots(
        "quiet-radar",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        information=["visible"] * half + ["unknowable"] * half,
        aim_jitter=[0.90] * half + [0.08] * half,
    )
    subjects += _shots(
        "steady-hands",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        information=["visible"] * half + ["unknowable"] * half,
        aim_jitter=[0.40] * half + [0.40] * half,
    )
    subjects += _shots(
        "listened",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        information=["visible"] * half + ["audio"] * half,
        aim_jitter=[0.90] * half + [0.08] * half,
    )
    shared = _noise_seq(mirror_n)
    subjects += _shots(
        "clone-source",
        mirror_n,
        mirror_hits,
        mirror_heads,
        avg_dist.mid,
        "average",
        recoil=1.35,
        spray=True,
        applied=kicks,
        compensation=[-kick + 0.2 * sample for kick, sample in zip(kicks, shared)],
    )
    subjects += _shots(
        "clone-buyer",
        mirror_n,
        mirror_hits,
        mirror_heads,
        avg_dist.mid,
        "average",
        recoil=1.35,
        spray=True,
        applied=kicks,
        compensation=[-0.2 * kick + sample for kick, sample in zip(kicks, shared)],
    )
    subjects += _shots(
        "replay-lock",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        private_ms=REPLAY_LOCK_PRIVATE_MS,
    )
    subjects += _shots(
        "real-fight",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        information="visible",
        enemy_id="seen-1",
        private_ms=REAL_FIGHT_PRIVATE_MS,
    )
    subjects += _shots(
        "wire-lock",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        information="visible",
        enemy_id="strafe-1",
        wire_error=0.18,
        picture_error=2.40,
        interp_delay=100,
    )
    subjects += _shots(
        "picture-track",
        48,
        window_hits,
        window_heads,
        avg_dist.mid,
        "average",
        information="visible",
        enemy_id="strafe-1",
        wire_error=2.40,
        picture_error=0.18,
        interp_delay=100,
    )
    subjects += _shots(
        "angle-holder",
        mirror_n,
        mirror_hits,
        mirror_heads,
        avg_dist.mid,
        "average",
        hidden_ms=0,
        acquire=25,
    )

    history = [
        HistoryWindow("account-changed", "rifle", "average", 200, 8),
    ]
    reports = {"reported-streamer": 25}
    records = {row.player_id: row for row in summarize(subjects, profile)}
    cases = []
    for record in records.values():
        cases.append(
            assess_player(record, cohorts, profile, history, reports.get(record.player_id, 0))
        )
    from .score import annotate_batch

    annotate_batch(cases, list(records.values()), profile)
    by_id = {case.player_id: case for case in cases}
    for player_id, decision in EXPECT.items():
        got = by_id[player_id].decision
        if got != decision:
            failures.append(
                f"{player_id}: expected {decision}, got {got}; "
                f"reasons={by_id[player_id].reasons}; observations={by_id[player_id].observations[:4]}"
            )
    if "newgun|" not in by_id["new-gun"].untrained:
        failures.append(f"new-gun untrained list was {by_id['new-gun'].untrained}")
    if not by_id["glitch"].speed or not by_id["glitch"].speed.spike_samples:
        failures.append("glitch was not kept as a spike")
    if not by_id["blasted"].speed or by_id["blasted"].speed.excluded_innocent < 30:
        failures.append("blast samples were not excluded")
    if scan_order(cases)[0].player_id != "reported-streamer":
        failures.append("reported player was not first in scan order")
    if review_order(cases)[0].decision != "review":
        failures.append("review queue did not lead with a review")
    if "weight-cheat" not in by_id["rage"].party_note:
        failures.append("party link missing on rage")
    def _reason_has(player_id: str, needle: str) -> bool:
        return any(needle in reason for reason in by_id[player_id].reasons)

    if not _reason_has("mirror-script", "same tick"):
        failures.append(f"mirror-script reasons: {by_id['mirror-script'].reasons}")
    if _reason_has("mirror-script", "recoil stayed under"):
        failures.append("blatant recoil stole the mirror tape")
    if (
        _reason_has("late-compensate", "same tick")
        or _reason_has("late-compensate", "recoil stayed under")
        or _reason_has("late-compensate", "leftover command")
    ):
        failures.append(f"late-compensate was scored as a mirror: {by_id['late-compensate'].reasons}")
    if not _reason_has("metronome", "std 0.00"):
        failures.append(f"metronome reasons: {by_id['metronome'].reasons}")
    if _reason_has("metronome", "fired faster"):
        failures.append("fire-rate stole the metronome tape")
    if not _reason_has("wall-eye", "hidden mover"):
        failures.append(f"wall-eye reasons: {by_id['wall-eye'].reasons}")
    if by_id["angle-holder"].reasons:
        failures.append(f"angle-holder reasons: {by_id['angle-holder'].reasons}")
    if not _reason_has("quiet-radar", "could not have known"):
        failures.append(f"quiet-radar reasons: {by_id['quiet-radar'].reasons}")
    if _reason_has("listened", "could not have known") or _reason_has("steady-hands", "could not have known"):
        failures.append("knowable smoothness was scored as unknowable")
    if not _reason_has("clone-source", "same tick"):
        failures.append(f"clone-source reasons: {by_id['clone-source'].reasons}")
    if not _reason_has("clone-buyer", "leftover command matches clone-source"):
        failures.append(f"clone-buyer reasons: {by_id['clone-buyer'].reasons}")
    if _reason_has("clone-buyer", "same tick"):
        failures.append("the buyer was scored as their own mirror")
    if not _reason_has("radar-friend", "a voice needs"):
        failures.append(f"radar-friend reasons: {by_id['radar-friend'].reasons}")
    if _reason_has("callout-friend", "a voice needs"):
        failures.append(f"callout-friend reasons: {by_id['callout-friend'].reasons}")
    if not _reason_has("replay-lock", "private replay"):
        failures.append(f"replay-lock reasons: {by_id['replay-lock'].reasons}")
    if _reason_has("replay-lock", "hidden mover"):
        failures.append("the private replay was scored as a hidden mover")
    if _reason_has("real-fight", "private replay") or _reason_has("real-fight", "hidden mover"):
        failures.append(f"real-fight reasons: {by_id['real-fight'].reasons}")
    if not _reason_has("wire-lock", "wire snapshot"):
        failures.append(f"wire-lock reasons: {by_id['wire-lock'].reasons}")
    if _reason_has("wire-lock", "hidden mover") or _reason_has("wire-lock", "private replay"):
        failures.append("the wire lock was scored as a hidden mover or a private replay")
    if (
        _reason_has("picture-track", "wire snapshot")
        or _reason_has("picture-track", "hidden mover")
        or _reason_has("picture-track", "private replay")
    ):
        failures.append(f"picture-track reasons: {by_id['picture-track'].reasons}")
    unreported = [case.player_id for case in scan_order(cases) if case.reports <= 0]
    if not unreported or unreported[0] != "clone-buyer":
        failures.append(f"vendor twin was not next in the scan queue: {unreported[:4]}")
    pop_cases = [
        assess_player(record, cohorts, profile, [], 0) for record in pop_records
    ]
    popped = [case.player_id for case in pop_cases if case.decision == "review"]
    if popped:
        failures.append(f"background players reviewed: {popped[:5]}")
    anchors = {
        "elite_accuracy_max": elite_acc.maximum,
        "average_accuracy_p95": avg_acc.p95,
        "average_accuracy_mid": avg_acc.mid,
        "elite_headshot_max": elite_hs.maximum,
        "average_headshot_p95": avg_hs.p95,
        "elite_distance_p95": cohorts.dist("elite", "rifle", "median_distance", None).p95
        if cohorts.dist("elite", "rifle", "median_distance", None)
        else None,
    }
    return Demo(profile, cases, failures, events=subjects, anchors=anchors)


def demo_rows(demo: Demo) -> str:
    lines = [f"{'player':<20} {'decision':<18} {'reports':>7}  why"]
    for case in review_order(demo.cases):
        why = case.reasons[0] if case.reasons else (case.observations[0] if case.observations else "")
        lines.append(f"{case.player_id:<20} {case.decision:<18} {case.reports:7d}  {why[:88]}")
    return "\n".join(lines)


def case_payloads(demo: Demo) -> list[dict]:
    return [case_to_dict(case) for case in review_order(demo.cases)]
