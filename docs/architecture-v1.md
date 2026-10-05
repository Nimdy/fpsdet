# Architecture v1: where fpsdet is, and where it is going

This is the engineering map for the move from "anti-cheat detector" to an evidence engine that sits beside VAC, EAC, BattlEye, Vanguard, a studio's own tools, human reviewers and downstream AI. It records the code as it is at `0.3.0+` (commit `26acb77`), then the target and the order of work.

Every statement is marked:

| Mark | Meaning |
| --- | --- |
| **Implemented** | In `src/fpsdet/` and covered by a test or the planted demo. |
| **Experimental** | In the repo, not relied on for a decision. |
| **Proposed** | Designed here. Not in the code. |
| **Unsupported** | Out of scope on purpose. |

Nothing in the Proposed sections is deployed.

## 1. What does not change

These hold before, during and after the work below. A change that breaks one stops the work (section 12).

1. `automated_action` is `"none"` on every case. The scorer recommends a person or nothing.
2. A missing or untrustworthy field turns a check off. It is never inferred.
3. A server measurement beats a client claim, and a server's statement about what a client could know beats a guess.
4. Reports set queue order. They never enter a decision or a seal.
5. AI runs after the decision, on the finished case, and cannot change it.
6. Standard library only at runtime.
7. A new check ships with a planted cheater and a planted honest twin.

## 2. Runtime map (implemented)

| Layer | Module | Lines | Responsibility |
| --- | --- | --- | --- |
| Types | `models.py` | 399 | `Event`, `GameProfile`, per-player summaries, `MetricView`, `Case`, the `CHECKS` id table, `ACTIONS`. |
| Input | `parse.py` | 334 | Event and profile parsing. Type checks on every reserved field; enums for `skill_band`, `event_type`, `information_state`; any other numeric key becomes `Event.extras`. `aim_key`, `recoil_floor_for`. |
| Input | `lake.py` | 60 | Append-only NDJSON lake, `game=<id>/dt=<day>/events.ndjson`. |
| Reduce | `summarize.py` | 375 | Events to one `PlayerRecord` per player. Also holds two detectors (the speed run and the blatant-recoil run) and the knowledge filters (audio, grace window, per-shot windowing). |
| Baseline | `baseline.py` | 216 | `CohortTable` (one number per player per band, key and metric), `build_cohorts`, the match screen. |
| Stats | `statsutil.py` | 116 | Median, nearest-rank percentile, Wilson bounds, order-statistic median bound, design effect, clustered bound. |
| Detect | `signals.py` | 407 | Mirror, metronome, hidden mover, private replay, wire, quiet aim, leftover residual and signature, poison alarms, `evidence_seal`. |
| Decide | `score.py` | 826 | `assess_player` (every per-player check and the decision), `decide`, the fire-interval rule, the human-baseline comparisons, and the batch passes: shared leftover, voice-speed teammate, party notes. |
| Evidence | `evidence.py` | — | *Added in P1.* `Observation`, the `evidence` block on each case, `implied_decision` (a check, not used by the scorer). See [observations.md](observations.md). |
| Input | `timeline.py` | — | *Added with event normalization.* The canonical player timeline every check reads, and grouping by server moment. See [event-normalization.md](event-normalization.md). |
| Knowledge | `knowledge.py` | — | *Added in P3.* Could this client know this target at this moment: known, unknown or unknowable, from declared channels; the one answer every information check reads. See [knowledge-engine.md](knowledge-engine.md). |
| Evidence | `provenance.py` | — | *Added in P2.1, extended in P2.2 and P2.3.* Digests of the detector source, the parsed profile, the cohort (and its integrity stamp), each player's events and readable history in `evidence.provenance`; the evidence packet digest and `verify_packet`; the guard that keeps the detector module list honest; the check that refuses a cohort file whose stored digest does not match. See [provenance.md](provenance.md). |
| Orchestrate | `pipeline.py` | 40 | `run_score`: summarize, cohort (frozen or in-file), assess, batch. |
| Order | `priority.py` | 23 | Scan order (reports first) and review order (decision first). |
| Output | `persist.py` | 166 | JSON shapes for cohort, history, reports, case, event. |
| Output | `casefile.py` | 118 | One case as JSON and an offline HTML page. |
| Output | `cli.py` | 349 | `demo`, `sample`, `pages`, `baseline`, `score`, `dashboard`, `ingest`. |
| AI | `ai_triage.py` | 135 | Redaction and one chat call per case a person might open. |
| View | `ops.py` | 423 | The operations payload (`ops.json`) and merging nightly runs. |
| View | `opsview.py`, `board.py`, `pages.py` | 992, 2,024, 53 | The dashboard, the review desk (Python, HTML, CSS and JS in one file), the public site. |
| Synthetic | `synthetic.py`, `week.py` | 796, 526 | The 32 planted players with their expected decisions, and the 400-player synthetic week. |
| Real data | `examples/cs2/cs2cd.py`, `examples/tf2/tf2logs.py` | 444, 510 | Converters from CS2CD and logs.tf to events, label joins, reports, desk pages. |

