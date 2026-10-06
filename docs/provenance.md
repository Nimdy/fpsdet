# Provenance

A case's evidence now says what produced it:

- the detector code,
- the game configuration,
- the human baseline the player was compared with,
- the player's own events,
- the account history it was checked against, and
- one packet digest that binds all of that to the observations and the decision.

A reviewer, an auditor or another system can tell whether two cases were scored the same way, and spot when they were not, without trusting whoever handed the case over.

The code is `fpsdet.provenance`. It uses the standard library only.

## What it proves, and what it does not yet

| Bound to the evidence | Status |
| --- | --- |
| The detector implementation: the source of every module that can change a finding | Implemented |
| The game profile as the scorer parsed it | Implemented |
| The human baseline (cohort) the run compared players with, and its advisory integrity stamp | Implemented |
| The subject player's own parsed events, in the canonical timeline order the scorer reads | Implemented |
| The account-history windows the scorer could read for the subject | Implemented |
| One identity over the material evidence and all of the above (the evidence packet) | Implemented |
| Other players' events that a relationship observation used | Not yet (see "What the input digest does not cover") |
| External evidence, challenge seeds | Not yet |
| Who produced the packet (authenticity) | Not yet; it needs a server-held signing key |
| What a reviewer then did | Not yet; that stays in the studio's own review tool |

Provenance says what produced a case, not that the case is correct. The packet digest makes an edited case detectable against a digest someone stored, but not on its own: SHA-256 proves content identity, not origin, and whoever edits a case can recompute it.

## Where it is

```json
"evidence": {
  "version": 1,
  "observations": [ ... ],
  "eligibility": { ... },
  "provenance": {
    "version": 1,
    "profile": {"recipe": "fpsdet.profile/1", "digest": "sha256:<64 hex>"},
    "detector": {
      "recipe": "fpsdet.detector/1",
      "digest": "sha256:<64 hex>",
      "modules": ["fpsdet", "fpsdet.baseline", "fpsdet.evidence", "fpsdet.models", "fpsdet.parse", "fpsdet.persist",
                  "fpsdet.pipeline", "fpsdet.provenance", "fpsdet.score", "fpsdet.signals", "fpsdet.statsutil",
                  "fpsdet.summarize", "fpsdet.timeline"]
    },
    "cohort": {
      "mode": "external",
      "recipe": "fpsdet.cohort/1",
      "digest": "sha256:<64 hex>",
      "stored_digest": "matched",
      "integrity": {"recipe": "fpsdet.cohort-integrity/1", "status": "ok", "digest": "sha256:<64 hex>"}
    },
    "inputs": {"recipe": "fpsdet.player-events/2", "digest": "sha256:<64 hex>", "events": 412, "matches": 8},
    "history": {"mode": "external", "recipe": "fpsdet.history/1", "digest": "sha256:<64 hex>", "windows": 1}
  },
  "packet": {"recipe": "fpsdet.packet/1", "status": "complete", "digest": "sha256:<64 hex>"}
}
```

- `recipe` names the canonicalisation. It changes only if the bytes being hashed would be built differently.
- `digest` is a full SHA-256, written `sha256:` and 64 lowercase hex digits.
- `modules` is the detector boundary, so a reviewer can see what the digest covers. It holds module names only: no paths, no source.
- If a module's source cannot be read (for example, a build that ships only bytecode), `detector.digest` is null and `detector.missing` names those modules. Nothing is guessed.
- `fpsdet.pipeline.run_score` stamps every case of a run. `profile`, `detector` and `cohort` are one object shared by the run. `inputs` and `history` are the subject player's own. The planted demo does the same.
- A case built by calling `assess_player` directly has `"provenance": null`.
- `cohort` or `inputs` is null when the run did not supply it.

Costs, measured:

