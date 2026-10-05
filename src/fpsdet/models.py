"""Event, profile, and case records.

The game already knows its own rules. A movement sample should carry
``expected_max_ground_speed_mps`` for the stance and gear that sample was in.
A shot should carry ``expected_min_recoil_pitch_deg`` for that weapon and mod
set. Those fields override any curve shipped in the profile, so a perk,
adrenaline item, or new attachment cannot be flagged from a stale table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import Observation
from .knowledge import DEFAULT_CHANNELS
from .provenance import CaseProvenance


INNOCENT_CAUSES = frozenset(
    {
        "explosion",
        "knockback",
        "ragdoll",
        "vehicle",
        "parachute",
        "ladder",
        "ability",
        "launch",
        "teleport_volume",
        "zipline",
        "admin",
    }
)

BANDS = ("developing", "average", "advanced", "elite")
BAND_RANK = {name: i for i, name in enumerate(BANDS)}


def band_from_prior(prior: float) -> str:
    if prior < 0.30:
        return "developing"
    if prior < 0.70:
        return "average"
    if prior < 0.95:
        return "advanced"
    return "elite"


def higher_band(a: str, b: str) -> str:
    return a if BAND_RANK.get(a, -1) >= BAND_RANK.get(b, -1) else b


@dataclass(frozen=True)
class WeightClass:
    max_kg: float
    name: str = ""
    max_ground_speed_mps: float | None = None
    speed_multiplier: float | None = None


@dataclass(frozen=True)
class WeaponRule:
    min_shot_interval_ms: int | None = None
    interval_slack_ms: int = 0
    min_intervals: int = 20
    min_violations: int = 10
    min_violation_rate: float = 0.30
    server_paced: bool = False


@dataclass(frozen=True)
class RecoilFloor:
    weapon_id: str
    mod_set: tuple[str, ...]
    min_pitch_deg: float

    @property
    def build_key(self) -> str:
        return build_key(self.weapon_id, self.mod_set)


@dataclass(frozen=True)
class ExtraMetric:
    name: str
    source: str
    kind: str  # "primary" or "supporting"
    direction: str  # "high" or "low"
    min_samples: int = 30
    group_by: tuple[str, ...] = ("skill_band", "weapon_class")


@dataclass
class GameProfile:
    game_id: str
    min_shots: int = 40
    min_hits_for_headshot: int = 25
    min_cohort_players: int = 30
    self_jump_gap: float = 0.10
    aim_group: str = "weapon_class"  # or "weapon_id"
    speed_over_fraction: float = 0.08
    speed_min_run: int = 25
    speed_run_gap_ms: int = 400
    recoil_floor_fraction: float = 0.25
    recoil_min_spray_index: int = 3
    recoil_min_run: int = 10
    mirror_min_shots: int = 16
    mirror_max_r: float = -0.90
    mirror_lag_shots: int = 1
    mirror_lag_gap: float = 0.25
    # "learnable": the kick repeats by spray_index and players can memorise it.
    # "random": every shot's kick is drawn fresh, so nobody can anticipate it.
    recoil_pattern: str = "learnable"
    metronome_min_gaps: int = 40
    metronome_max_std_ms: float = 1.0
    tick_ms: int | None = None
    hidden_track_min_ms: float = 1200
    hidden_track_min_samples: int = 8
    hidden_grace_ms: float = 1000
    poison_jump: float = 0.08
    # The legal channels through which a client can know an enemy in this game (fpsdet.knowledge). An
    # enemy is unknowable only when every one was checked and failed; an unreported one means unknown.
    knowledge_channels: tuple[str, ...] = DEFAULT_CHANNELS
    # A match whose whole lobby sits this many robust standard deviations past the window's median match.
    match_outlier_sd: float = 5.0
    unknowable_min_samples: int = 12
    unknowable_jitter_ratio: float = 0.35
    vendor_min_r: float = 0.85
    vendor_min_shots: int = 32
    vendor_min_points: int = 12
    vendor_min_z: float = 5.0
    voice_min_ms: int = 350
    inherit_min_events: int = 4
    reference_lightest_speed_mps: float | None = None
    innocent_causes: frozenset[str] = field(default_factory=lambda: INNOCENT_CAUSES)
    weight_classes: list[WeightClass] = field(default_factory=list)
    weapons: dict[str, WeaponRule] = field(default_factory=dict)
    recoil_floors: dict[str, RecoilFloor] = field(default_factory=dict)
    extra_metrics: list[ExtraMetric] = field(default_factory=list)
    notes: str = ""

    def weapon_rule(self, weapon_class: str, weapon_id: str | None) -> WeaponRule | None:
        if weapon_id and weapon_id in self.weapons:
            return self.weapons[weapon_id]
        return self.weapons.get(weapon_class)


def build_key(weapon_id: str | None, mod_set: tuple[str, ...] | list[str]) -> str:
    wid = weapon_id or "unknown"
    mods = ",".join(sorted(mod_set))
    return f"{wid}|{mods}"


def weight_class_for(profile: GameProfile, kg: float) -> WeightClass | None:
    if not profile.weight_classes:
        return None
    chosen = profile.weight_classes[-1]
    for row in profile.weight_classes:
        if kg <= row.max_kg:
            chosen = row
            break
    return chosen


def curve_speed(profile: GameProfile, kg: float) -> float | None:
    chosen = weight_class_for(profile, kg)
    if chosen is None:
        return None
    if chosen.max_ground_speed_mps is not None:
        return chosen.max_ground_speed_mps
    if (
        chosen.speed_multiplier is not None
        and profile.reference_lightest_speed_mps is not None
    ):
        return profile.reference_lightest_speed_mps * chosen.speed_multiplier
    return None


def weight_class_name(profile: GameProfile, kg: float | None) -> str:
    if kg is None:
        return "unknown"
    chosen = weight_class_for(profile, kg)
    if chosen is None:
        return "unknown"
    return chosen.name or f"le-{chosen.max_kg:g}kg"


@dataclass(slots=True)
class Event:
    game_id: str
    match_id: str
    player_id: str
    t_ms: int
    event_type: str  # "shot" or "movement"
    skill_band: str
    weapon_class: str = "unknown"
    weapon_id: str | None = None
    mod_set: tuple[str, ...] = ()
    hit: bool = False
    hitbox: str | None = None
    distance_m: float | None = None
    through_geometry: bool | None = None
    loadout_weight_kg: float | None = None
    speed_mps: float | None = None
    on_ground: bool | None = None
    displacement_cause: str = "none"
    expected_max_ground_speed_mps: float | None = None
    recoil_pitch_deg: float | None = None
    spray_index: int | None = None
    expected_min_recoil_pitch_deg: float | None = None
    applied_recoil_pitch_deg: float | None = None
    compensation_pitch_deg: float | None = None
    hidden_track_ms: float | None = None
    private_track_ms: float | None = None
    wire_error_deg: float | None = None
    picture_error_deg: float | None = None
    interp_delay_ms: float | None = None
    information_state: str | None = None
    since_perceived_ms: float | None = None
    aim_jitter_deg: float | None = None
    enemy_id: str | None = None
    view_delta_deg: float | None = None
    acquire_ms: float | None = None
    map_id: str | None = None
    party_id: str | None = None
    # Per-channel knowledge of the shot's enemy, when the server reports channels one at a time.
    vision_state: str | None = None
    audio_state: str | None = None
    # A response to a planned challenge (fpsdet.challenge): which one, and the aim time on its target.
    challenge_id: str | None = None
    challenge_track_ms: float | None = None
    extras: dict[str, float] = field(default_factory=dict)

    @property
    def build(self) -> str:
        return build_key(self.weapon_id, self.mod_set)


@dataclass
class WeaponSummary:
    weapon_key: str
    weapon_class: str
    skill_band: str
    shots: int = 0
    hits: int = 0
    head_hits: int = 0
    head_known_hits: int = 0
    distances: list[float] = field(default_factory=list)
    geometry_true: int = 0
    geometry_known: int = 0
    fire_intervals: int = 0
    fire_violations: int = 0
    fire_gaps: list[int] = field(default_factory=list)
    # The same gaps, one list per match and gun. A cheat switched on mid-week is judged on its own matches.
    fire_matches: dict[str, list[int]] = field(default_factory=dict)
    hidden_track_ms: list[float] = field(default_factory=list)
    private_track_ms: list[float] = field(default_factory=list)
    wire_error_deg: list[float] = field(default_factory=list)
    picture_error_deg: list[float] = field(default_factory=list)
    interp_delay_ms: list[float] = field(default_factory=list)
    knowable_jitter: list[float] = field(default_factory=list)
    unknowable_jitter: list[float] = field(default_factory=list)
    # (match_id, t_ms, enemy_id). t_ms only compares inside one match.
    hidden_contacts: list[tuple[str, int, str]] = field(default_factory=list)
    view_deltas: list[float] = field(default_factory=list)
    acquire_ms: list[float] = field(default_factory=list)
    match_ids: set[str] = field(default_factory=set)
    # match_id -> [shots, hits, head_known_hits, head_hits]. Shots in one match are not independent.
    per_match: dict[str, list[int]] = field(default_factory=dict)
    # Information samples the knowledge engine kept out, by check and cause (perceived, recent, unchecked,
    # conflict, disagreed), and the recent-perception state of the hidden samples it counted.
    knowledge_skipped: dict[str, dict[str, int]] = field(default_factory=dict)
    hidden_recent: dict[str, int] = field(default_factory=dict)


@dataclass
class SpeedReport:
    eligible: int = 0
    excluded_innocent: int = 0
    excluded_unknown: int = 0
    excluded_airborne: int = 0
    missing_cap: int = 0
    violations: int = 0
    longest_run: int = 0
    sustained: bool = False
    spike_samples: int = 0
    cap_source: str = "none"
    detail: str = ""
    # Where the longest over-cap run was, and its fastest sample against the cap that sample had.
    run_match: str = ""
    run_start_ms: int | None = None
    run_end_ms: int | None = None
    run_peak_mps: float | None = None
    run_peak_cap_mps: float | None = None


@dataclass
class RecoilSummary:
    build_key: str
    weapon_key: str
    skill_band: str
    pitches: list[float] = field(default_factory=list)
    longest_low_run: int = 0
    low_samples: int = 0
    floor: float | None = None
    blatant: bool = False
    untrained: bool = False
    applied: list[float] = field(default_factory=list)
    compensation: list[float] = field(default_factory=list)
    spray: list[int | None] = field(default_factory=list)
    # Moments (one match, time and spray index) where the server sent different kicks: no order between them.
    unordered_moments: int = 0


@dataclass
class ExtraObs:
    name: str
    group_key: str
    direction: str
    kind: str
    values: list[float] = field(default_factory=list)


@dataclass
class PlayerRecord:
    player_id: str
    game_id: str
    skill_band: str
    weapons: list[WeaponSummary] = field(default_factory=list)
    speed: SpeedReport = field(default_factory=SpeedReport)
    recoils: list[RecoilSummary] = field(default_factory=list)
    extras: list[ExtraObs] = field(default_factory=list)
    match_ids: set[str] = field(default_factory=set)
    party_ids: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    # Every event that named a challenge or carried challenge time, in timeline order (fpsdet.challenge).
    challenge_samples: list = field(default_factory=list)


@dataclass
class HistoryWindow:
    player_id: str
    weapon_key: str
    skill_band: str
    shots: int
    hits: int


@dataclass
class MetricView:
    name: str
    player_value: float | None
    bound: float | None
    own_p95: float | None
    own_max: float | None
    ceiling_band: str | None
    ceiling_p95: float | None
    ceiling_extreme: float | None
    beyond_band: bool = False
    beyond_human: bool = False
    skipped: str | None = None
    key: str = ""  # the weapon key or extra group the number belongs to


@dataclass
class Case:
    player_id: str
    game_id: str
    decision: str
    recommended_action: str
    automated_action: str
    skill_band: str
    reports: int
    reasons: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    metrics: list[MetricView] = field(default_factory=list)
    match_ids: list[str] = field(default_factory=list)
    party_ids: list[str] = field(default_factory=list)
    untrained: list[str] = field(default_factory=list)
    speed: SpeedReport | None = None
    party_note: str = ""
    ai_brief: str = ""
    seal: str = ""
    queue_rank: int = 0
    vendor_twin: str = ""
    vendor_r: float | None = None
    inherit_lags_ms: list[int] = field(default_factory=list)
    # Machine-readable ids of what fired, from CHECKS. Dashboards group on these, not on reason text.
    checks: list[str] = field(default_factory=list)
    # The same findings as data (fpsdet.evidence), and each (metric, key) compared with a thick cohort.
    evidence: list[Observation] = field(default_factory=list)
    compared_on: list[tuple[str, str]] = field(default_factory=list)
    # What produced the evidence: the run's detector, profile and cohort, shared, and this player's own inputs.
    provenance: CaseProvenance | None = None
    # One result per challenge this player was given or named, followed or not (fpsdet.challenge.ChallengeResult).
    challenges: list = field(default_factory=list)
    limits: str = (
        "This case is evidence for a person. It is not a ban. "
        "A sustained gear-rule break or a result past the best measured humans "
        "is the finding. One wild frame is not."
    )


# id -> (family, label). A case lists the ids that fired in ``Case.checks``.
CHECKS: dict[str, tuple[str, str]] = {
    "speed": ("gear", "Speed over the gear cap"),
    "fire_rate": ("gear", "Faster than the gun cycles"),
    "metronome": ("gear", "Legal cycle, no human variation"),
    "recoil_floor": ("gear", "Recoil under the build floor"),
    "recoil_learned": ("gear", "Recoil under every human on the build"),
    "mirror": ("gear", "Command mirrors the kick, same tick"),
    "accuracy": ("baseline", "Accuracy past every human"),
    "headshot_rate": ("baseline", "Headshots past every human"),
    "median_distance": ("baseline", "Distance past every human"),
    "geometry_rate": ("baseline", "Through geometry past every human"),
    "extra": ("baseline", "Declared metric past every human"),
    "account_jump": ("baseline", "Account stopped looking like itself"),
    "rank_tail": ("baseline", "Above this rank, inside humans"),
    "supporting": ("baseline", "Supporting tell (snaps, timing)"),
    "hidden": ("information", "Aim on a hidden mover"),
    "quiet_aim": ("information", "Quiet only while unknowable"),
    "private_replay": ("information", "Aim on a private replay"),
    "wire": ("information", "Aim on the wire, not the picture"),
    "leftover": ("batch", "Shared humanizer leftover"),
    "voice": ("batch", "Teammate faster than a voice"),
}


ACTIONS = {
    "review": "human_review",
    "watch": "monitor",
    "clean": "none",
    "insufficient_data": "none",
}