Presentation (`ops.py`, `opsview.py`, `board.py`, `pages.py`, `casefile.py`) reads cases. It never feeds back into a decision. The detection code is the eight modules from `models.py` to `pipeline.py`.

## 3. Event to case (implemented)

```text
game server ──NDJSON──► lake.ingest_lines ──► lake partitions (append only)
                                                   │
                         parse.load_events / iter_events (types, enums, extras)
                                                   ▼
                                             list[Event]
                                                   │ summarize.summarize
                                                   ▼
   PlayerRecord: WeaponSummary per aim key (rates, per-match tallies, fire gaps per match and gun,
                 knowledge-filtered hidden/private/wire/jitter samples, hidden contacts)
                 SpeedReport (speed run already judged), RecoilSummary per build (blatant run judged),
                 ExtraObs per declared metric and group
                                                   │
             ┌─────────────────────────────────────┤
             ▼                                     ▼
 baseline.build_cohorts (in-file)        persist.cohort_from_dict (frozen by `fpsdet baseline`,
   + IN_FILE_NOTE on every case            with screen_matches and poison_alarms → integrity)
             └───────────────┬─────────────────────┘
                             ▼
              score.assess_player(record, cohort, profile, history, reports)
                gear rules → information checks → aim rates → continuous numbers
                → recoil → declared extras → account history → decide() → seal
                             │ list[Case]
                             ▼
              score.annotate_batch: leftover pairs → voice-speed teammates → party notes
                (may raise clean or held to watch, reseals; never makes a review)
                             │
                             ▼
   persist.case_to_dict → case JSON + HTML, scan-index, review-index, features.csv,
                          ops.json, dashboard.html (cli._write_outputs) → optional AI brief
```

`decide()` is the whole decision:

