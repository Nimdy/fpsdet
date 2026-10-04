"""Cohorts are distributions of per-player numbers, not of single shots."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import BANDS, Event, GameProfile, PlayerRecord
from .statsutil import median, percentile, wilson_lower

# Fewer matches than this and the window's median match is not a line worth drawing.
SCREEN_MIN_MATCHES = 10
# MAD times this is the standard deviation of a normal sample.
MAD_TO_SD = 1.4826


@dataclass
class Dist:
    n: int
    values: list[float]
    mid: float
    p05: float
    p95: float
    minimum: float
    maximum: float


class CohortTable:
    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], list[tuple[str, float]]] = defaultdict(list)
        self.integrity: dict = {"status": "unchecked", "alarms": []}

    def add(self, band: str, key: str, metric: str, player_id: str, value: float) -> None:
        self._values[(band, key, metric)].append((player_id, value))

    def series(self, band: str, key: str, metric: str, exclude: str | None) -> list[float]:
        return [value for pid, value in self._values.get((band, key, metric), []) if pid != exclude]

    def dist(self, band: str, key: str, metric: str, exclude: str | None) -> Dist | None:
        return make_dist(self.series(band, key, metric, exclude))

    def extreme(
        self, key: str, metric: str, exclude: str | None, min_players: int, *, high: bool
    ) -> tuple[str, float] | None:
        """The most extreme human measured in any thick band, and that band.

        Rank does not order every number. Long engagements are a habit of
        some players in every band, so "past every measured human" looks at all
        of them, not only the top band.
        """
        best: tuple[str, float] | None = None
        for band in (*BANDS, "unrated"):
            values = self.series(band, key, metric, exclude)
            if len(values) < min_players:
                continue
            value = max(values) if high else min(values)
            if best is None or (value > best[1] if high else value < best[1]):
                best = (band, value)
        return best

    def ceiling_band(
        self, key: str, metric: str, exclude: str | None, min_players: int
    ) -> str | None:
        """The highest rated band with enough players. A server that sends no rank is one band."""
        for band in (*reversed(BANDS), "unrated"):
            if len(self.series(band, key, metric, exclude)) >= min_players:
                return band
        return None


def make_dist(values: list[float]) -> Dist | None:
    if not values:
        return None
    ys = sorted(values)
    return Dist(
        n=len(ys),
        values=ys,
        mid=median(ys),
        p05=percentile(ys, 0.05),
        p95=percentile(ys, 0.95),
        minimum=ys[0],
        maximum=ys[-1],
    )


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mid = sum(values) / len(values)
    return math.sqrt(sum((v - mid) ** 2 for v in values) / (len(values) - 1))


def _thick(dist: Dist | None, min_players: int) -> bool:
    return dist is not None and dist.n >= min_players


def build_cohorts(records: list[PlayerRecord], profile: GameProfile) -> CohortTable:
    table = CohortTable()
    min_shots = profile.min_shots
    min_hits = profile.min_hits_for_headshot
    for record in records:
        for weapon in record.weapons:
            band = weapon.skill_band
            key = weapon.weapon_key
            pid = record.player_id
            if weapon.shots >= min_shots:
                table.add(band, key, "accuracy", pid, weapon.hits / weapon.shots)
            if weapon.head_known_hits >= min_hits and (
                weapon.hits == 0 or weapon.head_known_hits >= 0.9 * weapon.hits
            ):
                table.add(band, key, "headshot_rate", pid, weapon.head_hits / weapon.head_known_hits)
            if len(weapon.distances) >= min_shots:
                table.add(band, key, "median_distance", pid, median(weapon.distances))
            if weapon.geometry_known >= min_shots:
                table.add(
                    band, key, "geometry_rate", pid, weapon.geometry_true / weapon.geometry_known
                )
            if len(weapon.view_deltas) >= min_shots:
                table.add(band, key, "view_p95", pid, percentile(sorted(weapon.view_deltas), 0.95))
            if len(weapon.acquire_ms) >= min_shots:
                table.add(band, key, "acquire_median", pid, median(weapon.acquire_ms))
                table.add(band, key, "acquire_std", pid, sample_std(weapon.acquire_ms))
        for recoil in record.recoils:
            if len(recoil.pitches) >= profile.recoil_min_run:
                table.add(
                    recoil.skill_band,
                    recoil.build_key,
                    "recoil",
                    record.player_id,
                    median(recoil.pitches),
                )
        for extra in record.extras:
            spec = next((item for item in profile.extra_metrics if item.name == extra.name), None)
            if spec is None or len(extra.values) < spec.min_samples:
                continue
            table.add(extra.group_key, extra.name, "extra", record.player_id, median(extra.values))
    return table


def cohort_is_thick(dist: Dist | None, profile: GameProfile) -> bool:
    return _thick(dist, profile.min_cohort_players)


@dataclass
class MatchScreen:
    """Matches whose whole lobby sits far outside the rest of the window."""

    judged: bool = False
    matches: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


def _outlier_line(rates: list[float], spreads: float) -> tuple[float, float] | None:
    """The median rate and the line ``spreads`` robust standard deviations above it."""
    if len(rates) < SCREEN_MIN_MATCHES:
        return None
    mid = median(rates)
    spread = MAD_TO_SD * median([abs(rate - mid) for rate in rates])
    if spread <= 0:
        return None
    return mid, mid + spreads * spread


def screen_matches(events: list[Event], profile: GameProfile) -> MatchScreen:
    """A lobby where cheaters played each other, found before it becomes the ceiling.

    Nobody reviews every match before a baseline is frozen, and a hack-vs-hack lobby
    nobody was banned from looks like any other match until its numbers sit beside the
    window's. Each match is one point: every shot fired in it, by everyone. A match is out
    of line when the lower bound of its hit rate, or of its rate of shots through geometry,
    is more than ``match_outlier_sd`` robust standard deviations above the window's median
    match. The median and the spread come from the window, so the line moves with the game.
    A lobby of very good honest players sits a few spreads up, not five.
    """
    shots: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    known: Counter[str] = Counter()
    through: Counter[str] = Counter()
    for event in events:
        if event.event_type != "shot":
            continue
        shots[event.match_id] += 1
        hits[event.match_id] += int(event.hit)
        if event.through_geometry is not None:
            known[event.match_id] += 1
            through[event.match_id] += int(event.through_geometry)
    # Only matches big enough to have a rate of their own set the median and the spread.
    hit_line = _outlier_line(
        [hits[m] / shots[m] for m in shots if shots[m] >= 5 * profile.min_shots], profile.match_outlier_sd
    )
    geo_line = _outlier_line(
        [through[m] / known[m] for m in known if known[m] >= profile.min_shots], profile.match_outlier_sd
    )
    screen = MatchScreen(judged=hit_line is not None or geo_line is not None)
    for match in sorted(shots):
        reasons = []
        # A match too small to put anyone in the baseline is not judged on its own noise.
        if hit_line is not None and shots[match] >= profile.min_shots:
            low = wilson_lower(hits[match], shots[match])
            if low > hit_line[1]:
                reasons.append(
                    f"the lobby hit {hits[match] / shots[match]:.0%} of {shots[match]} shots (lower bound {low:.0%}); "
                    f"the median match hits {hit_line[0]:.0%} and the line is {hit_line[1]:.0%}"
                )
        if geo_line is not None and known[match] >= profile.min_shots:
            low = wilson_lower(through[match], known[match])
            if low > geo_line[1]:
                reasons.append(
                    f"{through[match] / known[match]:.0%} of {known[match]} traced shots went through geometry "
                    f"(lower bound {low:.0%}); the median match is {geo_line[0]:.0%} and the line is {geo_line[1]:.0%}"
                )
        if reasons:
            screen.matches.append(match)
            screen.lines.append(f"match {match}: " + "; ".join(reasons))
    return screen
