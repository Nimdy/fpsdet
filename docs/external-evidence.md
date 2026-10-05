# External evidence

## Before external evidence: where it could enter

This audit was written at `ce00ac6`, before anything changed. `tests/test_external.py` (`NativeFusionSurfacesTest`) pins what it describes.

### What a decision is made of

- **`score.decide`** turns local counts into a decision: a review-grade finding, past-human metrics, an account change, supporting tells, a comparison. It runs once per player, in `assess_player`, from fpsdet's own events only.
- **Batch passes** (`annotate_batch`) run after every player is assessed. The shared-leftover and teammate checks add `relationship` observations with role `watch` through `_watch_for_batch`, which moves a clean or `insufficient_data` case to watch, appends the line to `reasons` and reseals. It never makes a review: on a review, the line goes to `observations`, and the decision and seal stay. Party notes come last and read the decisions.
- **Roles** (`evidence.ROLES`) are exactly the inputs of `decide` plus the batch passes: `review`, `past_human`, `account_change`, `supporting`, `watch`. `implied_decision` rebuilds the decision from them, and `tools/regress.py verify` checks it on every case.
- **The `supporting` role escalates.** A supporting tell with one past-human metric is a review. Anything outside fpsdet that used that role could turn a watch into a review. External evidence must not use it.
- **Reports** are a count per player. They change queue order (`priority.scan_order`, `review_order`) and add one context line to a clean case. They never change a decision, the evidence, the seal or the packet.

### Where evidence is identified and bound

- **Observations** carry `source: "fpsdet"`, and the model refuses any other source. Families are fpsdet's own: `physics`, `weapon_rules`, `human_baseline`, `information`, `challenge`, `relationship`, `account_history`.
- **`Case.checks`** lists fpsdet's checks by id. The dashboards (`ops.json`, `dashboard.html`) group and filter on them.
- **Provenance** binds the detector, profile, cohort, inputs and history (`fpsdet.provenance`).
- **`fpsdet.packet/1`** binds the subject, game, decision, eligibility, every observation id, and those five provenance parts with fixed fields. A part or field added later is not bound by `packet/1`. External evidence therefore needs a new packet recipe; changing `packet/1` would change what old packets mean.
- **Challenge evidence** binds its plan through the observation's own evidence.

### Where text goes

- **The AI brief** is sent the case without its `evidence` block, with player ids redacted from `reasons`, `observations` and `untrained`. Any text placed in `reasons` or `observations` reaches the AI.
- **Case pages** escape every field and do not render evidence. **`ops.json`** leaves evidence out. **The dashboard** embeds `ops.json` and builds its rows with text nodes.
- **The command line** prints each case's first reason or context line.

### Where external evidence can go

1. **As its own family and source.** External observations get family `external` and source `external`, so the source boundary is visible on every record. They are never in a native family.
2. **With their own roles.** Two new roles keep external evidence outside every native rule. One can make a watch and is never part of a review. The other changes nothing. Neither is `supporting`.
3. **After every native pass.** Fusion runs after `annotate_batch`, so the native decision, every native observation and every native relationship are computed exactly as before, and the decision fusion started from is recorded.
4. **Never into the AI.** A line that reaches `reasons` or `observations` names only fpsdet's own words, the source class and the record id: never a provider string. The evidence block, where provider strings live, is already excluded.
5. **Not into `checks`.** An external record is not an fpsdet check. The dashboards stay as they were.
6. **Into provenance and a new packet recipe.** A per-player digest of the external records, and `fpsdet.packet/2`, which binds everything `packet/1` binds and the external input and fusion state. `packet/1` keeps its meaning, and packets written with it keep verifying.
7. **Independent of reports.** Fusion reads no report count.
