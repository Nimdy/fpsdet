"""Turn a player record into a case. Never a ban.

Review means one of:
- sustained break of a server gear rule (speed, fire interval, metronome,
  blatant recoil, same-tick recoil mirror, tracking a hidden mover, aim on a
  private replay, aim that matches the wire snapshot ahead of the picture the
  client draws, or aim noise that drops only while the server says this
  client could not have known)
- learned recoil far under every human measured on that same build
- confidently past the best measured human on two or more kinds of combat
  number (accuracy, headshots, distance, geometry, a primary extra). The same
  number on two weapons is one kind: the best human in the building is often
  the best on every gun.
- past the best measured human on one kind, and the account no longer looks
  like itself or a supporting tell agrees

Watch means the player is out of place for their rank, at the top of the human
range, or the account jumped, and a person can look when reports or time allow.
Outperforming your own rank but staying inside the best humans is a watch.
That is a smurf or a good player, not a review.
"""

from __future__ import annotations

import math

from .baseline import CohortTable, Dist, cohort_is_thick, sample_std
from .evidence import KINDS, Observation
from .challenge import (
    FOLLOWED,
    ChallengeRegistry,
    challenge_context,
    challenge_evidence,
    challenge_line,
    challenge_notes,
    evaluate_challenges,
    legacy_challenge_id,
    legacy_context,
    legacy_evidence,
)
from .knowledge import FUTURE_CHANNELS, presentation, private_knowledge
from .models import (
    ACTIONS,
    Case,
    GameProfile,
    HistoryWindow,
    MetricView,
    PlayerRecord,
    WeaponSummary,
)
from .signals import (
    evidence_seal,
    hidden_finding,
    keyed_match_gaps,
    leftover_signature,
    metronome_finding,
    mirror_check_finding,
    private_finding,
    pearson,
    smoothness_finding,
    wire_finding,
)
from .statsutil import clustered_lower, design_effect, median, median_bound, percentile, wilson_upper

# A rate that moves this much more between matches than chance is worth a line on the case.
NOTE_DESIGN_EFFECT = 1.5
# A match needs this many gaps on a gun before its own violation rate means anything.
FIRE_MATCH_GAPS = 5


def decide(
    *,
    physics: bool,
    beyond_human: int,
    beyond_band: int,
    supporting: int,
    identity: bool,
    compared: bool,
) -> str:
    if physics:
        return "review"
    if beyond_human >= 2 or (beyond_human >= 1 and (identity or supporting >= 1)):
        return "review"
    if beyond_band or beyond_human or identity or supporting >= 2:
        return "watch"
    if compared:
        return "clean"
    return "insufficient_data"


def _view(
    name: str,
    player_value: float | None,
    bound: float | None,
    own: Dist | None,
    ceiling_name: str | None,
    ceiling: Dist | None,
    *,
    beyond_band: bool,
    beyond_human: bool,
    skipped: str | None = None,
) -> MetricView:
    return MetricView(
        name=name,
        player_value=player_value,
        bound=bound,
        own_p95=None if own is None else own.p95,
        own_max=None if own is None else own.maximum,
        ceiling_band=ceiling_name,
        ceiling_p95=None if ceiling is None else ceiling.p95,
        ceiling_extreme=None if ceiling is None else ceiling.maximum,
        beyond_band=beyond_band,
        beyond_human=beyond_human,
        skipped=skipped,
    )


def _rate_flags(
    name: str,
    successes: int,
    total: int,
    point: float,
    record: PlayerRecord,
    weapon: WeaponSummary,
    cohorts: CohortTable,
    profile: GameProfile,
    deff: float = 1.0,
) -> tuple[MetricView, bool, bool, bool]:
    """Return view, beyond_band, beyond_human, compared.

    ``deff`` shrinks the sample when the rate swings between matches more than
    independent shots would, so one hot match cannot carry the bound.
    """
    view, band, human, did, _facts = _rate_compare(name, successes, total, point, record, weapon, cohorts, profile, deff)
    return view, band, human, did


def _rate_compare(
    name: str,
    successes: int,
    total: int,
    point: float,
    record: PlayerRecord,
    weapon: WeaponSummary,
    cohorts: CohortTable,
    profile: GameProfile,
    deff: float = 1.0,
) -> tuple[MetricView, bool, bool, bool, dict]:
    """``_rate_flags``, and the numbers it compared (empty when it could not compare)."""
    own = cohorts.dist(weapon.skill_band, weapon.weapon_key, name, record.player_id)
    ceiling_name = cohorts.ceiling_band(
        weapon.weapon_key, name, record.player_id, profile.min_cohort_players
    )
    ceiling = (
        None
        if ceiling_name is None
        else cohorts.dist(ceiling_name, weapon.weapon_key, name, record.player_id)
    )
    if total <= 0 or not cohort_is_thick(own, profile) or not cohort_is_thick(ceiling, profile):
        why = "cohort for this weapon and rank is still thinner than min_cohort_players"
        return _view(name, point, None, own, ceiling_name, ceiling, beyond_band=False, beyond_human=False, skipped=why), False, False, False, {}
    lower = clustered_lower(successes, total, deff)
    past_band = lower > own.p95  # type: ignore[union-attr]
    top = cohorts.extreme(weapon.weapon_key, name, record.player_id, profile.min_cohort_players, high=True)
    best_band, best = top if top is not None else (ceiling_name, ceiling.maximum)  # type: ignore[union-attr]
    past_human = lower > best
    # The band named on the case is the one holding the best human, so its p95 comes from that band too.
    shown = ceiling if best_band == ceiling_name else cohorts.dist(best_band, weapon.weapon_key, name, record.player_id)
    view = _view(name, point, lower, own, best_band, shown, beyond_band=past_band, beyond_human=past_human)
    view.ceiling_extreme = best
    facts = {
        "metric": name,
        "successes": successes,
        "trials": total,
        "observed": point,
        "matches": len(weapon.per_match),
        "bound": {"method": "wilson_lower", "level": 0.95, "design_effect": deff, "value": lower},
        "rank": {"band": weapon.skill_band, "players": own.n, "p95": own.p95},  # type: ignore[union-attr]
        "ceiling": {"band": best_band, "players": shown.n, "max": best},  # type: ignore[union-attr]
        "past_rank": past_band,
        "past_human": past_human,
    }
    return view, past_band, past_human, True, facts


