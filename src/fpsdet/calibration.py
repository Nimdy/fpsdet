"""Measuring native detectors against a labelled population: ``fpsdet.evaluation/2``. Measurement only.

The question is narrow. On one labelled population, among the players a detector had enough evidence to
run on, how often did it fire in the positive-labelled group, how often in the comparison group, and how
sure is that? This module answers it from finished cases (as ``fpsdet score`` or ``tools/regress.py
snapshot`` writes them) and a label file. The scorer never imports it. No threshold, role or decision moves
here, and no number here becomes a weight anywhere.

Labels are someone else's, never ground truth. A dataset definition (``fpsdet.evaluation-dataset/1``) says
what each label means and what it does not, and every report prints that before any number.

Who counts. A player enters a detector's rate only when the detector could run on them: the telemetry it
reads, enough samples to compute its number, and a baseline thick enough to compare against. Players it
could not run on are coverage, never a miss. A detector this dataset cannot run at all is
``not_observable``, with what is missing. It is never 0%.

Rates are counts over denominators with two-sided 95% Wilson intervals (z = Z_TWO_SIDED_95). Below
MIN_DESCRIPTIVE players a rate is not shown, and below MIN_MEASURED it is descriptive only. Both were set
before any rate was computed and have not moved since. There are no p-values, and the word for a status is
never "calibrated": a detector here is at most ``measured``, on one population, with that population's
labels.

The artifact binds the cases (each one's packet digest), the labels, the profile, detector and cohort
digests, the configuration, this code's digest and every statistic in one digest. It is not signed, and it
is never written into a case packet. ``verify`` recomputes the statistics from the per-player rows the
artifact carries.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from .evidence import ELIGIBILITY, KINDS, RETIRED_KINDS, SOURCE, canonical_json, eligibility_unit, rollup
from .models import GameProfile
from .provenance import normalized_source, profile_digest
from .statsutil import wilson_bound

# /2 reads each detector's eligibility from the case (evidence.detector_eligibility) and counts coverage by
# status; /1 inferred it from what P8's cases happened to record. Both are read; /2 is written.
EVALUATION_V1 = "fpsdet.evaluation/1"
EVALUATION_SCHEMA = "fpsdet.evaluation/2"
READABLE = (EVALUATION_V1, EVALUATION_SCHEMA)
# Coverage by why a detector could not run: the eligibility statuses, and not_recorded for a case written before
# cases recorded eligibility.
COVERAGE_STATUSES = (*ELIGIBILITY[1:], "not_recorded")
DATASET_SCHEMA = "fpsdet.evaluation-dataset/1"
FIXTURE_SCHEMA = "fpsdet.fixture-qualification/1"
SPLIT_RECIPE = "fpsdet.evaluation-split/1"
LABELS_RECIPE = "fpsdet.evaluation-labels/1"
INPUTS_RECIPE = "fpsdet.evaluation-inputs/1"
EVALUATOR_RECIPE = "fpsdet.evaluator/1"

# Two-sided 95% normal quantile, for the Wilson intervals here. statsutil.Z95 is the one-sided one the
# scorer's own bounds use; nothing here changes it.
Z_TWO_SIDED_95 = 1.959963984540054
# Fewer evaluated players than this in a cell, and its rate is not shown: "insufficient calibration sample".
MIN_DESCRIPTIVE = 20
# A detector is "measured" when the positive group and the primary comparison group each have at least
# this many evaluated players; between the two minimums it is "descriptive_only".
MIN_MEASURED = 100
STATUSES = ("not_observable", "insufficient_sample", "descriptive_only", "measured")
DECISIONS = ("review", "watch", "clean", "insufficient_data")
# Matches per player, the evidence amount. The published TF2 bins, plus one for anything longer.
EVIDENCE_BINS = ((1, 1), (2, 5), (6, 10), (11, 14), (15, 20), (21, None))
ROLES = ("positive", "comparison", "other")
DIGITS = 6

# Every native detector: the observation kinds fpsdet emits today. External kinds are another system's,
# and a retired kind is no longer emitted.
NATIVE_KINDS = tuple(kind for kind, (family, _check, _role) in KINDS.items() if family != "external" and kind not in RETIRED_KINDS)
NATIVE_FAMILIES = tuple(dict.fromkeys(KINDS[kind][0] for kind in NATIVE_KINDS))

# What each detector reads before it can run at all. ``fields``: event fields it needs, every one, where a
# tuple means any one of its fields. ``profile``: something the game profile must declare. ``timing``: shot
# times must be the server's own. ``history``: the run must be given account history. ``case``: the case
# records which players the detector could run on. Without that, the data may feed the detector but no
# rate is computed from cases, because nobody can say who was eligible.
NEEDS: dict[str, dict] = {
    "speed": {"fields": ("speed_mps", "on_ground"), "profile": "speed_cap", "case": True},
    "fire_rate": {"fields": ("hit",), "timing": True, "profile": "fire_intervals"},
    "metronome": {"fields": ("hit",), "timing": True, "profile": "metronome_intervals"},
    "recoil_floor": {"fields": ("recoil_pitch_deg",), "profile": "recoil_floor"},
    "mirror": {"fields": ("applied_recoil_pitch_deg", "compensation_pitch_deg")},
    "recoil_learned": {"fields": ("recoil_pitch_deg",)},
    "accuracy": {"fields": ("hit",), "case": True},
    "headshot_rate": {"fields": ("hit", "hitbox"), "case": True},
    "median_distance": {"fields": ("hit", "distance_m"), "case": True},
    "geometry_rate": {"fields": ("hit", "through_geometry"), "case": True},
    "extra": {"profile": "extra_primary", "case": True},
    "rank_tail": {"case": True},
    "view_snaps": {"fields": ("view_delta_deg",), "case": True},
    "acquire_timing": {"fields": ("acquire_ms",), "case": True},
    "supporting_extra": {"profile": "extra_supporting", "case": True},
    "account_jump": {"fields": ("hit",), "history": True},
    "hidden": {"fields": ("hidden_track_ms", ("information_state", "vision_state"))},
    "quiet_aim": {"fields": ("aim_jitter_deg", ("information_state", "vision_state"))},
    "wire": {"fields": ("wire_error_deg", "picture_error_deg", "interp_delay_ms")},
    "occluded_motion_replay": {"fields": (("challenge_track_ms", "private_track_ms"),)},
    "leftover": {"fields": ("applied_recoil_pitch_deg", "compensation_pitch_deg")},
    "voice": {"fields": ("enemy_id", "party_id", "hidden_track_ms", ("information_state", "vision_state"))},
}
PROFILE_NEEDS = {
    "speed_cap": "a ground-speed cap: expected_max_ground_speed_mps on the samples, or weight classes in the profile with loadout_weight_kg",
    "fire_intervals": "a weapon rule with a minimum shot interval",
    "metronome_intervals": "a weapon rule with a minimum shot interval that is not server-paced",
    "recoil_floor": "a recoil floor in the profile, or expected_min_recoil_pitch_deg on shots",
    "extra_primary": "a primary declared metric whose source the data carries",
    "extra_supporting": "a supporting declared metric whose source the data carries",
}
# Event fields that say who, where and when, not what was measured. A census does not ask them to be declared.
IDENTITY_FIELDS = frozenset({"game_id", "match_id", "player_id", "t_ms", "event_type", "skill_band", "weapon_class", "weapon_id", "map_id", "mod_set"})

# The metrics the scorer compares with a cohort, by the detector that fires past every human.
PAST_HUMAN = {"accuracy": "accuracy", "headshot_rate": "headshot_rate", "median_distance": "median_distance", "geometry_rate": "geometry_rate"}
# The metrics whose rank tail is a rank_tail observation. Primary declared metrics are added per profile.
RANK_METRICS = ("accuracy", "headshot_rate", "median_distance", "geometry_rate", "recoil")
# Supporting tells the scorer records as a metric row: compared when the row was not skipped.
ROW_KINDS = {"view_snaps": "view_p95", "acquire_timing": "acquire_median"}
# Kinds whose observation key is "metric:key", because one key can carry several metrics.
METRIC_KEYED = frozenset({"rank_tail", "extra", "supporting_extra"})
# Shared structure between two different findings on one player, read from the case's evidence graph.
STRUCTURES = ("cohort", "match", "key", "dependency", "partner")


class EvaluationError(ValueError):
    """The inputs cannot be evaluated as given. Nothing was written."""


class PublishedMismatch(EvaluationError):
    """The cases no longer give the decisions the dataset definition says were published."""


# Reading inputs.


def load_cases(path: str | Path) -> list[dict]:
    """Cases from a snapshot (one per line), a JSON list, or an index written by ``fpsdet score --out``."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        return data["cases"]
    return data if isinstance(data, list) else [data]


