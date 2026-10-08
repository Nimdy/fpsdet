# Observations

An observation is one finding the scorer makes, kept as data: which check fired, the role it played in the decision, and the numbers it compared. The sentence a reviewer reads (`reasons`, and the context lines in `observations`) stays exactly as it was. The observation is the same fact in a form a dashboard, a downstream model or another anti-cheat system can read without parsing English.

The model is `fpsdet.evidence`. It is data only and standard library only.

## Status

| Part | Status |
| --- | --- |
| `Observation`, the evidence block, `implied_decision` | Implemented (`src/fpsdet/evidence.py`, `tests/test_observations.py`) |
| The scorer recording every finding that feeds a decision | Implemented (`src/fpsdet/score.py`, `src/fpsdet/signals.py`) |
| Case files (`cases/*.json`, `scan-index.json`, `review-index.json`) carry `evidence` | Implemented |
| `ops.json`, the dashboard, the review desk and the AI brief read `evidence` | Not yet. They read what they read before, so none of them changed |
| Provenance of the detector code, the parsed profile, the cohort, the player's events and history (`evidence.provenance`), and one evidence-packet digest over all of it (`evidence.packet`) | Implemented; see [provenance.md](provenance.md) |
| Knowledge state, challenges, external evidence and the evidence graph | Implemented; see [knowledge-engine.md](knowledge-engine.md), [challenges.md](challenges.md), [external-evidence.md](external-evidence.md) and [evidence-graph.md](evidence-graph.md) |
| A signature over the packet | Not built: it needs a server-held signing key ([provenance.md](provenance.md)) |

Native decisions still come from `score.decide`. After it, external records can move a case with no native finding to watch, by the fusion rules ([external-evidence.md](external-evidence.md)); they read the records and the native decision, not observations. Nothing reads an observation back into a decision. The legacy fields stay authoritative.

What is proven, on every planted case, every case of the synthetic week (weekly and all seven nights), and the real CS2 (1,529) and TF2 (2,764) reruns:

- `implied_decision(evidence)` equals the case's decision.
- The check ids the observations fire are exactly the case's `checks`.
- Every line in `reasons` is the `context.line` of one observation printed in the reasons, and no observation claims a reason that is not there.
- A watch with no reason line, the rank-tail watch that is most of the real-data queue, has an observation that explains it (78 of 78 in TF2, 15 of 15 in CS2).
- No existing case field moved (`tests/test_golden.py`, `tools/regress.py diff`).

## Fields

| Field | Meaning |
| --- | --- |
| `observation_id` | `obs-` and 24 hex digits; see Identity |
| `source` | `fpsdet`: computed by fpsdet from events a game server wrote. `external`: another integrity system's record, carried as given ([external-evidence.md](external-evidence.md)) |
| `family` | Where the evidence comes from: `physics`, `weapon_rules`, `human_baseline`, `information`, `challenge`, `relationship`, `account_history`, and `external` for external records only |
| `kind` | The detector. Each kind fires one case check id |
| `role` | The role the scorer gives it in the decision (below) |
| `subject_id` | The player the case is about |
| `key` | The weapon key, build key or declared-metric group, or `""` for the whole account |
| `match_ids` | The matches the finding was measured on, sorted, when the detector knows them |
| `depends_on` | Ids of observations, on this case or another, that this one rests on. The evidence graph turns each into a `depends_on` edge ([evidence-graph.md](evidence-graph.md)) |
| `evidence` | The numbers and facts the detector compared: measurements, bounds, cohort lines, thresholds |
| `context` | Explanation that did not drive the result, such as the sentence printed on the case |

`evidence` is shaped per detector. It is not one schema with empty slots. Each value has a name that says what it is and, where it has one, its unit.

## Roles

A role says what the scorer does with the finding. There is no confidence number: a native observation's strength is its numbers, and where a bound exists `evidence` says which bound at what level.

| Role | Alone | With others |
| --- | --- | --- |
| `review` | review | — |
| `past_human` | watch | two of different metrics, or one with an `account_change` or a `supporting`, is a review |
| `account_change` | watch | with a `past_human`, a review |
| `supporting` | nothing | two of different families are a watch; with a `past_human`, a review |
| `watch` | watch | never part of a review |
| `external_watch` | watch, when nothing native fired | never part of a review, never counted with a native role, and any number of them is still a watch |
| `external_context` | nothing | nothing |