def _continuous_flags(
    name: str,
    value: float,
    record: PlayerRecord,
    weapon: WeaponSummary,
    cohorts: CohortTable,
    profile: GameProfile,
    *,
    direction: str,
    bound: float | None = None,
    samples: int = 0,
) -> tuple[MetricView, bool, bool, bool, dict]:
    """``bound`` is the conservative end of the player's number, when the number has one.

    Also returns the numbers it compared, empty when it could not compare.
    """
    own = cohorts.dist(weapon.skill_band, weapon.weapon_key, name, record.player_id)
    ceiling_name = cohorts.ceiling_band(
        weapon.weapon_key, name, record.player_id, profile.min_cohort_players
    )
    ceiling = (
        None
        if ceiling_name is None
        else cohorts.dist(ceiling_name, weapon.weapon_key, name, record.player_id)
    )
    if not cohort_is_thick(own, profile) or not cohort_is_thick(ceiling, profile):
        why = "cohort for this weapon and rank is still thinner than min_cohort_players"
        return _view(name, value, value, own, ceiling_name, ceiling, beyond_band=False, beyond_human=False, skipped=why), False, False, False, {}
    assert own is not None and ceiling is not None
    # Past the rank is the rank's tail. Past humans is past every human measured,
    # the same bar the rates use. The top band's p95 is crossed by one player in twenty.
    top = cohorts.extreme(
        weapon.weapon_key, name, record.player_id, profile.min_cohort_players, high=direction == "high"
    )
    tested = value if bound is None else bound
    if direction == "high":
        best_band, extreme = top if top is not None else (ceiling_name, ceiling.maximum)
        past_band = tested > own.p95
        past_human = tested > extreme
    else:
        best_band, extreme = top if top is not None else (ceiling_name, ceiling.minimum)
        past_band = tested < own.p05
        past_human = tested < extreme
    shown = ceiling if best_band == ceiling_name else cohorts.dist(best_band, weapon.weapon_key, name, record.player_id)
    view = _view(
        name, value, tested, own, best_band, shown, beyond_band=past_band, beyond_human=past_human
    )
    view.ceiling_extreme = extreme
    high = direction == "high"
    facts: dict = {
        "metric": name,
        "observed": value,
        "samples": samples,
        "direction": direction,
        "rank": {"band": weapon.skill_band, "players": own.n, **({"p95": own.p95} if high else {"p05": own.p05})},
        "ceiling": {"band": best_band, "players": shown.n if shown else None, ("max" if high else "min"): extreme},
        "past_rank": past_band,
        "past_human": past_human,
    }
    if bound is not None:
        facts["bound"] = {"method": "median_order_statistic", "side": "lower" if high else "upper", "level": 0.95, "value": bound}
    return view, past_band, past_human, True, facts


def history_for(record: PlayerRecord, history: list[HistoryWindow]) -> list[HistoryWindow]:
    """The history rows the account check can read for this player: its own, on a weapon key and band it used now.

    Provenance fingerprints exactly these rows, so the two cannot drift apart.
    """
    used = {(weapon.weapon_key, weapon.skill_band) for weapon in record.weapons}
    return [row for row in history if row.player_id == record.player_id and (row.weapon_key, row.skill_band) in used]


def _identity(
    record: PlayerRecord, history: list[HistoryWindow], profile: GameProfile
) -> tuple[bool, str, dict, WeaponSummary | None]:
    """The first weapon on which this window is confidently above the account's own history."""
    own = history_for(record, history)
    for weapon in record.weapons:
        matched = [row for row in own if row.weapon_key == weapon.weapon_key and row.skill_band == weapon.skill_band]
        shots = sum(row.shots for row in matched)
        hits = sum(row.hits for row in matched)
        if shots < profile.min_shots or weapon.shots < profile.min_shots:
            continue
        deff = design_effect([(row[0], row[1]) for row in weapon.per_match.values()])
        lower = clustered_lower(weapon.hits, weapon.shots, deff)
        upper = wilson_upper(hits, shots)
        gap = lower - upper
        if gap >= profile.self_jump_gap:
            text = (
                f"{weapon.weapon_key} accuracy is confidently above this account's own history "
                f"by {gap:.0%} (history upper {upper:.0%}, this window lower {lower:.0%})"
            )
            facts = {
                "metric": "accuracy",
                "band": weapon.skill_band,
                "history": {"windows": len(matched), "shots": shots, "hits": hits, "bound": {"method": "wilson_upper", "level": 0.95, "value": upper}},
                "window": {
                    "shots": weapon.shots,
                    "hits": weapon.hits,
                    "matches": len(weapon.per_match),
                    "bound": {"method": "wilson_lower", "level": 0.95, "design_effect": deff, "value": lower},
                },
                "gap": gap,
                "thresholds": {"self_jump_gap": profile.self_jump_gap, "min_shots": profile.min_shots},
            }
            return True, text, facts, weapon
    return False, "", {}, None