- **Detector source:** read once per process and cached, about 1 ms.
- **Profile digest:** about 0.1 ms per run.
- **Cohort digest:** about 9 ms per run for TF2's 4,927 baseline rows.
- **Input digests:** the only part that grows with the data, at about 1.3 to 2 µs per event, because every value of every event is hashed. On the TF2 run (4.1 M events) scoring went from 6.3 s to 10.8 s, about a tenth of a full load-and-score run. On the synthetic week, the weekly batch went from 0.64 s to 0.99 s. Measured stage by stage on the 4.07 M CS2 events, the time is in reading every field of every event and encoding it; `hashlib` itself is under 5%. Two column-building variants gave the same digests bit for bit but no material speed-up, so the code is unchanged.
- **History digests and the packet:** see "The history digest" and "The evidence packet".

### Versioning

`version` is the shape of the provenance block. It increments only for an incompatible change: a field removed, renamed, or given a different meaning. A new optional key, such as `cohort` and `inputs` in this release, does not increment it, and readers must ignore keys they do not know. Each `recipe` names how one digest's bytes are built and changes whenever they would be built differently, so a digest is only ever compared with one made by the same recipe. The `version` of the evidence block follows the same rule.

## The profile digest

The profile is fingerprinted as the scorer sees it after `parse.profile_from_dict`, not as the file was written. The parser turns some explicit values into defaults; an explicit `"min_shots": 0`, for example, becomes 40. The digest describes the 40 that actually ran. Two files that parse to the same profile have the same digest, whatever their spacing, key order or redundant values. Fixing the parser is separate work.

Canonical form, `fpsdet.profile/1`:

- Every field of the parsed `GameProfile` dataclass is included, and so is every field of the nested weight classes, weapon rules, recoil floors and declared metrics. A field added to the dataclass later is included automatically.
- **Left out:** `notes`, which the scorer never reads.
- **Sets are sorted:** the innocent displacement causes, and a recoil floor's mods (matched through a sorted build key).
- **Order is kept where the scorer reads in order:** weight classes are matched first to last, and a declared metric's `group_by` builds its group key in that order.
- **Integers and floats stay distinct:** a fire cycle of `100` and one of `100.0` print differently in a reason.
- A non-finite float is written as `{"non_finite": "Infinity"}` (or `"-Infinity"`, `"NaN"`).
- The bytes hashed are the canonical JSON `{"recipe": "fpsdet.profile/1", "profile": {...}}`: sorted keys, no spaces, UTF-8, no NaN.

The digest is the same on Python 3.11 and 3.12. A test pins it for `profiles/example-loadout.json`, and CI runs that test on both versions.

## The detector digest

The detector is every module whose code can change a finding, the evidence written for it, or how an event, a profile, a cohort or a player's history is read:

| Module | Why it is in |
| --- | --- |
| `fpsdet` (`__init__.py`) | Runs on every import of the package, before any module. Its `__version__` is in it, so a release moves the digest |
| `fpsdet.pipeline` | Scores a batch: in-file cohort, batch passes, the provenance stamp |
| `fpsdet.parse` | Turns event lines and profiles into the objects scored |
| `fpsdet.summarize` | Reduces events to per-player facts, with the speed run, the blatant recoil run and the knowledge filters |
| `fpsdet.timeline` | The canonical player timeline every check reads ([event-normalization.md](event-normalization.md)) |
| `fpsdet.baseline` | Cohorts and leave-one-out distributions |
| `fpsdet.statsutil` | Bounds, percentiles, design effect |
| `fpsdet.signals` | The gear, information and leftover detectors |
| `fpsdet.score` | Every per-player check, the decision, the batch passes |
| `fpsdet.models` | The shared types and the check table |
| `fpsdet.evidence` | The observation model, the evidence block, `implied_decision` |
| `fpsdet.persist` | Reads cohorts and history; writes the case and its evidence |
| `fpsdet.provenance` | Writes this block |

Not in it, and why:

| Module | Reason |
| --- | --- |
| `fpsdet.ops`, `fpsdet.opsview` | The dashboard |
| `fpsdet.board`, `fpsdet.pages` | The review desk and the public site |
| `fpsdet.casefile` | The case HTML page |
| `fpsdet.ai_triage` | AI prose about a finished case |
| `fpsdet.cli`, `fpsdet.__main__` | The command line |
| `fpsdet.priority` | Queue order, after the decision |
| `fpsdet.lake` | Raw event storage. Which events were scored is bound by the input digest, not by the code that stored them |
| `fpsdet.synthetic`, `fpsdet.week` | The planted demo and the synthetic week |