"Different metrics" counts the same metric on several weapons once, and each declared metric as its own. "Different families" counts view snaps, acquire timing, and each supporting declared metric. These are the rules of `score.decide`, and the batch passes' rule that a shared leftover or a voice-speed teammate is a watch and never a review.

Every kind has exactly one role, and the model refuses any other. Reports are not observations. They change queue order only. External records are the two external kinds, `external_signal` (role `external_watch`) and `external_context` (role `external_context`); they add no case check id. Which one a record becomes is decided by the fusion rules.

## The evidence block

A case carries one new key, `evidence`:

```json
"evidence": {
  "version": 1,
  "observations": [ ... ],
  "eligibility": {"compared": [{"metric": "accuracy", "key": "rifle"}]},
  "provenance": {"version": 1, "profile": {...}, "detector": {...}, "cohort": {...}, "inputs": {...}, "history": {...}},
  "packet": {"recipe": "fpsdet.packet/1", "status": "complete", "digest": "sha256:..."}
}
```

`version` is the shape of this block. `observations` are in the order the scorer found them, which is the order of its sentences. `eligibility.compared` lists each metric and key the scorer actually measured against a thick cohort. It is there because no observation tells clean from insufficient data: a player nobody could compare and a player who was compared and passed both have no finding. With it, `implied_decision` gives back clean or `insufficient_data` exactly.

`implied_decision(block)` is the decision the roles imply. It is how the tests prove the evidence explains every decision. The scorer does not call it.

`provenance` says what produced the observations: the detector code, the parsed game profile, the cohort, and the subject player's own events ([provenance.md](provenance.md)). Everything but the events is shared by every case of a run. It is `null` on a case scored outside one.

The case keys that were there before (`reasons`, the context lines in `observations`, `checks`, `metrics`, `seal` and the rest) do not change.

## Every detector

Each row is a detector the scorer runs today. `key` is the weapon key unless the row says otherwise. Thresholds are under `evidence.thresholds`. A bound records its `method`, its one-sided `level` (0.95) and its `value`; where a number has no bound, the observed value is what was compared.