def _fire_break(weapon: WeaponSummary, profile: GameProfile) -> tuple[str, dict] | None:
    """Gaps under the cycle, counted in the matches where they are the habit. Returns (sentence, numbers).

    A match counts when it has at least FIRE_MATCH_GAPS gaps and at least
    ``min_violation_rate`` of them broke the cycle. One jittery timestamp in an
    honest match does not reach that, even when the gun fired only two or three
    times that match, and a macro switched on midweek is not diluted by the
    matches before it.
    """
    rule = profile.weapon_rule(weapon.weapon_class, weapon.weapon_key)
    if rule is None or rule.min_shot_interval_ms is None:
        return None
    floor = rule.min_shot_interval_ms - rule.interval_slack_ms
    intervals = violations = matches = 0
    counted: list[dict] = []
    for match_id, gun, gaps in keyed_match_gaps(weapon):
        bad = sum(1 for gap in gaps if gap < floor)
        if len(gaps) >= FIRE_MATCH_GAPS and bad / len(gaps) >= rule.min_violation_rate:
            intervals += len(gaps)
            violations += bad
            matches += 1
            counted.append({"match_id": match_id, "gun": gun, "gaps": len(gaps), "under_floor": bad})
    if intervals >= rule.min_intervals and violations >= rule.min_violations:
        where = f" in {matches} matches" if matches > 1 else ""
        text = (
            f"{weapon.weapon_key} fired faster than its cycle "
            f"({violations}/{intervals} gaps under {floor} ms{where})"
        )
        return text, {
            "gaps": intervals,
            "under_floor": violations,
            "matches": matches,
            "floor_ms": floor,
            "counted": counted,
            "thresholds": {
                "cycle_ms": rule.min_shot_interval_ms,
                "slack_ms": rule.interval_slack_ms,
                "min_gaps_per_match": FIRE_MATCH_GAPS,
                "min_violation_rate": rule.min_violation_rate,
                "min_gaps": rule.min_intervals,
                "min_under_floor": rule.min_violations,
            },
        }
    return None


def _knowledge_context(kind: str, weapon: WeaponSummary, profile: GameProfile) -> dict:
    """Why the knowledge engine let these samples count, and what it kept out. Context: not identity."""
    skipped = {cause: n for cause, n in sorted(weapon.knowledge_skipped.get(kind, {}).items())}
    required = list(profile.knowledge_channels)
    if kind == "hidden":
        return {
            "status": "unknowable",
            "required": required,
            "basis": "hidden_track_ms: the server's line-of-sight and audio queries both failed for the tracked enemy",
            "counted_recent_perception": dict(sorted(weapon.hidden_recent.items())),
            "not_counted": skipped,
        }
    if kind == "quiet_aim":
        return {
            "knowable": "the enemy was seen or heard at the shot",
            "unknowable": "every declared channel was checked and failed",
            "required": required,
            "not_counted": skipped,
        }
    return {}


def _knowledge_notes(record: PlayerRecord, profile: GameProfile) -> list[str]:
    """Telemetry the knowledge engine could not trust, said once, so a reviewer can fix the emitter."""
    notes = []
    for weapon in record.weapons:
        seen = weapon.knowledge_skipped.get("hidden", {}).get("seen", 0)
        torn = sum(causes.get("conflict", 0) + causes.get("disagreed", 0) for causes in weapon.knowledge_skipped.values())
        parts = []
        if seen:
            parts.append(f"{seen} carried hidden-mover time for an enemy the server also marked visible")
        if torn:
            parts.append(f"{torn} carried knowledge telemetry that contradicted itself")
        if parts:
            notes.append(f"{weapon.weapon_key}: {' and '.join(parts)}. The information checks did not count them; check the emitter.")
    unreported = [name for name in profile.knowledge_channels if name in FUTURE_CHANNELS]
    unchecked = any(causes.get("unchecked", 0) for weapon in record.weapons for causes in weapon.knowledge_skipped.values())
    if unreported and unchecked:
        notes.append(
            f"The profile declares {', '.join(unreported)}, which no event reports, so information samples were not "
            "known either way and the information checks did not count them."
        )
    return notes


def _speed_facts(record: PlayerRecord, profile: GameProfile) -> dict:
    speed = record.speed
    return {
        "longest_run": speed.longest_run,
        "over_cap_samples": speed.violations,
        "eligible_samples": speed.eligible,
        "cap_source": speed.cap_source,
        "run": {
            "match_id": speed.run_match,
            "start_ms": speed.run_start_ms,
            "end_ms": speed.run_end_ms,
            "peak_mps": speed.run_peak_mps,
            "cap_mps": speed.run_peak_cap_mps,
        },
        "thresholds": {
            "min_run": profile.speed_min_run,
            "over_fraction": profile.speed_over_fraction,
            "run_gap_ms": profile.speed_run_gap_ms,
        },
    }


def _matches_in(rows: list[dict]) -> set[str]:
    return {row["match_id"] for row in rows if row["match_id"]}