Presentation code is outside the boundary so that a new chart, a reworded page or a dashboard fix does not make old evidence look as if a different detector produced it. The static pages under `site/` are never read at all.

**The guard.** The boundary is an explicit list, `DETECTOR_MODULES`, and nothing else is read at run time. A test checks that list against the code itself:

- It parses the imports, including imports inside functions, of the package `__init__`, `pipeline`, `parse` and `persist`, and follows every package module they reach. The `__init__` is a root because Python runs it before any module of the package, so code placed there runs whatever is imported.
- That reachable set must equal `DETECTOR_MODULES` exactly.
- Every other module in the package must be listed in `NOT_DETECTOR` with a reason.
- Detection code may not import any module in `NOT_DETECTOR`.

A new detection module, a new import from detection code, or a new module nobody classified each fails CI until someone decides where it belongs.

Canonical form, `fpsdet.detector/1`:

- Each module's source is read from the running package, as bytes. A leading UTF-8 byte order mark is dropped and CRLF becomes LF, so a Windows checkout hashes like any other.
- The hash covers `fpsdet.detector/1`, a NUL, then for each module in name order its name, a NUL, the source length, a NUL and the source.
- No path, modification time, commit, virtualenv or interpreter goes in, so the same source has the same digest on any machine and in any folder.
- Comments and docstrings are part of the source, so editing one moves the digest. Only line endings and a byte order mark are normalised; nothing tries to interpret the code.

Module names come from `DETECTOR_MODULES`, never from events or profiles. Each must be a plain `fpsdet.<name>` and resolve to a file directly inside the package directory, or it is refused. Only digests and names are written out, never source.

The digest describes the source files when they were first read in the process. The code that runs is the code that was imported. The two only differ if someone edits the source of a running scorer.

## The cohort digest

The cohort is the human baseline: one number per player per rank band, weapon or build key and metric. The digest answers which exact baseline values could have influenced the run.

Canonical form, `fpsdet.cohort/1`:

- **One row per value the table holds:** band, key, metric, player id, value. Every occurrence is kept.
- **The player id is part of the row.** Scoring leaves the subject out of the distribution by id, so the same numbers held by other players are a different baseline.
- **Sorted** by band, key, metric, player id, then the value's spelling. File layout, pretty-printing and insertion order do not matter.
- **Values are the floats the scorer holds,** written with Python's shortest round-trip spelling. Nothing is rounded, so two values one float apart give different digests. NaN and the infinities are written as the tokens `NaN`, `Infinity` and `-Infinity`; these bytes are hashed, never emitted.
- **The bytes:** `fpsdet.cohort/1`, a NUL, then each row as the JSON array `[band, key, metric, player id]`, a NUL, the value and a newline.

**Integrity is kept separate.** A cohort carries an advisory stamp, `integrity`: its status (`ok`, `unchecked`, `poison_risk`), the alarms, and the matches the screen left out. The scorer never reads it; the command line prints it and the dashboard shows it. It therefore has its own digest, `integrity.digest` over the canonical JSON of the stamp, and its `status` is shown in plain text. Changing the stamp changes `integrity.digest`, never the cohort `digest`.

**Stored digests are checked, not trusted.** `fpsdet baseline` writes both digests into the cohort file under `provenance`. When a cohort file is loaded, the table is rebuilt and both digests are recomputed.

- **The file carries digests and they match:** `stored_digest` is `matched`.
- **The file carries digests that do not match** (its values or its integrity stamp were edited after `fpsdet baseline` wrote it, for example to clear a poison warning), or uses a recipe this version cannot check: loading fails with `ProvenanceMismatch`, and `fpsdet score` stops with a message. A baseline that claims an identity it does not have is not used. Rebuild it.
- **The file has no digests, because `fpsdet baseline` wrote it before provenance:** it still loads, the digest is computed on load, and `stored_digest` is `absent`.
- **A table built in memory:** `stored_digest` is `not_from_file`.

**External or in-file.** `mode` says where the baseline came from:

