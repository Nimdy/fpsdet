"""Turn a player record into a case. Never a ban.

Review means one of:
- sustained break of a server gear rule (speed, fire interval, metronome,
  blatant recoil, same-tick recoil mirror, tracking a hidden mover, aim on a
  private replay, aim that matches the wire snapshot ahead of the picture the
  client draws, or aim noise that drops only while the server says this
  client could not have known)
- learned recoil far under every human measured on that same build
- confidently past the best measured human on two or more combat rates
- past the best measured human on one combat rate, and the account no longer
  looks like itself or a supporting tell agrees

Watch means the player is out of place for their rank, at the top of the human
range, or the account jumped, and a person can look when reports or time allow.
Outperforming your own rank but staying inside the best humans is a watch.
That is a smurf or a good player, not a review.
"""

from __future__ import annotations

from .baseline import CohortTable, Dist, cohort_is_thick, sample_std
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
    command_residual,
    evidence_seal,
    hidden_break,
    metronome_break,
    private_break,
    mirror_break,
    pearson,
    smoothness_break,
    wire_break,
)
from .statsutil import median, percentile, wilson_lower, wilson_upper


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
) -> tuple[MetricView, bool, bool, bool]:
    """Return view, beyond_band, beyond_human, compared."""
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
        return _view(name, point, None, own, ceiling_name, ceiling, beyond_band=False, beyond_human=False, skipped=why), False, False, False
    lower = wilson_lower(successes, total)
    past_band = lower > own.p95  # type: ignore[union-attr]
    past_human = lower > ceiling.maximum  # type: ignore[union-attr]
    return (
        _view(name, point, lower, own, ceiling_name, ceiling, beyond_band=past_band, beyond_human=past_human),
        past_band,
        past_human,
        True,
    )


def _continuous_flags(
    name: str,
    value: float,
    record: PlayerRecord,
    weapon: WeaponSummary,
    cohorts: CohortTable,
    profile: GameProfile,
    *,
    direction: str,
) -> tuple[MetricView, bool, bool, bool]:
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
        return _view(name, value, value, own, ceiling_name, ceiling, beyond_band=False, beyond_human=False, skipped=why), False, False, False
    assert own is not None and ceiling is not None
    if direction == "high":
        past_band = value > own.p95
        past_human = value > ceiling.p95
        extreme = ceiling.p95
    else:
        past_band = value < own.p05
        past_human = value < ceiling.p05
        extreme = ceiling.p05
    view = _view(
        name, value, value, own, ceiling_name, ceiling, beyond_band=past_band, beyond_human=past_human
    )
    view.ceiling_extreme = extreme
    return view, past_band, past_human, True


def _identity(record: PlayerRecord, history: list[HistoryWindow], profile: GameProfile) -> tuple[bool, str]:
    for weapon in record.weapons:
        matched = [
            row
            for row in history
            if row.player_id == record.player_id
            and row.weapon_key == weapon.weapon_key
            and row.skill_band == weapon.skill_band
        ]
        shots = sum(row.shots for row in matched)
        hits = sum(row.hits for row in matched)
        if shots < profile.min_shots or weapon.shots < profile.min_shots:
            continue
        lower = wilson_lower(weapon.hits, weapon.shots)
        upper = wilson_upper(hits, shots)
        gap = lower - upper
        if gap >= profile.self_jump_gap:
            return True, (
                f"{weapon.weapon_key} accuracy is confidently above this account's own history "
                f"by {gap:.0%} (history upper {upper:.0%}, this window lower {lower:.0%})"
            )
    return False, ""


def _fire_break(weapon: WeaponSummary, profile: GameProfile) -> str | None:
    rule = profile.weapon_rule(weapon.weapon_class, weapon.weapon_key)
    if rule is None or rule.min_shot_interval_ms is None or weapon.fire_intervals <= 0:
        return None
    rate = weapon.fire_violations / weapon.fire_intervals
    if (
        weapon.fire_intervals >= rule.min_intervals
        and weapon.fire_violations >= rule.min_violations
        and rate >= rule.min_violation_rate
    ):
        return (
            f"{weapon.weapon_key} fired faster than its cycle "
            f"({weapon.fire_violations}/{weapon.fire_intervals} gaps under "
            f"{rule.min_shot_interval_ms - rule.interval_slack_ms} ms)"
        )
    return None