def assess_player(
    record: PlayerRecord,
    cohorts: CohortTable,
    profile: GameProfile,
    history: list[HistoryWindow] | None = None,
    reports: int = 0,
    challenges: ChallengeRegistry | None = None,
) -> Case:
    history = history or []
    reasons: list[str] = []
    observations: list[str] = []
    metrics: list[MetricView] = []
    untrained: list[str] = []
    physics = False
    checks: list[str] = []
    found: list[Observation] = []
    compared_on: list[tuple[str, str]] = []

    def fired(check: str) -> None:
        if check not in checks:
            checks.append(check)

    def observe(kind: str, role: str, line: str, printed_in: str, evidence: dict, *, key: str = "", match_ids=(), context=None) -> None:
        """The finding just written as a sentence, kept as data. The sentence and the check id are unchanged."""
        found.append(
            Observation(
                family=KINDS[kind][0],
                kind=kind,
                role=role,
                subject_id=record.player_id,
                key=key,
                match_ids=tuple(sorted(match_ids)),
                evidence=evidence,
                context={"line": line, "printed_in": printed_in, **(context or {})},
            )
        )

    human_families: set[str] = set()
    beyond_band = 0
    supporting_families: set[str] = set()
    compared = False
    if record.skill_band == "unrated":
        observations.append(
            "No skill_band or skill_prior was sent, so this player is compared with everyone else "
            "who has no rank on this server."
        )

    if record.speed.sustained:
        physics = True
        line = record.speed.detail or "sustained ground speed over the gear cap"
        reasons.append(line)
        fired("speed")
        speed = record.speed
        observe(
            "speed", "review", line, "reasons", _speed_facts(record, profile),
            match_ids=[speed.run_match] if speed.run_match else (),
            context={"excluded": {
                "innocent_cause": speed.excluded_innocent,
                "unknown_cause": speed.excluded_unknown,
                "airborne_or_unknown_ground": speed.excluded_airborne,
                "no_cap": speed.missing_cap,
            }},
        )
    elif record.speed.spike_samples:
        observations.append(record.speed.detail)
    elif record.speed.detail:
        observations.append(record.speed.detail)

    for weapon in record.weapons:
        first_metric = len(metrics)
        key = weapon.weapon_key
        where = weapon.match_ids
        fire = _fire_break(weapon, profile)
        if fire:
            physics = True
            reasons.append(fire[0])
            fired("fire_rate")
            observe("fire_rate", "review", fire[0], "reasons", fire[1], key=key, match_ids=_matches_in(fire[1]["counted"]))
        steady = metronome_finding(weapon, profile)
        if steady:
            physics = True
            reasons.append(steady[0])
            fired("metronome")
            observe("metronome", "review", steady[0], "reasons", steady[1], key=key, match_ids=_matches_in(steady[1]["counted"]))
        hidden = hidden_finding(weapon, profile)
        if hidden:
            physics = True
            reasons.append(hidden[0])
            fired("hidden")
            observe("hidden", "review", hidden[0], "reasons", hidden[1], key=key, match_ids=where, context={"knowledge": _knowledge_context("hidden", weapon, profile)})
        private = private_finding(weapon, profile)
        if private:
            physics = True
            reasons.append(private[0])
            fired("private_replay")
            # The legacy field, read as an occluded motion replay with no plan: same bar, same reason.
            observe(
                "occluded_motion_replay", "review", private[0], "reasons", legacy_evidence(key, private[1], profile),
                key=legacy_challenge_id(key), match_ids=where,
                context=legacy_context(private_knowledge(profile), weapon.knowledge_skipped.get("private_replay", {})),
            )
        wire = wire_finding(weapon, profile)
        if wire:
            physics = True
            reasons.append(wire[0])
            fired("wire")
            observe("wire", "review", wire[0], "reasons", wire[1], key=key, match_ids=where, context={"knowledge": {
                "client_data": "the snapshot the server sent (wire)",
                "human_perception": "the position the official client draws (picture), one interpolation delay earlier",
            }})
        smooth = smoothness_finding(weapon, profile)
        if smooth:
            physics = True
            reasons.append(smooth[0])
            fired("quiet_aim")
            observe("quiet_aim", "review", smooth[0], "reasons", smooth[1], key=key, match_ids=where, context={"knowledge": _knowledge_context("quiet_aim", weapon, profile)})
        if weapon.shots < profile.min_shots:
            observations.append(
                f"{weapon.weapon_key}: {weapon.shots} shots, need {profile.min_shots} before aim is scored"
            )
            continue
        acc_deff = design_effect([(row[0], row[1]) for row in weapon.per_match.values()])
        if acc_deff >= NOTE_DESIGN_EFFECT:
            observations.append(
                f"{weapon.weapon_key} accuracy varies between matches {acc_deff:.1f}x more than chance, "
                "so its bound is wider."
            )
        acc_view, band, human, did, facts = _rate_compare(
            "accuracy",
            weapon.hits,
            weapon.shots,
            weapon.hits / weapon.shots,
            record,
            weapon,
            cohorts,
            profile,
            acc_deff,
        )
        metrics.append(acc_view)
        compared = compared or did
        if did:
            compared_on.append(("accuracy", key))
        beyond_band += int(band)
        if band and not human:
            fired("rank_tail")
        if human:
            human_families.add("accuracy")
            fired("accuracy")
            line = (
                f"{weapon.weapon_key} accuracy lower bound {acc_view.bound:.0%} is past the best "
                f"measured {acc_view.ceiling_band} human ({acc_view.ceiling_extreme:.0%})"
            )
            reasons.append(line)
            observe("accuracy", "past_human", line, "reasons", facts, key=key, match_ids=where)
        elif band:
            line = f"{weapon.weapon_key} accuracy is above this rank's range and inside the best humans measured"
            observations.append(line)
            observe("rank_tail", "watch", line, "observations", facts, key=key, match_ids=where)
        if acc_view.skipped:
            observations.append(f"{weapon.weapon_key} accuracy: {acc_view.skipped}")

        if weapon.head_known_hits >= profile.min_hits_for_headshot:
            hs_deff = design_effect([(row[2], row[3]) for row in weapon.per_match.values()])
            hs_view, band, human, did, facts = _rate_compare(
                "headshot_rate",
                weapon.head_hits,
                weapon.head_known_hits,
                weapon.head_hits / weapon.head_known_hits,
                record,
                weapon,
                cohorts,
                profile,
                hs_deff,
            )
            metrics.append(hs_view)
            compared = compared or did
            if did:
                compared_on.append(("headshot_rate", key))
            beyond_band += int(band)
            if band and not human:
                fired("rank_tail")
            if human:
                human_families.add("headshot_rate")
                fired("headshot_rate")
            if human:
                line = (
                    f"{weapon.weapon_key} headshot lower bound {hs_view.bound:.0%} is past the best "
                    f"measured {hs_view.ceiling_band} human ({hs_view.ceiling_extreme:.0%})"
                )
                reasons.append(line)
                observe("headshot_rate", "past_human", line, "reasons", facts, key=key, match_ids=where)
            elif band:
                line = f"{weapon.weapon_key} headshot rate is above this rank's range and inside the best humans measured"
                observations.append(line)
                observe("rank_tail", "watch", line, "observations", facts, key=key, match_ids=where)
            if hs_view.skipped:
                observations.append(f"{weapon.weapon_key} headshots: {hs_view.skipped}")

        if len(weapon.distances) >= profile.min_shots:
            dist_value = median(weapon.distances)
            dist_view, band, human, did, facts = _continuous_flags(
                "median_distance",
                dist_value,
                record,
                weapon,
                cohorts,
                profile,
                direction="high",
                bound=median_bound(weapon.distances, upper=False),
                samples=len(weapon.distances),
            )
            metrics.append(dist_view)
            compared = compared or did
            if did:
                compared_on.append(("median_distance", key))
            beyond_band += int(band)
            if band and not human:
                fired("rank_tail")
            if human:
                human_families.add("median_distance")
                fired("median_distance")
                line = (
                    f"{weapon.weapon_key} median engagement {dist_value:.0f} m (lower bound {dist_view.bound:.0f} m) is past the farthest measured "
                    f"{dist_view.ceiling_band} human ({dist_view.ceiling_extreme:.0f} m)"
                )
                reasons.append(line)
                observe("median_distance", "past_human", line, "reasons", facts, key=key, match_ids=where)
            elif band:
                line = f"{weapon.weapon_key} median engagement distance is above this rank's range and inside the farthest humans measured"
                observations.append(line)
                observe("rank_tail", "watch", line, "observations", facts, key=key, match_ids=where)

        if weapon.geometry_known >= profile.min_shots:
            geo_view, band, human, did, facts = _rate_compare(
                "geometry_rate",
                weapon.geometry_true,
                weapon.geometry_known,
                weapon.geometry_true / weapon.geometry_known,
                record,
                weapon,
                cohorts,
                profile,
            )
            metrics.append(geo_view)
            compared = compared or did
            if did:
                compared_on.append(("geometry_rate", key))
            beyond_band += int(band)
            if band and not human:
                fired("rank_tail")
            if human:
                human_families.add("geometry_rate")
                fired("geometry_rate")
                line = f"{weapon.weapon_key} shots through geometry are past the best measured human rate"
                reasons.append(line)
                observe("geometry_rate", "past_human", line, "reasons", facts, key=key, match_ids=where)
            elif band:
                line = f"{weapon.weapon_key} shots through geometry are above this rank's range and inside the best humans measured"
                observations.append(line)
                observe("rank_tail", "watch", line, "observations", facts, key=key, match_ids=where)

        if len(weapon.view_deltas) >= profile.min_shots:
            view_value = percentile(sorted(weapon.view_deltas), 0.95)
            view, _band, human, did, facts = _continuous_flags(
                "view_p95", view_value, record, weapon, cohorts, profile, direction="high", samples=len(weapon.view_deltas)
            )
            metrics.append(view)
            if did and human:
                supporting_families.add("view")
                fired("supporting")
                line = f"{weapon.weapon_key} view snaps sit past every measured human"
                observations.append(line)
                observe("view_snaps", "supporting", line, "observations", facts, key=key, match_ids=where)

        if len(weapon.acquire_ms) >= profile.min_shots:
            acquire_med = median(weapon.acquire_ms)
            acquire_dev = sample_std(weapon.acquire_ms)
            med_view, _b1, human1, did1, med_facts = _continuous_flags(
                "acquire_median", acquire_med, record, weapon, cohorts, profile, direction="low", samples=len(weapon.acquire_ms)
            )
            dev_view, _b2, human2, _did2, dev_facts = _continuous_flags(
                "acquire_std", acquire_dev, record, weapon, cohorts, profile, direction="low", samples=len(weapon.acquire_ms)
            )
            metrics.extend([med_view, dev_view])
            if did1 and (human1 or human2):
                supporting_families.add("acquire")
                fired("supporting")
                line = f"{weapon.weapon_key} target-acquire timing is tighter than the human low end"
                observations.append(line)
                observe("acquire_timing", "supporting", line, "observations", {"metric": "acquire_ms", "median": med_facts, "spread": dev_facts}, key=key, match_ids=where)
        for view in metrics[first_metric:]:
            view.key = weapon.weapon_key

    for recoil in record.recoils:
        build = recoil.build_key
        mirror, mirror_note = mirror_check_finding(recoil, profile)
        if mirror:
            physics = True
            reasons.append(mirror[0])
            fired("mirror")
            observe("mirror", "review", mirror[0], "reasons", mirror[1], key=build)
        elif mirror_note:
            observations.append(mirror_note)
        if recoil.blatant:
            physics = True
            floor = recoil.floor if recoil.floor is not None else 0.0
            line = (
                f"{recoil.build_key} recoil stayed under {profile.recoil_floor_fraction:.0%} of the "
                f"build floor ({floor:.2f} deg) for {recoil.longest_low_run} shots"
            )
            reasons.append(line)
            fired("recoil_floor")
            observe("recoil_floor", "review", line, "reasons", {
                "floor_deg": recoil.floor,
                "longest_low_run": recoil.longest_low_run,
                "low_shots": recoil.low_samples,
                "shots": len(recoil.pitches),
                "thresholds": {
                    "floor_fraction": profile.recoil_floor_fraction,
                    "min_run": profile.recoil_min_run,
                    "min_spray_index": profile.recoil_min_spray_index,
                },
            }, key=build)
            continue
        if len(recoil.pitches) < profile.recoil_min_run:
            continue
        player_med = median(recoil.pitches)
        # Low recoil is the finding, so test the high end of the median.
        pitch_high = median_bound(recoil.pitches, upper=True)
        pitch = {
            "metric": "recoil",
            "observed": player_med,
            "samples": len(recoil.pitches),
            "direction": "low",
            "bound": {"method": "median_order_statistic", "side": "upper", "level": 0.95, "value": pitch_high},
        }
        if recoil.untrained:
            ceiling_name = cohorts.ceiling_band(
                recoil.build_key, "recoil", record.player_id, profile.min_cohort_players
            )
            ceiling = (
                None
                if ceiling_name is None
                else cohorts.dist(ceiling_name, recoil.build_key, "recoil", record.player_id)
            )
            if not cohort_is_thick(ceiling, profile) or ceiling is None:
                untrained.append(recoil.build_key)
                observations.append(
                    f"{recoil.build_key} has recoil samples but no curve and not enough humans yet. Not flagged."
                )
                continue
            learned_floor = ceiling.mid * profile.recoil_floor_fraction
            learned = {
                **pitch,
                "ceiling": {"band": ceiling_name, "players": ceiling.n, "min": ceiling.minimum, "p05": ceiling.p05, "median": ceiling.mid},
                "learned_floor": learned_floor,
                "thresholds": {"floor_fraction": profile.recoil_floor_fraction},
            }
            if pitch_high < ceiling.minimum and ceiling.mid > 0 and pitch_high < learned_floor:
                physics = True
                fired("recoil_learned")
                line = (
                    f"{recoil.build_key} recoil median {player_med:.2f} deg is far under every measured "
                    f"human on this build (lowest {ceiling.minimum:.2f}, median {ceiling.mid:.2f})"
                )
                reasons.append(line)
                observe("recoil_learned", "review", line, "reasons", learned, key=build)
            elif pitch_high < ceiling.p05:
                beyond_band += 1
                fired("rank_tail")
                line = f"{recoil.build_key} recoil is in the low tail of humans who use this build"
                observations.append(line)
                observe("rank_tail", "watch", line, "observations", learned, key=build)
            compared = True
            compared_on.append(("recoil", build))
            continue
        # A designer floor exists and the spray was not blatant. Still learn the human tail.
        own = cohorts.dist(recoil.skill_band, recoil.build_key, "recoil", record.player_id)
        if cohort_is_thick(own, profile) and own is not None and pitch_high < own.p05:
            beyond_band += 1
            fired("rank_tail")
            line = f"{recoil.build_key} recoil is low for humans on this build, above the hard floor"
            observations.append(line)
            observe("rank_tail", "watch", line, "observations", {
                **pitch,
                "rank": {"band": recoil.skill_band, "players": own.n, "p05": own.p05},
                "floor_deg": recoil.floor,
            }, key=build)
            compared = True
            compared_on.append(("recoil", build))

    for extra in record.extras:
        spec = next((item for item in profile.extra_metrics if item.name == extra.name), None)
        if spec is None:
            continue
        if len(extra.values) < spec.min_samples:
            observations.append(
                f"{extra.name}: {len(extra.values)} samples, need {spec.min_samples}"
            )
            continue
        player_med = median(extra.values)
        extra_bound = median_bound(extra.values, upper=extra.direction == "low")
        dist = cohorts.dist(extra.group_key, extra.name, "extra", record.player_id)
        if not cohort_is_thick(dist, profile) or dist is None:
            untrained.append(f"{extra.name}:{extra.group_key}")
            observations.append(f"{extra.name} on {extra.group_key} is waiting for a baseline")
            continue
        compared = True
        compared_on.append((extra.name, extra.group_key))
        # The tail is one player in twenty. Past humans is past every one measured.
        if extra.direction == "high":
            tail = extra_bound > dist.p95
            past = extra_bound > dist.maximum
            extreme = dist.maximum
            line = f"{extra.name} median {player_med:.3g} is past every measured human (highest {extreme:.3g})"
        else:
            tail = extra_bound < dist.p05
            past = extra_bound < dist.minimum
            extreme = dist.minimum
            line = f"{extra.name} median {player_med:.3g} is under every measured human (lowest {extreme:.3g})"
        metrics.append(
            MetricView(
                name=extra.name,
                player_value=player_med,
                bound=extra_bound,
                own_p95=dist.p95,
                own_max=dist.maximum,
                ceiling_band=extra.group_key,
                ceiling_p95=dist.p95,
                ceiling_extreme=extreme,
                beyond_band=tail,
                beyond_human=past,
                key=extra.group_key,
            )
        )
        high = extra.direction == "high"
        declared = {
            "metric": extra.name,
            "declared_as": extra.kind,
            "group": extra.group_key,
            "direction": extra.direction,
            "observed": player_med,
            "samples": len(extra.values),
            "bound": {"method": "median_order_statistic", "side": "lower" if high else "upper", "level": 0.95, "value": extra_bound},
            "cohort": {"players": dist.n, **({"p95": dist.p95, "max": extreme} if high else {"p05": dist.p05, "min": extreme})},
            "past_tail": tail,
            "past_human": past,
        }
        if tail and not past and extra.kind == "primary":
            beyond_band += 1
            fired("rank_tail")
            tail_line = f"{extra.name} median {player_med:.3g} is in the human tail, inside every measured human"
            observations.append(tail_line)
            observe("rank_tail", "watch", tail_line, "observations", declared, key=extra.group_key)
        if not past:
            continue
        if extra.kind == "primary":
            human_families.add(f"extra:{extra.name}")
            fired("extra")
            reasons.append(line)
            observe("extra", "past_human", line, "reasons", declared, key=extra.group_key)
        else:
            supporting_families.add(f"extra:{extra.name}")
            fired("supporting")
            observations.append(line)
            observe("supporting_extra", "supporting", line, "observations", declared, key=extra.group_key)

    results, unlinked = evaluate_challenges(record.player_id, record.match_ids, record.challenge_samples, challenges, profile)
    for result in results:
        if result.status != FOLLOWED:
            continue
        physics = True
        line = challenge_line(result)
        reasons.append(line)
        fired("private_replay")
        observe(
            "occluded_motion_replay", "review", line, "reasons", challenge_evidence(result, profile),
            key=result.challenge_id, match_ids=[result.plan.match_id], context=challenge_context(result, results),
        )
    observations.extend(_knowledge_notes(record, profile))
    observations.extend(challenge_notes(results, unlinked))
    identity, identity_text, identity_facts, jumped = _identity(record, history, profile)
    if identity:
        reasons.append(identity_text)
        fired("account_jump")
        assert jumped is not None
        observe("account_jump", "account_change", identity_text, "reasons", identity_facts, key=jumped.weapon_key, match_ids=jumped.match_ids)

    decision = decide(
        physics=physics,
        beyond_human=len(human_families),
        beyond_band=beyond_band,
        supporting=len(supporting_families),
        identity=identity,
        compared=compared,
    )
    if reports > 0 and decision in {"clean", "insufficient_data"}:
        observations.append(
            f"{reports} player reports put this account at the front of the scan. Reports are not a finding."
        )
    case = Case(
        player_id=record.player_id,
        game_id=record.game_id,
        decision=decision,
        recommended_action=ACTIONS[decision],
        automated_action="none",
        skill_band=record.skill_band,
        reports=reports,
        reasons=reasons,
        observations=observations + record.notes,
        metrics=metrics,
        match_ids=sorted(record.match_ids),
        party_ids=sorted(record.party_ids),
        untrained=untrained,
        speed=record.speed,
        checks=checks,
        evidence=found,
        compared_on=compared_on,
        challenges=results,
    )
    case.seal = evidence_seal(case)
    return case


