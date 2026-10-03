"""Parse server events and game profiles. Unknown extra numeric fields are kept."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .models import (
    BANDS,
    INNOCENT_CAUSES,
    Event,
    ExtraMetric,
    GameProfile,
    RecoilFloor,
    WeaponRule,
    WeightClass,
    band_from_prior,
    build_key,
)


class ParseError(ValueError):
    pass


_RESERVED = {
    "game_id",
    "match_id",
    "player_id",
    "t_ms",
    "event_type",
    "skill_band",
    "skill_prior",
    "weapon_class",
    "weapon_id",
    "mod_set",
    "hit",
    "hitbox",
    "distance_m",
    "through_geometry",
    "loadout_weight_kg",
    "speed_mps",
    "on_ground",
    "displacement_cause",
    "expected_max_ground_speed_mps",
    "recoil_pitch_deg",
    "spray_index",
    "expected_min_recoil_pitch_deg",
    "applied_recoil_pitch_deg",
    "compensation_pitch_deg",
    "hidden_track_ms",
    "private_track_ms",
    "wire_error_deg",
    "picture_error_deg",
    "interp_delay_ms",
    "information_state",
    "since_perceived_ms",
    "aim_jitter_deg",
    "enemy_id",
    "view_delta_deg",
    "acquire_ms",
    "map_id",
    "party_id",
    "utc",
}


_INFO_STATES = {"visible", "audio", "unknowable"}
_RECOIL_PATTERNS = {"learnable", "random"}


def _information_state(obj: dict) -> str | None:
    if "information_state" not in obj or obj["information_state"] is None:
        return None
    value = obj["information_state"]
    if value not in _INFO_STATES:
        raise ParseError("information_state must be visible, audio, or unknowable")
    return value


def _str(obj: dict, key: str, *, required: bool = False, default: str | None = None) -> str | None:
    if key not in obj or obj[key] is None:
        if required:
            raise ParseError(f"{key} is required")
        return default
    value = obj[key]
    if not isinstance(value, str) or not value:
        raise ParseError(f"{key} must be a non-empty string")
    return value


def _num(obj: dict, key: str) -> float | None:
    if key not in obj or obj[key] is None:
        return None
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParseError(f"{key} must be a number")
    return float(value)


def _int(obj: dict, key: str, *, required: bool = False) -> int | None:
    if key not in obj or obj[key] is None:
        if required:
            raise ParseError(f"{key} is required")
        return None
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParseError(f"{key} must be an integer")
    return value


def _bool(obj: dict, key: str) -> bool | None:
    if key not in obj or obj[key] is None:
        return None
    value = obj[key]
    if not isinstance(value, bool):
        raise ParseError(f"{key} must be a boolean")
    return value


def parse_event(obj: dict) -> Event:
    if not isinstance(obj, dict):
        raise ParseError("event must be an object")
    skill_band = _str(obj, "skill_band")
    if skill_band is None and "skill_prior" in obj and obj["skill_prior"] is not None:
        prior = _num(obj, "skill_prior")
        skill_band = band_from_prior(prior if prior is not None else 0.0)
    if skill_band is None:
        skill_band = "unrated"
    elif skill_band not in BANDS and skill_band != "unrated":
        raise ParseError(f"unknown skill_band {skill_band}")
    mods = obj.get("mod_set") or []
    if isinstance(mods, str):
        mods = [mods]
    if not isinstance(mods, list) or not all(isinstance(m, str) and m for m in mods):
        raise ParseError("mod_set must be a list of item ids")
    event_type = _str(obj, "event_type", default="shot") or "shot"
    if event_type not in ("shot", "movement"):
        raise ParseError("event_type must be shot or movement")
    extras: dict[str, float] = {}
    for key, value in obj.items():
        if key in _RESERVED:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        extras[key] = float(value)
    cause = _str(obj, "displacement_cause", default="none") or "none"
    return Event(
        game_id=_str(obj, "game_id", required=True) or "",
        match_id=_str(obj, "match_id", required=True) or "",
        player_id=_str(obj, "player_id", required=True) or "",
        t_ms=_int(obj, "t_ms", required=True) or 0,
        event_type=event_type,
        skill_band=skill_band,
        weapon_class=_str(obj, "weapon_class", default="unknown") or "unknown",
        weapon_id=_str(obj, "weapon_id"),
        mod_set=tuple(mods),
        hit=bool(_bool(obj, "hit") or False),
        hitbox=_str(obj, "hitbox"),
        distance_m=_num(obj, "distance_m"),
        through_geometry=_bool(obj, "through_geometry"),
        loadout_weight_kg=_num(obj, "loadout_weight_kg"),
        speed_mps=_num(obj, "speed_mps"),
        on_ground=_bool(obj, "on_ground"),
        displacement_cause=cause,
        expected_max_ground_speed_mps=_num(obj, "expected_max_ground_speed_mps"),
        recoil_pitch_deg=_num(obj, "recoil_pitch_deg"),
        spray_index=_int(obj, "spray_index"),
        expected_min_recoil_pitch_deg=_num(obj, "expected_min_recoil_pitch_deg"),
        applied_recoil_pitch_deg=_num(obj, "applied_recoil_pitch_deg"),
        compensation_pitch_deg=_num(obj, "compensation_pitch_deg"),
        hidden_track_ms=_num(obj, "hidden_track_ms"),
        private_track_ms=_num(obj, "private_track_ms"),
        wire_error_deg=_num(obj, "wire_error_deg"),
        picture_error_deg=_num(obj, "picture_error_deg"),
        interp_delay_ms=_num(obj, "interp_delay_ms"),
        information_state=_information_state(obj),
        since_perceived_ms=_num(obj, "since_perceived_ms"),
        aim_jitter_deg=_num(obj, "aim_jitter_deg"),
        enemy_id=_str(obj, "enemy_id"),
        view_delta_deg=_num(obj, "view_delta_deg"),
        acquire_ms=_num(obj, "acquire_ms"),
        map_id=_str(obj, "map_id"),
        party_id=_str(obj, "party_id"),
        extras=extras,
    )


def load_events(path: str | Path) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            events.append(parse_event(obj))
        except (json.JSONDecodeError, ParseError) as exc:
            errors.append(f"line {lineno}: {exc}")
    return events, errors


def iter_events(lines: Iterable[str]) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(parse_event(json.loads(line)))
        except (json.JSONDecodeError, ParseError) as exc:
            errors.append(f"line {lineno}: {exc}")
    return events, errors


def _weight_class(obj: dict) -> WeightClass:
    return WeightClass(
        max_kg=float(obj["max_kg"]),
        name=str(obj.get("name") or ""),
        max_ground_speed_mps=(
            float(obj["max_ground_speed_mps"]) if obj.get("max_ground_speed_mps") is not None else None
        ),
        speed_multiplier=(
            float(obj["speed_multiplier"]) if obj.get("speed_multiplier") is not None else None
        ),
    )


def _extra(obj: dict) -> ExtraMetric:
    group = obj.get("group_by") or ["skill_band", "weapon_class"]
    return ExtraMetric(
        name=str(obj["name"]),
        source=str(obj["source"]),
        kind=str(obj.get("kind") or "supporting"),
        direction=str(obj["direction"]),
        min_samples=int(obj.get("min_samples") or 30),
        group_by=tuple(str(g) for g in group),
    )


def profile_from_dict(obj: dict) -> GameProfile:
    weapons: dict[str, WeaponRule] = {}
    for key, raw in (obj.get("weapons") or {}).items():
        weapons[str(key)] = WeaponRule(
            min_shot_interval_ms=raw.get("min_shot_interval_ms"),
            interval_slack_ms=int(raw.get("interval_slack_ms") or 0),
            min_intervals=int(raw.get("min_intervals") or 20),
            min_violations=int(raw.get("min_violations") or 10),
            min_violation_rate=float(raw.get("min_violation_rate") or 0.30),
            server_paced=bool(raw.get("server_paced") or False),
        )
    floors: dict[str, RecoilFloor] = {}
    for raw in obj.get("recoil_floors") or []:
        floor = RecoilFloor(
            weapon_id=str(raw["weapon_id"]),
            mod_set=tuple(raw.get("mod_set") or []),
            min_pitch_deg=float(raw["min_pitch_deg"]),
        )
        floors[floor.build_key] = floor
    causes = obj.get("innocent_causes")
    innocent = frozenset(str(c) for c in causes) if causes is not None else INNOCENT_CAUSES
    pattern = str(obj.get("recoil_pattern") or "learnable")
    if pattern not in _RECOIL_PATTERNS:
        raise ParseError("recoil_pattern must be learnable or random")
    ref = obj.get("reference_lightest_speed_mps")
    return GameProfile(
        game_id=str(obj.get("game_id") or "unknown"),
        min_shots=int(obj.get("min_shots") or 40),
        min_hits_for_headshot=int(obj.get("min_hits_for_headshot") or 25),
        min_cohort_players=int(obj.get("min_cohort_players") or 30),
        self_jump_gap=float(obj.get("self_jump_gap") or 0.10),
        aim_group=str(obj.get("aim_group") or "weapon_class"),
        speed_over_fraction=float(obj.get("speed_over_fraction") or 0.08),
        speed_min_run=int(obj.get("speed_min_run") or 25),
        speed_run_gap_ms=int(obj.get("speed_run_gap_ms") or 400),
        recoil_floor_fraction=float(obj.get("recoil_floor_fraction") or 0.25),
        recoil_min_spray_index=int(obj.get("recoil_min_spray_index") or 3),
        recoil_min_run=int(obj.get("recoil_min_run") or 10),
        mirror_min_shots=int(obj.get("mirror_min_shots") or 16),
        mirror_max_r=float(obj.get("mirror_max_r") if obj.get("mirror_max_r") is not None else -0.90),
        mirror_lag_shots=int(obj.get("mirror_lag_shots") if obj.get("mirror_lag_shots") is not None else 1),
        mirror_lag_gap=float(obj.get("mirror_lag_gap") if obj.get("mirror_lag_gap") is not None else 0.25),
        recoil_pattern=pattern,
        metronome_min_gaps=int(obj.get("metronome_min_gaps") or 40),
        metronome_max_std_ms=float(
            obj.get("metronome_max_std_ms") if obj.get("metronome_max_std_ms") is not None else 1.0
        ),
        tick_ms=int(obj["tick_ms"]) if obj.get("tick_ms") is not None else None,
        hidden_track_min_ms=float(obj.get("hidden_track_min_ms") or 1200),
        hidden_track_min_samples=int(obj.get("hidden_track_min_samples") or 8),
        hidden_grace_ms=float(obj.get("hidden_grace_ms") if obj.get("hidden_grace_ms") is not None else 1000),
        poison_jump=float(obj.get("poison_jump") if obj.get("poison_jump") is not None else 0.08),
        unknowable_min_samples=int(obj.get("unknowable_min_samples") or 12),
        unknowable_jitter_ratio=float(
            obj.get("unknowable_jitter_ratio")
            if obj.get("unknowable_jitter_ratio") is not None
            else 0.35
        ),
        vendor_min_r=float(obj.get("vendor_min_r") if obj.get("vendor_min_r") is not None else 0.85),
        vendor_min_shots=int(obj.get("vendor_min_shots") or 32),
        vendor_min_points=int(obj.get("vendor_min_points") or 12),
        vendor_min_z=float(obj.get("vendor_min_z") or 5.0),
        voice_min_ms=int(obj.get("voice_min_ms") or 350),
        inherit_min_events=int(obj.get("inherit_min_events") or 4),
        reference_lightest_speed_mps=float(ref) if ref is not None else None,
        innocent_causes=innocent,
        weight_classes=[_weight_class(row) for row in obj.get("weight_classes") or []],
        weapons=weapons,
        recoil_floors=floors,
        extra_metrics=[_extra(row) for row in obj.get("extra_metrics") or []],
        notes=str(obj.get("notes") or ""),
    )


def load_profile(path: str | Path) -> GameProfile:
    return profile_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def aim_key(event: Event, profile: GameProfile) -> str:
    if profile.aim_group == "weapon_id" and event.weapon_id:
        return event.weapon_id
    return event.weapon_class


def recoil_floor_for(event: Event, profile: GameProfile) -> float | None:
    if event.expected_min_recoil_pitch_deg is not None:
        return event.expected_min_recoil_pitch_deg
    floor = profile.recoil_floors.get(build_key(event.weapon_id, event.mod_set))
    return None if floor is None else floor.min_pitch_deg
