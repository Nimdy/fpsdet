# Provenance

A case's evidence now says which detector code and which game configuration produced it. A reviewer, an auditor or another system can tell whether two cases were scored by the same code under the same rules, and spot when they were not, without trusting whoever handed the case over.

The code is `fpsdet.provenance`. It uses the standard library only.

## What it proves, and what it does not yet

| Bound to the evidence | Status |
| --- | --- |
| The detector implementation: the source of every module that can change a finding | Implemented |
| The game profile as the scorer parsed it | Implemented |
| The exact events that were scored | Not yet (next packet) |
| The baseline or cohort the player was compared with | Not yet (next packet) |
| Player history, external evidence, challenge seeds | Not yet |
| What a reviewer then did | Not yet; that stays in the studio's own review tool |

Two cases with the same detector and profile digests were scored by the same code under the same configuration. They may still have been scored on different events or against a different baseline. Provenance does not say that a case is correct, only what produced it.

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
    }
  }
}
```

- `recipe` names the canonicalisation. It changes only if the bytes being hashed would be built differently.
- `digest` is a full SHA-256, written `sha256:` and 64 lowercase hex digits.
- `modules` is the detector boundary, so a reviewer can see what the digest covers. It holds module names only: no paths, no source.
- If a module's source cannot be read (for example, a build that ships only bytecode), `detector.digest` is null and `detector.missing` names those modules. Nothing is guessed.
- `fpsdet.pipeline.run_score` stamps every case of a run with the same provenance. The planted demo does the same.
- A case built by calling `assess_player` directly has `"provenance": null`.

Both digests are computed once per scoring run. Reading the detector source happens once per process (about 1 ms) and is cached. The profile digest takes about 0.1 ms per run. Nothing is read or hashed per player.

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
| `fpsdet.lake` | Raw event storage. Which events were scored is input provenance, the next packet |
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

## What does not change

- **Observation ids.** An id says which observation this is; provenance says what produced it. A different profile or detector does not change an id for the same evidence, and an id does not include provenance. Tests pin both.
- **The case seal.** The v1 seal is still the hash of the player, the game, the decision and the reasons. A seal that also covers the evidence and its provenance is a later step.
- **Every other case field.** Checked on every planted and synthetic case by `tests/test_golden.py`, which locks the evidence except its provenance. Checked on the real CS2 and TF2 reruns by `tools/regress.py diff`.

## Checking a run

```bash
PYTHONPATH=src python tools/regress.py verify snapshot.jsonl
```

This prints the provenance shared by the run, or says which cases do not share it.