| Detector (case check) | Family | Kind | Role | Material evidence |
| --- | --- | --- | --- | --- |
| Speed over the gear cap (`speed`) | physics | `speed` | review | `longest_run`, `over_cap_samples`, `eligible_samples`, `cap_source`, `run` (its match, start and end ms, and the fastest sample `peak_mps` against that sample's `cap_mps`); `min_run`, `over_fraction`, `run_gap_ms`. Key: none. Samples left out (innocent cause, unknown cause, airborne, no cap) are context |
| Faster than the gun cycles (`fire_rate`) | weapon_rules | `fire_rate` | review | `gaps`, `under_floor`, `matches`, `floor_ms`, `counted` (each match and gun that counted, with its gaps and gaps under the floor); cycle, slack, gaps per match, violation rate, totals |
| Legal cycle, no variation (`metronome`) | weapon_rules | `metronome` | review | `steady_gaps`, `matches`, `max_std_ms`, `mean_ms`, `counted` (each match: gaps, std, mean); cycle, `pace_ms`, cadence cycles, max std, min gaps; `shot_clock` and `tick_ms` when the profile declares a shot clock |
| Recoil under the build floor (`recoil_floor`) | weapon_rules | `recoil_floor` | review | `floor_deg`, `longest_low_run`, `low_shots`, `shots`; floor fraction, min run, min spray index. Key: the build |
| Command mirrors the kick (`mirror`) | weapon_rules | `mirror` | review | `same_tick_r`, `lagged_r`, `shots`, `pattern_removed`; max r, lag shots, lag gap, min shots. Key: the build |
| Recoil under every human on the build (`recoil_learned`) | human_baseline | `recoil_learned` | review | recoil median `observed`, its upper `bound`, `samples`, `ceiling` (band, players, min, p05, median), `learned_floor`; floor fraction. Key: the build |
| Accuracy, headshot rate, shots through geometry (`accuracy`, `headshot_rate`, `geometry_rate`) | human_baseline | same as the check | past_human | `successes`, `trials`, `observed`, `matches`, `bound` (Wilson lower, with the `design_effect` it used), `rank` (band, players, p95), `ceiling` (band holding the best human, players, `max`), `past_rank`, `past_human` |
| Engagement distance (`median_distance`) | human_baseline | `median_distance` | past_human | median `observed`, `samples`, `bound` (order statistic, lower), `rank` p95, `ceiling` max, `past_rank`, `past_human` |
| Declared primary number (`extra`) | human_baseline | `extra` | past_human | `metric`, `declared_as`, `group`, `direction`, median `observed`, `samples`, `bound`, `cohort` (players, p95 and max, or p05 and min), `past_tail`, `past_human`. Key: the declared group |
| Above the rank, inside the humans (`rank_tail`) | human_baseline | `rank_tail` | watch | the numbers of the metric it is about (any of the five above, recoil, or a primary declared number), with `metric` naming it |
| View snaps (`supporting`) | human_baseline | `view_snaps` | supporting | view p95 `observed`, `samples`, `ceiling` max. No bound: the observed p95 is compared |
| Target-acquire timing (`supporting`) | human_baseline | `acquire_timing` | supporting | `median` and `spread`, each with observed, samples, the rank's p05 and the ceiling's min. Median and spread are one family |
| Declared supporting number (`supporting`) | human_baseline | `supporting_extra` | supporting | as `extra` |
| Account stopped looking like itself (`account_jump`) | account_history | `account_jump` | account_change | `history` (windows, shots, hits, Wilson upper), `window` (shots, hits, matches, Wilson lower with its design effect), `gap`; `self_jump_gap`, `min_shots` |
| Aim on a hidden mover (`hidden`) | information | `hidden` | review | `shots`, `total_ms`; min shots, min total, `grace_ms`. Audio shots and shots inside the grace window were never counted |
| Quiet only while unknowable (`quiet_aim`) | information | `quiet_aim` | review | `knowable_median_deg`, `unknowable_median_deg`, shots on each side; max ratio, min shots each, min knowable noise |
| Aim on the wire, not the picture (`wire`) | information | `wire` | review | `led_shots`, `shots_with_both_errors`, `led_delay_ms`, `median_wire_error_deg`, `median_picture_error_deg` over the led shots; error ratio, gap, min shots, min delay |
| Aim on a private replay (`private_replay`) | challenge | `occluded_motion_replay` | review | `challenge` (id, origin, type, version, commitment, plan digest, window), `linkage`, `scope`, `eligible_samples`, `tracked_samples`, `total_ms`, `knowledge` (required, defeated, not applicable); min samples, min total. Key: the challenge id, or `legacy_private_replay:<aim key>` for the legacy field. See [challenges.md](challenges.md) |
| Shared humanizer leftover (`leftover`) | relationship | `leftover` | watch | `partner`, `partner_in_review`, `r`, `fisher_z`, `points`, `keyed_by` (`spray` or `position`), `shared_habit_removed`; min r, min z, min points, min samples. Key: the weapon the pair matched on |
| Teammate faster than a voice (`voice`) | relationship | `voice` | watch | `partner`, `party_id`, `fast_lags_ms`, `lags`; `voice_ms`, `min_fast`. `match_ids` are the matches of the fast lags. `depends_on` holds the partner's `hidden` observation ids |

`eligibility.compared` gets an entry wherever the scorer marks a player as compared: accuracy, headshot rate, distance and geometry when their cohorts are thick; recoil when the build's cohort is thick and it has no designer floor, or when the player sits in the low tail of a floored build; a declared number when its group's cohort is thick. View snaps and acquire timing do not count as a comparison, as before.

Not observations, because they never change a decision: the context lines for short samples, thin cohorts and untrained builds, a short speed spike, a mirror test that could not run, the in-file cohort warning, party notes, and reports.

## Detector eligibility

An observation is a detector that fired. A detector that did not fire leaves nothing, and without more, "it ran and found nothing" looks the same as "it could not run". `evidence.detector_eligibility` records the difference. The scorer writes it as it goes, from the same state each check reads. Nothing reads it back: no decision, finding, observation id, seal or graph depends on it.

```json
"detector_eligibility": {
  "recipe": "fpsdet.detector-eligibility/1",
  "detectors": {
    "accuracy": {"eligible": ["rifle"], "insufficient_samples": ["pistol"]},
    "hidden": {"telemetry_unavailable": ["pistol", "rifle"]},
    "speed": {"eligible": [""]},
    "...": "every native detector, always"
  }
}
```

**Units.** Each detector lists its units under their status. A unit is what the detector judges one at a time:
- **Account:** speed, keyed `""`.
- **Weapon key:** the aim checks and the information checks.
- **Recoil build:** the recoil checks.
- **Declared metric and group:** `metric:group`.
- **Rank comparison:** `metric:key`, where the metric is any compared metric, recoil, or a primary declared number.
- **Challenge:** a challenge id, or `legacy_private_replay:<aim key>`.
- **Party:** the voice check.

A detector with nothing to judge lists `""` as `telemetry_unavailable`: a player who fired no shot has no weapon for the aim checks. Several weapons never collapse into one status.

**Statuses.** The vocabulary is closed, from a detector that ran to one that never could:

| Status | Meaning |
| --- | --- |
| `eligible` | It ran on this unit. It fired if and only if an observation names the unit |
| `baseline_too_thin` | The number was computed, but the humans to compare it with were too few |
| `insufficient_samples` | The telemetry is there, but too little of it for the detector ever to fire |
| `conflict` | The telemetry contradicts itself, so the detector does not read it |
| `telemetry_unavailable` | The fields the detector reads were never sent for this unit |
| `disabled` | The profile gives it no rule for this unit, or the run no input it needs |
| `not_applicable` | It does not apply here: a server-paced gun, a floored build for the learned floor, no partner to time against |

**Firing.** Firing is read from the observations: eligible and fired, or eligible and not fired. `packet/5` checks that every native finding names an eligible unit.

**Player-level rollup.** A detector could run on a player when any of its units is eligible. Otherwise the player's status is the unit that came closest to running, in the order of the table.

**When each detector is eligible.** The bar is the least the detector needs ever to fire, so a unit below it could not have fired whatever the player did.

| Detector | Unit | Eligible when | Otherwise |
| --- | --- | --- | --- |
| `speed` | account | at least `speed_min_run` eligible ground samples; under `speed_min_run_ms`, eligible samples in one match that span that many milliseconds | `insufficient_samples` (fewer, or every sample left out as airborne or a tagged cause); `disabled` (ground samples with no cap, or `speed_min_run_ms` without `movement_clock: "server"`); `telemetry_unavailable` |
| `fire_rate` | weapon | a cycle rule, and as many gaps in matches of 5 or more gaps as the rule needs to fire | `disabled` (no rule); `insufficient_samples` |
| `metronome` | weapon | a cycle rule that is not server-paced, and `metronome_min_gaps` cadence gaps in matches that did not fire at the gun's own cycle | `disabled` (no cycle rule, or `shot_clock: "server_tick"` without `tick_ms`); `not_applicable` (server-paced, or every long match at the cycle); `insufficient_samples` |
| `recoil_floor` | build | recoil pitches, a floor, and `recoil_min_run` eligible pitches | `telemetry_unavailable`; `disabled` (no floor); `insufficient_samples` |
| `mirror` | build | `mirror_min_shots` kick and command pairs, with repeated sprays (or a random pattern) | `conflict` (kicks the server did not order); `telemetry_unavailable`; `insufficient_samples` |
| `recoil_learned` | build | no designer floor, `recoil_min_run` pitches and a thick ceiling cohort | `not_applicable` (a floored build); `telemetry_unavailable`; `insufficient_samples`; `baseline_too_thin` |
| `accuracy` | weapon | `min_shots` shots and thick cohorts | `insufficient_samples`; `baseline_too_thin` |
| `headshot_rate`, `median_distance`, `geometry_rate` | weapon | enough hits with the field and thick cohorts | `telemetry_unavailable` (hits, none with the field); `insufficient_samples`; `baseline_too_thin` |
| `extra`, `supporting_extra` | `metric:group` | `min_samples` values and a thick cohort | `disabled` (no declared metric of that kind, unit `""`); `telemetry_unavailable` (a declared metric never sent, unit = its name); `insufficient_samples`; `baseline_too_thin` |
| `rank_tail` | `metric:key` | the comparison it reads ran | that comparison's status; `not_applicable` for a build the recoil floor already reviewed |
| `view_snaps`, `acquire_timing` | weapon | `min_shots` samples and thick cohorts | `telemetry_unavailable`; `insufficient_samples`; `baseline_too_thin` |
| `account_jump` | weapon | `min_shots` in this window and in the account's history on that weapon and band | `telemetry_unavailable` (no history, unit `""`, or none on this weapon); `insufficient_samples` |
| `hidden` | weapon | `hidden_track_min_samples` moments with a readable track time | `insufficient_samples`; `conflict` (every reported moment disagreed); `telemetry_unavailable` |
| `quiet_aim` | weapon | `unknowable_min_samples` aim-noise samples on each side | `not_applicable` (knowable noise already under 0.05°); `insufficient_samples`; `conflict`; `telemetry_unavailable` (noise with no knowledge labels) |
| `wire` | weapon | `hidden_track_min_samples` shots with both errors | `insufficient_samples`; `telemetry_unavailable` |
| `occluded_motion_replay` | challenge | `hidden_track_min_samples` readable moments in the window, or on the legacy field | `disabled` (no plan for a named challenge, or the declared knowledge makes it unjudgeable); `not_applicable` (planned for someone else, or plans exist and none for this player, unit `""`); `conflict`; `insufficient_samples`; `telemetry_unavailable` |
| `leftover` | weapon | a qualifying signature, and at least one other player with one on that weapon | `baseline_too_thin` (alone on the weapon); `insufficient_samples`; `telemetry_unavailable`; `not_applicable` (scored without the batch pass) |
| `voice` | party | `inherit_min_events` swings timed against a confirmed hidden-mover teammate | `not_applicable` (no party, unit `""`, or no such teammate); `insufficient_samples`; `telemetry_unavailable` (no shot on a named enemy the knowledge engine could place) |

**What it is not.**
- **Not evidence:** a detector that could run and did not fire is not a finding, and the evidence graph has no node for it.
- **Not a threshold change:** every bar above is the detector's own, read where the detector reads it.

### Open points

- **The private replay moved to the `challenge` family** with the challenge engine (P4): kind `occluded_motion_replay`, for a planned challenge and for the legacy `private_track_ms` field alike. The case check id stays `private_replay`. Its observation ids moved, deliberately; the kind `private_replay` in the `information` family is retired, still readable so old packets verify, and never written ([challenges.md](challenges.md)).
- **The voice-speed teammate still finds its partner by reason text.** That selection is unchanged here. The observation records the dependency by id. `depends_on` would be empty if that text matched something other than a hidden-mover finding, such as a player id containing the words; the structural fix is deferred with the text coupling.
- **A shared leftover has no `depends_on`.** When the partner is already a review, `partner_in_review` says so. That review comes from a combination of the partner's observations, not one of them.
- **Some detectors do not know their matches.** The hidden, private-replay, wire and quiet-aim samples, recoil and declared numbers are summarised without a match per sample. `match_ids` is then every match on that weapon in the window, or empty.
- **A number past every human is also past its rank.** The scorer counts that rank flag too, but writes no rank-tail line for it. The observation records `past_rank` instead of a second, rank-tail observation. The decision is the same: past every human is already a watch.

## Identity

`observation_id` is `obs-` followed by the first 24 hex digits of the SHA-256 of the canonical JSON of the material fields:

```json
{"v": 1, "source", "family", "kind", "role", "subject_id", "key", "match_ids", "depends_on", "evidence"}
```

Canonical JSON means sorted keys, no spaces, UTF-8, and no NaN. `context` is not material, so rewording an explanation does not move an id. Nothing random, counted, or timed goes in, so the same evidence scored twice has the same id and changed evidence has a different one. The id is not the case seal and does not change it. It does not include provenance either: the id says which observation this is, provenance says what produced it, so the same evidence keeps its id under another profile or a later detector. The evidence packet ([provenance.md](provenance.md#the-evidence-packet)) binds the ids, and `verify_packet` recomputes every id from its observation before trusting it.

## What can go in

- Strings, numbers, booleans, null, lists and objects with string keys. A set, a tuple of mixed objects, bytes or any other Python object is refused, not turned into its `repr`.
- Floats keep 12 significant digits. Python 3.12 rounds a float `sum()` differently from 3.11 in the last bit, and the same finding must have the same id on both.
- A float that is not finite is written as the string `"NaN"`, `"Infinity"` or `"-Infinity"`, so the JSON stays valid.
- No key named `action`, `automated_action` or `recommended_action`, at any depth. An observation describes evidence. It does not tell anyone to act on an account.

Everything an observation holds is data. The pages that show it escape it at the point of display, as they do every other field.