def load_dataset(path: str | Path) -> dict:
    return check_dataset(json.loads(Path(path).read_text(encoding="utf-8")))


def check_dataset(dataset: Mapping) -> dict:
    """A dataset definition, or EvaluationError. It must say what every label means, and name one positive
    label and at least one comparison label."""
    if dataset.get("schema") != DATASET_SCHEMA:
        raise EvaluationError(f"a dataset definition is {DATASET_SCHEMA}, not {dataset.get('schema')!r}")
    labels = dataset.get("labels")
    if not isinstance(labels, list) or not labels:
        raise EvaluationError("the dataset definition lists no labels")
    seen: set[str] = set()
    for entry in labels:
        for field in ("label", "role", "name", "meaning", "not_meaning"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise EvaluationError(f"label {entry.get('label')!r}: {field} must be said")
        if entry["role"] not in ROLES:
            raise EvaluationError(f"label {entry['label']!r}: role is one of {ROLES}")
        if entry["label"] in seen:
            raise EvaluationError(f"label {entry['label']!r} is listed twice")
        seen.add(entry["label"])
    roles = Counter(entry["role"] for entry in labels)
    if roles["positive"] != 1 or roles["comparison"] < 1:
        raise EvaluationError("a dataset has exactly one positive label and at least one comparison label")
    if not isinstance(dataset.get("telemetry"), list) or not all(isinstance(field, str) for field in dataset["telemetry"]):
        raise EvaluationError("telemetry lists the event fields the data carries")
    if not isinstance(dataset.get("server_shot_timing"), bool):
        raise EvaluationError("server_shot_timing says whether shot times are the server's own (true) or made by a converter (false)")
    for field in ("dataset", "title", "source", "unit", "match_level", "not_scored"):
        if not isinstance(dataset.get(field), str) or not dataset[field].strip():
            raise EvaluationError(f"the dataset definition must say its {field}")
    if not isinstance(dataset.get("caveats"), list) or not dataset["caveats"]:
        raise EvaluationError("a dataset definition lists its caveats")
    return json.loads(canonical_json(dataset))


def census(path: str | Path) -> dict:
    """Which event fields the data carries, counted over every line. A field present as null is absent."""
    counts: Counter = Counter()
    events = 0
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            events += 1
            row = json.loads(line)
            counts.update(key for key, value in row.items() if value is not None and value != [] and key not in IDENTITY_FIELDS)
    return {"events": events, "fields": dict(sorted(counts.items()))}


def check_census(declared: Iterable[str], found: Mapping) -> list[str]:
    """Where a census and a declaration disagree. The declaration is what the report prints, so it must be true."""
    fields = {name for name, n in found["fields"].items() if n > 0}
    declared = set(declared)
    problems = [f"{name} is declared but no event carries it" for name in sorted(declared - fields)]
    problems += [f"{name} is on {found['fields'][name]} events but is not declared" for name in sorted(fields - declared)]
    return problems


# Which detectors this dataset can run at all.


def _profile_has(need: str, profile: GameProfile, telemetry: set[str]) -> bool:
    if need == "speed_cap":
        return "expected_max_ground_speed_mps" in telemetry or (bool(profile.weight_classes) and "loadout_weight_kg" in telemetry)
    if need == "fire_intervals":
        return any(rule.min_shot_interval_ms is not None for rule in profile.weapons.values())
    if need == "metronome_intervals":
        return any(rule.min_shot_interval_ms is not None and not rule.server_paced for rule in profile.weapons.values())
    if need == "recoil_floor":
        return bool(profile.recoil_floors) or "expected_min_recoil_pitch_deg" in telemetry
    if need in ("extra_primary", "extra_supporting"):
        kind = need.split("_", 1)[1]
        return any(spec.kind == kind and spec.source in telemetry for spec in profile.extra_metrics)
    raise KeyError(need)


def _missing_fields(fields: Iterable, telemetry: set[str]) -> list[str]:
    missing = []
    for field in fields:
        options = field if isinstance(field, tuple) else (field,)
        if not any(option in telemetry for option in options):
            missing.append(" or ".join(options))
    return missing


def observability(profile: GameProfile, telemetry: Iterable[str], *, server_shot_timing: bool, history: bool, recorded: bool = False) -> dict[str, dict]:
    """Per native detector: can this dataset run it, and if not, what is missing. Machine-readable reasons:
    missing_telemetry, no_server_shot_timing, profile_declares_none, no_history_input, eligibility_not_recorded.
    ``recorded``: every case carries its detector eligibility, so no detector lacks a denominator for that reason."""
    telemetry = set(telemetry)
    out: dict[str, dict] = {}
    for kind in NATIVE_KINDS:
        need = NEEDS[kind]
        if kind == "rank_tail":
            # The rank tail of any compared metric: it runs where any of them runs.
            sources = [out[name] for name in ("accuracy", "headshot_rate", "median_distance", "geometry_rate", "extra")]
            recoil = "recoil_pitch_deg" in telemetry
            missing = [] if recoil or any(row["observable"] for row in sources) else ["a compared metric (hit, a declared primary metric, or recoil_pitch_deg)"]
        else:
            missing = _missing_fields(need.get("fields", ()), telemetry)
        reasons = ["missing_telemetry"] if missing else []
        if need.get("timing") and not server_shot_timing:
            reasons.append("no_server_shot_timing")
        if need.get("profile") and not _profile_has(need["profile"], profile, telemetry):
            reasons.append("profile_declares_none")
        if need.get("history") and not history:
            reasons.append("no_history_input")
        if not reasons and not need.get("case") and not recorded:
            reasons.append("eligibility_not_recorded")
        row = {"observable": not reasons, "reasons": reasons, "missing_telemetry": missing}
        if need.get("profile"):
            row["profile_needs"] = PROFILE_NEEDS[need["profile"]]
        if reasons:
            row["text"] = (
                "the data may feed it, but cases do not record which players it could run on, so no rate is computed"
                if reasons == ["eligibility_not_recorded"] else "not observable with this dataset"
            )
        out[kind] = row
    return out


# One row per scored player, from the case alone.


def split_of(player_id: str) -> str:
    """A fixed half of the population, from the pseudonym alone. Nothing about the player, their label or
    their decision goes in, and the scorer never reads it."""
    digest = hashlib.sha256(f"{SPLIT_RECIPE}\0{player_id}".encode("utf-8")).digest()
    return "dev" if digest[0] < 128 else "eval"


def _kind(obs: Mapping) -> str:
    """A retired kind reads as the kind that replaced it, as tools/regress.py migrates it."""
    return "occluded_motion_replay" if obs["kind"] in RETIRED_KINDS else obs["kind"]


def _structure(case: Mapping, native: list[dict]) -> list[list]:
    """For each pair of different detectors that fired on this player, what the evidence graph says they share."""
    graph = (case["evidence"].get("graph") or {})
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for edge in graph.get("edges") or []:
        out[edge["source"]][edge["relation"]].add(edge["target"])
    found: dict[tuple[str, str], set[str]] = {}
    for index, a in enumerate(native):
        for b in native[index + 1 :]:
            if _kind(a) == _kind(b):
                continue
            pair = tuple(sorted((_kind(a), _kind(b)), key=NATIVE_KINDS.index))
            shared = found.setdefault(pair, set())  # type: ignore[arg-type]
            a_id, b_id = f"observation:{a['observation_id']}", f"observation:{b['observation_id']}"
            ea, eb = out[a_id], out[b_id]
            if ea["compared_against"] & eb["compared_against"]:
                shared.add("cohort")
            if ea["occurred_in"] & eb["occurred_in"]:
                shared.add("match")
            if a["key"] and a["key"] == b["key"]:
                shared.add("key")
            if b_id in ea["depends_on"] or a_id in eb["depends_on"] or ea["depends_on"] & eb["depends_on"]:
                shared.add("dependency")
            if ea["names_partner"] & eb["names_partner"]:
                shared.add("partner")
    return [[a, b, sorted(shared)] for (a, b), shared in sorted(found.items(), key=lambda item: (NATIVE_KINDS.index(item[0][0]), NATIVE_KINDS.index(item[0][1])))]


def player_row(case: Mapping, label: str, profile: GameProfile, labels: Mapping[str, str], kinds: Iterable[str] = NATIVE_KINDS) -> dict:
    """What one case says about which detectors could run on this player, and which fired.

    ``evaluated`` is each detector's eligible units. ``not_evaluated`` is, for each detector in ``kinds`` (the
    ones the dataset can run) with no eligible unit, why: the player-level rollup of its eligibility. A case
    written before cases recorded eligibility has it inferred from what the case does record, as P8 did, and
    its reason is ``not_recorded``.
    """
    block = case["evidence"]
    recorded = block.get("detector_eligibility")
    if isinstance(recorded, Mapping):
        evaluated = {kind: set(entry.get("eligible", ())) for kind, entry in recorded["detectors"].items()}
        not_evaluated = {kind: rollup(entry) for kind, entry in recorded["detectors"].items() if kind in kinds and not entry.get("eligible")}
    else:
        evaluated = _inferred(case, profile)
        not_evaluated = {kind: "not_recorded" for kind in kinds if not evaluated.get(kind)}
    return _row(case, label, labels, evaluated, not_evaluated, checked=recorded is not None)


def _inferred(case: Mapping, profile: GameProfile) -> dict[str, set[str]]:
    """P8's eligibility, for a case that does not record its own: compared metrics, metric rows that were not
    skipped, and enough eligible movement samples."""
    block = case["evidence"]
    extras = {spec.name: spec.kind for spec in profile.extra_metrics}
    evaluated: dict[str, set[str]] = defaultdict(set)
    for entry in block["eligibility"]["compared"]:
        metric, key = entry["metric"], entry["key"]
        if metric in PAST_HUMAN:
            evaluated[PAST_HUMAN[metric]].add(key)
        if metric in RANK_METRICS or extras.get(metric) == "primary":
            evaluated["rank_tail"].add(f"{metric}:{key}")
        if metric in extras:
            evaluated["extra" if extras[metric] == "primary" else "supporting_extra"].add(f"{metric}:{key}")
    for row in case["metrics"]:
        for kind, metric in ROW_KINDS.items():
            if row["name"] == metric and not row["skipped"]:
                evaluated[kind].add(row["key"])
    speed = case.get("speed") or {}
    if int(speed.get("eligible") or 0) >= profile.speed_min_run:
        evaluated["speed"].add("")
    return evaluated


def _row(case: Mapping, label: str, labels: Mapping[str, str], evaluated: Mapping[str, set[str]], not_evaluated: Mapping[str, str], *, checked: bool) -> dict:
    block = case["evidence"]
    fired: dict[str, set[str]] = defaultdict(set)
    counts: Counter = Counter()
    partners: dict[str, set[str]] = defaultdict(set)
    native = []
    for obs in block["observations"]:
        if obs["source"] != SOURCE:
            continue
        native.append(obs)
        kind = _kind(obs)
        counts[kind] += 1
        fired[kind].add(eligibility_unit(obs))
        if obs["family"] == "relationship" and obs["evidence"].get("partner"):
            partners[kind].add(obs["evidence"]["partner"])
    for kind, units in fired.items():
        if (checked or NEEDS[kind].get("case")) and not units <= set(evaluated.get(kind, ())):
            raise EvaluationError(
                f"{case['player_id']}: {kind} fired on {sorted(units - set(evaluated.get(kind, ())))}, which the case does not record as compared. "
                "The eligibility rules here disagree with the scorer"
            )
    inputs = (block.get("provenance") or {}).get("inputs") or {}
    row = {
        "player": case["player_id"],
        "label": label,
        "split": split_of(case["player_id"]),
        "decision": case["decision"],
        "matches": int(inputs.get("matches") or len(case.get("match_ids") or [])),
        "band": case["skill_band"],
        "evaluated": {kind: sorted(units) for kind, units in sorted(evaluated.items()) if units},
        "not_evaluated": dict(sorted(not_evaluated.items())),
        "fired": {kind: sorted(units) for kind, units in sorted(fired.items())},
        "observations": dict(sorted(counts.items())),
    }
    pairs = _structure(case, native)
    if pairs:
        row["pairs"] = pairs
    if partners:
        row["partners"] = {kind: [[partner, labels.get(partner)] for partner in sorted(ids)] for kind, ids in sorted(partners.items())}
    return row


# Statistics. Every one is a pure function of the rows and the stored inputs, so verify can recompute them.


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, DIGITS)


