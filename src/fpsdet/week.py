"""A synthetic week, scored the way a studio would score a real one.

Four hundred players play seven days on a server that writes the fields this
repo asks for. Seventeen of them cheat, each from a day of their own. Last week
the same population played clean, and that week is the frozen baseline. Each
night's events are scored on their own, as a nightly batch would score them, and
the whole week is scored once more for the queue.

Everything here is invented, including who cheats. The dashboard says so. The
point is to show what the output of a real week looks like, and how the checks
behave on a population instead of one planted player at a time.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from .baseline import CohortTable, build_cohorts
from .models import Case, Event, GameProfile, HistoryWindow, PlayerRecord, curve_speed
from .parse import load_profile
from .pipeline import run_score
from .summarize import summarize
from .synthetic import PROFILE_PATH

WEEK_START = dt.date(2026, 9, 26)
DAYS = 7
PLAYERS = 400
# The day the server build that writes wire_error_deg, picture_error_deg and interp_delay_ms shipped.
WIRE_SHIPS = 3
# Share of matches where the server plays a private replay.
DECOY_MATCHES = 0.2

BANDS = {
    # share, rifle accuracy, rifle headshot rate
    "developing": (0.25, 0.14, 0.18),
    "average": (0.40, 0.20, 0.25),
    "advanced": (0.25, 0.27, 0.32),
    "elite": (0.10, 0.34, 0.40),
}
WEAPONS = {
    # accuracy shift, headshot shift, median distance, share of a match's shots
    "rifle": (0.00, 0.00, 30.0, 0.62),
    "smg": (0.04, -0.03, 16.0, 0.28),
    "dmr": (-0.03, 0.08, 55.0, 0.10),
}

# label -> (how many, first day, short description). Days are 0 = Saturday.
CHEATS = {
    "speed": (2, (1, 4), "sprints at the light-kit speed with a heavy kit"),
    "rage": (1, (2,), "aimbot, no attempt to hide"),
    "closet": (2, (0, 0), "assist tuned to stay under the elite ceiling"),
    "no-recoil": (1, (3,), "cancels the kick, camera stays flat"),
    "mirror": (1, (2,), "cancels the kick on the same tick, paints a legal camera"),
    "vendor-buyer": (1, (2,), "same humanizer table as the mirror account"),
    "fire-rate": (1, (5,), "rifle fires faster than it cycles"),
    "metronome": (1, (1,), "tap macro on the legal cycle"),
    "wallhack": (2, (2, 4), "tracks enemies it cannot see or hear"),
    "radar-friend": (1, (2,), "teammate of a wallhacker, swings on their calls"),
    "quiet-radar": (1, (3,), "aim goes still only when the enemy is unknowable"),
    "wire": (1, (WIRE_SHIPS,), "packet aimbot, aims at the snapshot"),
    "esp": (1, (2,), "ESP that draws every body it receives"),
    "boosted": (1, (0,), "account played by someone much better this week"),
}
HONEST = "honest"


@dataclass
class Player:
    pid: str
    band: str
    skill: float
    weapons: tuple[str, ...]
    activity: float
    weight_kg: float
    build: tuple[str, ...]
    gain: float
    jitter: float
    party: str | None = None
    cheat: str = HONEST
    start: int = 0


@dataclass
class Week:
    profile: GameProfile
    days: list[str]
    players: dict[str, Player]
    events: list[Event]
    prior_events: int
    cohort: CohortTable
    history: list[HistoryWindow]
    reports: dict[str, int]
    nightly: list[list[Case]]
    cases: list[Case]
    records: list[PlayerRecord]
    truth: dict[str, str]
    features: dict[str, str] = field(default_factory=dict)


def _day(index: int) -> str:
    return (WEEK_START + dt.timedelta(days=index)).isoformat()


def _short(index: int) -> str:
    return (WEEK_START + dt.timedelta(days=index)).strftime("%a %d %b")


def _population(rng: random.Random) -> dict[str, Player]:
    players: dict[str, Player] = {}
    names = list(BANDS)
    weights = [BANDS[name][0] for name in names]
    for index in range(PLAYERS):
        band = rng.choices(names, weights)[0]
        weapons = ["rifle"]
        if rng.random() < 0.55:
            weapons.append("smg")
        if rng.random() < 0.25:
            weapons.append("dmr")
        modded = rng.random() < 0.3
        players[f"p-{index:04d}"] = Player(
            pid=f"p-{index:04d}",
            band=band,
            skill=rng.gauss(0.0, 1.0),
            weapons=tuple(weapons),
            activity=rng.choice([0.6, 1.0, 1.2, 1.5, 2.0]),
            weight_kg=round(rng.uniform(3.0, 24.0), 1),
            build=("compensator", "vertical_grip") if modded else (),
            gain=rng.uniform(0.3, 0.6),
            jitter=rng.uniform(0.5, 1.1),
        )
    ids = list(players)
    rng.shuffle(ids)
    # A third of players queue in parties of two to four.
    cursor = 0
    party = 0
    while cursor < len(ids) // 3:
        size = rng.randint(2, 4)
        for pid in ids[cursor : cursor + size]:
            players[pid].party = f"party-{party:03d}"
        cursor += size
        party += 1
    return players


def _assign_cheats(rng: random.Random, players: dict[str, Player]) -> None:
    solo = [p for p in players.values() if p.party is None and p.band in ("average", "advanced")]
    rng.shuffle(solo)
    for label, (count, starts, _) in CHEATS.items():
        if label in ("radar-friend", "vendor-buyer"):
            continue
        for slot in range(count):
            player = solo.pop()
            player.cheat = label
            player.start = starts[slot]
            if label == "boosted":
                player.band = "average"
    # The radar friend queues with the first wallhacker. The buyer bought the mirror account's tool.
    wallhacker = next(p for p in players.values() if p.cheat == "wallhack" and p.start == CHEATS["wallhack"][1][0])
    friend = solo.pop()
    friend.cheat = "radar-friend"
    friend.start = CHEATS["radar-friend"][1][0]
    wallhacker.party = friend.party = "party-stack"
    buyer = solo.pop()
    buyer.cheat = "vendor-buyer"
    buyer.start = CHEATS["vendor-buyer"][1][0]
    for player in players.values():
        if player.cheat in ("rage", "speed", "esp", "wire"):
            player.weapons = ("rifle",)
        if player.cheat != HONEST:
            # Someone who bought a cheat plays. An account that barely plays leaves too little to score.
            player.activity = max(player.activity, 1.2)


def _rates(player: Player, weapon: str, cheating: bool, prior: bool) -> tuple[float, float]:
    _, acc, hs = BANDS[player.band]
    d_acc, d_hs, _, _ = WEAPONS[weapon]
    acc = acc + d_acc + 0.022 * player.skill
    hs = hs + d_hs + 0.03 * player.skill
    if cheating and player.cheat == "rage":
        return 0.78, 0.72
    if cheating and player.cheat == "closet":
        return BANDS["elite"][1] + d_acc - 0.01, BANDS["elite"][2] + d_hs
    if player.cheat == "boosted" and not prior:
        # Someone at the elite level is playing this account.
        _, elite_acc, elite_hs = BANDS["elite"]
        return elite_acc + d_acc + 0.022 * abs(player.skill), elite_hs + d_hs
    return min(0.6, max(0.05, acc)), min(0.7, max(0.05, hs))


class _Writer:
    """Builds one player's lines for one match, honest or not."""

    def __init__(self, rng: random.Random, profile: GameProfile, table: list[float]):
        self.rng = rng
        self.profile = profile
        self.table = table

    def shots(
        self,
        player: Player,
        match: str,
        day: int,
        *,
        prior: bool,
        decoy: bool,
        count: int,
        contacts: list[tuple[int, str]] | None = None,
    ) -> list[Event]:
        rng = self.rng
        cheating = not prior and player.cheat != HONEST and day >= player.start
        rows: list[Event] = []
        t_ms = rng.randint(5_000, 40_000)
        weights = [WEAPONS[w][3] for w in player.weapons]
        remaining = count
        while remaining > 0:
            weapon = rng.choices(player.weapons, weights)[0]
            spray = min(remaining, rng.randint(1, 14) if weapon != "dmr" else rng.randint(1, 3))
            acc, hs = _rates(player, weapon, cheating, prior)
            acc = min(0.95, max(0.02, acc + rng.gauss(0, 0.02)))
            before = 0.0
            for k in range(spray):
                rows.append(self._shot(player, match, day, weapon, k, t_ms, acc, hs, before, cheating, prior, decoy))
                before = rows[-1].applied_recoil_pitch_deg or 0.0
                if weapon == "dmr":
                    gap = rng.randint(380, 600)
                elif cheating and player.cheat == "fire-rate" and weapon == "rifle":
                    gap = rng.randint(48, 56)
                elif cheating and player.cheat == "metronome" and weapon == "rifle":
                    gap = 140
                else:
                    gap = rng.randint(105, 165)
                t_ms += gap
            t_ms += rng.randint(1_500, 9_000)
            remaining -= spray
        if contacts is not None:
            contacts.extend(
                (row.t_ms, row.enemy_id or "")
                for row in rows
                if row.information_state == "unknowable" and row.hidden_track_ms
            )
        return rows

    def _shot(
        self,
        player: Player,
        match: str,
        day: int,
        weapon: str,
        k: int,
        t_ms: int,
        acc: float,
        hs: float,
        before: float,
        cheating: bool,
        prior: bool,
        decoy: bool,
    ) -> Event:
        rng = self.rng
        cheat = player.cheat if cheating else HONEST
        hit = rng.random() < acc
        head = hit and rng.random() < hs
        distance = max(3.0, rng.gauss(WEAPONS[weapon][2] + (5.0 if cheat == "rage" else 0.0), 6.0))
        event = Event(
            game_id=self.profile.game_id,
            match_id=match,
            player_id=player.pid,
            t_ms=t_ms,
            event_type="shot",
            skill_band=player.band,
            weapon_class=weapon,
            weapon_id={"rifle": "ak", "smg": "vector", "dmr": "mk14"}[weapon],
            mod_set=player.build if weapon == "rifle" else (),
            hit=hit,
            hitbox=("head" if head else rng.choice(["upper_torso", "lower_torso", "limbs"])) if hit else None,
            distance_m=round(distance, 1),
            party_id=player.party,
        )
        if weapon == "rifle":
            self._recoil(event, player, k, before, cheat)
        if prior:
            return event
        self._information(event, player, match, day, cheat, decoy)
        return event

    def _recoil(self, event: Event, player: Player, k: int, before: float, cheat: str) -> None:
        rng = self.rng
        stock = not player.build
        kick = rng.uniform(0.8, 2.4) if stock else rng.uniform(0.35, 1.05)
        if cheat == "no-recoil":
            command = -kick + rng.uniform(0.0, 0.12)
        elif cheat == "mirror":
            command = -kick + 0.9 + self.table[k]
        elif cheat == "vendor-buyer":
            command = -player.gain * before + self.table[k]
        else:
            command = -player.gain * before + rng.gauss(0, 0.08)
        event.spray_index = k
        event.applied_recoil_pitch_deg = round(kick, 3)
        event.compensation_pitch_deg = round(command, 3)
        event.recoil_pitch_deg = round(max(0.0, kick + command), 3)

    def _information(self, event: Event, player: Player, match: str, day: int, cheat: str, decoy: bool) -> None:
        rng = self.rng
        roll = rng.random()
        hidden_rate = {"wallhack": 0.25, "quiet-radar": 0.3}.get(cheat, 0.06)
        if roll < hidden_rate:
            state = "unknowable"
        elif roll < hidden_rate + 0.12:
            state = "audio"
        else:
            state = "visible"
        event.information_state = state
        event.enemy_id = f"{match}:o{rng.randint(1, 5)}"
        jitter = player.jitter * rng.uniform(0.85, 1.15)
        if cheat == "quiet-radar" and state == "unknowable":
            jitter = rng.uniform(0.05, 0.1)
        event.aim_jitter_deg = round(jitter, 3)
        event.hidden_track_ms = 0.0
        if state == "unknowable":
            if cheat in ("wallhack", "quiet-radar"):
                event.since_perceived_ms = None
            else:
                event.since_perceived_ms = float(rng.randint(150, 4_000))
            if cheat == "wallhack":
                event.hidden_track_ms = float(rng.randint(80, 160))
            elif (event.since_perceived_ms or 0) >= self.profile.hidden_grace_ms and rng.random() < 0.35:
                event.hidden_track_ms = float(rng.randint(10, 50))
        else:
            event.since_perceived_ms = 0.0
        if day >= WIRE_SHIPS:
            moving = rng.random() < 0.7
            picture = abs(rng.gauss(0, 0.9)) + 0.1
            wire = picture + (abs(rng.gauss(0.8, 0.5)) if moving else rng.gauss(0, 0.04))
            if cheat == "wire" and rng.random() < 0.7:
                wire, picture = rng.uniform(0.1, 0.25), rng.uniform(1.8, 2.8)
            event.wire_error_deg = round(max(0.0, wire), 3)
            event.picture_error_deg = round(max(0.0, picture), 3)
            event.interp_delay_ms = 100.0
        if decoy:
            crossing = rng.random() < 0.03
            locked = cheat == "esp" and rng.random() < 0.4
            event.private_track_ms = 80.0 if locked else (float(rng.randint(20, 60)) if crossing else 0.0)

    def movement(self, player: Player, match: str, day: int) -> list[Event]:
        rng = self.rng
        cheating = player.cheat == "speed" and day >= player.start
        rows: list[Event] = []
        t_ms = rng.randint(2_000, 30_000)

        def burst(samples: int, speed: float, weight: float, cause: str = "none", ground: bool = True) -> None:
            nonlocal t_ms
            cap = curve_speed(self.profile, weight) or 5.0
            for _ in range(samples):
                rows.append(
                    Event(
                        game_id=self.profile.game_id,
                        match_id=match,
                        player_id=player.pid,
                        t_ms=t_ms,
                        event_type="movement",
                        skill_band=player.band,
                        party_id=player.party,
                        loadout_weight_kg=weight,
                        speed_mps=round(speed + rng.gauss(0, 0.05), 2),
                        on_ground=ground,
                        displacement_cause=cause,
                        expected_max_ground_speed_mps=cap,
                    )
                )
                t_ms += 100
            t_ms += rng.randint(3_000, 20_000)

        weight = max(2.0, player.weight_kg + rng.gauss(0, 0.8))
        cap = curve_speed(self.profile, weight) or 5.0
        burst(10, cap * rng.uniform(0.85, 0.99), weight)
        if cheating:
            burst(30, 7.1, 10.0)
        if rng.random() < 0.25:
            burst(6, rng.uniform(9.0, 14.0), weight, "explosion")
        if rng.random() < 0.08:
            burst(8, 15.0, weight, "vehicle")
        if rng.random() < 0.04:
            burst(2, 15.0, weight)  # an untagged two-frame glitch
        if rng.random() < 0.1:
            burst(3, 7.5, weight, ground=False)
        return rows


