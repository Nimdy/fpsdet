# External evidence

## Why it is kept separate

fpsdet is not a replacement for client anti-cheat, platform attestation, account systems or human review. It is one more independent source of evidence, built on what the game server can prove. Studios already have other sources. fpsdet can carry what they say, beside its own evidence, without pretending to understand how they reached it.

So an external record stays what it is: one provider's claim about one player. fpsdet keeps it exactly as the provider made it, records where it came from, and never mixes it into a native family. A client-integrity signal is not server physics. A league ruling is not an fpsdet review. A ban on an account's record is not evidence about these matches.

## What exists

| | Status |
| --- | --- |
| The native format, `fpsdet.external/1` | Implemented. The integration contract for studios; `schema/external_record.schema.json` |
| Data-only adapters, `fpsdet.external-adapter/1` | Implemented, with three fictional examples in `examples/external/` |
| Adapters for VAC, EAC, BattlEye, Vanguard or any real provider | **Not built.** fpsdet has no provider-supported record schema for any of them. An adapter can be written when a provider supports one |
| Signature verification | Not built. Every record is `unverified` |

## The record

| Field | Meaning |
| --- | --- |
| `provider` | Who made the claim, as a short id (`example-integrity`) |
| `provider_group` | Who stands behind the provider. Two products of one vendor share a group. Defaults to the provider |
| `source_class` | What kind of system it is (below) |
| `telemetry_domain` | What it measured (below). Defaults to `unspecified` |
| `kind` | The provider's own label for the record, verbatim, up to 128 characters |
| `direction` | What the provider asserts: `adverse` (something was wrong), `favorable` (something was checked and fine) or `context` (neither) |
| `subject_id` | The player, as the same pseudonym the game server uses in its events |
| `match_id`, `started_ms`, `ended_ms` | Optional scope inside a match, in match time. A time needs a match |
| `observed_at` | Optional: the provider's own date or UTC time for the record, verbatim |
| `confidence`, `confidence_scale`, `confidence_meaning` | Optional: the provider's confidence, exactly as given (below) |
| `provider_record_id` | Optional: the provider's own id for the record |
| `metadata` | Optional: at most 16 flat fields of strings, numbers, booleans or nulls, 4 KB in all |

### Source classes

| Class | What it is |
| --- | --- |
| `client_integrity` | Software on the player's machine: memory, process, driver or input integrity |
| `platform_attestation` | The device or platform vouching, or failing to vouch, for itself |
| `account_status` | Standing on an account: past bans, restrictions, history. About the account, not these matches |
| `tournament_finding` | A league's or tournament administration's ruling |
| `human_review` | A person's finding outside fpsdet: a studio reviewer, a moderator |
| `custom_detector` | A studio's own automated detector |

The classes are not interchangeable. A person's ruling, a kernel driver's alert and a ban from three years ago are different kinds of claim, and the fusion rules treat them differently.

### Telemetry domains and provider groups

`telemetry_domain` is what a signal was measured from: `endpoint_memory`, `endpoint_process`, `endpoint_input`, `platform_attestation`, `server_behavior`, `account_history`, `human_review`, or `unspecified`. With `provider_group`, it is how fpsdet will later tell independent confirmation from the same thing seen twice. Two providers reading the same memory, or two products of one vendor, are not two independent witnesses. Nothing uses this to escalate yet. It is recorded so that a later, calibrated rule can.

### Confidence

fpsdet keeps the provider's confidence exactly as given, says which scale the provider puts it on, and keeps the provider's own words for what it means. It never converts one into an fpsdet probability, and no fusion rule reads it.

| `confidence_scale` | Value | Example |
| --- | --- | --- |
| `probability` | A number from 0 to 1 that the provider defines as a probability | `0.9`, "the provider's stated probability that the device was modified" |
| `score` | A number on the provider's own scale. Not a probability | `87`, "anomaly score from 0 to 100; higher is more anomalous; not a probability" |
| `label` | A word on the provider's own scale | `"high"`, "the studio's three-step label: low, medium, high" |
| `unspecified` | Either, when the provider did not say | |

`confidence: 0.82` with no scale stays `0.82`, `unspecified`. It is not read as 82%. An integer stays an integer and a float a float, as received. A probability outside 0 to 1, a word given as a score, a number given as a label, `true`, a list or an object are all refused.

## The native format: `fpsdet.external/1`