def assess_player(
    record: PlayerRecord,
    cohorts: CohortTable,
    profile: GameProfile,
    history: list[HistoryWindow] | None = None,
    reports: int = 0,
) -> Case:
    history = history or []
    reasons: list[str] = []
    observations: list[str] = []
    metrics: list[MetricView] = []
    untrained: list[str] = []
    physics = False
    beyond_human = 0
    beyond_band = 0
    supporting_families: set[str] = set()
    compared = False

    if record.speed.sustained:
        physics = True
        reasons.append(record.speed.detail or "sustained ground speed over the gear cap")
    elif record.speed.spike_samples:
        observations.append(record.speed.detail)
    elif record.speed.detail:
        observations.append(record.speed.detail)

    for weapon in record.weapons:
        fire = _fire_break(weapon, profile)
        if fire:
            physics = True
            reasons.append(fire)
        steady = metronome_break(weapon, profile)
        if steady:
            physics = True
            reasons.append(steady)
        hidden = hidden_break(weapon, profile)
        if hidden:
            physics = True
            reasons.append(hidden)
        private = private_break(weapon, profile)
        if private:
            physics = True
            reasons.append(private)
        wire = wire_break(weapon, profile)
        if wire:
            physics = True
            reasons.append(wire)
        smooth = smoothness_break(weapon, profile)
        if smooth:
            physics = True
            reasons.append(smooth)
        if weapon.shots < profile.min_shots:
            observations.append(
                f"{weapon.weapon_key}: {weapon.shots} shots, need {profile.min_shots} before aim is scored"
            )
            continue
        acc_view, band, human, did = _rate_flags(
            "accuracy",
            weapon.hits,
            weapon.shots,
            weapon.hits / weapon.shots,
            record,
            weapon,
            cohorts,
            profile,
        )
        metrics.append(acc_view)
        compared = compared or did
        beyond_band += int(band)
        beyond_human += int(human)
        if human:
            reasons.append(
                f"{weapon.weapon_key} accuracy lower bound {acc_view.bound:.0%} is past the best "
                f"measured {acc_view.ceiling_band} human ({acc_view.ceiling_extreme:.0%})"
            )
        elif band:
            observations.append(
                f"{weapon.weapon_key} accuracy is above this rank's range and inside the best humans measured"
            )
        if acc_view.skipped:
            observations.append(f"{weapon.weapon_key} accuracy: {acc_view.skipped}")

        if weapon.head_known_hits >= profile.min_hits_for_headshot:
            hs_view, band, human, did = _rate_flags(
                "headshot_rate",
                weapon.head_hits,
                weapon.head_known_hits,
                weapon.head_hits / weapon.head_known_hits,
                record,
                weapon,
                cohorts,
                profile,
            )
            metrics.append(hs_view)
            compared = compared or did
            beyond_band += int(band)
            beyond_human += int(human)
            if human:
                reasons.append(
                    f"{weapon.weapon_key} headshot lower bound {hs_view.bound:.0%} is past the best "
                    f"measured {hs_view.ceiling_band} human ({hs_view.ceiling_extreme:.0%})"
                )
            if hs_view.skipped:
                observations.append(f"{weapon.weapon_key} headshots: {hs_view.skipped}")

        if len(weapon.distances) >= profile.min_shots:
            dist_value = median(weapon.distances)
            dist_view, band, human, did = _continuous_flags(
                "median_distance",
                dist_value,
                record,
                weapon,
                cohorts,
                profile,
                direction="high",
            )
            metrics.append(dist_view)
            compared = compared or did
            beyond_band += int(band)
            beyond_human += int(human)
            if human:
                reasons.append(
                    f"{weapon.weapon_key} median engagement {dist_value:.0f} m is past the {dist_view.ceiling_band} "
                    f"p95 ({dist_view.ceiling_extreme:.0f} m)"
                )

        if weapon.geometry_known >= profile.min_shots:
            geo_view, band, human, did = _rate_flags(
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
            beyond_band += int(band)
            beyond_human += int(human)
            if human:
                reasons.append(
                    f"{weapon.weapon_key} shots through geometry are past the best measured human rate"
                )

        if len(weapon.view_deltas) >= profile.min_shots:
            view_value = percentile(sorted(weapon.view_deltas), 0.95)
            view, _band, human, did = _continuous_flags(
                "view_p95", view_value, record, weapon, cohorts, profile, direction="high"
            )
            metrics.append(view)
            if did and human:
                supporting_families.add("view")
                observations.append(f"{weapon.weapon_key} view snaps sit past the human p95")

        if len(weapon.acquire_ms) >= profile.min_shots:
            acquire_med = median(weapon.acquire_ms)
            acquire_dev = sample_std(weapon.acquire_ms)
            med_view, _b1, human1, did1 = _continuous_flags(
                "acquire_median", acquire_med, record, weapon, cohorts, profile, direction="low"
            )
            dev_view, _b2, human2, _did2 = _continuous_flags(
                "acquire_std", acquire_dev, record, weapon, cohorts, profile, direction="low"
            )
            metrics.extend([med_view, dev_view])
            if did1 and (human1 or human2):
                supporting_families.add("acquire")
                observations.append(
                    f"{weapon.weapon_key} target-acquire timing is tighter than the human low end"
                )

    for recoil in record.recoils:
        mirror = mirror_break(recoil.applied, recoil.compensation, profile)
        if mirror:
            physics = True
            reasons.append(mirror)
        if recoil.blatant:
            physics = True
            floor = recoil.floor if recoil.floor is not None else 0.0
            reasons.append(
                f"{recoil.build_key} recoil stayed under {profile.recoil_floor_fraction:.0%} of the "
                f"build floor ({floor:.2f} deg) for {recoil.longest_low_run} shots"
            )
            continue
        if len(recoil.pitches) < profile.recoil_min_run:
            continue
        player_med = median(recoil.pitches)
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
            if player_med < ceiling.minimum and ceiling.mid > 0 and player_med < learned_floor:
                physics = True
                reasons.append(
                    f"{recoil.build_key} recoil median {player_med:.2f} deg is far under every measured "
                    f"human on this build (lowest {ceiling.minimum:.2f}, median {ceiling.mid:.2f})"
                )
            elif player_med < ceiling.p05:
                beyond_band += 1
                observations.append(
                    f"{recoil.build_key} recoil is in the low tail of humans who use this build"
                )
            compared = True
            continue
        # A designer floor exists and the spray was not blatant. Still learn the human tail.
        own = cohorts.dist(recoil.skill_band, recoil.build_key, "recoil", record.player_id)
        if cohort_is_thick(own, profile) and own is not None and player_med < own.p05:
            beyond_band += 1
            observations.append(f"{recoil.build_key} recoil is low for humans on this build, above the hard floor")
            compared = True

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
        dist = cohorts.dist(extra.group_key, extra.name, "extra", record.player_id)
        if not cohort_is_thick(dist, profile) or dist is None:
            untrained.append(f"{extra.name}:{extra.group_key}")
            observations.append(f"{extra.name} on {extra.group_key} is waiting for a baseline")
            continue
        compared = True
        if extra.direction == "high":
            past = player_med > dist.p95
            line = f"{extra.name} median {player_med:.3g} vs human p95 {dist.p95:.3g}"
        else:
            past = player_med < dist.p05
            line = f"{extra.name} median {player_med:.3g} vs human p05 {dist.p05:.3g}"
        metrics.append(
            MetricView(
                name=extra.name,
                player_value=player_med,
                bound=player_med,
                own_p95=dist.p95,
                own_max=dist.maximum,
                ceiling_band=extra.group_key,
                ceiling_p95=dist.p95,
                ceiling_extreme=dist.p05 if extra.direction == "low" else dist.p95,
                beyond_band=past,
                beyond_human=past,
            )
        )
        if not past:
            continue
        if extra.kind == "primary":
            beyond_human += 1
            reasons.append(line)
        else:
            supporting_families.add(f"extra:{extra.name}")
            observations.append(line)

    identity, identity_text = _identity(record, history, profile)
    if identity:
        reasons.append(identity_text)

    decision = decide(
        physics=physics,
        beyond_human=beyond_human,
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
    )
    case.seal = evidence_seal(case)
    return case


def _watch_for_batch(case: Case, reason: str, rank: int) -> None:
    """A batch tell can move a clean player to watch. It does not make a review."""
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


def annotate_vendors(cases: list[Case], records: list[PlayerRecord], profile: GameProfile) -> None:
    """Same leftover, after the kick is removed, across two customers."""
    by_case = {case.player_id: case for case in cases}
    residuals: dict[str, list[float]] = {}
    for record in records:
        best: list[float] | None = None
        for recoil in record.recoils:
            residual = command_residual(recoil.applied, recoil.compensation)
            if residual is None or len(residual) < profile.vendor_min_shots:
                continue
            if best is None or len(residual) > len(best):
                best = residual
        if best is not None:
            residuals[record.player_id] = best
    confirmed = {case.player_id for case in cases if case.decision == "review"}
    ids = sorted(residuals)
    for left_index, left in enumerate(ids):
        for right in ids[left_index + 1 :]:
            width = min(len(residuals[left]), len(residuals[right]))
            if width < profile.vendor_min_shots:
                continue
            correlation = pearson(residuals[left][:width], residuals[right][:width])
            if correlation is None or correlation < profile.vendor_min_r:
                continue
            pair_confirmed = left in confirmed or right in confirmed
            for player_id, other in ((left, right), (right, left)):
                case = by_case.get(player_id)
                if case is None:
                    continue
                _remember_twin(case, other, correlation)
                if pair_confirmed and player_id in confirmed:
                    case.observations.append(
                        f"leftover command matches {other} (r {correlation:.2f})"
                    )
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
                _watch_for_batch(case, reason, 2)


def annotate_inheritance(cases: list[Case], records: list[PlayerRecord], profile: GameProfile) -> None:
    """A teammate swings the hidden enemy faster than a voice can travel."""
    by_case = {case.player_id: case for case in cases}
    contacts: dict[str, list[tuple[int, str]]] = {}
    parties: dict[str, set[str]] = {}
    for record in records:
        found: list[tuple[int, str]] = []
        for weapon in record.weapons:
            found.extend(weapon.hidden_contacts)
        if found:
            contacts[record.player_id] = found
        for party in record.party_ids:
            parties.setdefault(party, set()).add(record.player_id)
    confirmed = [
        case.player_id
        for case in cases
        if case.decision == "review" and any("hidden mover" in reason for reason in case.reasons)
    ]
    for party, members in parties.items():
        cheaters = [player_id for player_id in confirmed if player_id in members]
        for cheater in cheaters:
            cheater_hits = contacts.get(cheater, [])
            if not cheater_hits:
                continue
            for mate in sorted(members):
                if mate == cheater or mate not in contacts:
                    continue
                lags: list[int] = []
                for moment, enemy in contacts[mate]:
                    priors = [stamp for stamp, tagged in cheater_hits if tagged == enemy and stamp <= moment]
                    if not priors:
                        continue
                    lags.append(moment - max(priors))
                case = by_case.get(mate)
                if case is None or not lags:
                    continue
                case.inherit_lags_ms = lags
                fast = [lag for lag in lags if 0 <= lag < profile.voice_min_ms]
                if len(fast) < profile.inherit_min_events:
                    continue
                low = min(fast)
                high = max(fast)
                span = f"{low} ms" if low == high else f"{low}–{high} ms"
                _watch_for_batch(
                    case,
                    (
                        f"{len(fast)} swings on a hidden enemy landed {span} after {cheater}, "
                        f"under the {profile.voice_min_ms} ms a voice needs"
                    ),
                    1,
                )


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