def _batch_observation(
    case: Case, kind: str, line: str, printed_in: str, evidence: dict, *, key: str = "", match_ids=(), depends_on=(), context=None
) -> Observation:
    return Observation(
        family=KINDS[kind][0],
        kind=kind,
        role="watch",
        subject_id=case.player_id,
        key=key,
        match_ids=tuple(sorted(match_ids)),
        evidence=evidence,
        depends_on=tuple(depends_on),
        context={"line": line, "printed_in": printed_in, **(context or {})},
    )


def _watch_for_batch(
    case: Case, reason: str, rank: int, kind: str, evidence: dict, *, key: str = "", match_ids=(), depends_on=(), context=None
) -> None:
    """A batch tell can move a clean player to watch. It does not make a review."""
    check = KINDS[kind][1]
    if check not in case.checks:
        case.checks.append(check)
    printed_in = "observations" if case.decision == "review" else "reasons"
    case.evidence.append(
        _batch_observation(case, kind, reason, printed_in, evidence, key=key, match_ids=match_ids, depends_on=depends_on, context=context)
    )
    if case.decision == "review":
        case.observations.append(reason)
        return
    case.reasons.append(reason)
    case.queue_rank = max(case.queue_rank, rank)
    if case.decision != "watch":
        case.decision = "watch"
        case.recommended_action = ACTIONS["watch"]
    case.seal = evidence_seal(case)