One JSON object per line. Every line names the format, so nothing is guessed:

```json
{"format": "fpsdet.external/1", "provider": "example-studio-detector", "source_class": "custom_detector",
 "telemetry_domain": "server_behavior", "kind": "macro_timing", "direction": "adverse", "subject_id": "weak-human",
 "match_id": "m1", "started_ms": 3000, "ended_ms": 12000, "confidence": "high", "confidence_scale": "label",
 "confidence_meaning": "the studio's own three-step label: low, medium, high", "provider_record_id": "SD-5",
 "metadata": {"rule": "R-12"}}
```

A key the format does not define is refused, so a misspelt `confidance` is an error, not a silently empty field. `examples/external/native.ndjson` has more.

## Adapters: `fpsdet.external-adapter/1`

A provider's own format is mapped by an adapter: a JSON file of field paths, constants and an allowlist.

```json
{
  "format": "fpsdet.external-adapter/1", "name": "example-integrity", "version": 1,
  "provider": "example-integrity", "source_class": "client_integrity", "telemetry_domain": "endpoint_memory",
  "fields": {"subject_id": "player.pseudonym", "match_id": "session.match", "kind": "detection.type",
             "confidence": "detection.score", "provider_record_id": "id"},
  "constants": {"confidence_scale": "score", "confidence_meaning": "anomaly score from 0 to 100; not a probability"},
  "directions": {"memory_integrity_anomaly": "adverse", "integrity_ok": "favorable"},
  "metadata": {"module": "detection.module", "client_build": "client.build"}
}
```

An adapter is untrusted configuration, and it can do nothing but this:

- **Paths look values up by key.** `detection.type` is two key lookups. There are no indexes, wildcards, expressions, templates or functions, and a path is at most six names of letters, digits, `_` and `-`.
- **Values are not transformed.** A looked-up value goes through exactly the checks a native record's field does. A number where an id should be is refused, not converted.
- **The provider, its group, its source class and its telemetry domain are the adapter's.** A record cannot claim to come from someone else.
- **Constants** can set only the kind, the direction, the confidence scale and its meaning. The scale and meaning apply only to records that carry a confidence.
- **The direction is declared exactly one way:** a mapped field, a constant, or a table by kind. A kind missing from the table is refused, never guessed.
- **Metadata is an allowlist.** Only the named paths are kept, under the names the adapter gives them.
- **Unknown keys are refused.** An adapter is at most 64 KB.

`examples/external/` has three, all fictional: `example-integrity` (client integrity, `memory_integrity_anomaly`), `example-account-status` (account status) and `example-league-admin` (tournament rulings). They map sample records and nothing else.

## Bounds and bad input

External input is untrusted, so every limit is enforced while reading:

| Limit | Value |
| --- | --- |
| File | 256 MB; a larger file is not read |
| Line | 16 KB; a longer line is read past in pieces and skipped, so it is never held whole |
| JSON | UTF-8; no repeated keys; no `NaN` or `Infinity`; nested at most 8 levels |
| Ids, kinds, labels | 128 characters; providers and groups 64, lowercase |
| Free text | 256 characters; no control characters or bidirectional overrides |
| Metadata | 16 flat fields, 4 KB |
| Numbers | Finite, at most 10¹² in size; match times 0 to 10¹⁰ ms |
| Records | 64 per player and 1,000,000 per run, after duplicates |

A line fpsdet will not read is skipped, and named by file, line and reason. The error never quotes the value, so a hostile string cannot reach a terminal or a log through it. The source's error count goes into the run's provenance. Native scoring is never affected. `--external-strict` makes the first bad line stop the run instead.

## Identity

Each record's id is `ext-` and the first 24 hex digits of SHA-256 over `fpsdet.external/1`, a zero byte, and the canonical JSON of everything it claims: provider and group, source class, telemetry domain, kind, direction, player, match and window, `observed_at`, the confidence with its scale and meaning, the provider's record id and the metadata. It does not cover the file, the line, the order or the adapter, so the same claim read twice, from any file or through any adapter, is one record and counts once. An integer and a float of the same size are different claims, as received.

## Authenticity

A digest is an identity, not a signature. It shows that a record has not changed since it was digested. It does not show who wrote it. Nothing in `fpsdet.external/1` is signed, and fpsdet does not pretend otherwise: every external observation says `authenticity: unverified`. A verified state will exist only when a format carries a signature fpsdet checks.

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