def rate(count: int, denominator: int) -> dict:
    """A count over a denominator, with a two-sided 95% Wilson interval. Too small a denominator shows none."""
    out: dict = {"count": count, "denominator": denominator}
    if denominator < MIN_DESCRIPTIVE:
        out.update(rate=None, ci95=None, insufficient_sample=True)
        return out
    out["rate"] = _r(count / denominator)
    out["ci95"] = [
        _r(wilson_bound(count, denominator, upper=False, z=Z_TWO_SIDED_95)),
        _r(wilson_bound(count, denominator, upper=True, z=Z_TWO_SIDED_95)),
    ]
    return out


def enrichment(positive: Mapping, comparison: Mapping) -> dict:
    """The positive group's rate over the comparison group's, and a range from their intervals.

    ``range`` is the positive group's low end over the comparison's high end, and its high end over the
    comparison's low end. When the two groups are independent samples it covers the true ratio at least
    about 90% of the time (0.95 squared); it is not itself a 95% interval for the ratio. No pseudo-count
    or continuity correction is added. With no comparison fires the ratio is not a number: it is said so,
    with the low end only.
    """
    out: dict = {"ratio": None, "range": None, "note": None}
    if positive.get("rate") is None or comparison.get("rate") is None:
        out["note"] = "insufficient calibration sample"
        return out
    pk, pn, ck, cn = positive["count"], positive["denominator"], comparison["count"], comparison["denominator"]
    # From the exact bounds, not the rounded ones shown.
    p_lo, p_hi = (wilson_bound(pk, pn, upper=side, z=Z_TWO_SIDED_95) for side in (False, True))
    c_lo, c_hi = (wilson_bound(ck, cn, upper=side, z=Z_TWO_SIDED_95) for side in (False, True))
    if pk == 0 and ck == 0:
        out["note"] = "no fires in either group"
    elif ck == 0:
        out["note"] = "no comparison fires observed"
        out["range"] = [_r(p_lo / c_hi), None]
    else:
        out["ratio"] = _r((pk / pn) / (ck / cn))
        out["range"] = [_r(p_lo / c_hi), _r(p_hi / c_lo)]
    return out


def status_of(positive_n: int, comparison_n: int) -> str:
    low = min(positive_n, comparison_n)
    if low < MIN_DESCRIPTIVE:
        return "insufficient_sample"
    return "measured" if low >= MIN_MEASURED else "descriptive_only"


def _groups(dataset: Mapping) -> tuple[list[dict], str, list[str]]:
    groups = dataset["labels"]
    positive = next(group["label"] for group in groups if group["role"] == "positive")
    comparisons = [group["label"] for group in groups if group["role"] == "comparison"]
    return groups, positive, comparisons


def _bin_name(low: int, high: int | None) -> str:
    return f"{low}+" if high is None else (str(low) if low == high else f"{low}-{high}")


def _in_bin(matches: int, low: int, high: int | None) -> bool:
    return matches >= low and (high is None or matches <= high)


def _queue(members: list[dict], pool: list[dict], positive: str) -> dict:
    """Who is in a queue, by label, against the positive share of the pool it was drawn from."""
    in_queue = Counter(row["label"] for row in members)
    pool_positive = sum(row["label"] == positive for row in pool)
    prevalence = pool_positive / len(pool) if pool else None
    share = rate(in_queue[positive], len(members))
    return {
        "size": len(members),
        "by_label": dict(sorted(in_queue.items())),
        "positive_share": share,
        "pool": len(pool),
        "pool_positive": pool_positive,
        "prevalence": _r(prevalence),
        "expected_positive_at_prevalence": _r(len(members) * prevalence) if prevalence is not None else None,
        "share_over_prevalence": _r((in_queue[positive] / len(members)) / prevalence) if share["rate"] is not None and prevalence else None,
    }


