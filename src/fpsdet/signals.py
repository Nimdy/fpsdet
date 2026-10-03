"""Signals that sit inside a human average and are still not human.

A no-recoil rule sees the camera. A mirror script cancels the kick and paints
a legal camera on top, so the command stream is the evidence. A fire-rate rule
sees illegal gaps. A metronome fires legally, with no motor noise. An accuracy
rule sees hits. A wallhack that tracks a hidden mover can miss every shot and
still have known where they were. A person aims at the picture the official
client draws. A packet aimbot aims at the snapshot the server just sent.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from .baseline import CohortTable, sample_std
from .models import Case, GameProfile, RecoilSummary, WeaponSummary
from .statsutil import median


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n != len(ys) or n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = 0.0
    var_x = 0.0
    var_y = 0.0
    for x_value, y_value in zip(xs, ys):
        dx = x_value - mean_x
        dy = y_value - mean_y
        num += dx * dy
        var_x += dx * dx
        var_y += dy * dy
    if var_x <= 1e-12 or var_y <= 1e-12:
        return None
    return num / math.sqrt(var_x * var_y)


def mirror_break(applied: list[float], command: list[float], profile: GameProfile) -> str | None:
    """Review when the view command is the server kick, flipped, on the same tick.

    A person who pulls down does it late. Lag 0 has to beat that lagged
    correlation by ``mirror_lag_gap``, and the kick has to actually move.
    A flat kick has no correlation to test and is left to the recoil floor.
    """
    count = min(len(applied), len(command))
    if count < profile.mirror_min_shots:
        return None
    applied = applied[:count]
    command = command[:count]
    same_tick = pearson(applied, command)
    if same_tick is None or same_tick > profile.mirror_max_r:
        return None
    lag = profile.mirror_lag_shots
    lagged = None
    if lag > 0 and count > lag + 3:
        lagged = pearson(applied[:-lag], command[lag:])
    if lagged is not None and same_tick > lagged - profile.mirror_lag_gap:
        return None
    lagged_text = "n/a" if lagged is None else f"{lagged:.2f}"
    return (
        f"player command matched the server kick on the same tick "
        f"(r {same_tick:.2f}) and was looser at a human lag (r {lagged_text}), {count} shots"
    )


# A spray index needs this many sprays before its usual kick is known.
PATTERN_MIN_SPRAYS = 3


def _by_spray(spray: list[int | None], count: int) -> dict[int, list[int]] | None:
    if len(spray) < count or any(index is None for index in spray[:count]):
        return None
    groups: dict[int, list[int]] = defaultdict(list)
    for position in range(count):
        groups[spray[position]].append(position)  # type: ignore[index]
    return groups


def mirror_check(recoil: RecoilSummary, profile: GameProfile) -> tuple[str | None, str | None]:
    """Run the mirror test on the part of the kick a person could not have learned.

    A memorised spray pattern is anticipated on the same tick by a practiced
    player, so the raw kick would frame them. Subtract the average kick and the
    average command at each spray_index, and what is left is the part of the
    kick that changes from spray to spray. Nobody can anticipate that. A
    script that reads it still cancels it on the same tick.

    Returns (finding, note). The note says why the test did not run.
    """
    count = min(len(recoil.applied), len(recoil.compensation))
    if count < profile.mirror_min_shots:
        return None, None
    applied = recoil.applied[:count]
    command = recoil.compensation[:count]
    groups = _by_spray(recoil.spray, count)
    if groups is not None:
        known = {index: rows for index, rows in groups.items() if len(rows) >= PATTERN_MIN_SPRAYS}
        keep = sorted(position for rows in known.values() for position in rows)
        if len(keep) >= profile.mirror_min_shots:
            kick_mean = {i: sum(applied[p] for p in rows) / len(rows) for i, rows in known.items()}
            cmd_mean = {i: sum(command[p] for p in rows) / len(rows) for i, rows in known.items()}
            spray = recoil.spray
            kick_left = [applied[p] - kick_mean[spray[p]] for p in keep]  # type: ignore[index]
            cmd_left = [command[p] - cmd_mean[spray[p]] for p in keep]  # type: ignore[index]
            found = mirror_break(kick_left, cmd_left, profile)
            if found:
                found += ", after the spray pattern was removed"
            return found, None
    if profile.recoil_pattern == "random":
        return mirror_break(applied, command, profile), None
    return None, (
        f"{recoil.build_key} mirror check needs {PATTERN_MIN_SPRAYS} sprays that reach the same "
        "spray_index, so a memorised pattern is not mistaken for a script. "
        "Set recoil_pattern to random only if every kick is drawn fresh."
    )


# A gap longer than this many cycles is a pause between bursts, not the firing cadence.
CADENCE_CYCLES = 2.5
# A match needs this many cadence gaps before its spread means anything.
METRONOME_MATCH_GAPS = 5


def match_gaps(weapon: WeaponSummary) -> list[list[int]]:
    """Fire gaps one match at a time. A summary built by hand has one pooled list."""
    if weapon.fire_matches:
        return list(weapon.fire_matches.values())
    return [weapon.fire_gaps] if weapon.fire_gaps else []


def metronome_break(weapon: WeaponSummary, profile: GameProfile) -> str | None:
    """Legal gaps with no variation. The server-paced weapons opt out.

    Only the cadence counts: gaps inside a burst. A macro fires a burst, pauses
    while the player repositions, and fires another; the pauses vary like a
    person's do, and they would hide a perfect cadence if they were measured.
    Each match is judged alone, so a macro switched on halfway through the week
    is not averaged away by the honest matches before it.
    """
    rule = profile.weapon_rule(weapon.weapon_class, weapon.weapon_key)
    if rule is None or rule.min_shot_interval_ms is None or rule.server_paced:
        return None
    legal = rule.min_shot_interval_ms - rule.interval_slack_ms
    steady: list[int] = []
    spreads: list[float] = []
    matches = 0
    for gaps in match_gaps(weapon):
        cadence = [gap for gap in gaps if gap <= CADENCE_CYCLES * rule.min_shot_interval_ms]
        if len(cadence) < METRONOME_MATCH_GAPS:
            continue
        if sum(cadence) / len(cadence) < legal:
            continue
        if profile.tick_ms and all(gap == profile.tick_ms for gap in cadence):
            continue
        spread = sample_std([float(gap) for gap in cadence])
        if spread > profile.metronome_max_std_ms:
            continue
        steady.extend(cadence)
        spreads.append(spread)
        matches += 1
    if len(steady) < profile.metronome_min_gaps:
        return None
    mean = sum(steady) / len(steady)
    where = f" in {matches} matches" if matches > 1 else ""
    return (
        f"{weapon.weapon_key} fire interval std {max(spreads):.2f} ms across {len(steady)} legal gaps{where} "
        f"(mean {mean:.0f} ms)"
    )


def hidden_break(weapon: WeaponSummary, profile: GameProfile) -> str | None:
    """Time the aim spent on an enemy the server had not made visible."""
    samples = [ms for ms in weapon.hidden_track_ms if ms > 0]
    if len(samples) < profile.hidden_track_min_samples:
        return None
    total = sum(samples)
    if total < profile.hidden_track_min_ms:
        return None
    return (
        f"{weapon.weapon_key} aim stayed on a hidden mover for {total:.0f} ms across {len(samples)} shots"
    )


def private_break(weapon: WeaponSummary, profile: GameProfile) -> str | None:
    """Time the aim spent on a replay this client was not allowed to perceive.

    Same bar as a hidden mover. One crossing is not a review. The field is the
    server's measurement of its own private body. A body this client could see,
    labeled private, is an emitter bug.
    """
    samples = [ms for ms in weapon.private_track_ms if ms > 0]
    if len(samples) < profile.hidden_track_min_samples:
        return None
    total = sum(samples)
    if total < profile.hidden_track_min_ms:
        return None
    return (
        f"{weapon.weapon_key} aim stayed on a private replay for {total:.0f} ms across {len(samples)} shots"
    )


# A shot is on the wire when the snapshot explains the aim and the picture does not.
# Standing targets and coin-flip noise stay under the gap. Not an accuracy ceiling.
WIRE_ERROR_RATIO = 0.35
WIRE_MIN_GAP_DEG = 0.20


def wire_break(weapon: WeaponSummary, profile: GameProfile) -> str | None:
    """Aim closer to the snapshot than to the picture, before the delay elapses.

    The wire is the quantized position the server just sent. The picture is
    where the official client draws that player, one interpolation delay
    earlier. A standing target makes the two errors match. One shot is not a
    case. The gap is a separation floor, not a limit on how close a legal aim
    may sit to the picture. Omit a side, or send a delay that has not started,
    and that shot does not count.
    """
    led: list[float] = []
    for wire, picture, delay in zip(
        weapon.wire_error_deg, weapon.picture_error_deg, weapon.interp_delay_ms
    ):
        if delay <= 0 or wire < 0 or picture < 0:
            continue
        if picture - wire < WIRE_MIN_GAP_DEG:
            continue
        if picture <= 0 or wire > WIRE_ERROR_RATIO * picture:
            continue
        led.append(delay)
    if len(led) < profile.hidden_track_min_samples:
        return None
    total = sum(led)
    if total < profile.hidden_track_min_ms:
        return None
    return (
        f"{weapon.weapon_key} aim matched the wire snapshot instead of the drawn picture on "
        f"{len(led)} shots ({total:.0f} ms of interpolation delay in total)"
    )


def poison_alarms(
    previous: CohortTable,
    current: CohortTable,
    *,
    jump: float,
    min_players: int,
) -> list[str]:
    """Ceiling moved up. The cohort is learning the cheat."""
    watched = {"accuracy", "headshot_rate", "geometry_rate"}
    shared = set(previous._values) & set(current._values)
    alarms: list[str] = []
    for band, key, metric in sorted(shared):
        if metric not in watched:
            continue
        old = previous.dist(band, key, metric, None)
        new = current.dist(band, key, metric, None)
        if old is None or new is None or old.n < min_players or new.n < min_players:
            continue
        if new.maximum >= old.maximum + jump:
            alarms.append(
                f"{band} {key} {metric} maximum moved {old.maximum:.0%} -> {new.maximum:.0%}"
            )
    return alarms


def smoothness_break(weapon: WeaponSummary, profile: GameProfile) -> str | None:
    """Aim gets quiet only while the server says this client could not have known.

    Audio and a visible enemy are the knowable baseline. A player who is smooth
    all the time has no drop to score. Samples with no information_state are
    ignored, because a missing label is not evidence.
    """
    knowable = weapon.knowable_jitter
    unknowable = weapon.unknowable_jitter
    if (
        len(knowable) < profile.unknowable_min_samples
        or len(unknowable) < profile.unknowable_min_samples
    ):
        return None
    know = median(knowable)
    hidden = median(unknowable)
    if know < 0.05 or hidden > profile.unknowable_jitter_ratio * know:
        return None
    return (
        f"{weapon.weapon_key} aim noise dropped to {hidden:.2f} deg while this client could not "
        f"have known (knowable median {know:.2f} deg, {len(unknowable)} samples)"
    )


def command_residual(
    applied: list[float], command: list[float], spray: list[int | None] | None = None
) -> list[float] | None:
    """What is left of the player command after the server kick and its lag are removed.

    With spray indices, the lagged kick is the previous shot of the same spray,
    and nothing at the first shot. A person reacts to the kick they just felt,
    not to the last shot of a spray that ended seconds ago. Lagging across the
    gap would leave the same spike at every spray's first shot, for every
    player, and unrelated accounts would correlate on it.
    """
    count = min(len(applied), len(command))
    if count < 8:
        return None
    in_spray = spray is not None and _by_spray(spray, count) is not None
    rows: list[tuple[float, float, float]] = []
    values: list[float] = []
    for index in range(1, count):
        lagged = applied[index - 1]
        if in_spray and spray[index] != spray[index - 1] + 1:  # type: ignore[index, operator]
            lagged = 0.0
        rows.append((1.0, applied[index], lagged))
        values.append(command[index])
    coeff = _solve3(rows, values)
    if coeff is None:
        return None
    intercept, same, lagged = coeff
    residual = [
        value - (intercept + same * row[1] + lagged * row[2])
        for row, value in zip(rows, values)
    ]
    if sample_std(residual) < 1e-4:
        return None
    return residual


def leftover_signature(
    recoil: RecoilSummary, min_points: int = 12
) -> tuple[dict[tuple[str, int], float], int] | None:
    """The leftover, keyed so two accounts line up shot for shot.

    With spray_index, each point is the average leftover at that spray index,
    so a humanizer table lines up across accounts whatever their spray lengths
    or where their logs start. Each average is divided by its standard error, so
    every point has the same weight: a late spray index reached by two sprays is
    noisy, and unscaled it would decide the correlation by itself. Indices
    reached by fewer than PATTERN_MIN_SPRAYS sprays are left out. Without
    spray_index, or with fewer than ``min_points`` indices that qualify (one long
    spray, say), points are keyed by position in the log, which only lines up a
    replay that starts at the same shot.
    Returns (points, leftover samples).
    """
    residual = command_residual(recoil.applied, recoil.compensation, recoil.spray)
    if residual is None:
        return None
    count = min(len(recoil.applied), len(recoil.compensation))
    by_position = {("position", index): value for index, value in enumerate(residual, start=1)}
    if _by_spray(recoil.spray, count) is None:
        return by_position, len(residual)
    sums: dict[int, list[float]] = defaultdict(list)
    for position, value in enumerate(residual, start=1):
        sums[recoil.spray[position]].append(value)  # type: ignore[index]
    spread = sample_std(residual)
    points = {
        ("spray", index): (sum(rows) / len(rows)) * math.sqrt(len(rows)) / spread
        for index, rows in sums.items()
        if len(rows) >= PATTERN_MIN_SPRAYS
    }
    if len(points) < min_points:
        return by_position, len(residual)
    return points, len(residual)


def _solve3(
    rows: list[tuple[float, float, float]], values: list[float]
) -> tuple[float, float, float] | None:
    xtx = [[0.0, 0.0, 0.0] for _ in range(3)]
    xty = [0.0, 0.0, 0.0]
    for row, value in zip(rows, values):
        for left in range(3):
            xty[left] += row[left] * value
            for right in range(3):
                xtx[left][right] += row[left] * row[right]
    matrix = [xtx[index][:] + [xty[index]] for index in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row_index: abs(matrix[row_index][col]))
        if abs(matrix[pivot][col]) < 1e-12:
            return None
        matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
        scale = matrix[col][col]
        for column in range(col, 4):
            matrix[col][column] /= scale
        for row_index in range(3):
            if row_index == col:
                continue
            factor = matrix[row_index][col]
            for column in range(col, 4):
                matrix[row_index][column] -= factor * matrix[col][column]
    return matrix[0][3], matrix[1][3], matrix[2][3]


def evidence_seal(case: Case) -> str:
    """Same case, same hash. A changed finding is a different packet."""
    body = {
        "v": 1,
        "player_id": case.player_id,
        "game_id": case.game_id,
        "decision": case.decision,
        "reasons": case.reasons,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()