def _remember_twin(case: Case, other: str, correlation: float) -> None:
    if case.vendor_r is None or correlation > case.vendor_r:
        case.vendor_twin = other
        case.vendor_r = round(correlation, 2)


def _center(players: dict[str, dict[tuple[str, int], float]]) -> None:
    """Remove the leftover every human on this build shares, so only the odd part is compared."""
    sums: dict[tuple[str, int], list[float]] = {}
    for points in players.values():
        for key, value in points.items():
            sums.setdefault(key, []).append(value)
    means = {key: math.fsum(values) / len(values) for key, values in sums.items()}
    for player_id, points in players.items():
        players[player_id] = {key: value - means[key] for key, value in points.items()}


def _signature_match(
    left: dict[tuple[str, int], float],
    right: dict[tuple[str, int], float],
    profile: GameProfile,
) -> dict | None:
    """r, the points it was measured on, how they were keyed, and the Fisher z; None under the bar."""
    common = sorted(set(left) & set(right))
    if not common:
        return None
    keyed_by = common[0][0]
    need = profile.vendor_min_shots if keyed_by == "position" else profile.vendor_min_points
    if len(common) < need:
        return None
    correlation = pearson([left[key] for key in common], [right[key] for key in common])
    if correlation is None or correlation < profile.vendor_min_r:
        return None
    # Every pair on a build is a test. A short signature needs a stronger r to clear the same
    # bar: Fisher z = atanh(r) * sqrt(n - 3). Five is about one chance pair in three million.
    fisher = math.atanh(min(correlation, 0.999999)) * math.sqrt(len(common) - 3)
    if fisher < profile.vendor_min_z:
        return None
    return {"r": correlation, "points": len(common), "keyed_by": keyed_by, "fisher_z": fisher, "min_points": need}


