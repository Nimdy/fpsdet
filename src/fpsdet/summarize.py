"""Reduce raw events to per-player facts. Thresholds live in score.py.

Every player's events are read in canonical timeline order (fpsdet.timeline), never in file order, and
what each player yields comes out in key order: weapons, recoil builds and declared metrics alike.
"""

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
from .timeline import player_timelines, same_time_groups, timeline


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


def _speed_sample(event: Event, cap: float | None, limit: float, profile: GameProfile, report: SpeedReport) -> bool:
    """Count one movement sample. True when it is an eligible ground sample over its cap."""
    # Only an explicit normal sprint is evidence. A cause the profile does not
    # list yet (a new traversal gadget, a typo) is dropped, not flagged.
    if event.displacement_cause in profile.innocent_causes:
        report.excluded_innocent += 1
        return False
    if event.displacement_cause != "none":
        report.excluded_unknown += 1
        return False
    if event.on_ground is False or event.on_ground is None:
        report.excluded_airborne += 1
        return False
    if cap is None or cap <= 0:
        report.missing_cap += 1
        return False
    report.eligible += 1
    if event.speed_mps is not None and event.speed_mps > cap * limit:
        report.violations += 1
        return True
    return False


def analyze_speed(events: list[Event], profile: GameProfile) -> SpeedReport:
    """A sustained run of over-cap ground samples, read one server moment at a time.

    Samples at the same match and time are one moment. The moment extends a run by its number of
    samples only when every one of them is an eligible ground sample over its cap; any other sample
    at that moment (under the cap, airborne, an innocent cause) breaks the run there, whichever order
    the file listed them in. A moment never extends a run on one side and starts it on the other.
    """
    report = SpeedReport()
    samples = [ev for ev in events if ev.speed_mps is not None]
    samples.sort(key=lambda ev: (ev.match_id, ev.t_ms))
    limit = 1.0 + profile.speed_over_fraction
    run = 0
    run_start = 0
    run_peak = (0.0, 1.0)  # (speed, cap) of the fastest sample in the current run, against its own cap
    prev: Event | None = None
    for moment in same_time_groups(samples):
        sources = set()
        over: list[tuple[float, float]] = []
        all_over = True
        for event in moment:
            cap, source = _resolve_cap(event, profile)
            sources.add(source)
            if _speed_sample(event, cap, limit, profile, report):
                over.append((event.speed_mps or 0.0, cap))  # type: ignore[arg-type]
            else:
                all_over = False
        # The first source seen in time order names the cap; the server's own cap always wins.
        if "server" in sources:
            report.cap_source = "server"
        elif report.cap_source == "none" and "curve" in sources:
            report.cap_source = "curve"
        elif report.cap_source == "none" and "unset_curve" in sources:
            report.cap_source = "unset_curve"
        first = moment[0]
        same_run = (
            prev is not None
            and prev.match_id == first.match_id
            and first.t_ms - prev.t_ms <= profile.speed_run_gap_ms
        )
        if all_over:
            count = len(moment)
            fastest = max(over, key=lambda pair: (pair[0] / pair[1], pair[0], pair[1]))
            run = run + count if same_run and run > 0 else count
            if run == count:
                run_start = first.t_ms
                run_peak = fastest
            elif fastest[0] / fastest[1] > run_peak[0] / run_peak[1]:
                run_peak = fastest
            if run > report.longest_run:
                report.run_match = first.match_id
                report.run_start_ms = run_start
                report.run_end_ms = first.t_ms
                report.run_peak_mps, report.run_peak_cap_mps = run_peak
            report.longest_run = max(report.longest_run, run)
        else:
            run = 0
        prev = moment[-1]
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


def _windowed(value: float | None, gap: int | None) -> float | None:
    """A per-shot track time covers only the time since the previous shot.

    An emitter that sends a running total would count the same second once per
    shot in a spray. Cutting each value back to its own window makes that
    harmless, and leaves a correct emitter unchanged.
    """
    if value is None or value <= 0:
        return None
    if gap is not None:
        value = min(value, float(gap))
    return value if value > 0 else None


