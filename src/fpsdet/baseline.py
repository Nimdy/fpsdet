"""Cohorts are distributions of per-player numbers, not of single shots."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .models import BANDS, GameProfile, PlayerRecord
from .statsutil import median, percentile


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