def annotate_vendors(cases: list[Case], records: list[PlayerRecord], profile: GameProfile) -> None:
    """Same leftover, after the kick is removed, across two customers.

    Accounts are compared per weapon key. A humanizer table belongs to the
    tool, so the same table on a stock and a modded rifle still lines up; the
    leftover is already scaled per account. Inside a weapon every pair is
    compared. When it has a full cohort, the leftover all of its players share
    is removed first.
    """
    by_case = {case.player_id: case for case in cases}
    builds: dict[str, dict[str, dict[tuple[str, int], float]]] = {}
    for record in records:
        longest: dict[str, int] = {}
        for recoil in record.recoils:
            found = leftover_signature(recoil, profile.vendor_min_points)
            if found is None:
                continue
            points, samples = found
            if samples < profile.vendor_min_shots or samples <= longest.get(recoil.weapon_key, 0):
                continue
            longest[recoil.weapon_key] = samples
            builds.setdefault(recoil.weapon_key, {})[record.player_id] = points
    best: dict[tuple[str, str], float] = {}
    measured: dict[tuple[str, str], dict] = {}
    for weapon_key in sorted(builds):
        players = builds[weapon_key]
        centered = len(players) >= profile.min_cohort_players
        if centered:
            _center(players)
        ids = sorted(players)
        for left_index, left in enumerate(ids):
            for right in ids[left_index + 1 :]:
                found = _signature_match(players[left], players[right], profile)
                correlation = None if found is None else found["r"]
                if correlation is None or correlation < profile.vendor_min_r:
                    continue
                if correlation > best.get((left, right), -2.0):
                    best[(left, right)] = correlation
                    measured[(left, right)] = {**found, "weapon_key": weapon_key, "centered": centered}  # type: ignore[dict-item]
    confirmed = {case.player_id for case in cases if case.decision == "review"}
    for (left, right), correlation in sorted(best.items()):
        pair_confirmed = left in confirmed or right in confirmed
        pair = measured[(left, right)]
        for player_id, other in ((left, right), (right, left)):
            case = by_case.get(player_id)
            if case is None:
                continue
            _remember_twin(case, other, correlation)
            facts = {
                "partner": other,
                "partner_in_review": other in confirmed,
                "r": correlation,
                "fisher_z": pair["fisher_z"],
                "points": pair["points"],
                "keyed_by": pair["keyed_by"],
                "shared_habit_removed": pair["centered"],
                "thresholds": {
                    "min_r": profile.vendor_min_r,
                    "min_z": profile.vendor_min_z,
                    "min_points": pair["min_points"],
                    "min_samples": profile.vendor_min_shots,
                },
            }
            if pair_confirmed and player_id in confirmed:
                if "leftover" not in case.checks:
                    case.checks.append("leftover")
                line = f"leftover command matches {other} (r {correlation:.2f})"
                case.observations.append(line)
                case.evidence.append(_batch_observation(case, "leftover", line, "observations", facts, key=pair["weapon_key"]))
                continue
            if pair_confirmed:
                reason = (
                    f"leftover command matches {other} (r {correlation:.2f}), "
                    "who is already a review in this batch"
                )
            else:
                reason = (
                    f"leftover command matches {other} (r {correlation:.2f}). "
                    "Nobody in the pair is a review yet"
                )
            _watch_for_batch(case, reason, 2, "leftover", facts, key=pair["weapon_key"])