def _recently_perceived(event: Event, profile: GameProfile) -> bool:
    """This client saw or heard that enemy a moment ago. Tracking where they went is human."""
    return event.since_perceived_ms is not None and event.since_perceived_ms < profile.hidden_grace_ms


def _track_times(shots: list[Event], profile: GameProfile) -> tuple[dict[int, float], dict[int, float]]:
    """The hidden-mover and private-replay time each shot adds, keyed by id(shot).

    Each server moment (one match and time) adds at most one sample to each, cut to the time since
    the player's previous moment in that match. Shots at one moment are one aim. When the shots there
    that report a track time agree, it counts once; when they disagree, nothing at that moment says
    which is right, so it adds nothing. ``shots`` must be in timeline order.
    """
    hidden: dict[int, float] = {}
    private: dict[int, float] = {}
    previous: Event | None = None
    for moment in same_time_groups(shots):
        first = moment[0]
        window = first.t_ms - previous.t_ms if previous is not None and previous.match_id == first.match_id else None
        previous = first
        claims = [ev for ev in moment if ev.hidden_track_ms is not None]
        if claims and len({(ev.hidden_track_ms, ev.information_state, ev.since_perceived_ms) for ev in claims}) == 1:
            shot = claims[0]
            value = _windowed(shot.hidden_track_ms, window)
            # Sound is knowable. Following footsteps through a wall, or a body
            # that just broke line of sight, is a player, not a wallhack.
            if value is not None and shot.information_state != "audio" and not _recently_perceived(shot, profile):
                hidden[id(shot)] = value
        claims = [ev for ev in moment if ev.private_track_ms is not None]
        if claims and len({ev.private_track_ms for ev in claims}) == 1:
            value = _windowed(claims[0].private_track_ms, window)
            if value is not None:
                private[id(claims[0])] = value
    return hidden, private


