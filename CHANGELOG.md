# Changelog

## Unreleased

Fewer honest players flagged, plus fixes, onboarding and project files. Every planted demo decision is unchanged.

### Scoring: fewer honest players flagged

- **Recoil mirror and memorised spray patterns.** In fixed-pattern games, players who learned the spray pull on the same tick as the kick, and the mirror check flagged them. In a simulation, 188 to 200 of 200 such players were flagged; now none are. The check now removes the mean kick and command at each `spray_index` (when at least 3 sprays reach it) and tests only what is left. A script that reads the kick is still caught.
- **New profile field `recoil_pattern`:** `learnable` (default) or `random`. With too few sprays, `learnable` skips the mirror check and says why on the case. `random` keeps the old raw-kick test.
- **Distance, view snaps, acquire time and declared extras.** "Past humans" used to mean "past the top band's p95", which one player in twenty crosses on every metric. It now means past the band's maximum (or minimum), the same bar as accuracy.
- **The same metric on several weapons now counts once** toward a review. Being best on every gun is what the best human looks like.
- In a simulation of 600 honest elite players using 3 weapons, these two changes cut the review rate from 1.8% to 0.2%.
- **Accuracy and headshot bounds use a design effect** when a player has at least 5 matches. One hot match no longer carries the bound. A design effect of 1.5 or more is noted on the case.
- **Hidden tracking.**
  - Shots labeled `audio` no longer count.
  - New optional event field `since_perceived_ms`. A shot within `hidden_grace_ms` (default 1000) of last seeing or hearing that enemy is not hidden tracking. It is also left out of the unknowable smoothness sample and the teammate check.
  - Per-shot `hidden_track_ms` and `private_track_ms` are cut to the time since the player's previous shot, so a running total is not counted once per shot.

### Fixes

- The teammate (voice-speed) check compared contacts across matches. `t_ms` restarts each match, so it now compares only within the same `match_id`.
- **Shared leftover:**
  - It compares only accounts on the same build.
  - With `spray_index`, it lines leftovers up by spray index (new `vendor_min_points`, default 24), so a humanizer table matches whatever the spray lengths.
  - With a full cohort on the build, the shared human habit is removed first.
- AI briefs leaked other players' ids: the leftover twin and the teammate's partner, in `vendor_twin` and in reason text. Every id the case mentions is now aliased, and the seal is removed.
- A server with no ranks (`unrated`) never got human-baseline checks. `unrated` is now a band of its own, and the case says the player was compared with everyone unranked.
- `fpsdet baseline --out dir/file.json` crashed when the folder did not exist. `write_json` now creates parent folders.
- Case files could overwrite each other: `p 1` and `p_1`, or `Bob` and `bob` on Windows and macOS. Names that are not already safe get a short hash.
- The wire check's reason no longer reads as "4800 ms ahead". It now says how many shots matched the wire and the total interpolation delay.

### Added

- `fpsdet sample --out week.ndjson` writes the synthetic population and the planted players as NDJSON, so the README's ingest, baseline and score walkthrough runs as written.
- docs/culling.md: server-side culling (fog of war) and how it pairs with these checks.
- docs/players.md: a page operators can link for players (data, appeals, notice template).
- CONTRIBUTING.md, SECURITY.md, CODE_OF_CONDUCT.md, issue and PR templates.
- A CI workflow that runs the tests and the planted demo on every push and pull request.
- The profile schema now lists every field the parser reads. The event schema has `since_perceived_ms`.

### Docs

- The README was rewritten for newcomers: what it is, a runnable walkthrough, a consistent list of checks, a glossary, population guidance and a license summary.
- docs/scoring.md specifies every changed rule.
- The engine snippets were fixed: Godot 4 `is_on_floor()`, real file output and JSON escaping. The Unity emitter now has movement and recoil fields.
- docs/games.md and site/games.html carry a non-affiliation notice.

## 0.2.0

Baseline before this changelog.