def _decisions(rows: list[dict], dataset: Mapping) -> dict:
    groups, positive, comparisons = _groups(dataset)
    by_group = {}
    for group in groups:
        members = [row for row in rows if row["label"] == group["label"]]
        counts = Counter(row["decision"] for row in members)
        scored = len(members)
        evaluated = scored - counts["insufficient_data"]
        flagged = counts["review"] + counts["watch"]
        by_group[group["label"]] = {
            "scored": scored,
            "evaluated": evaluated,
            "counts": {decision: counts[decision] for decision in DECISIONS},
            "of_evaluated": {"review": rate(counts["review"], evaluated), "watch": rate(counts["watch"], evaluated), "review_or_watch": rate(flagged, evaluated)},
            "of_scored": {"review_or_watch": rate(flagged, scored)},
        }
    evaluated_pool = [row for row in rows if row["decision"] != "insufficient_data"]
    queues = {}
    for name, decisions in (("review", {"review"}), ("watch", {"watch"}), ("review_or_watch", {"review", "watch"})):
        members = [row for row in rows if row["decision"] in decisions]
        queues[name] = _queue(members, evaluated_pool, positive)
        queues[name]["scored_pool"] = len(rows)
        queues[name]["scored_prevalence"] = _r(sum(row["label"] == positive for row in rows) / len(rows)) if rows else None
    return {
        "by_group": by_group,
        "queues": queues,
        "enrichment": {
            comparison: {
                name: enrichment(by_group[positive]["of_evaluated"][name], by_group[comparison]["of_evaluated"][name])
                for name in ("review", "watch", "review_or_watch")
            }
            for comparison in comparisons
        },
    }


def _evidence_amount(rows: list[dict], dataset: Mapping) -> list[dict]:
    groups, _positive, _comparisons = _groups(dataset)
    out = []
    for low, high in EVIDENCE_BINS:
        cell = {"matches": _bin_name(low, high), "groups": {}}
        for group in groups:
            members = [row for row in rows if row["label"] == group["label"] and _in_bin(row["matches"], low, high)]
            evaluated = [row for row in members if row["decision"] != "insufficient_data"]
            cell["groups"][group["label"]] = {
                "scored": len(members),
                "evaluated": len(evaluated),
                "review": sum(row["decision"] == "review" for row in members),
                "review_or_watch": rate(sum(row["decision"] in ("review", "watch") for row in evaluated), len(evaluated)),
            }
        out.append(cell)
    return out


def _coverage(rows: list[dict], kinds: Iterable[str]) -> dict:
    """Who a detector (or any of a family's) could run on, and why not for everyone else: each player counted once,
    under the reason closest to running. A /1 artifact's rows carry P8's inferred breakdown instead."""
    kinds = tuple(kinds)
    if any("partial" in row for row in rows):
        return _coverage_v1(rows, kinds)
    players = len(rows)
    why: Counter = Counter()
    for row in rows:
        if any(kind in row["evaluated"] for kind in kinds):
            continue
        reasons = [row["not_evaluated"].get(kind, "not_recorded") for kind in kinds]
        why[min(reasons, key=COVERAGE_STATUSES.index)] += 1
    evaluated = players - sum(why.values())
    return {
        "players": players,
        "evaluated": evaluated,
        "insufficient": players - evaluated,
        "by_status": {status: why[status] for status in COVERAGE_STATUSES if why[status]},
    }


def _coverage_v1(rows: list[dict], kinds: tuple[str, ...]) -> dict:
    players = len(rows)
    evaluated = sum(any(kind in row["evaluated"] for kind in kinds) for row in rows)
    with_input = sum(any(kind in row["evaluated"] or kind in row["partial"] for kind in kinds) for row in rows)
    return {
        "players": players,
        "with_input": with_input,
        "evaluated": evaluated,
        "insufficient": players - evaluated,
        "below_sample_minimum": players - with_input,
        "thin_baseline": with_input - evaluated,
    }


def _measure(rows: list[dict], dataset: Mapping, kinds: tuple[str, ...]) -> dict:
    """Player-level coverage, rates, enrichment and queue share for one detector, or any of a family's."""
    groups, positive, comparisons = _groups(dataset)
    evaluated_rows = [row for row in rows if any(kind in row["evaluated"] for kind in kinds)]
    fired_rows = [row for row in evaluated_rows if any(kind in row["fired"] for kind in kinds)]
    coverage, rates = {}, {}
    for group in groups:
        members = [row for row in rows if row["label"] == group["label"]]
        coverage[group["label"]] = _coverage(members, kinds)
        mine = [row for row in evaluated_rows if row["label"] == group["label"]]
        rates[group["label"]] = rate(sum(any(kind in row["fired"] for kind in kinds) for row in mine), len(mine))
    primary = comparisons[0]
    return {
        "status": status_of(rates[positive]["denominator"], rates[primary]["denominator"]),
        "coverage": coverage,
        "rates": rates,
        "enrichment": {comparison: enrichment(rates[positive], rates[comparison]) for comparison in comparisons},
        "queue": _queue(fired_rows, evaluated_rows, positive),
    }


def _observation_level(rows: list[dict], dataset: Mapping, kind: str) -> dict:
    """Units (a weapon, a metric on a weapon) instead of players. Units of one player are not independent,
    so these are counts and plain shares with no interval."""
    groups, _positive, _comparisons = _groups(dataset)
    out = {}
    for group in groups:
        members = [row for row in rows if row["label"] == group["label"]]
        units = sum(len(row["evaluated"].get(kind, ())) for row in members)
        fired = sum(len(set(row["fired"].get(kind, ())) & set(row["evaluated"].get(kind, ()))) for row in members)
        out[group["label"]] = {
            "units_evaluated": units,
            "units_fired": fired,
            "share": _r(fired / units) if units else None,
            "observations": sum(row["observations"].get(kind, 0) for row in members),
        }
    return out


def _pairs(rows: list[dict], kind: str) -> dict:
    """A relationship finding names a partner. Pair-level counts, kept apart from the player-level rate."""
    pairs: dict[frozenset, tuple] = {}
    for row in rows:
        for partner, partner_label in row.get("partners", {}).get(kind, []):
            pairs[frozenset((row["player"], partner))] = (row["label"], partner_label)
    by_labels = Counter(" / ".join(sorted(str(label) for label in labels)) for labels in pairs.values())
    return {"pairs": len(pairs), "by_labels": dict(sorted(by_labels.items()))}


def _strata(rows: list[dict], dataset: Mapping, kind: str) -> dict:
    groups, positive, comparisons = _groups(dataset)
    primary = comparisons[0]

    def cells(select) -> dict:
        out = {}
        for group in groups:
            mine = [row for row in rows if row["label"] == group["label"] and select(row) is not None]
            out[group["label"]] = rate(sum(bool(select(row)) for row in mine), len(mine))
        return out

    def stratum(name: str, select) -> dict:
        found = cells(select)
        return {"stratum": name, "status": status_of(found[positive]["denominator"], found[primary]["denominator"]), "rates": found}

    keys = sorted({unit.split(":", 1)[1] if kind in METRIC_KEYED else unit for row in rows for unit in row["evaluated"].get(kind, ())} - {""})

    def by_key(key: str):
        def select(row):
            units = [unit for unit in row["evaluated"].get(kind, ()) if (unit.split(":", 1)[1] if kind in METRIC_KEYED else unit) == key]
            if not units:
                return None
            return any(unit in row["fired"].get(kind, ()) for unit in units)
        return select

    def evaluated_where(test):
        return lambda row: (kind in row["fired"]) if kind in row["evaluated"] and test(row) else None

    return {
        "key": [stratum(key, by_key(key)) for key in keys],
        "skill_band": [stratum(band, evaluated_where(lambda row, band=band: row["band"] == band)) for band in sorted({row["band"] for row in rows})],
        "matches": [
            stratum(_bin_name(low, high), evaluated_where(lambda row, low=low, high=high: _in_bin(row["matches"], low, high)))
            for low, high in EVIDENCE_BINS
        ],
    }


def _co_occurrence(rows: list[dict], dataset: Mapping, kinds: list[str]) -> dict:
    """How often two detectors fired on the same player, among players both could run on. Co-occurrence,
    not independence: two findings that share a cohort, a match or a weapon are not separate evidence."""
    groups, _positive, _comparisons = _groups(dataset)
    by_group = {}
    for group in groups:
        members = [row for row in rows if row["label"] == group["label"]]
        cells = []
        for index, a in enumerate(kinds):
            for b in kinds[index + 1 :]:
                both = [row for row in members if a in row["evaluated"] and b in row["evaluated"]]
                cells.append({
                    "a": a,
                    "b": b,
                    "both_evaluated": len(both),
                    "a_fired": sum(a in row["fired"] for row in both),
                    "b_fired": sum(b in row["fired"] for row in both),
                    "both_fired": sum(a in row["fired"] and b in row["fired"] for row in both),
                })
        by_group[group["label"]] = cells
    structure: dict[str, dict] = {}
    for row in rows:
        for a, b, shared in row.get("pairs", []):
            entry = structure.setdefault(f"{a} + {b}", {"players": 0, **{name: 0 for name in STRUCTURES}, "none_recorded": 0})
            entry["players"] += 1
            for name in shared:
                entry[name] += 1
            entry["none_recorded"] += int(not shared)
    return {"detectors": kinds, "by_group": by_group, "graph_structure": structure}