def _weapon_summaries(events: list[Event], profile: GameProfile, band: str) -> list[WeaponSummary]:
    shots = [ev for ev in events if ev.event_type == "shot"]
    hidden_times, private_times = _track_times(shots, profile)
    groups: dict[str, list[Event]] = defaultdict(list)
    for event in shots:
        groups[aim_key(event, profile)].append(event)
    summaries: list[WeaponSummary] = []
    for key in sorted(groups):
        rows = groups[key]
        summary = WeaponSummary(
            weapon_key=key,
            weapon_class=rows[0].weapon_class,
            skill_band=band,
        )
        by_match: dict[tuple[str, str], list[Event]] = defaultdict(list)
        for event in rows:
            summary.shots += 1
            summary.match_ids.add(event.match_id)
            tally = summary.per_match.setdefault(event.match_id, [0, 0, 0, 0])
            tally[0] += 1
            if event.hit:
                summary.hits += 1
                tally[1] += 1
                if event.hitbox is not None:
                    summary.head_known_hits += 1
                    tally[2] += 1
                    if event.hitbox.lower() == "head":
                        summary.head_hits += 1
                        tally[3] += 1
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
            recent = _recently_perceived(event, profile)
            if id(event) in hidden_times:
                summary.hidden_track_ms.append(hidden_times[id(event)])
            if id(event) in private_times:
                summary.private_track_ms.append(private_times[id(event)])
            if (
                event.wire_error_deg is not None
                and event.picture_error_deg is not None
                and event.interp_delay_ms is not None
            ):
                summary.wire_error_deg.append(event.wire_error_deg)
                summary.picture_error_deg.append(event.picture_error_deg)
                summary.interp_delay_ms.append(event.interp_delay_ms)
            # Inside the grace window an unknowable label is not yet evidence either way.
            unknowable = event.information_state == "unknowable" and not recent
            if event.aim_jitter_deg is not None and unknowable:
                summary.unknowable_jitter.append(event.aim_jitter_deg)
            elif event.aim_jitter_deg is not None and event.information_state in {"visible", "audio"}:
                summary.knowable_jitter.append(event.aim_jitter_deg)
            if unknowable and event.enemy_id:
                summary.hidden_contacts.append((event.match_id, event.t_ms, event.enemy_id))
            phys_id = event.weapon_id or event.weapon_class
            by_match[(event.match_id, phys_id)].append(event)
        # Matches and guns in key order; each one's shots in time order, as the timeline gave them.
        for where in sorted(by_match):
            grouped = by_match[where]
            rule = profile.weapon_rule(grouped[0].weapon_class, grouped[0].weapon_id)
            if rule is None or rule.min_shot_interval_ms is None:
                continue
            floor = rule.min_shot_interval_ms - rule.interval_slack_ms
            in_match = summary.fire_matches.setdefault(f"{grouped[0].match_id}|{grouped[0].weapon_id or grouped[0].weapon_class}", [])
            for prev, nxt in zip(grouped, grouped[1:]):
                gap = nxt.t_ms - prev.t_ms
                summary.fire_intervals += 1
                summary.fire_gaps.append(gap)
                in_match.append(gap)
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
    for key in sorted(groups):
        # The timeline order: match, time, then the server's spray count for shots at one time.
        rows = groups[key]
        summary = RecoilSummary(
            build_key=key,
            weapon_key=aim_key(rows[0], profile),
            skill_band=band,
        )
        run = 0
        prev: Event | None = None
        # One moment is one match, time and spray index: the server gave those shots no order.
        for moment in _recoil_moments(rows):
            kicks = set()
            all_low = True
            for event in moment:
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
                    summary.spray.append(event.spray_index)
                    kicks.add((event.applied_recoil_pitch_deg, event.compensation_pitch_deg))
                if not eligible or event.recoil_pitch_deg is None:
                    all_low = False
                    continue
                summary.pitches.append(event.recoil_pitch_deg)
                if floor is not None and event.recoil_pitch_deg < floor * profile.recoil_floor_fraction:
                    summary.low_samples += 1
                else:
                    all_low = False
            if len(kicks) > 1:
                summary.unordered_moments += 1
            first = moment[0]
            same = (
                prev is not None
                and prev.match_id == first.match_id
                and first.t_ms - prev.t_ms <= profile.speed_run_gap_ms
                and prev.spray_index is not None
                and first.spray_index is not None
                and first.spray_index >= prev.spray_index
            )
            # A moment extends a low run only when every shot in it is an eligible low one.
            if all_low:
                run = run + len(moment) if same and run > 0 else len(moment)
                summary.longest_low_run = max(summary.longest_low_run, run)
            else:
                run = 0
            prev = moment[-1]
        if summary.floor is None:
            summary.untrained = True
        summary.blatant = summary.longest_low_run >= profile.recoil_min_run
        out.append(summary)
    return out


def _recoil_moments(rows: list[Event]):
    """Runs of shots at one match, time and spray index. ``rows`` are in timeline order."""
    start = 0
    while start < len(rows):
        end = start + 1
        moment = (rows[start].match_id, rows[start].t_ms, rows[start].spray_index)
        while end < len(rows) and (rows[end].match_id, rows[end].t_ms, rows[end].spray_index) == moment:
            end += 1
        yield rows[start:end]
        start = end


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
    return [found[name_and_group] for name_and_group in sorted(found)]


def summarize_player(player_id: str, events: list[Event], profile: GameProfile) -> PlayerRecord:
    """One player's facts, from their events in canonical timeline order whatever order they came in."""
    return _summarize_timeline(player_id, timeline(events), profile)


def _summarize_timeline(player_id: str, events: list[Event], profile: GameProfile) -> PlayerRecord:
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
    """Every player's facts, players in id order."""
    return summarize_timelines(player_timelines(events), profile)


def summarize_timelines(timelines: dict[str, list[Event]], profile: GameProfile) -> list[PlayerRecord]:
    """Facts from timelines already built by fpsdet.timeline.player_timelines, so a run groups its events once."""
    return [_summarize_timeline(player_id, timelines[player_id], profile) for player_id in sorted(timelines)]