- `external`: the run was given a baseline, from a file or built in memory from other events, such as last week.
- `in_file`: no baseline was given, so `run_score` fitted one on the very events it is scoring. That includes the players under review, and it is not independent evidence about what humans do. The digest is still given, because that table has an exact identity, but `mode` is what a consumer should read first.

## The input digest

The input digest answers which exact events of this player the scorer evaluated. It is over the parsed `Event` objects, not the raw NDJSON, so spacing, key order and how a number was written in the file do not matter. A value that parses to something else does matter: `30` and `30.0` are an integer and a float, and an event with `hidden_track_ms: 0` is not one without the field.

Canonical form, `fpsdet.player-events/2`:

- **The events of one player, in the canonical timeline the scorer reads** ([event-normalization.md](event-normalization.md)): match, server time, kind, `spray_index`, then the event's canonical text. Since event normalization the scorer reads nothing from arrival order, so neither does the digest. The same events listed in any order, players interleaved any way, or extras keys in any order give one digest. `run_score` builds each timeline once and both the scorer and the digest read that same list.
- **Every occurrence counts.** A duplicated event is a different input from a single one, wherever in the file it sits.
- **All of the event.** The bytes are `fpsdet.player-events/2`, a NUL, the event count, a NUL, then for every `Event` field in name order that at least one of the events sets: the field name, a NUL, a JSON array of that field's value on each event in timeline order (null where it is not set), and a NUL.
- **Absent fields.** An empty mod set or an empty set of extras counts as not set. A field none of the events sets leaves no trace, so adding an optional field to `Event` later does not change existing digests.
- **Extras** are written with sorted keys.
- **Floats and non-finite values** are written as in the cohort digest.

`inputs.events` and `inputs.matches` are counts for people. The digest already binds both.

**`fpsdet.player-events/1`, before normalization.** Until event normalization, fpsdet wrote `fpsdet.player-events/1`.

- **What it binds:** the same bytes as `/2`, but over the events in the order they arrived. The scorer read that order then: findings, metrics and context lines followed the first-seen weapon, and events at the same time kept their arrival order.
- **It is not order-neutral, and never claimed to be.** It stays as it was (`fpsdet.provenance.arrival_digest`, with its P2.2 test digest pinned).
- **Old packets:** a packet that carries it still verifies.
- **Nothing writes it any more.** A `/1` digest and a `/2` digest of the same events differ, as they should: they describe different inputs to different scorers.

**What the input digest does not cover.** It binds the subject's own events, and `cohort.digest` binds the baseline. Some observations rest on other players' events too:

- a shared leftover compares two players' recoil commands,
- a voice-speed teammate is timed against a partner's hidden-mover contacts.

The party note, a context line rather than an observation, also names other players' decisions. Those observations name their partner in `evidence` and, for the voice watch, in `depends_on`, but the partner's events are not in this case's input digest. Binding them is part of the packet identity that comes next. Until then, a relationship observation can depend on events this case's provenance does not cover.

## The history digest

The account check compares this window's accuracy with the player's own earlier windows. Its input is the history file: one row per player, weapon key and rank band, with shots and hits.

**Scope: what the scorer can read for the subject.** The scorer reads a history row for a player when the row's player id is the player's, and its weapon key and band are ones the player used in this window. It adds up shots and hits over those rows. `score.history_for` is that rule, and both the account check and the digest use it, so they cannot drift apart. Rows of other players, or of weapons and bands the player did not use now, cannot change the case, so they are not in its digest. Changing one player's history moves only that player's history digest.

Canonical form, `fpsdet.history/1`:

- **The rows as a multiset.** The check adds them up, so their order cannot matter and is not in the digest; a repeated row counts twice, as the scorer counts it.
- **The bytes:** `fpsdet.history/1`, a NUL, the row count, a NUL, then each row as the JSON array `[player_id, weapon_key, skill_band, shots, hits]`, sorted by that text, one per line.

**Modes.**