def _schedule(rng: random.Random, players: dict[str, Player]) -> list[list[tuple[str, list[Player], bool]]]:
    """Per day: (match_id, who plays it together, decoy on)."""
    groups: dict[str, list[Player]] = {}
    for player in players.values():
        groups.setdefault(player.party or player.pid, []).append(player)
    days: list[list[tuple[str, list[Player], bool]]] = []
    serial = 0
    for day in range(DAYS):
        weekend = day in (0, 1)
        matches = []
        for members in groups.values():
            rate = max(member.activity for member in members) * (1.3 if weekend else 1.0)
            count = int(rate) + (1 if rng.random() < rate - int(rate) else 0)
            if any(member.cheat == "esp" for member in members):
                count = max(count, 2)
            for _ in range(count):
                serial += 1
                decoy = rng.random() < DECOY_MATCHES
                if any(member.cheat == "esp" for member in members) and rng.random() < 0.6:
                    decoy = True
                matches.append((f"m{_day(day).replace('-', '')}-{serial:05d}", members, decoy))
        days.append(matches)
    return days


def _reports(rng: random.Random, players: dict[str, Player]) -> list[dict[str, int]]:
    """Cumulative report counts at the end of each day."""
    running: dict[str, int] = {}
    days: list[dict[str, int]] = []
    streamer = max((p for p in players.values() if p.band == "elite" and p.cheat == HONEST), key=lambda p: p.skill)
    for day in range(DAYS):
        for player in players.values():
            if player.cheat not in (HONEST, "boosted", "closet", "radar-friend") and day >= player.start:
                chance = 0.45
            elif player is streamer:
                chance = 1.0
            else:
                chance = 0.012 + (0.02 if player.band == "elite" else 0.0)
            if rng.random() < chance:
                running[player.pid] = running.get(player.pid, 0) + (rng.randint(4, 9) if player is streamer else rng.randint(1, 3))
        days.append(dict(running))
    return days


