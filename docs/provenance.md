# Provenance

A case's evidence now says what produced it:

- the detector code,
- the game configuration,
- the human baseline the player was compared with, and
- the player's own events.

A reviewer, an auditor or another system can tell whether two cases were scored the same way, and spot when they were not, without trusting whoever handed the case over.

The code is `fpsdet.provenance`. It uses the standard library only.

## What it proves, and what it does not yet

| Bound to the evidence | Status |
| --- | --- |
| The detector implementation: the source of every module that can change a finding | Implemented |
| The game profile as the scorer parsed it | Implemented |
| The human baseline (cohort) the run compared players with, and its advisory integrity stamp | Implemented |
| The subject player's own parsed events, in the order they were scored | Implemented |
| Other players' events that a relationship observation used | Not yet (see "What the input digest does not cover") |
| Player history (the account-jump windows) | Not yet |
| External evidence, challenge seeds | Not yet |
| One tamper-evident identity over the whole evidence packet | Not yet (the next step) |
| What a reviewer then did | Not yet; that stays in the studio's own review tool |

Provenance says what produced a case, not that the case is correct. Nothing yet ties the observations and these digests together under one identity, so a case file can still be edited after scoring without that being detectable from the file alone.

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
                  "fpsdet.summarize"]
    },
    "cohort": {
      "mode": "external",
      "recipe": "fpsdet.cohort/1",
      "digest": "sha256:<64 hex>",
      "stored_digest": "matched",
      "integrity": {"recipe": "fpsdet.cohort-integrity/1", "status": "ok", "digest": "sha256:<64 hex>"}
    },
    "inputs": {"recipe": "fpsdet.player-events/1", "digest": "sha256:<64 hex>", "events": 412, "matches": 8}
  }
}
```

- `recipe` names the canonicalisation. It changes only if the bytes being hashed would be built differently.
- `digest` is a full SHA-256, written `sha256:` and 64 lowercase hex digits.
- `modules` is the detector boundary, so a reviewer can see what the digest covers. It holds module names only: no paths, no source.
- If a module's source cannot be read (for example, a build that ships only bytecode), `detector.digest` is null and `detector.missing` names those modules. Nothing is guessed.
- `fpsdet.pipeline.run_score` stamps every case of a run. `profile`, `detector` and `cohort` are one object shared by the run. `inputs` is the subject player's own. The planted demo does the same.
- A case built by calling `assess_player` directly has `"provenance": null`.
- `cohort` or `inputs` is null when the run did not supply it.

Costs, measured:

- **Detector source:** read once per process and cached, about 1 ms.
- **Profile digest:** about 0.1 ms per run.
- **Cohort digest:** about 9 ms per run for TF2's 4,927 baseline rows.
- **Input digests:** the only part that grows with the data, at about 2 µs per event, because every value of every event is hashed. On the TF2 run (4.1 M events) scoring went from 6.3 s to 10.8 s, about a tenth of a full load-and-score run. On the synthetic week, the weekly batch went from 0.64 s to 0.99 s.

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

Canonical form, `fpsdet.player-events/1`:

- **The events of one player, in the order the scorer received them.** The order is kept because the scorer is not order-neutral within a player: it lists findings, metrics and context lines in the order the player's weapons first appear, and events with the same time keep their arrival order. On the synthetic week, shuffling each player's own events changed 99 of 400 cases (metric order, context lines, one seal). Interleaving players differently while keeping each player's own order changed none. The digest follows the same rule: it moves when the player's own order moves, and not when only the interleaving with other players does. A test pins both.
- **Every occurrence counts.** A duplicated event is a different input from a single one.
- **All of the event.** The bytes are `fpsdet.player-events/1`, a NUL, the event count, a NUL, then for every `Event` field in name order that at least one of the events sets: the field name, a NUL, a JSON array of that field's value on each event (null where it is not set), and a NUL.
- **Absent fields.** An empty mod set or an empty set of extras counts as not set. A field none of the events sets leaves no trace, so adding an optional field to `Event` later does not change existing digests.
- **Extras** are written with sorted keys.
- **Floats and non-finite values** are written as in the cohort digest.

`inputs.events` and `inputs.matches` are counts for people. The digest already binds both.

**What the input digest does not cover.** It binds the subject's own events, and `cohort.digest` binds the baseline. Some observations rest on other players' events too:

- a shared leftover compares two players' recoil commands,
- a voice-speed teammate is timed against a partner's hidden-mover contacts.

The party note, a context line rather than an observation, also names other players' decisions. Those observations name their partner in `evidence` and, for the voice watch, in `depends_on`, but the partner's events are not in this case's input digest. Binding them is part of the packet identity that comes next. Until then, a relationship observation can depend on events this case's provenance does not cover.

## What does not change

- **Observation ids.** An id says which observation this is; provenance says what produced it. A different profile or detector does not change an id for the same evidence, and an id does not include provenance. Tests pin both.
- **The case seal.** The v1 seal is still the hash of the player, the game, the decision and the reasons. An identity that also covers the observations and all of this provenance is the next step.
- **Every other case field.** Checked on every planted and synthetic case by `tests/test_golden.py`, which locks the evidence except its provenance. Checked on the real CS2 and TF2 reruns by `tools/regress.py diff`.

## Checking a run

```bash
PYTHONPATH=src python tools/regress.py verify snapshot.jsonl
```

This prints the provenance the run shares: the detector, the profile, the cohort with its mode, stored digest and integrity status. It counts the cases that carry their own input digest, and names any that do not.