- `{"mode": "none"}`: the run was given no history. An empty history file is the same input to the scorer and gets the same mode.
- `{"mode": "external", "recipe": "fpsdet.history/1", "digest": ..., "windows": n}`: the run was given history. `windows` is how many rows the scorer could read for this player, which can be 0. That case reads exactly like `none` to the scorer, but the mode says a history was supplied.

The history rows themselves are never written into a case. Cohort files carry stored digests, but history files do not yet; verifying a stored history digest on load can follow the same pattern later.

## External input

When a run is given external records ([external-evidence.md](external-evidence.md)), each case's `provenance.external` binds the ones about its player: `fpsdet.external-input/1`, a SHA-256 over the recipe, the count, and each record's whole `fpsdet.external/1` digest, sorted. It also lists the run's sources: each file's SHA-256, how it was mapped (`fpsdet.external/1` or the adapter's digest), and its counts of records added, repeated and unreadable. A run with no external input says `{"mode": "none"}`, which is not the same as external input with no records about this player (`"records": 0`). Like every digest here, these identify the input. They do not show who wrote it.

## Challenge plans

A planned challenge has its own public record, digested with `fpsdet.challenge-plan/1`: SHA-256 of the recipe name, a zero byte, and the canonical JSON of its id, type, version, game, match, player, counter, nonce, window and commitment. A challenge observation carries that digest, the commitment and the window in its evidence, so its observation id, and through it the packet, binds the exact plan it was judged against. Neither the secret nor any realization is in it. `fpsdet challenge verify --cases` checks a case's challenge findings against the plan files ([challenges.md](challenges.md)).

The plan digest shows a record was not edited after it was digested. It is not a signature. The commitment inside it binds the plan to a realization only the server can reproduce.

## The evidence packet

`evidence.packet` is one digest over the material evidence of the case: what the evidence is, and what produced it. It answers "is this the same evidence packet?"

```json
"packet": {"recipe": "fpsdet.packet/1", "status": "complete", "digest": "sha256:<64 hex>"}
```

Every new case is written with `fpsdet.packet/2`. Packets written earlier with `fpsdet.packet/1` keep verifying: `verify_packet` reads each packet with the recipe it names, and `packet/1` means exactly what it always did.

**What `fpsdet.packet/1` binds**, as canonical JSON:

| Field | From |
| --- | --- |
| `evidence_version`, `provenance_version` | The shapes it was read with |
| `subject`, `game` | `player_id`, `game_id` |
| `decision` | The case's decision. The same observations under another decision are another packet |
| `eligibility` | `evidence.eligibility`, which with the roles reproduces the decision |
| `observations` | Every observation id, sorted. Each id covers that observation's family, kind, role, subject, key, matches, dependencies and evidence. The partner and party in a relationship observation are therefore bound through it, not repeated |
| `provenance.detector` | recipe, digest, modules |
| `provenance.profile` | recipe, digest |
| `provenance.cohort` | mode, recipe, digest, stored digest, and the integrity stamp's recipe, status and digest |
| `provenance.inputs` | recipe, digest, events, matches |
| `provenance.history` | mode, recipe, digest, windows |

**What `fpsdet.packet/2` adds:**

| Field | From |
| --- | --- |
| `provenance.external` | mode, recipe, digest, records, sources: the external records about this player and the files the run read (see External input). `{"mode": "none"}` when the run had none |
| `fusion` | `evidence.fusion`: the native decision, the final one, the rule, and the providers and domains behind the signals. Null when the run had no external input |

External observations are bound by both recipes the way every observation is, by id in `observations`. `packet/1` could not bind the external input or the fusion state: its provenance parts are fixed, and a part added later is not in it. So the recipe moved instead of `packet/1` changing what it means.

**Why every new case uses `packet/2`, external input or not.** One recipe for everything fpsdet writes now is simpler to check and to explain than two, chosen by whether a run happened to have external records. For a run without them, `packet/2` binds `{"mode": "none"}` and a null fusion, which is a true statement about that run. Every packet digest moved once, when this recipe arrived; nothing else in any case did.

**What they leave out:**

- The reason sentences and the context lines, since rewording is not new evidence.
- `observations[].context`.
- The AI brief.
- Reports, since a report changes queue order, not evidence. This is an evidence packet, not a queue-state record.
- `queue_rank`, `party_note`, `metrics`, `limits` and `untrained`.
- The legacy `seal`, and anything a dashboard or page shows.

**Why integrity is bound when it does not change the score.** Two packets scored against the same baseline numbers, one stamped `poison_risk` and one not, were presented to a reviewer with different warnings. As review artefacts they are not the same, even though every number in them is.

**Why the cohort mode is bound.** An external baseline and a baseline fitted on the scored events are different evidence even if their tables happened to hash alike: independence is part of the provenance.

**Incomplete packets.** A packet needs a profile, a detector digest, a cohort, inputs and a history mode. A case scored outside `run_score`, by calling `assess_player` directly, lacks them. It gets `{"recipe": "fpsdet.packet/1", "status": "incomplete", "missing": [...]}` and no digest. Nothing is filled with placeholders and called complete.

### Checking a packet

`fpsdet.provenance.verify_packet(case)` reads a serialized case and returns what does not hold together, or nothing.

1. **Observation ids.** Each id is recomputed from the observation's own fields, and the model refuses a role the scorer never gives that kind. An edited value with its old id is caught here, before the packet is considered.
2. **Recipes.** Each provenance part names a recipe this version knows, with a well-formed digest. Old recipes stay known: a packet written with `fpsdet.player-events/1` before event normalization still verifies (`tests/fixtures/historical-packets-p23.json`), and so do `packet/1` cases from the knowledge and challenge engines (`historical-packets-p3.json`, `historical-packets-p4.json`).
3. **The packet.** It is rebuilt, with the recipe it names, by the same code that built it, and compared. A recipe this version does not know is a problem, not a guess.

It needs no events, cohort, profile or code, and it does not prove that those sources would produce the packet again. That second level, source reproduction, is a rerun: score the same events with the same detector, profile, cohort and history, and `tools/regress.py diff` the result. `tools/regress.py verify` runs `verify_packet` on every case of a snapshot.

**Caught:** an edited observation value, an edited observation id, an observation with a recomputed id (the packet moves), a changed role, the decision, the eligibility, the subject or game, and any detector, profile, cohort, cohort mode, integrity, input or history digest. With `packet/2`, also: an edited or removed external record, the external input digest, mode, record count or any source's counts, and any field of the fusion state.

**Not caught, by design:** a reworded reason or context line, a new AI brief, a changed report count or queue rank, a different party note.

### What the digest does and does not prove

The packet digest gives content addressing and a deterministic evidence identity. Compared with a digest stored elsewhere, for example in the studio's review tool when a case was opened, it shows whether the case changed since.

It does not prove who produced the case. Anyone who edits a case can recompute every id and the packet digest, so it is not tamper-proof on its own. Authenticity comes from a signature over the packet digest with a key only the scoring server holds:

```text
server-held private key  ->  sign(packet digest)  ->  anyone with the public key can check it
```

That is a later step. Nothing is signed today.

### The packet and the seal

| | Legacy `seal` | `evidence.packet` |
| --- | --- | --- |
| Covers | Player, game, decision, the reason sentences | The material evidence and all provenance |
| Moves when | A sentence is reworded | Evidence, decision or provenance changes |
| Kept for | Compatibility: reviewers and tools already store it | Structured evidence identity, and a future signature |

## What does not change

- **Observation ids.** An id says which observation this is; provenance says what produced it. A different profile or detector does not change an id for the same evidence, and an id does not include provenance. Tests pin both.
- **The case seal.** The v1 seal is still the hash of the player, the game, the decision and the reasons. The packet digest is the structured identity beside it, not a replacement.
- **Every other case field.** Checked on every planted and synthetic case by `tests/test_golden.py`, which locks the evidence except its provenance. Checked on the real CS2 and TF2 reruns by `tools/regress.py diff`.

## Checking a run

```bash
PYTHONPATH=src python tools/regress.py verify snapshot.jsonl
```

This prints the provenance the run shares: the detector, the profile, the cohort with its mode, stored digest and integrity status. It counts the cases that carry their own input digest, checks every evidence packet with `verify_packet`, and names any case that does not hold together.