def _history(records: list[PlayerRecord]) -> list[HistoryWindow]:
    return [
        HistoryWindow(record.player_id, weapon.weapon_key, weapon.skill_band, weapon.shots, weapon.hits)
        for record in records
        for weapon in record.weapons
    ]


def build_week(seed: int = 7) -> Week:
    profile = load_profile(PROFILE_PATH)
    rng = random.Random(seed)
    players = _population(rng)
    _assign_cheats(rng, players)
    table = [rng.uniform(-0.35, 0.35) for _ in range(16)]
    writer = _Writer(rng, profile, table)

    # Last week: the same people, clean, four matches each. This is the frozen baseline.
    prior: list[Event] = []
    for player in players.values():
        for index in range(4):
            prior += writer.shots(player, f"prior-{player.pid}-{index}", -1, prior=True, decoy=False, count=rng.randint(34, 46))
    prior_records = summarize(prior, profile)
    cohort = build_cohorts(prior_records, profile)
    history = _history(prior_records)

    schedule = _schedule(rng, players)
    reports = _reports(rng, players)
    by_day: list[list[Event]] = []
    for day, matches in enumerate(schedule):
        rows: list[Event] = []
        for match, members, decoy in matches:
            contacts: list[tuple[int, str]] = []
            for member in sorted(members, key=lambda p: p.cheat != "wallhack"):
                if member.cheat == "radar-friend" and day >= member.start and contacts:
                    rows += _friend_swings(rng, member, match, profile, contacts)
                count = max(8, int(rng.gauss(30, 8)))
                calls = contacts if member.cheat == "wallhack" and day >= member.start else None
                rows += writer.shots(member, match, day, prior=False, decoy=decoy, count=count, contacts=calls)
                rows += writer.movement(member, match, day)
        by_day.append(rows)

    nightly = [run_score(rows, profile, cohort, history, reports[day]) for day, rows in enumerate(by_day)]
    events = [event for rows in by_day for event in rows]
    records = summarize(events, profile)
    cases = run_score(events, profile, cohort, history, reports[-1])
    truth = {pid: player.cheat for pid, player in players.items()}
    return Week(
        profile=profile,
        days=[_day(day) for day in range(DAYS)],
        players=players,
        events=events,
        prior_events=len(prior),
        cohort=cohort,
        history=history,
        reports=reports[-1],
        nightly=nightly,
        cases=cases,
        records=records,
        truth=truth,
        features={
            "baseline": f"Baseline frozen {_short(-7)} – {_short(-1)}: the same players, clean",
            "wire": f"Wire and picture fields shipped {_short(WIRE_SHIPS)}",
            "decoys": f"Private replays on about {DECOY_MATCHES:.0%} of matches",
        },
    )


def _friend_swings(
    rng: random.Random, friend: Player, match: str, profile: GameProfile, contacts: list[tuple[int, str]]
) -> list[Event]:
    """Swings on the wallhacker's hidden enemy, 25 to 60 ms after each call."""
    rows = []
    for stamp, enemy in contacts[:8]:
        rows.append(
            Event(
                game_id=profile.game_id,
                match_id=match,
                player_id=friend.pid,
                t_ms=stamp + rng.randint(25, 60),
                event_type="shot",
                skill_band=friend.band,
                weapon_class="rifle",
                weapon_id="ak",
                mod_set=friend.build,
                party_id=friend.party,
                information_state="unknowable",
                enemy_id=enemy,
                aim_jitter_deg=round(friend.jitter, 3),
                hidden_track_ms=0.0,
            )
        )
    return rows
