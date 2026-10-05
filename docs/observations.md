# Observations

An observation is one finding the scorer makes, kept as data: which check fired, the role it played in the decision, and the numbers it compared. The sentence a reviewer reads (`reasons`, and the context lines in `observations`) stays exactly as it was. The observation is the same fact in a form a dashboard, a downstream model or another anti-cheat system can read without parsing English.

The model is `fpsdet.evidence`. It is data only and standard library only.

## Status

| Part | Status |
| --- | --- |
| `Observation`, the evidence block, `implied_decision` | Implemented (`src/fpsdet/evidence.py`, `tests/test_observations.py`) |
| The scorer recording its findings as observations | Not yet: the next commit |
| Provenance, knowledge state, challenges, external evidence, the evidence graph | Proposed in [architecture-v1.md](architecture-v1.md); none of them is here |

Decisions still come from `score.decide`. Nothing reads an observation back into a decision.

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