def annotate_inheritance(cases: list[Case], records: list[PlayerRecord], profile: GameProfile) -> None:
    """A teammate swings the hidden enemy faster than a voice can travel."""
    by_case = {case.player_id: case for case in cases}
    contacts: dict[str, list[tuple[str, int, str]]] = {}
    parties: dict[str, set[str]] = {}
    for record in records:
        found: list[tuple[str, int, str]] = []
        for weapon in record.weapons:
            found.extend(weapon.hidden_contacts)
        # Contacts in match, time and enemy order, whichever weapon or file line they came from.
        found.sort()
        if found:
            contacts[record.player_id] = found
        for party in record.party_ids:
            parties.setdefault(party, set()).add(record.player_id)
    confirmed = sorted(
        case.player_id
        for case in cases
        if case.decision == "review" and any("hidden mover" in reason for reason in case.reasons)
    )
    # Every partner a teammate was timed against: (case, rank key, lags). One lag list fits in the legacy field.
    timed: dict[str, list[tuple[tuple, list[int]]]] = {}
    for party in sorted(parties):
        members = parties[party]
        cheaters = [player_id for player_id in confirmed if player_id in members]
        for cheater in cheaters:
            cheater_hits = contacts.get(cheater, [])
            if not cheater_hits:
                continue
            for mate in sorted(members):
                if mate == cheater or mate not in contacts:
                    continue
                lags: list[int] = []
                lag_matches: list[str] = []
                for match, moment, enemy in contacts[mate]:
                    # t_ms restarts every match. Only a swing in the same match can follow a callout.
                    priors = [
                        stamp
                        for where, stamp, tagged in cheater_hits
                        if where == match and tagged == enemy and stamp <= moment
                    ]
                    if not priors:
                        continue
                    lags.append(moment - max(priors))
                    lag_matches.append(match)
                case = by_case.get(mate)
                if case is None or not lags:
                    continue
                fast = [lag for lag in lags if 0 <= lag < profile.voice_min_ms]
                timed.setdefault(mate, []).append((_partner_rank(fast, profile, cheater, party), lags))
                if len(fast) < profile.inherit_min_events:
                    continue
                low = min(fast)
                high = max(fast)
                span = f"{low} ms" if low == high else f"{low}–{high} ms"
                # The partner was chosen above by the text of its reason. The dependency is recorded by id.
                partner = by_case.get(cheater)
                _watch_for_batch(
                    case,
                    (
                        f"{len(fast)} swings on a hidden enemy landed {span} after {cheater}, "
                        f"under the {profile.voice_min_ms} ms a voice needs"
                    ),
                    1,
                    "voice",
                    {
                        "partner": cheater,
                        "party_id": party,
                        "fast_lags_ms": fast,
                        "lags": len(lags),
                        "thresholds": {"voice_ms": profile.voice_min_ms, "min_fast": profile.inherit_min_events},
                    },
                    match_ids={match for lag, match in zip(lags, lag_matches) if 0 <= lag < profile.voice_min_ms},
                    depends_on=[obs.observation_id for obs in (partner.evidence if partner else []) if obs.kind == "hidden"],
                    context={"knowledge": {
                        "contacts": "both players' shots on an enemy that was unknowable to the shooter",
                        "required": list(profile.knowledge_channels),
                        "teammates": "a relationship check: it times a swing against the partner's, and does not assume the teammate could not have been told",
                    }},
                )


    for mate, candidates in timed.items():
        by_case[mate].inherit_lags_ms = min(candidates)[1]


def _partner_rank(fast: list[int], profile: GameProfile, cheater: str, party: str) -> tuple:
    """Which partner's lags the legacy ``inherit_lags_ms`` keeps when a teammate was timed against several.

    The strongest relationship by the check's own terms: one that reaches the watch bar, then the most
    swings faster than a voice, then the faster median of those, then the partner and party ids. Every
    partner that reaches the bar also has its own voice observation, whichever one is kept here.
    """
    reaches = len(fast) >= profile.inherit_min_events
    return (not reaches, -len(fast), median(fast) if fast else math.inf, cheater, party)


def annotate_batch(cases: list[Case], records: list[PlayerRecord], profile: GameProfile) -> None:
    annotate_vendors(cases, records, profile)
    annotate_inheritance(cases, records, profile)
    annotate_parties(cases)


def annotate_parties(cases: list[Case]) -> None:
    groups: dict[str, list[str]] = {}
    for case in cases:
        if case.decision not in {"watch", "review"}:
            continue
        for party in case.party_ids:
            groups.setdefault(party, []).append(case.player_id)
    for case in cases:
        notes = []
        for party in case.party_ids:
            mates = sorted(pid for pid in groups.get(party, []) if pid != case.player_id)
            if mates:
                notes.append(
                    f"party {party} also has {', '.join(mates)} at watch or review in this batch"
                )
        case.party_note = "; ".join(notes)
