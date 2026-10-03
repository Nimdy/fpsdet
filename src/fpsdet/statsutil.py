"""Small numeric helpers. Ports must match these definitions."""

from __future__ import annotations

import math


# One-sided 95% normal quantile. Used by the Wilson bound.
Z95 = 1.6448536269514722


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median of empty")
    ys = sorted(values)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return 0.5 * (ys[mid - 1] + ys[mid])


def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile. q is in [0, 1]. rank = ceil(q * n), 1-based."""
    if not sorted_values:
        raise ValueError("percentile of empty")
    n = len(sorted_values)
    if q <= 0:
        return sorted_values[0]
    if q >= 1:
        return sorted_values[-1]
    rank = math.ceil(q * n)
    rank = min(max(rank, 1), n)
    return sorted_values[rank - 1]


def mad(values: list[float]) -> float:
    if not values:
        raise ValueError("mad of empty")
    mid = median(values)
    return median([abs(v - mid) for v in values])


def wilson_bound(successes: float, n: float, *, upper: bool, z: float = Z95) -> float:
    """One-sided 95% Wilson bound for a rate. Empty samples return 0.

    Counts may be fractional after a design effect has shrunk them.
    """
    if n <= 0:
        return 0.0
    successes = max(0, min(successes, n))
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    value = center + margin if upper else center - margin
    return min(1.0, max(0.0, value))


def wilson_lower(successes: float, n: float) -> float:
    return wilson_bound(successes, n, upper=False)


def wilson_upper(successes: float, n: float) -> float:
    return wilson_bound(successes, n, upper=True)


# Fewer matches than this and the between-match spread is not measurable.
DESIGN_MIN_GROUPS = 5


def design_effect(groups: list[tuple[int, int]]) -> float:
    """How much more a rate moves between matches than independent shots would.

    ``groups`` is (trials, successes) per match. Shots in one match share an
    opponent, a map, and a mood, so they are not independent. The Pearson
    dispersion of the per-match rates is the factor the effective sample
    shrinks by. Never below 1, and 1 when there are too few matches to tell.
    """
    rows = [(n, k) for n, k in groups if n > 0]
    if len(rows) < DESIGN_MIN_GROUPS:
        return 1.0
    total = sum(n for n, _ in rows)
    p = sum(k for _, k in rows) / total
    if p <= 0.0 or p >= 1.0:
        return 1.0
    chi2 = sum((k - n * p) ** 2 / (n * p * (1.0 - p)) for n, k in rows)
    return max(1.0, chi2 / (len(rows) - 1))


def clustered_lower(successes: int, n: int, deff: float) -> float:
    """Wilson lower bound on the effective sample: n / deff trials."""
    if deff <= 1.0:
        return wilson_lower(successes, n)
    return wilson_lower(successes / deff, n / deff)
