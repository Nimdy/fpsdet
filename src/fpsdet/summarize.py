"""Reduce raw events to per-player facts. Thresholds live in score.py."""

from __future__ import annotations

from collections import defaultdict

from .models import (
    Event,
    ExtraObs,
    GameProfile,
    PlayerRecord,
    RecoilSummary,
    SpeedReport,
    WeaponSummary,
    curve_speed,
    higher_band,
    weight_class_name,
)
from .parse import aim_key, recoil_floor_for


def _majority_band(events: list[Event]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[event.skill_band] += 1
    if not counts:
        return "unrated"
    best = max(counts.values())
    tied = [band for band, n in counts.items() if n == best]
    chosen = tied[0]
    for band in tied[1:]:
        chosen = higher_band(chosen, band)
    return chosen


def _resolve_cap(event: Event, profile: GameProfile) -> tuple[float | None, str]:
    if event.expected_max_ground_speed_mps is not None:
        return event.expected_max_ground_speed_mps, "server"
    if event.loadout_weight_kg is None:
        return None, "none"
    cap = curve_speed(profile, event.loadout_weight_kg)
    if cap is None:
        return None, "unset_curve"
    return cap, "curve"


def analyze_speed(events: list[Event], profile: GameProfile) -> SpeedReport:
    report = SpeedReport()
    samples = [ev for ev in events if ev.speed_mps is not None]
    samples.sort(key=lambda ev: (ev.match_id, ev.t_ms))
    limit = 1.0 + profile.speed_over_fraction
    run = 0
    prev: Event | None = None
    for event in samples:
        cap, source = _resolve_cap(event, profile)
        if source == "server":
            report.cap_source = "server"
        elif report.cap_source == "none" and source == "curve":
            report.cap_source = "curve"
        elif report.cap_source == "none" and source == "unset_curve":
            report.cap_source = "unset_curve"
        # Only an explicit normal sprint is evidence. A cause the profile does not
        # list yet (a new traversal gadget, a typo) is dropped, not flagged.
        if event.displacement_cause in profile.innocent_causes:
            report.excluded_innocent += 1
            run = 0
            prev = event
            continue
        if event.displacement_cause != "none":
            report.excluded_unknown += 1
            run = 0
            prev = event
            continue
        airborne = event.on_ground is False or event.on_ground is None
        if airborne:
            report.excluded_airborne += 1
            run = 0
            prev = event
            continue
        if cap is None or cap <= 0:
            report.missing_cap += 1
            run = 0
            prev = event
            continue
        report.eligible += 1
        over = event.speed_mps is not None and event.speed_mps > cap * limit
        same_run = (
            prev is not None
            and prev.match_id == event.match_id
            and event.t_ms - prev.t_ms <= profile.speed_run_gap_ms
        )
        if over:
            report.violations += 1
            run = run + 1 if same_run and run > 0 else 1
            report.longest_run = max(report.longest_run, run)
        else:
            run = 0
        prev = event
    report.sustained = report.longest_run >= profile.speed_min_run
    if report.violations and not report.sustained:
        report.spike_samples = report.violations
        report.detail = (
            f"{report.spike_samples} over-cap ground samples, longest run "
            f"{report.longest_run} (need {profile.speed_min_run}). Treated as a glitch or a blast "
            "the server did not tag, not as a cheat."
        )
    elif report.sustained:
        report.detail = (
            f"Ground speed stayed over the gear cap for {report.longest_run} samples "
            f"with displacement_cause none. Cap source: {report.cap_source}. "
            "If this was a ragdoll or explosion, the server did not mark it."
        )
    elif report.cap_source == "unset_curve" and report.missing_cap:
        report.detail = (
            "Weight classes are loaded but no speed cap is set. Send "
            "expected_max_ground_speed_mps from the server, or set "
            "reference_lightest_speed_mps. Nothing was flagged."
        )
    elif report.excluded_innocent and not report.sustained:
        report.detail = (
            f"{report.excluded_innocent} movement samples excluded "
            "(explosion, knockback, vehicle, parachute, ability, or another innocence tag)."
        )
    return report


def _weapon_summaries(events: list[Event], profile: GameProfile, band: str) -> list[WeaponSummary]:
    shots = [ev for ev in events if ev.event_type == "shot"]
    groups: dict[str, list[Event]] = defaultdict(list)
    for event in shots:
        groups[aim_key(event, profile)].append(event)
    summaries: list[WeaponSummary] = []
    for key, rows in groups.items():
        summary = WeaponSummary(
            weapon_key=key,
            weapon_class=rows[0].weapon_class,
            skill_band=band,
        )
        by_match: dict[tuple[str, str], list[Event]] = defaultdict(list)
        for event in rows:
            summary.shots += 1
            summary.match_ids.add(event.match_id)
            if event.hit:
                summary.hits += 1
                if event.hitbox is not None:
                    summary.head_known_hits += 1
                    if event.hitbox.lower() == "head":
                        summary.head_hits += 1
            if event.distance_m is not None:
                summary.distances.append(event.distance_m)
            if event.through_geometry is not None:
                summary.geometry_known += 1
                if event.through_geometry:
                    summary.geometry_true += 1
            if event.view_delta_deg is not None:
                summary.view_deltas.append(event.view_delta_deg)
            if event.acquire_ms is not None:
                summary.acquire_ms.append(event.acquire_ms)
            if event.hidden_track_ms is not None and event.hidden_track_ms > 0:
                summary.hidden_track_ms.append(event.hidden_track_ms)
            if event.private_track_ms is not None and event.private_track_ms > 0:
                summary.private_track_ms.append(event.private_track_ms)
            if (
                event.wire_error_deg is not None
                and event.picture_error_deg is not None
                and event.interp_delay_ms is not None
            ):
                summary.wire_error_deg.append(event.wire_error_deg)
                summary.picture_error_deg.append(event.picture_error_deg)
                summary.interp_delay_ms.append(event.interp_delay_ms)
            if event.aim_jitter_deg is not None and event.information_state == "unknowable":
                summary.unknowable_jitter.append(event.aim_jitter_deg)
            elif event.aim_jitter_deg is not None and event.information_state in {"visible", "audio"}:
                summary.knowable_jitter.append(event.aim_jitter_deg)
            if event.information_state == "unknowable" and event.enemy_id:
                summary.hidden_contacts.append((event.t_ms, event.enemy_id))
            phys_id = event.weapon_id or event.weapon_class
            by_match[(event.match_id, phys_id)].append(event)
        for grouped in by_match.values():
            grouped.sort(key=lambda ev: ev.t_ms)
            rule = profile.weapon_rule(grouped[0].weapon_class, grouped[0].weapon_id)
            if rule is None or rule.min_shot_interval_ms is None:
                continue
            floor = rule.min_shot_interval_ms - rule.interval_slack_ms
            for prev, nxt in zip(grouped, grouped[1:]):
                gap = nxt.t_ms - prev.t_ms
                summary.fire_intervals += 1
                summary.fire_gaps.append(gap)
                if gap < floor:
                    summary.fire_violations += 1
        summaries.append(summary)
    return summaries


def _recoil_summaries(events: list[Event], profile: GameProfile, band: str) -> list[RecoilSummary]:
    shots = [
        ev
        for ev in events
        if ev.event_type == "shot"
        and (
            ev.recoil_pitch_deg is not None
            or (ev.applied_recoil_pitch_deg is not None and ev.compensation_pitch_deg is not None)
        )
    ]
    groups: dict[str, list[Event]] = defaultdict(list)
    for event in shots:
        groups[event.build].append(event)
    out: list[RecoilSummary] = []
    for key, rows in groups.items():
        rows.sort(key=lambda ev: (ev.match_id, ev.t_ms))
        summary = RecoilSummary(
            build_key=key,
            weapon_key=aim_key(rows[0], profile),
            skill_band=band,
        )
        run = 0
        prev: Event | None = None
        for event in rows:
            floor = recoil_floor_for(event, profile)
            if summary.floor is None:
                summary.floor = floor
            eligible = (
                event.spray_index is not None
                and event.spray_index >= profile.recoil_min_spray_index
                and event.displacement_cause == "none"
            )
            if (
                event.applied_recoil_pitch_deg is not None
                and event.compensation_pitch_deg is not None
            ):
                summary.applied.append(event.applied_recoil_pitch_deg)
                summary.compensation.append(event.compensation_pitch_deg)
            if not eligible or event.recoil_pitch_deg is None:
                run = 0
                prev = event
                continue
            summary.pitches.append(event.recoil_pitch_deg)
            same = (
                prev is not None
                and prev.match_id == event.match_id
                and event.t_ms - prev.t_ms <= profile.speed_run_gap_ms
                and prev.spray_index is not None
                and event.spray_index is not None
                and event.spray_index >= prev.spray_index
            )
            low = floor is not None and event.recoil_pitch_deg < floor * profile.recoil_floor_fraction
            if low:
                summary.low_samples += 1
                run = run + 1 if same and run > 0 else 1
                summary.longest_low_run = max(summary.longest_low_run, run)
            else:
                run = 0
            prev = event
        if summary.floor is None:
            summary.untrained = True
        summary.blatant = summary.longest_low_run >= profile.recoil_min_run
        out.append(summary)
    return out


def _extra_group(event: Event, profile: GameProfile, spec_group: tuple[str, ...]) -> str:
    parts: list[str] = []
    for name in spec_group:
        if name == "skill_band":
            parts.append(event.skill_band)
        elif name == "weapon_class":
            parts.append(event.weapon_class)
        elif name == "weapon_id":
            parts.append(event.weapon_id or event.weapon_class)
        elif name == "build_key":
            parts.append(event.build)
        elif name == "weight_class":
            parts.append(weight_class_name(profile, event.loadout_weight_kg))
        else:
            parts.append(name)
    return "|".join(parts)


def _extras(events: list[Event], profile: GameProfile) -> list[ExtraObs]:
    found: dict[tuple[str, str], ExtraObs] = {}
    for spec in profile.extra_metrics:
        if spec.kind not in {"primary", "supporting"} or spec.direction not in {"high", "low"}:
            continue
        for event in events:
            if spec.source not in event.extras and not hasattr(event, spec.source):
                raw = event.extras.get(spec.source)
            else:
                raw = event.extras.get(spec.source)
                if raw is None:
                    raw = getattr(event, spec.source, None)
                    if not isinstance(raw, (int, float)):
                        raw = None
            if raw is None:
                continue
            group = _extra_group(event, profile, spec.group_by)
            slot = found.get((spec.name, group))
            if slot is None:
                slot = ExtraObs(
                    name=spec.name,
                    group_key=group,
                    direction=spec.direction,
                    kind=spec.kind,
                )
                found[(spec.name, group)] = slot
            slot.values.append(float(raw))
    return list(found.values())


def summarize_player(player_id: str, events: list[Event], profile: GameProfile) -> PlayerRecord:
    band = _majority_band(events)
    record = PlayerRecord(player_id=player_id, game_id=profile.game_id, skill_band=band)
    record.weapons = _weapon_summaries(events, profile, band)
    record.speed = analyze_speed(events, profile)
    record.recoils = _recoil_summaries(events, profile, band)
    record.extras = _extras(events, profile)
    for event in events:
        record.match_ids.add(event.match_id)
        if event.party_id:
            record.party_ids.add(event.party_id)
    if record.speed.cap_source == "unset_curve":
        record.notes.append(record.speed.detail)
    return record


def summarize(events: list[Event], profile: GameProfile) -> list[PlayerRecord]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        grouped[event.player_id].append(event)
    return [summarize_player(pid, rows, profile) for pid, rows in grouped.items()]