def _splits(rows: list[dict], dataset: Mapping, kinds: list[str]) -> dict:
    groups, _positive, _comparisons = _groups(dataset)
    out: dict = {"recipe": SPLIT_RECIPE, "counts": {}, "decisions": {}, "detectors": {}}
    for split in ("dev", "eval"):
        mine = [row for row in rows if row["split"] == split]
        out["counts"][split] = {group["label"]: sum(row["label"] == group["label"] for row in mine) for group in groups}
        out["decisions"][split] = {}
        for group in groups:
            members = [row for row in mine if row["label"] == group["label"] and row["decision"] != "insufficient_data"]
            out["decisions"][split][group["label"]] = rate(sum(row["decision"] in ("review", "watch") for row in members), len(members))
        for kind in kinds:
            cell = out["detectors"].setdefault(kind, {})[split] = {}
            for group in groups:
                members = [row for row in mine if row["label"] == group["label"] and kind in row["evaluated"]]
                cell[group["label"]] = rate(sum(kind in row["fired"] for row in members), len(members))
    return out


def statistics(rows: list[dict], dataset: Mapping, observable: Mapping[str, Mapping], label_counts: Mapping[str, int], unlabelled: int = 0) -> dict:
    """Every number of the evaluation, from the rows and the stored inputs alone. ``unlabelled`` is how many
    scored cases had no label; they have no row."""
    groups, positive, comparisons = _groups(dataset)
    labelled = rows
    population = {
        "groups": [
            {
                "label": group["label"],
                "role": group["role"],
                "name": group["name"],
                "labelled": int(label_counts.get(group["label"], 0)),
                "scored": sum(row["label"] == group["label"] for row in labelled),
                "evaluated": sum(row["label"] == group["label"] and row["decision"] != "insufficient_data" for row in labelled),
            }
            for group in groups
        ],
        "unlabelled_cases": unlabelled,
        "positive": positive,
        "primary_comparison": comparisons[0],
    }
    for entry in population["groups"]:
        entry["not_scored"] = entry["labelled"] - entry["scored"]
    detectors = []
    measured_kinds = []
    for kind in NATIVE_KINDS:
        family, check, role = KINDS[kind]
        entry: dict = {"kind": kind, "family": family, "check": check, "role": role, "observability": dict(observable[kind])}
        if family == "challenge":
            # No public dataset carries planned challenges: whatever a run shows, it is never real-world calibration.
            entry["real_world_calibration"] = "unavailable"
            entry["controlled_fixture"] = "fpsdet evaluate fixtures: planted by construction, never a real-world rate"
        if not observable[kind]["observable"]:
            fired = Counter(row["label"] for row in labelled if kind in row["fired"])
            if fired and observable[kind]["reasons"] != ["eligibility_not_recorded"]:
                raise EvaluationError(f"{kind} fired on this dataset, but the dataset is said not to be able to run it")
            entry["status"] = "not_observable"
            if fired:
                # Counts with no denominator: nobody can say who it could have fired on.
                entry["fired_players"] = {group["label"]: fired[group["label"]] for group in groups}
            detectors.append(entry)
            continue
        entry.update(_measure(labelled, dataset, (kind,)))
        entry["observation_level"] = _observation_level(labelled, dataset, kind)
        if family == "relationship":
            entry["pair_level"] = _pairs(labelled, kind)
        if entry["status"] != "insufficient_sample":
            entry["strata"] = _strata(labelled, dataset, kind)
        measured_kinds.append(kind)
        detectors.append(entry)
    families = []
    for family in NATIVE_FAMILIES:
        kinds = tuple(kind for kind in NATIVE_KINDS if KINDS[kind][0] == family and observable[kind]["observable"])
        entry = {"family": family, "detectors": list(kinds)}
        if not kinds:
            entry["status"] = "not_observable"
        else:
            entry.update(_measure(labelled, dataset, kinds))
        families.append(entry)
    firing = [kind for kind in measured_kinds if any(kind in row["fired"] for row in labelled)]
    missing: dict[str, list[str]] = defaultdict(list)
    for kind in NATIVE_KINDS:
        for field in observable[kind]["missing_telemetry"]:
            missing[field].append(kind)
    return {
        "population": population,
        "decisions": _decisions(labelled, dataset),
        "evidence_amount": _evidence_amount(labelled, dataset),
        "detectors": detectors,
        "families": families,
        "missing_telemetry": dict(sorted(missing.items())),
        "co_occurrence": _co_occurrence(labelled, dataset, firing),
        "splits": _splits(labelled, dataset, measured_kinds),
    }


# The artifact.


