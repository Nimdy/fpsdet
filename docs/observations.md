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
| Provenance, knowledge state, challenges, external evidence, the evidence graph | Proposed in [architecture-v1.md](architecture-v1.md); none of them is here |

Decisions still come from `score.decide`. Nothing reads an observation back into a decision. The legacy fields stay authoritative.

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
| `source` | `fpsdet`: computed by fpsdet from events a game server wrote. The only source today |
| `family` | Where the evidence comes from: `physics`, `weapon_rules`, `human_baseline`, `information`, `relationship`, `account_history` |
| `kind` | The detector. Each kind fires one case check id |
| `role` | The role the scorer gives it in the decision (below) |
| `subject_id` | The player the case is about |
| `key` | The weapon key, build key or declared-metric group, or `""` for the whole account |
| `match_ids` | The matches the finding was measured on, sorted, when the detector knows them |
| `depends_on` | Ids of observations, on this case or another, that this one rests on |
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

"Different metrics" counts the same metric on several weapons once, and each declared metric as its own. "Different families" counts view snaps, acquire timing, and each supporting declared metric. These are the rules of `score.decide`, and the batch passes' rule that a shared leftover or a voice-speed teammate is a watch and never a review.

Every kind has exactly one role, and the model refuses any other. Reports are not observations. They change queue order only.

## The evidence block

A case carries one new key, `evidence`:

```json
"evidence": {
  "version": 1,
  "observations": [ ... ],
  "eligibility": {"compared": [{"metric": "accuracy", "key": "rifle"}]}
}
```

`version` is the shape of this block. `observations` are in the order the scorer found them, which is the order of its sentences. `eligibility.compared` lists each metric and key the scorer actually measured against a thick cohort. It is there because no observation tells clean from insufficient data: a player nobody could compare and a player who was compared and passed both have no finding. With it, `implied_decision` gives back clean or `insufficient_data` exactly.

`implied_decision(block)` is the decision the roles imply. It is how the tests prove the evidence explains every decision. The scorer does not call it.

The case keys that were there before (`reasons`, the context lines in `observations`, `checks`, `metrics`, `seal` and the rest) do not change.

## Every detector

Each row is a detector the scorer runs today. `key` is the weapon key unless the row says otherwise. Thresholds are under `evidence.thresholds`. A bound records its `method`, its one-sided `level` (0.95) and its `value`; where a number has no bound, the observed value is what was compared.

| Detector (case check) | Family | Kind | Role | Material evidence |
| --- | --- | --- | --- | --- |
| Speed over the gear cap (`speed`) | physics | `speed` | review | `longest_run`, `over_cap_samples`, `eligible_samples`, `cap_source`, `run` (its match, start and end ms, and the fastest sample `peak_mps` against that sample's `cap_mps`); `min_run`, `over_fraction`, `run_gap_ms`. Key: none. Samples left out (innocent cause, unknown cause, airborne, no cap) are context |
| Faster than the gun cycles (`fire_rate`) | weapon_rules | `fire_rate` | review | `gaps`, `under_floor`, `matches`, `floor_ms`, `counted` (each match and gun that counted, with its gaps and gaps under the floor); cycle, slack, gaps per match, violation rate, totals |
| Legal cycle, no variation (`metronome`) | weapon_rules | `metronome` | review | `steady_gaps`, `matches`, `max_std_ms`, `mean_ms`, `counted` (each match: gaps, std, mean); cycle, `pace_ms`, cadence cycles, max std, min gaps |
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
| Aim on a private replay (`private_replay`) | information | `private_replay` | review | `shots`, `total_ms`; min shots, min total |
| Shared humanizer leftover (`leftover`) | relationship | `leftover` | watch | `partner`, `partner_in_review`, `r`, `fisher_z`, `points`, `keyed_by` (`spray` or `position`), `shared_habit_removed`; min r, min z, min points, min samples. Key: the weapon the pair matched on |
| Teammate faster than a voice (`voice`) | relationship | `voice` | watch | `partner`, `party_id`, `fast_lags_ms`, `lags`; `voice_ms`, `min_fast`. `match_ids` are the matches of the fast lags. `depends_on` holds the partner's `hidden` observation ids |

`eligibility.compared` gets an entry wherever the scorer marks a player as compared: accuracy, headshot rate, distance and geometry when their cohorts are thick; recoil when the build's cohort is thick and it has no designer floor, or when the player sits in the low tail of a floored build; a declared number when its group's cohort is thick. View snaps and acquire timing do not count as a comparison, as before.

Not observations, because they never change a decision: the context lines for short samples, thin cohorts and untrained builds, a short speed spike, a mirror test that could not run, the in-file cohort warning, party notes, and reports.

### Open points

- **Private replay is `information` for now.** It is today's single-field check, not the challenge engine. When the challenge engine exists it moves to a `challenge` family with the same meaning.
- **The voice-speed teammate still finds its partner by reason text.** That selection is unchanged here. The observation records the dependency by id. `depends_on` would be empty if that text matched something other than a hidden-mover finding, such as a player id containing the words; the structural fix is deferred with the text coupling.
- **A shared leftover has no `depends_on`.** When the partner is already a review, `partner_in_review` says so. That review comes from a combination of the partner's observations, not one of them.
- **Some detectors do not know their matches.** The hidden, private-replay, wire and quiet-aim samples, recoil and declared numbers are summarised without a match per sample. `match_ids` is then every match on that weapon in the window, or empty.
- **A number past every human is also past its rank.** The scorer counts that rank flag too, but writes no rank-tail line for it. The observation records `past_rank` instead of a second, rank-tail observation. The decision is the same: past every human is already a watch.

## Identity

`observation_id` is `obs-` followed by the first 24 hex digits of the SHA-256 of the canonical JSON of the material fields:

```json
{"v": 1, "source", "family", "kind", "role", "subject_id", "key", "match_ids", "depends_on", "evidence"}
```

Canonical JSON means sorted keys, no spaces, UTF-8, and no NaN. `context` is not material, so rewording an explanation does not move an id. Nothing random, counted, or timed goes in, so the same evidence scored twice has the same id and changed evidence has a different one. The id is not the case seal and does not change it.

## What can go in

- Strings, numbers, booleans, null, lists and objects with string keys. A set, a tuple of mixed objects, bytes or any other Python object is refused, not turned into its `repr`.
- Floats keep 12 significant digits. Python 3.12 rounds a float `sum()` differently from 3.11 in the last bit, and the same finding must have the same id on both.
- A float that is not finite is written as the string `"NaN"`, `"Infinity"` or `"-Infinity"`, so the JSON stays valid.
- No key named `action`, `automated_action` or `recommended_action`, at any depth. An observation describes evidence. It does not tell anyone to act on an account.

Everything an observation holds is data. The pages that show it escape it at the point of display, as they do every other field.