| Input | Comes from |
| --- | --- |
| `physics` | Any sustained gear-rule break, any information or private-replay check, the learned recoil floor. |
| `beyond_human` | Distinct kinds of number past every measured human (accuracy, headshot rate, median distance, geometry rate, each primary extra). The same kind on two weapons counts once. |
| `beyond_band` | Count of rank-tail flags (past the rank's p95 or p05, inside the best humans). |
| `supporting` | Distinct supporting families: view snaps, acquire timing, each supporting extra. |
| `identity` | The account broke its own history. |
| `compared` | At least one metric was actually compared with a thick cohort. |

Review: `physics`, or two kinds past every human, or one kind plus `identity` or a supporting tell. Watch: any of the inputs otherwise, or two supporting families. Clean: compared and nothing fired. Otherwise `insufficient_data`. The batch passes can then raise clean or held to watch.

## 4. Every check, by evidence family

The `family` column in `models.CHECKS` today is `gear`, `baseline`, `information` or `batch`. The target families are on the right. "Grade" is the role the check plays in `decide()`.

| Check id | Today | Target family | Grade | Reads | Abstains when | Code |
| --- | --- | --- | --- | --- | --- | --- |
| `speed` | gear | physics | decisive | `speed_mps`, `on_ground`, `displacement_cause`, cap from the sample or the weight curve | no cap, airborne or unknown ground, any cause but `none`, run under 25 | `summarize.analyze_speed` |
| `fire_rate` | gear | weapon_rules | decisive | shot `t_ms` per match and gun, `WeaponRule` | no rule; a match with under 5 gaps or under the violation rate | `score._fire_break` |
| `metronome` | gear | weapon_rules | decisive | same | `server_paced`; a match whose cadence averages at the server's pace | `signals.metronome_break` |
| `recoil_floor` | gear | weapon_rules | decisive | `recoil_pitch_deg`, `spray_index` ≥ 3, floor from the shot or profile | no floor; run under 10 | `summarize._recoil_summaries` |
| `mirror` | gear | weapon_rules | decisive | `applied_recoil_pitch_deg`, `compensation_pitch_deg`, `spray_index` | under 16 pairs; learnable pattern with too few sprays (note on case) | `signals.mirror_check` |
| `recoil_learned` | gear | human_baseline | decisive | recoil median vs the build's cohort | thin cohort (build listed untrained) | `score.assess_player` |
| `accuracy` | baseline | human_baseline | beyond_human | hits and shots, per-match tallies | under `min_shots`; thin cohort | `score._rate_flags` |
| `headshot_rate` | baseline | human_baseline | beyond_human | `hitbox` on hits | under 25 hits with a hitbox (the 90% coverage rule applies to the cohort only; see section 9) | `score._rate_flags` |
| `median_distance` | baseline | human_baseline | beyond_human | `distance_m` | under `min_shots` values; thin cohort | `score._continuous_flags` |
| `geometry_rate` | baseline | human_baseline | beyond_human | `through_geometry` | under `min_shots` known; thin cohort | `score._rate_flags` |
| `extra` | baseline | human_baseline | beyond_human | a declared primary metric | under `min_samples`; no baseline for the group | `score.assess_player` |
| `rank_tail` | baseline | human_baseline | tail | any of the above, recoil and primary extras | as above | `score.assess_player` |
| `supporting` | baseline | human_baseline | corroborating | `view_delta_deg`, `acquire_ms`, supporting extras | as above | `score.assess_player` |
| `account_jump` | baseline | account_history | corroborating | `HistoryWindow` rows | either window under `min_shots`; rank changed | `score._identity` |
| `hidden` | information | information | decisive | `hidden_track_ms`, `information_state` (audio dropped), `since_perceived_ms` (grace) | under 8 samples or 1,200 ms | `signals.hidden_break` |
| `quiet_aim` | information | information | decisive | `aim_jitter_deg` with `information_state` | under 12 per side; knowable median under 0.05° | `signals.smoothness_break` |
| `wire` | information | information | decisive | `wire_error_deg`, `picture_error_deg`, `interp_delay_ms` | delay ≤ 0, negative error, no separation; under 8 shots or 1,200 ms | `signals.wire_break` |
| `private_replay` | challenge (P4: kind `occluded_motion_replay`) | challenge | decisive | `challenge_id` and `challenge_track_ms`, or the legacy `private_track_ms` | under 8 samples or 1,200 ms, on one challenge (legacy: on one aim key) | `challenge.evaluate_challenges`; legacy `signals.private_break` |
| `leftover` | batch | relationship | batch watch | recoil command residuals, per weapon key | under 32 samples or 12 points; r < 0.85 or Fisher z < 5 | `score.annotate_vendors` |
| `voice` | batch | relationship | batch watch | unknowable contacts with `enemy_id`, same `party_id`, same `match_id` | no partner already in review for a hidden mover; under 4 fast lags | `score.annotate_inheritance` |
| (party note) | — | relationship | context | `party_id` | — | `score.annotate_parties` |
| (reports) | — | report | informational | `reports.json` count | — | `priority.py`, a line on clean or held cases |

There are no `external` checks yet.

Baseline integrity is not evidence about a player: `baseline.screen_matches` (a lobby far past the window's median match) and `signals.poison_alarms` (a ceiling that jumped since the last cohort) stamp `cohort.integrity`. It is advisory: `score` prints it and decisions do not change.

`recoil_learned` is the one cohort-derived check that reviews on its own. Its bar is the upper bound of the player's median under the lowest human in the build's top thick band, and under a quarter of that band's median.

What the cases show today, measured on the planted demo, the synthetic week (weekly and seven nightly batches) and the two real-data runs:

| Run | Flagged (review or watch) | With a check id | With a reason line |
| --- | --- | --- | --- |
| Planted demo | 15 | 15 | 14 |
| Synthetic week | 54 | 54 | 43 |
| TF2 | 97 | 97 | 19 |
| CS2 | 18 | 18 | 3 |

Every flagged case names what fired. A rank-tail watch carries its explanation only in the free-text context list, which is why the observation protocol in section 10 is the first task.

## 5. Trust boundaries (implemented, with the gaps)

| # | Boundary | What is trusted | What protects it | Gap |
| --- | --- | --- | --- | --- |
| 1 | Client → game server | Nothing the client claims. The server measures hits, speed, time, the kick it applied, and the command it received. | The event contract asks for server values only (docs/integration.md). | No per-field authority. A game with client-side hit registration could send client-claimed `hit` and fpsdet could not tell. |
| 2 | Game server → events | Server judgements: `information_state`, `hidden_track_ms`, `private_track_ms`, `since_perceived_ms`, `displacement_cause`, the caps and floors. A wrong judgement manufactures a case, and the docs say so field by field. | Missing means off. Audio and the grace window are exclusions. The innocence list is an allow-list. | No emitter build or version on events, so a bad emitter cannot be traced to the cases it touched. |
| 3 | Event text → parse | Types and three enums. | `ParseError` per line. Cases are written with `safe_name`. | NaN and Infinity are accepted (`json.loads` allows them). Schema minimums and the `hitbox` enum are not enforced. Strings are unbounded. Every file is read whole. |
| 4 | Event text → lake paths | — | A game id with a path separator, or a `utc` that is not a date, is skipped (fixed with this audit). | Before the fix, `game_id` `x/../../elsewhere` wrote `events.ndjson` outside the lake. |
| 5 | Operator files → scorer | Profile, cohort, history, reports, labels. | Profile parse checks types. Labels never reach the scorer. | The cohort shape is not validated. The profile parser turns an explicit `0` into the default for most fields, so the file is not the configuration (provenance has to hash the resolved profile). |
| 6 | One case → another | Batch passes write one player's evidence onto another's case. | A batch tell is a watch, never a review. The voice check needs a partner already in review. Leftover pairs need r ≥ 0.85 and Fisher z ≥ 5. | The voice check finds that partner by matching the reason text `"hidden mover"`, not the `hidden` check id. |
| 7 | Cases → people | Rendered text. | `casefile` escapes everything. The dashboards build the DOM with `textContent`, and the embedded JSON escapes `<`. | Dashboard links (`links`, `home`) come from the operator's payload unchecked. Only fpsdet's own scripts write them today. |
| 8 | Cases → AI | The aggregated case. | Every player id the case names is aliased; party ids, match ids and the seal are dropped; the brief is stored apart and is not in the seal. | Event-derived strings (weapon ids, build keys, metric names) reach the model inside free text. The prompt does not mark the case as untrusted data, and nothing bounds its size. |
| 9 | Public data → examples | Public APIs and the CS2CD files. | TF2 ids are pseudonymised with an HMAC key kept beside the data. CS2 uses the dataset's own placeholders. Only scored summaries are committed. | — |

## 6. Provenance and the seal today (implemented)

- **Case seal, v1.** `signals.evidence_seal`: SHA-256 of canonical JSON `{v: 1, player_id, game_id, decision, reasons}`. A batch watch reseals. An observation on a case that is already a review does not. Reports, notes, party notes and the AI brief are not in it. It is stable across Python 3.11 and 3.12 (the design effect uses `math.fsum`).
- **What it cannot answer.** Which scorer, which profile, which cohort, which events, which game build. Two runs with different profiles that write the same reason text get the same seal. The reason text is prose, so rewording a sentence changes the seal although no evidence changed.
- **Shape versions.** Case JSON, cohort and history carry `"version": 1`. `ops.json` has none.
- **In-file cohorts** are marked by a context line on every case, not by a field.
- **Since P2.1 to P2.3:** `evidence.provenance` names the detector source, the parsed profile, the cohort, the subject player's events and the account history the scorer could read for them by SHA-256. `evidence.packet` binds those, the observations, the eligibility and the decision into one digest, which `verify_packet` checks from the case alone ([provenance.md](provenance.md)). A cohort file carries its digests and is refused if they do not match. Still not bound: other players' events behind a relationship observation. Not provided: authenticity, which needs a signature. The seal above is unchanged.

## 7. What keeps honest players out of review (implemented)

| Protection | Where | Planted twin or test |
| --- | --- | --- |
| Innocent displacement causes, unknown causes, airborne and missing ground flag excluded | `summarize.analyze_speed` | `blasted`, `glitch` |
| The cap or floor the server sent wins over the profile's table | `summarize._resolve_cap`, `parse.recoil_floor_for` | `adrenaline`, `modded-recoil` |
| A short over-cap run is a glitch | `analyze_speed` | `glitch` |
| A memorised spray pattern is removed before the mirror test | `signals.mirror_check` | `late-compensate`, `InnocentTwinTest` |
| Fire rules judged per match; under 5 gaps does not count; cadence at the server's pace is not a metronome | `score._fire_break`, `signals.metronome_break` | `CadenceTest` |
| Sound is knowable; the grace window after line of sight breaks; per-shot windowing of running totals | `summarize._weapon_summaries` | `listened`, `angle-holder`, `callout-friend` |
| Leave-one-out cohorts; a thin cohort does not flag and the build is untrained | `baseline.CohortTable`, `score` | `new-gun` |
| "Past every human" is past the best human in any thick band, on a confidence bound | `score._rate_flags`, `_continuous_flags` | `elite-human`, `rank-outlier` (watch) |
| Design effect widens the bound when one match carries the rate | `statsutil.design_effect` | `BoundTest` |
| Medians tested on an order-statistic bound | `statsutil.median_bound` | `BoundTest` |
| The same kind of number on several weapons counts once; review needs two kinds, or one plus a corroborating tell | `score.assess_player`, `decide` | `PopulationTest`: no honest player in review across the synthetic week |
| Batch tells are watches only; leftover pairs need r ≥ 0.85 and Fisher z ≥ 5, in-spray lag, centred signatures | `score.annotate_vendors` | `HonestLeftoverTest`, `clone-buyer` |
| Reports are priority only | `priority.py` | `reported-streamer` |
| Lobbies of cheaters screened out of the baseline | `baseline.screen_matches` | `MatchScreenTest`; CS2 run |

## 8. Real-data evaluation (implemented)

| | CS2 (`examples/cs2`) | TF2 (`examples/tf2`) |
| --- | --- | --- |
| Source | CS2CD, server-recorded demos, hand-labelled cheaters, CC BY 4.0 | logs.tf match logs, RGL's public ban list |
| Per player | One match | Up to 20 matches before the ban |
| Scored | 1,529 players, 4.07 M events | 2,764 players, 4.14 M events |
| Result | 0 reviews; 18 watches, 14 of them labelled cheaters | 3 reviews, all banned cheaters; 0 of 1,746 never-banned players in review; picks 7.7× better than random |
| Committed | `examples/cs2/desk.json` | `examples/tf2/desk.json` |

The scorer never sees a label. The adapters join labels to decisions after scoring (`report`, `desk`).

**Reproduction, checked for this audit.** Re-scoring the local CS2 and TF2 inputs at `26acb77` reproduces both committed desks player for player: 0 mismatches out of 1,529 and 2,764. Loading takes about 42 s per run; scoring takes 4 s (CS2) and 7 s (TF2).

**Regression procedure.** The inputs are too large to commit, so the check runs where the data is:

```bash
PYTHONPATH=src python tools/regress.py snapshot scored.ndjson --profile examples/tf2/tf2.json --cohort cohort.json --out before.jsonl
# change the scorer
PYTHONPATH=src python tools/regress.py snapshot scored.ndjson --profile examples/tf2/tf2.json --cohort cohort.json --out after.jsonl
python tools/regress.py diff before.jsonl after.jsonl --labels labels.json
```

`diff` allows new fields and fails on any change to an existing one. `tests/test_golden.py` pins the committed label-by-decision tables of both desks, so a regenerated desk that moves has to say why.

## 9. Debt that blocks the target

In order of what blocks first.

1. **Findings are sentences.** `assess_player` appends a reason string and, separately, a check id. Nothing links a sentence to its check, its numbers, its thresholds or its window. *Resolved in P1: each finding is also an observation that carries its check, its numbers and its sentence.*
2. **Decision inputs are local variables.** `physics`, the family sets and the counters live inside `assess_player` and are thrown away, so nobody can recompute a decision from the evidence. *Resolved in P1: roles and `eligibility.compared` reproduce every decision; `score.decide` still makes it.*
3. **`Case.observations` is taken.** It holds free-text context lines that consumers already read. The new protocol cannot use that JSON key. *Resolved in P1: the new key is `evidence`.*
4. **Batch passes rewrite finished cases,** and the voice check selects partners by reason text. *Phase 1 and 7.*
5. **Knowledge is scattered.** Audio exclusion, the grace window and the unknowable selection sit inside `summarize._weapon_summaries`; thresholds in `signals.py`; the emitter computes the rest. `information_state` has three values and no notion of which sources the server actually checked. *Blocks Phase 3.*
6. **The private replay is one number per shot.** No challenge id, no commitment, no schedule, no record of when it was active. *Resolved in P4 for planned challenges: a keyed plan per player and match, `challenge_id` on the events, a window, a commitment; the legacy field still works as it did.*
7. **No provenance beyond the seal** (section 6). The profile parser turns explicit zeros into defaults, so the profile must be hashed after parsing. *Mostly resolved in P2.1 and P2.2: the detector source, the parsed profile, the cohort and each player's events are bound. One identity over the whole packet is next.*
8. **`CohortTable._values` is read from four modules,** and every leave-one-out lookup is a linear scan. Fine at today's sizes (TF2 scores in 7 s); a calibration pass that asks many more questions will need an index.
9. **Input hardening** (section 5, row 3). The lake paths (row 4) are fixed.
10. **The leftover comparison is all pairs.** Measured at about 4 µs per pair: 100 players 0.02 s, 1,000 players 2 s, 3,000 players 18 s, per weapon key. Extrapolated, 10,000 players take about 200 s and 100,000 about 5.5 hours. Relationship work has to partition (candidates by weapon key and region, or by a coarse signature bucket, or only against accounts already in review) before it grows.
11. **Presentation reads prose.** `ops._why` picks the queue line by matching phrases; the planted demo checks reason text. Structured observations let both read ids.
12. **Size.** One 2,265-line test file; `board.py` is Python, HTML, CSS and JS in 2,024 lines.
13. **AI input** is not framed as data and not bounded (section 5, row 8). *Phase 12.*
14. **`MetricView` has no sample sizes** (shots, matches, cohort n). Calibration needs them. *Resolved in P1 for every finding: trials, samples, matches and cohort players are in its evidence. `MetricView` itself is unchanged.*
15. **Spec and code disagree on headshot coverage.** docs/scoring.md says headshot rate is skipped unless hits with a hitbox cover 90% of hits. `build_cohorts` applies that; `assess_player` does not, so a player whose emitter sends hitboxes on some hits is still scored on those. Measured: in TF2, 17 of the 783 player-weapons scored on headshot rate have coverage under 90% (older logs carry no headshot data); none of the 17 has a headshot flag, so no decision depends on it today. CS2: 0 of 488. An emitter that sends `hitbox` only on headshots would frame players. Fix in its own commit, behind the locks.

## 10. Proposed interfaces

All proposed. Plain dataclasses and JSON, standard library only. One new top-level key on the case JSON carries all of it, so every current consumer keeps working:

```json
"evidence": {"version": 1, "observations": [...], "provenance": {...}, "graph": {...}}
```

`reasons`, `observations` (the context lines), `checks`, `metrics` and `seal` stay as they are.

### 10.1 Observation

*Implemented in P1, narrower than proposed below; [observations.md](observations.md) is the reference.* What was built differs in five ways:

- Roles are `review`, `past_human`, `account_change`, `supporting` and `watch`, one per `decide()` input. They replace the proposed grades; `context` and `informational` are not roles, because nothing emits them.
- Only the families something emits exist: no `external` or `report` yet. The private replay was `information` until P4 made it `challenge`.
- No `statistic`, `alternatives`, `started_ms` or `ended_ms` fields. Bounds sit inside `evidence`, and alternatives are Phase 5.
- `dependencies` is `depends_on`, and `text` lives in `context.line`.
- The block also carries `eligibility.compared`, so clean and `insufficient_data` are told apart without an invented observation.

```python
FAMILIES = ("physics", "weapon_rules", "human_baseline", "information", "challenge",
            "relationship", "account_history", "external", "report")

# The role an observation plays in decide(). Replaces a free "severity" and an invented "confidence".
GRADES = ("decisive", "beyond_human", "corroborating", "tail", "batch_watch", "context", "informational")

@dataclass(frozen=True)
class Observation:
    observation_id: str        # "obs-" + sha256(canonical subject, source, family, kind, key, scope, evidence)[:20]
    subject_id: str            # the player pseudonym
    source: str                # "fpsdet", "report", or "external:<provider>"
    family: str                # FAMILIES
    kind: str                  # a CHECKS id ("accuracy", "hidden", ...), or "report"
    grade: str                 # GRADES
    key: str = ""              # weapon key, build key, or declared-metric group
    match_ids: tuple[str, ...] = ()
    started_ms: int | None = None   # only inside one match; t_ms restarts each match
    ended_ms: int | None = None
    evidence: dict = field(default_factory=dict)    # numbers: value, bound, line, n, matches, cohort_n, the thresholds used
    statistic: dict | None = None                   # {"method": "wilson_lower", "level": 0.95, "deff": 1.3}
    alternatives: tuple[dict, ...] = ()             # Phase 5: {"name", "status": supported|ruled_out|unknown|not_applicable, "basis"}
    dependencies: tuple[str, ...] = ()              # observation ids, e.g. the partner's hidden-mover finding
    text: str = ""             # the exact reason or context line already on the case
```

Rules:

- Every review and watch has at least one observation whose grade is not `context` or `informational`.
- `decide()` can be recomputed from the observations alone: `decisive` → physics; distinct `kind` among `beyond_human` → beyond_human; count of `tail` → beyond_band; distinct supporting families among `corroborating` → supporting; `account_jump` → identity; then `batch_watch` raises clean or held to watch. A test asserts this equals the case's decision on every planted, weekly, nightly and real case.
- A report is `family="report"`, `grade="informational"`. Nothing reads it into a decision.
- Native observations carry no invented confidence number. Where a bound exists, `statistic` says which one and at what level.

### 10.2 Provenance

*Partly implemented in P2.1 and P2.2: `profile`, the detector source (`scorer_source` below, written as `detector`), `cohort` (with its integrity stamp kept apart, and a `mode` of `external` or `in_file`) and `inputs`. `window` became the `events` and `matches` counts. P2.3 added `history` (the rows the scorer could read for the subject) and `evidence_id`, implemented as `evidence.packet`. Still proposed: game build, challenges, external sources, and a signature over the packet. [provenance.md](provenance.md) is the reference.*

```python
@dataclass(frozen=True)
class Provenance:
    schema: str = "fpsdet.provenance/1"
    scorer_version: str          # fpsdet.__version__
    scorer_source: str           # sha256 over the detection modules only (models … pipeline, evidence, knowledge)
    profile: str                 # sha256 of the parsed GameProfile, not the file bytes
    cohort: str | None           # sha256 of the cohort's values; None for an in-file cohort
    cohort_integrity: str        # ok | unchecked | poison_risk
    history: str | None
    inputs: str                  # sha256 of this player's canonical events in the window
    window: dict                 # {"matches": n, "events": n}
    game_build: str | None       # from a new optional event field, when sent
    challenges: tuple[str, ...] = ()   # challenge commitments, never realisations
    external: tuple[dict, ...] = ()    # provider, adapter, sha256 of the received record
```

`evidence_id` = sha256 of the canonical `{provenance, sorted observation ids}`. Presentation modules are not hashed, so a dashboard change cannot move evidence identity. The v1 seal stays, unchanged, for compatibility.

### 10.3 KnowledgeState

```python
CHANNELS = ("visible", "audible", "team_shared", "radar", "objective", "interp_visible")

@dataclass(frozen=True)
class KnowledgeState:
    observer: str
    entity: str | None
    match_id: str
    t_ms: int
    held: frozenset[str]           # legal channels that carried the entity at t
    absent: frozenset[str]         # channels the server checked and found empty
    since_perceived_ms: float | None
    private_challenge: bool = False

    def verdict(self, profile) -> str:
        # knowable | recently_knowable | unknowable | unknown
        # unknowable only when every channel the profile declares for this game is in `absent`.
        # A channel in neither set is unknown, and an unknown channel makes the verdict unknown.
```

A legitimate channel always wins over an accusation. The default declared channels are `visible` and `audible`, and today's `information_state` maps onto them exactly (`visible` → held visible; `audio` → held audible; `unknowable` → both absent), so current decisions do not move. A game with radar declares `radar`; until its server reports radar, the verdict is `unknown` and the information checks abstain.

### 10.4 Challenge

```python
@dataclass(frozen=True)
class Challenge:
    challenge_id: str          # public: HMAC(secret, "id" | match | subject | slot)[:16]
    commitment: str            # sha256(canonical realisation || salt); the realisation stays on the server
    type: str                  # "occluded_motion_replay" first
    subject_id: str
    match_id: str
    start_ms: int
    end_ms: int
    legal_visibility: str      # must be "absent, server-checked", or the challenge does not run
    legal_audio: str
    activation: dict           # the rule that started it, with no secret material
    result: dict               # private_track_ms total, samples, crossings
```

The realisation (route source, heading, delay, speed, place, timing) is derived from `HMAC-SHA256(server_secret, match | subject | slot)`. The detector can be public; the next challenge cannot be predicted without the secret. Neither the secret nor the realisation is ever written to a case, a dashboard, the desk or a fixture.

*Implemented in P4, differently in the details; [challenges.md](challenges.md) is the reference.* The spec (`ChallengeSpec`), the public plan (`ChallengePlan`), the secret realization (`challenge_plan.Realization`) and each player's result (`ChallengeResult`) are separate types, and only `challenge_plan` sees the secret. The id is 96 bits (`ch-` and 24 hex digits); the HMAC message is framed and carries the game, a public nonce, the type and its version as well; the commitment is SHA-256 over the public plan fields and the 256-bit realization material, which needs no extra salt. Legal visibility and audio are not per-challenge fields: the type declares the channels it defeats, and the knowledge engine decides. Activation is the scheduled window. The result lives in `evidence.challenges`, not in the plan.

### 10.5 ExternalObservation

```python
@dataclass(frozen=True)
class ExternalObservation:
    provider: str              # operator's name for the source
    source_class: str          # client_integrity | platform_attestation | account_risk | server_custom
                               # | tournament_admin | manual_review
    kind: str                  # the provider's own label, carried verbatim
    subject_id: str            # the operator's pseudonym, mapped before fpsdet sees it
    match_id: str | None
    observed_at: str | None
    confidence: float | None   # the provider's number, never rescaled
    confidence_meaning: str | None
    metadata: dict             # bounded size, scalar values only; secrets may be omitted
    received_sha256: str       # hash of the record as received
```

It becomes an `Observation` with `family="external"` and `grade="context"`. It never feeds `decide()` and is never merged into a native family. Adapters parse JSON only; nothing is imported or executed.

### 10.6 EvidenceGraph

```json
{"version": 1,
 "nodes": [{"id": "player:…", "type": "player"}, {"id": "obs-…", "type": "observation", "family": "information"},
           {"id": "challenge:…", "type": "challenge"}, {"id": "cohort:…", "type": "baseline"}],
 "edges": [{"from": "obs-…", "to": "player:…", "type": "supports"},
           {"from": "obs-…", "to": "cohort:…", "type": "derived_from"}]}
```

Built from the observations and the provenance, not stored separately. Node types: player, match, observation, target entity, challenge, external signal, report, account history, teammate, baseline. Edge types: supports, depends_on, contradicts, occurred_during, targeted, shared_with, derived_from, correlated_with.

## 11. Target

```text
game server (world state, hits, movement, visibility, audio, interpolation, knowledge, challenge state)
        │ events, challenge plans, external records
        ▼
fpsdet evidence engine
  ├─ deterministic rules        physics, weapon_rules
  ├─ human baselines            human_baseline, account_history
  ├─ knowledge engine           information                     (knowledge.py)
  ├─ challenge engine           challenge                       (challenge.py)
  ├─ relationship passes        relationship, partitioned
  └─ external adapters          external, attributed, never decisive
        │ Observation[] + Provenance        (evidence.py, provenance.py)
        ▼
case: decision from decide(), unchanged rules; evidence block; graph   (graph.py)
        │
        ├─► human reviewer (desk, dashboard, case HTML)
        ├─► downstream AI (structured, bounded, aliased; cannot write back)
        └─► VAC / EAC / BattlEye / a studio's tools (case JSON, ops.json)
```

Each box is a module with one job and a test file of its own. The decision stays one small function over typed inputs.

## 12. Regression gates and how each is enforced

| Gate | Enforced by |
| --- | --- |
| No planted decision moves | `fpsdet demo` exits 2; `GoldenPlantedTest` |
| No field a consumer reads moves on planted or synthetic cases | `tests/test_golden.py` (every case field; weekly and nightly) |
| The committed desk is what the scorer writes | CI `git diff --exit-code demo/board.html` |
| Real TF2 and CS2 results do not move | `RealDataContractTest` on the committed desks; `tools/regress.py diff` on a local rerun |
| `automated_action` stays `none` | `GoldenPlantedTest`, `GoldenWeekTest` |
| Reports never change evidence | `RecordedFindingsTest.test_reports_change_no_evidence` (P1) |
| Evidence identity ignores UI changes | `DetectorFingerprintTest` (P2.1): editing any presentation module, or a static page, leaves the detector digest unchanged |
| An edited case is detectable from the case alone | `PacketTest` (P2.3): `verify_packet` catches edits to evidence, ids, roles, decision, eligibility and every provenance digest; wording, briefs, reports and queue state are ignored |
| No secret challenge material in public artifacts | `SecretLeakTest` (P4) over every file a challenged `fpsdet score --out` writes and prints, the evidence and packet, the AI brief body, `challenge verify` output, error text and the desk; `PlanOutputLeakTest` over plan files and command output |
| AI cannot change a decision | Phase 12 test: a hostile brief leaves decision, seal and evidence unchanged |
| A new check has an honest control | `CONTRIBUTING.md` rule; review |

A golden re-record (`FPSDET_REGOLD=1`) is a decision, not a fix: the commit says why each moved case moved.

## 13. Order of work

Each step is one commit, run against the full suite, the demo, the board diff and both real-data diffs before it lands.

### P0

| Step | Status | Files |
| --- | --- | --- |
| 0.1 This map | done | `docs/architecture-v1.md` |
| 0.2 Whole-case behaviour lock | done | `tests/test_golden.py`, `tests/golden/*.json`, CI, `CONTRIBUTING.md` |
| 0.3 Real-data regression tool | done | `tools/regress.py` |
| 0.4 Lake path hardening | done, separate commit | `lake.py`, `cli.py`, `LakeTest` |
| 1.1 Observation model | done | `evidence.py`: `Observation`, families, roles, canonical JSON, ids, `implied_decision`; `tests/test_observations.py` |
| 1.2 Emit observations | done | `score.py` records an observation beside each sentence it already writes; `signals.py` detectors return their numbers through `*_finding` functions behind the old ones; batch passes record relationship observations, the voice watch with `depends_on`. `models.Case.evidence`, `persist.case_to_dict` adds `"evidence"`; `ops.json` and the AI brief leave it out. Deferred by decision: the voice check still selects partners by text; reports are not observations. |
| 1.3 Schema and doc | doc done | `docs/observations.md` done; `schema/observation.schema.json` waits for the versioned schemas (Phase 15) |
| 2.1 Provenance: detector and profile | done | `provenance.py`; `pipeline.py` and the planted demo stamp each run; `persist.case_to_dict` writes `evidence.provenance`; `tests/test_provenance.py`; `docs/provenance.md` |
| 2.2 Provenance: cohort and inputs | done | cohort and integrity digests, written into cohort files and checked on load; `mode` external or in-file; a per-player digest of the events scored, in the scorer's order; `docs/provenance.md` |
| 2.3 History and evidence packet identity | done | `score.history_for` shared by the account check and its provenance; `fpsdet.history/1`; `fpsdet.packet/1` and `verify_packet`; `tools/regress.py verify` checks every packet; `tests/test_packet.py` |
| Deterministic event normalization | done | `fpsdet.timeline`; simultaneous-event rules per check; key-ordered output; `math.fsum`; deterministic partner selection; `fpsdet.player-events/2`; [event-normalization.md](event-normalization.md). The plan was: 1. Characterise every place scoring depends on a player's event arrival order (weapon, build and declared-metric first appearance; equal-time ties; float sums). 2. Define a canonical per-player order. 3. Fix a deterministic tie-break for equal timestamps. 4. Prove which decision and output changes are intended, case by case. 5. Rerun CS2 and TF2. 6. Only then, if what the scorer reads changes, introduce a new input recipe beside `fpsdet.player-events/1`. |
| 3.1 Knowledge engine | done | `knowledge.py` (known, unknown, unknowable; channels known, absent, unchecked, not applicable); profile `knowledge_channels`; event `vision_state`, `audio_state`; hidden mover, private replay, quiet aim and teammate contacts read it; knowledge context on information findings; contradiction notes; `tests/test_knowledge.py`; [knowledge-engine.md](knowledge-engine.md) |
| 4 Challenge engine | done | `challenge.py` (types, public plans and their digest, budget, linkage, per-challenge results, evidence) and `challenge_plan.py` (the only module that reads the secret: keyed derivation, commitment, schedule without repeats); `fpsdet challenge keygen`, `plan`, `verify`, `types`; `fpsdet score --challenges`; event `challenge_id`, `challenge_track_ms`; the `challenge` family; the legacy private replay kept as `legacy_private_replay`; leak, false-positive and attacker tests in `tests/test_challenge.py`; [challenges.md](challenges.md) |

### P1

| Step | Files |
| --- | --- |
| 5 Alternatives | `evidence.py` `Alternative`; the explanations already computed (audio, grace, emitter missing, thin cohort) become explicit `ruled_out` or `unknown` entries; nothing becomes stronger because an alternative was not checked |
| 6 External evidence | new `external.py` (validation, size limits, attribution); `fpsdet score --external records.ndjson`; `docs/external-evidence.md` |
| 7 Evidence graph | new `graph.py`; `docs/evidence-graph.md` |

P2 (calibration, enrichment metrics, red-team harness, AI contract) and P3 (watermarking research, test split, capability matrix, API cleanup) follow, in the order the brief sets.

## 14. Deliberately not done in P0

- No scorer code changed. Phase 1 starts behind the locks above.
- NaN and Infinity in events, schema minimums, the `hitbox` enum, string bounds and streaming reads: logged here, fixed with the knowledge-engine parse changes so the event contract changes once.
- The profile parser's zero-to-default coercion: logged; provenance will hash the parsed profile so the identity is right either way.
- The headshot-coverage mismatch (section 9, item 15): a behaviour change, so it gets its own commit with the golden and real-data diffs.
- Splitting `tests/test_fpsdet.py`: Phase 13, after the evidence work is stable.