def _sha(recipe: str, payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(recipe.encode("ascii") + b"\0" + payload).hexdigest()


def labels_digest(labels: Mapping[str, str]) -> str:
    return _sha(LABELS_RECIPE, canonical_json(dict(sorted(labels.items()))).encode("utf-8"))


def inputs_digest(cases: Iterable[Mapping]) -> str:
    """The cases, by player and packet digest: the same cases give the same digest, in any order."""
    pairs = sorted((case["player_id"], (case["evidence"].get("packet") or {}).get("digest", "")) for case in cases)
    lines = "".join(f"{player}\0{packet}\n" for player, packet in pairs)
    return _sha(INPUTS_RECIPE, f"{len(pairs)}\0{lines}".encode("utf-8"))


def evaluator_digest() -> str:
    """This code and the interval code it calls, so a statistic names the code that computed it."""
    here = Path(__file__).resolve().parent
    hasher = hashlib.sha256(EVALUATOR_RECIPE.encode("ascii") + b"\0")
    for name in ("calibration.py", "statsutil.py"):
        source = normalized_source((here / name).read_bytes())
        hasher.update(f"fpsdet.{name[:-3]}\0{len(source)}\0".encode("ascii"))
        hasher.update(source)
    return "sha256:" + hasher.hexdigest()


def config() -> dict:
    return {
        "interval": "wilson",
        "level": 0.95,
        "sides": 2,
        "z": Z_TWO_SIDED_95,
        "min_descriptive": MIN_DESCRIPTIVE,
        "min_measured": MIN_MEASURED,
        "evidence_bins": [_bin_name(low, high) for low, high in EVIDENCE_BINS],
        "split": SPLIT_RECIPE,
        "statuses": list(STATUSES),
    }


def artifact_digest(artifact: Mapping) -> str:
    body = {key: value for key, value in artifact.items() if key != "digest"}
    return _sha(artifact.get("schema", EVALUATION_SCHEMA), canonical_json(body).encode("utf-8"))


def _run_provenance(cases: list[dict]) -> dict:
    """The provenance every case of the run shares, or EvaluationError when they do not share one."""
    stamps = Counter()
    history, external = Counter(), Counter()
    for case in cases:
        stamp = dict(case["evidence"].get("provenance") or {})
        history[(stamp.pop("history", None) or {}).get("mode", "none")] += 1
        external[(stamp.pop("external", None) or {}).get("mode", "none")] += 1
        stamp.pop("inputs", None)
        stamps[canonical_json(stamp)] += 1
    if len(stamps) != 1:
        raise EvaluationError(f"these cases come from {len(stamps)} different runs (detector, profile or cohort); evaluate one run at a time")
    shared = json.loads(next(iter(stamps)))
    if not shared:
        raise EvaluationError("these cases carry no provenance, so nothing says which detector or profile made them")
    return {
        "detector": shared["detector"]["digest"],
        "profile": shared["profile"]["digest"],
        "cohort": (shared.get("cohort") or {}).get("digest"),
        "cohort_mode": (shared.get("cohort") or {}).get("mode"),
        "history": dict(sorted(history.items())),
        "external": dict(sorted(external.items())),
    }


def _check_published(stats: Mapping, dataset: Mapping) -> dict | None:
    published = dataset.get("published")
    if not published:
        return None
    found = {label: cell["counts"] for label, cell in stats["decisions"]["by_group"].items()}
    expected = {label: {decision: int(counts.get(decision, 0)) for decision in DECISIONS} for label, counts in published["decisions"].items()}
    wrong = [label for label in expected if found.get(label) != expected[label]]
    if wrong:
        detail = "; ".join(f"{label}: published {expected[label]}, now {found.get(label)}" for label in wrong)
        raise PublishedMismatch(f"the published decisions in {published['where']} no longer follow from these cases: {detail}")
    return {"where": published["where"], "decisions": expected, "reproduced": True}


def evaluate(cases: list[dict], labels: Mapping[str, str], dataset: Mapping, profile: GameProfile, *, telemetry_census: Mapping | None = None) -> dict:
    """The evaluation artifact for one run's cases. Raises EvaluationError rather than measure something
    it cannot stand behind: a packet that does not verify, cases from mixed runs, the wrong profile, a
    telemetry declaration the data contradicts, or published numbers that no longer reproduce."""
    from .graph import verify_graph
    from .provenance import verify_packet

    dataset = check_dataset(dataset)
    if not cases:
        raise EvaluationError("no cases to evaluate")
    problems = []
    for case in cases:
        problems += [f"{case.get('player_id')}: {problem}" for problem in verify_packet(case) + verify_graph(case)]
        if len(problems) > 5:
            break
    if problems:
        raise EvaluationError("cases whose evidence packet does not verify are not evaluated: " + "; ".join(problems[:5]))
    run = _run_provenance(cases)
    if run["profile"] != profile_digest(profile):
        raise EvaluationError(f"the profile given ({profile_digest(profile)}) is not the one these cases were scored with ({run['profile']})")
    if telemetry_census is not None:
        problems = check_census(dataset["telemetry"], telemetry_census)
        if problems:
            raise EvaluationError("the dataset's telemetry declaration does not match the events: " + "; ".join(problems))
    history = any(mode != "none" for mode in run["history"])
    recorded = sum(isinstance(case["evidence"].get("detector_eligibility"), Mapping) for case in cases)
    observable = observability(
        profile, dataset["telemetry"], server_shot_timing=dataset["server_shot_timing"], history=history, recorded=recorded == len(cases)
    )
    runnable = [kind for kind in NATIVE_KINDS if observable[kind]["observable"]]
    undefined = sorted(set(labels.values()) - {group["label"] for group in dataset["labels"]})
    if undefined:
        raise EvaluationError(f"the labels use {undefined}, which the dataset definition does not explain")
    rows = sorted(
        (player_row(case, labels[case["player_id"]], profile, labels, runnable) for case in cases if case["player_id"] in labels),
        key=lambda row: row["player"],
    )
    unlabelled = sum(case["player_id"] not in labels for case in cases)
    label_counts = dict(sorted(Counter(labels.values()).items()))
    stats = statistics(rows, dataset, observable, label_counts, unlabelled)
    artifact = {
        "schema": EVALUATION_SCHEMA,
        "measurement_only": "Nothing here was read by the scorer, and no threshold, role or decision was changed or chosen from it.",
        "dataset": dataset,
        "inputs": {
            "cases": len(cases),
            "recipe": INPUTS_RECIPE,
            "digest": inputs_digest(cases),
            "packets_verified": len(cases),
            # Where each case's detector eligibility came from: recorded by the scorer, or inferred as P8 did.
            "eligibility": {"recorded": recorded, "inferred": len(cases) - recorded},
            **run,
        },
        "labels": {"recipe": LABELS_RECIPE, "digest": labels_digest(labels), "counts": label_counts},
        "telemetry": {"declared": sorted(dataset["telemetry"]), "census": None if telemetry_census is None else dict(telemetry_census)},
        "evaluator": {"recipe": EVALUATOR_RECIPE, "digest": evaluator_digest()},
        "config": config(),
        "observability": observable,
        "rows": rows,
        "statistics": stats,
        "published": _check_published(stats, dataset),
    }
    artifact = json.loads(canonical_json(artifact))
    artifact["digest"] = artifact_digest(artifact)
    return artifact


def verify_artifact(artifact: Mapping) -> list[str]:
    """Is an evaluation artifact what it says? Empty when it is. The statistics are recomputed from its rows
    by this code; a different evaluator digest alone is not a problem when they come out the same."""
    problems = []
    if artifact.get("schema") not in READABLE:
        return [f"not one of {', '.join(READABLE)}"]
    if artifact.get("digest") != artifact_digest(artifact):
        problems.append("the artifact digest does not match its contents")
    try:
        dataset = check_dataset(artifact["dataset"])
    except EvaluationError as exc:
        return problems + [str(exc)]
    stats = statistics(artifact["rows"], dataset, artifact["observability"], artifact["labels"]["counts"], artifact["statistics"]["population"]["unlabelled_cases"])
    if canonical_json(json.loads(canonical_json(stats))) != canonical_json(artifact["statistics"]):
        problems.append("the statistics do not follow from the rows")
    if artifact["config"] != json.loads(canonical_json(config())):
        problems.append("the configuration is not this code's")
    for row in artifact["rows"]:
        if row["split"] != split_of(row["player"]):
            problems.append(f"{row['player']}: split is not the one its pseudonym gives")
            break
    if artifact.get("published"):
        try:
            _check_published(artifact["statistics"], dataset)
        except PublishedMismatch as exc:
            problems.append(str(exc))
    return problems


def dumps(artifact: Mapping) -> str:
    """The artifact as text: readable statistics, one line per player row."""
    parts = []
    for key, value in artifact.items():
        if key == "rows":
            body = ",\n".join("  " + json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in value)
            parts.append(f' "rows": [\n{body}\n ]')
        else:
            text = json.dumps(value, indent=1, ensure_ascii=False).replace("\n", "\n ")
            parts.append(f" {json.dumps(key)}: {text}")
    return "{\n" + ",\n".join(parts) + "\n}\n"


# The report.


def _pct(cell: Mapping | None) -> str:
    if not cell or cell.get("rate") is None:
        return f"{cell['count']} of {cell['denominator']}, too few to rate" if cell else "-"
    lo, hi = cell["ci95"]
    return f"{cell['count']}/{cell['denominator']} = {cell['rate']:.1%} ({lo:.1%}–{hi:.1%})"


def _ratio(cell: Mapping) -> str:
    if cell["ratio"] is not None:
        lo, hi = cell["range"]
        return f"{cell['ratio']:.1f}x ({lo:.1f}–{hi:.1f})"
    if cell["range"]:
        return f"{cell['note']}; at least {cell['range'][0]:.1f}x"
    return cell["note"] or "-"


def _md(text: str) -> str:
    """Our own strings, but a table cell still cannot hold a pipe or a newline."""
    return str(text).replace("|", "\\|").replace("\n", " ")


STRATUM_NAMES = {"matches": "matches", "key": "weapon or group", "skill_band": "skill band"}
COVERAGE_NAMES = {
    "baseline_too_thin": "Baseline too thin", "insufficient_samples": "Too few samples", "conflict": "Conflict",
    "telemetry_unavailable": "No telemetry", "disabled": "Disabled", "not_applicable": "Not applicable", "not_recorded": "Not recorded",
}


def render_markdown(artifact: Mapping) -> str:
    dataset = artifact["dataset"]
    stats = artifact["statistics"]
    groups, positive, comparisons = _groups(dataset)
    names = {group["label"]: group["name"] for group in groups}
    primary = comparisons[0]
    out: list[str] = []
    add = out.append
    add(f"# {dataset['title']}: detector evaluation")
    add("")
    add(f"Generated by `fpsdet evaluate` ({artifact['schema']}). Measurement only: nothing here was read by the scorer, and no threshold, role or decision was changed or chosen from it.")
    add("")
    add("## Read this first")
    add("")
    for caveat in dataset["caveats"]:
        add(f"- {caveat}")
    add(f"- Every rate is among players a detector could run on. A rate below {MIN_DESCRIPTIVE} players is not shown; below {MIN_MEASURED} in either main group, a detector is descriptive only. Intervals are two-sided 95% Wilson intervals. No p-values.")
    add("- No status here is \"calibrated\". The strongest is \"measured\": on this population, with these labels.")
    add("")
    add("## What the labels mean")
    add("")
    add("| Label | Role | Who | What it does not mean |")
    add("| --- | --- | --- | --- |")
    for group in groups:
        add(f"| {_md(group['name'])} | {group['role']} | {_md(group['meaning'])} | {_md(group['not_meaning'])} |")
    add("")
    add("## Population")
    add("")
    add("| Group | Labelled | Scored | fpsdet could compare | Not scored |")
    add("| --- | ---: | ---: | ---: | ---: |")
    for group in stats["population"]["groups"]:
        add(f"| {_md(group['name'])} | {group['labelled']:,} | {group['scored']:,} | {group['evaluated']:,} | {group['not_scored']:,} |")
    add("")
    add(dataset["not_scored"])
    if stats["population"]["unlabelled_cases"]:
        add(f"{stats['population']['unlabelled_cases']} scored cases had no label and are left out.")
    add("")
    decisions = stats["decisions"]
    add("## Decisions")
    add("")
    add("| Group | Review | Watch | Clean | Insufficient | Review or watch, of compared | Of all scored |")
    add("| --- | ---: | ---: | ---: | ---: | --- | --- |")
    for label in names:
        cell = decisions["by_group"][label]
        counts = cell["counts"]
        add(f"| {_md(names[label])} | {counts['review']} | {counts['watch']} | {counts['clean']:,} | {counts['insufficient_data']:,} | {_pct(cell['of_evaluated']['review_or_watch'])} | {_pct(cell['of_scored']['review_or_watch'])} |")
    add("")
    add(f"Ratio of {names[positive]} to comparison, review or watch among compared players:")
    add("")
    for comparison in comparisons:
        add(f"- against {names[comparison]}: {_ratio(decisions['enrichment'][comparison]['review_or_watch'])}")
    add("")
    add("**Queue composition.** Who the queues hold, against the share of label-positive players among those fpsdet could compare (the chance level).")
    add("")
    add(f"| Queue | Players | {_md(names[positive])} | Share (95% CI) | Chance level | Expected by chance | Share over chance |")
    add("| --- | ---: | ---: | --- | ---: | ---: | ---: |")
    for name in ("review", "watch", "review_or_watch"):
        queue = decisions["queues"][name]
        expected = queue["expected_positive_at_prevalence"]
        over = queue["share_over_prevalence"]
        add(
            f"| {name.replace('_', ' ')} | {queue['size']} | {queue['by_label'].get(positive, 0)} | {_pct(queue['positive_share'])} | "
            f"{queue['prevalence']:.1%} | {expected:.1f} | {'-' if over is None else f'{over:.1f}x'} |"
        )
    add("")
    flagged = decisions["queues"]["review_or_watch"]
    if flagged["scored_prevalence"]:
        at_random = flagged["size"] * flagged["scored_prevalence"]
        add(
            f"Drawn at random from all {flagged['scored_pool']:,} scored players instead, {flagged['size']} players would hold about {at_random:.1f} "
            f"from the {names[positive]} group; the queue holds {flagged['by_label'].get(positive, 0)} ({flagged['by_label'].get(positive, 0) / at_random:.1f}x)."
        )
        add("")
    if artifact.get("published"):
        add(f"The decision counts above are the ones published in `{artifact['published']['where']}`, reproduced from these cases.")
        add("")
    add("**Evidence amount.** Review or watch among compared players, by how many matches a player has.")
    add("")
    add("| Matches | " + " | ".join(_md(names[group["label"]]) for group in groups) + " |")
    add("| --- |" + " --- |" * len(groups))
    for cell in stats["evidence_amount"]:
        if not any(group["scored"] for group in cell["groups"].values()):
            continue
        add(f"| {cell['matches']} | " + " | ".join(_pct(cell["groups"][group["label"]]["review_or_watch"]) for group in groups) + " |")
    add("")
    add("## Detectors")
    add("")
    add(f"Fired among players the detector could run on. Ratio is {names[positive]} over {names[primary]}, with the range from the two intervals (see docs/calibration.md).")
    add("")
    add(f"| Detector | Family | Status | {_md(names[positive])} | {_md(names[primary])} | Ratio | {_md(names[positive])} share of fired |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for entry in stats["detectors"]:
        if entry["status"] == "not_observable":
            continue
        add(
            f"| {entry['kind']} | {entry['family']} | {entry['status']} | {_pct(entry['rates'][positive])} | {_pct(entry['rates'][primary])} | "
            f"{_ratio(entry['enrichment'][primary])} | {_pct(entry['queue']['positive_share'])} |"
        )
    add("")
    others = [label for label in names if label not in (positive, primary)]
    if others:
        add("Other groups, fired among players the detector could run on:")
        add("")
        add("| Detector | " + " | ".join(_md(names[label]) for label in others) + " |")
        add("| --- |" + " --- |" * len(others))
        for entry in stats["detectors"]:
            if entry["status"] != "not_observable":
                add(f"| {entry['kind']} | " + " | ".join(_pct(entry["rates"][label]) for label in others) + " |")
        add("")
    add("**Not observable with this dataset.** These are not 0%: the data cannot show them.")
    add("")
    add("| Detector | Family | Why | Missing |")
    add("| --- | --- | --- | --- |")
    for entry in stats["detectors"]:
        if entry["status"] != "not_observable":
            continue
        obs = entry["observability"]
        why = ", ".join(obs["reasons"])
        missing = "; ".join(obs["missing_telemetry"] + ([obs["profile_needs"]] if "profile_declares_none" in obs["reasons"] else []))
        add(f"| {entry['kind']} | {entry['family']} | {why} | {_md(missing) or '-'} |")
    add("")
    add("The occluded-motion replay (challenge family) has no real-world measurement: no public dataset carries planned challenges. It is checked only on controlled fixtures (`fpsdet evaluate fixtures`), planted by construction.")
    add("")
    add("## Coverage")
    add("")
    v1 = artifact["schema"] == EVALUATION_V1
    if v1:
        add("Who each detector could run on. Below the sample minimum: too few shots or samples to compute its number. Thin baseline: the number was computed but the cohort to compare it with was too thin.")
        add("")
        add("| Detector | Group | Players | With input | Could run | Below sample minimum | Thin baseline |")
        add("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    else:
        add("Who each detector could run on, and why not for everyone else, as each case records it (`evidence.detector_eligibility`). A player is counted once, under the reason closest to running.")
        add("")
        add("| Detector | Group | Players | Could run | " + " | ".join(COVERAGE_NAMES[status] for status in COVERAGE_STATUSES) + " |")
        add("| --- | --- | ---: | ---: |" + " ---: |" * len(COVERAGE_STATUSES))
    for entry in stats["detectors"]:
        if entry["status"] == "not_observable":
            continue
        for label in (positive, *comparisons):
            cell = entry["coverage"][label]
            if v1:
                add(f"| {entry['kind']} | {_md(names[label])} | {cell['players']:,} | {cell['with_input']:,} | {cell['evaluated']:,} | {cell['below_sample_minimum']:,} | {cell['thin_baseline']:,} |")
            else:
                why = cell["by_status"]
                add(f"| {entry['kind']} | {_md(names[label])} | {cell['players']:,} | {cell['evaluated']:,} | " + " | ".join(f"{why.get(status, 0):,}" for status in COVERAGE_STATUSES) + " |")
    add("")
    add("**Observation level.** Units are a weapon, or a metric on a weapon, instead of a player. One player's units are not independent, so these are counts and plain shares with no interval.")
    add("")
    add(f"| Detector | {_md(names[positive])}: fired / units | {_md(names[primary])}: fired / units |")
    add("| --- | --- | --- |")
    for entry in stats["detectors"]:
        if entry["status"] == "not_observable":
            continue
        cells = [entry["observation_level"][label] for label in (positive, primary)]
        add(f"| {entry['kind']} | " + " | ".join(f"{cell['units_fired']} / {cell['units_evaluated']:,}" for cell in cells) + " |")
    add("")
    add("## Families")
    add("")
    add("Any finding in the family, among players at least one of its detectors could run on.")
    add("")
    add(f"| Family | Status | {_md(names[positive])} | {_md(names[primary])} | Ratio |")
    add("| --- | --- | --- | --- | --- |")
    for entry in stats["families"]:
        if entry["status"] == "not_observable":
            add(f"| {entry['family']} | not_observable | - | - | - |")
        else:
            add(f"| {entry['family']} | {entry['status']} | {_pct(entry['rates'][positive])} | {_pct(entry['rates'][primary])} | {_ratio(entry['enrichment'][primary])} |")
    add("")
    co = stats["co_occurrence"]
    add("## Overlap")
    add("")
    add("Co-occurrence, not independence: how often two detectors fired on the same player, among players both could run on.")
    add("")
    if co["by_group"].get(positive):
        add(f"| Pair | {_md(names[positive])}: both fired / both could run | {_md(names[primary])}: both fired / both could run |")
        add("| --- | --- | --- |")
        for a, b in zip(co["by_group"][positive], co["by_group"][primary]):
            add(f"| {a['a']} + {a['b']} | {a['both_fired']} / {a['both_evaluated']} | {b['both_fired']} / {b['both_evaluated']} |")
        add("")
    else:
        add("Fewer than two detectors fired on this dataset, so there is no overlap to show.")
        add("")
    if co["graph_structure"]:
        add("What the evidence graph says two co-firing findings share, by pair (players):")
        add("")
        add("| Pair | Players | Same cohort | Same match | Same weapon or group key | Dependency | Same partner | Nothing recorded |")
        add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        order = {f"{a} + {b}": (NATIVE_KINDS.index(a), NATIVE_KINDS.index(b)) for a in NATIVE_KINDS for b in NATIVE_KINDS}
        for pair in sorted(co["graph_structure"], key=order.__getitem__):
            cell = co["graph_structure"][pair]
            add(f"| {pair} | {cell['players']} | {cell['cohort']} | {cell['match']} | {cell['key']} | {cell['dependency']} | {cell['partner']} | {cell['none_recorded']} |")
        add("")
        add("Every native finding is server behaviour, one telemetry domain. Two findings measured against the same cohort move together if that cohort is off.")
        add("")
    add("## Strata")
    add("")
    add(f"Fired among players the detector could run on, by stratum. A cell with fewer than {MIN_DESCRIPTIVE} players is an insufficient calibration sample; strata where both main groups have one are left out of this table but kept in the JSON.")
    add("")
    add(f"| Detector | By | Stratum | {_md(names[positive])} | {_md(names[primary])} |")
    add("| --- | --- | --- | --- | --- |")
    for entry in stats["detectors"]:
        for by in ("matches", "key", "skill_band"):
            cells = (entry.get("strata") or {}).get(by, [])
            if len(cells) < 2:
                continue  # one stratum is the overall rate again
            for cell in cells:
                pos, comp = cell["rates"][positive], cell["rates"][primary]
                if pos["rate"] is None and comp["rate"] is None:
                    continue
                add(f"| {entry['kind']} | {STRATUM_NAMES[by]} | {_md(cell['stratum'])} | {_pct(pos)} | {_pct(comp)} |")
    add("")
    splits = stats["splits"]
    add("## Splits")
    add("")
    add("Two fixed halves of the population, from a hash of the pseudonym. No threshold was set on either: the split shows how much a rate moves between halves of the same population.")
    add("")
    add(f"| Measure | Half | {_md(names[positive])} | {_md(names[primary])} |")
    add("| --- | --- | --- | --- |")
    for split in ("dev", "eval"):
        add(f"| review or watch | {split} | {_pct(splits['decisions'][split][positive])} | {_pct(splits['decisions'][split][primary])} |")
    for kind in (kind for kind in NATIVE_KINDS if kind in splits["detectors"]):
        halves = splits["detectors"][kind]
        for split in ("dev", "eval"):
            add(f"| {kind} | {split} | {_pct(halves[split][positive])} | {_pct(halves[split][primary])} |")
    add("")
    add("## Limitations")
    add("")
    add(f"- Unit: {dataset['unit']}")
    add(f"- Match level: {dataset['match_level']}")
    add("- Observation-level counts (per weapon or metric) have no intervals: one player's weapons are not independent.")
    add("- A rate here describes this population, labelled this way. It is not the rate on another game, league or season, and it is not a probability that any one player cheated.")
    add("")
    add("## Provenance")
    add("")
    inputs = artifact["inputs"]
    add(f"- Cases: {inputs['cases']:,}, every evidence packet verified; input identity `{inputs['digest']}`")
    if "eligibility" in inputs:
        add(f"- Detector eligibility: recorded by the scorer on {inputs['eligibility']['recorded']:,} cases, inferred on {inputs['eligibility']['inferred']:,}")
    add(f"- Detector `{inputs['detector']}`, profile `{inputs['profile']}`, cohort `{inputs['cohort']}` ({inputs['cohort_mode']})")
    add(f"- Labels `{artifact['labels']['digest']}`")
    add(f"- Evaluator `{artifact['evaluator']['digest']}`")
    census_note = "checked against a census of every event" if artifact["telemetry"]["census"] else "declared, not checked against events"
    add(f"- Telemetry: {', '.join(artifact['telemetry']['declared'])} ({census_note})")
    add(f"- Evaluation digest `{artifact['digest']}` (not signed)")
    add("")
    return "\n".join(out)


# Controlled fixtures: the planted demo. Qualification of code on data built for it, never a rate.

# Each planted player, and the detectors it was built to trip.
PLANTED = {
    "weight-cheat": ("speed",),
    "fire-rate": ("fire_rate",),
    "metronome": ("metronome",),
    "no-recoil": ("recoil_floor",),
    "mirror-script": ("mirror",),
    "clone-source": ("mirror", "leftover"),
    "clone-buyer": ("leftover",),
    "rage": ("accuracy", "headshot_rate", "median_distance"),
    "rank-outlier": ("rank_tail",),
    "account-changed": ("account_jump",),
    "wall-eye": ("hidden",),
    "quiet-radar": ("quiet_aim",),
    "wire-lock": ("wire",),
    "replay-lock": ("occluded_motion_replay",),
    "radar-friend": ("voice",),
}
# Honest players built to look like a plant without the cheat, by the detector they must not trip.
TWINS = {
    "speed": ("legal-heavy", "blasted", "glitch", "adrenaline"),
    "recoil_floor": ("modded-recoil", "late-compensate"),
    "mirror": ("late-compensate",),
    "leftover": ("late-compensate",),
    "accuracy": ("elite-human", "weak-human", "reported-streamer"),
    "headshot_rate": ("elite-human", "weak-human", "reported-streamer"),
    "median_distance": ("elite-human", "weak-human", "reported-streamer"),
    "rank_tail": ("elite-human", "weak-human", "new-gun"),
    "hidden": ("angle-holder", "real-fight", "listened"),
    "quiet_aim": ("steady-hands", "listened"),
    "wire": ("picture-track",),
    "occluded_motion_replay": ("real-fight",),
    "voice": ("callout-friend",),
}


def qualify_fixtures() -> dict:
    """Does each planted behaviour trip the detector built for it, and does each honest twin stay clean?
    Outcomes per fixture player, never a rate: these players are planted by construction. Two worlds: the
    planted demo, and the controlled fixtures beside it (fpsdet.fixtures)."""
    from . import fixtures as controlled
    from .synthetic import EXPECT, build_demo

    demo = build_demo()
    world = controlled.build_fixtures()
    fired = {("demo", case.player_id): {obs.kind for obs in case.evidence} for case in demo.cases}
    fired.update({("fixtures", case.player_id): {obs.kind for obs in case.evidence} for case in world.cases})
    plants = [("demo", player, kinds) for player, kinds in PLANTED.items()] + [("fixtures", player, kinds) for player, kinds in controlled.PLANTED.items()]
    detectors = []
    for kind in NATIVE_KINDS:
        planted = [{"player": player, "world": where, "fired": kind in fired[(where, player)]} for where, player, kinds in plants if kind in kinds]
        twins = [{"player": player, "world": "demo", "fired": kind in fired[("demo", player)]} for player in TWINS.get(kind, ())]
        twins += [{"player": player, "world": "fixtures", "fired": kind in fired[("fixtures", player)]} for player in controlled.TWINS.get(kind, ())]
        if not planted:
            outcome = "no_controlled_fixture"
        elif all(row["fired"] for row in planted) and not any(row["fired"] for row in twins):
            outcome = "passes_controlled_fixture"
        else:
            outcome = "fails_controlled_fixture"
        detectors.append({"kind": kind, "family": KINDS[kind][0], "outcome": outcome, "planted": planted, "honest_twins": twins})
    honest = sorted(player for player, decision in EXPECT.items() if decision == "clean")
    return {
        "schema": FIXTURE_SCHEMA,
        "what": "controlled-fixture qualification: planted by construction, never a real-world rate, never calibration",
        "fixture": ["fpsdet.synthetic.build_demo", "fpsdet.fixtures.build_fixtures"],
        "demo_failures": list(demo.failures),
        "fixture_failures": controlled.fixture_failures(world),
        "detectors": detectors,
        "honest_fixtures_clean": {player: not fired[("demo", player)] for player in honest},
    }


def render_fixtures(result: Mapping) -> str:
    out = [
        "# Controlled-fixture qualification",
        "",
        "Planted by construction (`fpsdet.synthetic.build_demo` and `fpsdet.fixtures.build_fixtures`). This says whether the code trips on the behaviour it was built for and stays quiet on an honest twin built to look like it. It is not a real-world rate and not calibration.",
        "",
        "| Detector | Family | Outcome | Planted (fired?) | Honest twins (fired?) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in result["detectors"]:
        planted = ", ".join(f"{row['player']} {'yes' if row['fired'] else 'NO'}" for row in entry["planted"]) or "-"
        twins = ", ".join(f"{row['player']} {'YES' if row['fired'] else 'no'}" for row in entry["honest_twins"]) or "-"
        out.append(f"| {entry['kind']} | {entry['family']} | {entry['outcome']} | {planted} | {twins} |")
    clean = result["honest_fixtures_clean"]
    out += ["", f"Honest fixtures with no finding at all: {sum(clean.values())} of {len(clean)}.", ""]
    return "\n".join(out)
