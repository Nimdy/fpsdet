# Changelog

## Unreleased

### Added

- **Decoys page** (`site/decoys.html`, 07 in the site nav). Why skill-based evidence runs out, drawn from the real TF2 run (only 4 of 96 banned cheaters' sniper accuracy is past the best human), how a decoy works in three views and a five-step loop, the rules that keep honest players safe, and an honest table of how a cheat could counter it and what still gets through. A test ties its numbers to `examples/tf2/desk.json`.
- **The home page shows the real-match results.** A "Tested on real matches" section after the opening gives the TF2 numbers (picks 7.7 times better than random, 3 of 3 reviews banned cheaters, 0 of 1,746 never-banned players in review, and 131 of 189 banned cheaters that still looked clean) and links the TF2 and CS2 pages. A test recomputes each number from `examples/tf2/desk.json`, so the page cannot drift from the run.
- **"What fpsdet found" on every labelled page.** The CS2, TF2 and synthetic pages open with what scoring bought: how many players fpsdet picked for a person, how many of the picks are cheaters against picking at random, how many reviews were cheaters, and how many clean players went to review. A line says what fpsdet made and what came from the servers and the labels.
- **Real TF2 matches** (`examples/tf2`): players RGL banned for cheating, each with up to 20 server-logged matches from logs.tf before the ban, scored beside the honest players from the same lobbies. Unlike the one-match CS2 data, this can show detection: with 20 matches per player at most and 300 extra honest players fetched for a fair comparison, fpsdet flagged 37.5% of banned cheaters and 2.6% of never-banned players at equal evidence, and all three reviews were banned cheaters. Kills per minute and headshot kills per minute are declared as extra numbers in `tf2.json`; they added one review and two watches, all cheaters. Headshot counts larger than the hits they could belong to (about 1 in 80 sniper matches) are no longer sent; capped, they had set a sniper-rifle best human of 100%. Public endpoints only, polite and cached, with keyed pseudonyms. Standard library only. The desk links it as **D · Real TF2 matches**; a dataset can now have labels that count as neither cheater nor clean, such as a ban for something else.

## 0.3.0 (2026-10-04): the operations view and real CS2 matches

The review desk opens on an operations dashboard over a synthetic week, and studios get the same view from their own runs. Running the checks on a population of 400 players, instead of 32 planted ones, exposed several rules that were fine on one player and noisy on many. Those are fixed below. Every planted demo decision is unchanged. It also runs on real Counter-Strike 2 matches with hand-labelled cheaters (`examples/cs2`), shown in the desk beside the synthetic week.

### Added

- **Real CS2 matches in the desk.** The desk links **C · Real CS2 matches**, the operations view over the scored CS2 run with the dataset's labels beside each decision. `fpsdet demo` writes it as `demo/cs2.html` from `examples/cs2/desk.json`; the public site serves it beside the desk. The operations view has a labelled mode for real data: a label filter, an answer check against the dataset's labels, and no "synthetic" wording. A **Result on real data** issue form asks others to run it and share what they find.
- **Every watch says why.** The queue line is now the line behind the decision: a watch shows the tail that made it, a held player shows the weapon that was short of shots. Headshot rate, engagement distance and shots through geometry above a rank's range now write that line, as accuracy always did.
- **Match screen in `fpsdet baseline`.** Each match is set beside the others in the window. A lobby whose hit rate or shots-through-geometry rate is confidently more than `match_outlier_sd` (default 5) robust spreads past the median match is an alarm, and `--screen-matches` leaves it out. On real CS2 matches it flagged 20 of 120 unreviewed "no cheater" matches, none of 45 held-out ones, and 47 of 108 with labelled cheaters. In the sample week it flags the planted aimbot's lobby.
- **Real CS2 matches** (`examples/cs2`): a converter from the CS2CD dataset (CC BY 4.0, hand-labelled cheaters) to fpsdet events, a CS2 profile, and a report against the labels. Across 1,025 clean players nobody was put in review; no labelled cheater was either, because one match gives most of them under 40 shots. The README there has the numbers and the limits.
- **Operations view.** `demo/board.html` now has two tabs:
  - **Operations**: KPI tiles, the queue by night, what fired, the human-ceiling scatter, reports against decisions, an answer check, a sortable queue with a case drawer, field coverage per night, and baseline thickness. Every chart has a table twin.
  - **Answer key**: the 32 planted tapes. Deep links such as `board.html#card-mirror-script` still open them.
- **Synthetic week** (`fpsdet.week`): 400 players, 17 planted cheats with their own start days, parties, reports, a mid-week server build that ships the wire fields, and private replays on some matches. It is scored nightly and weekly against a frozen baseline from a clean prior week. It is deterministic.
- **`fpsdet score --out`** writes `ops.json` and an offline `dashboard.html` next to the cases.
- **`fpsdet dashboard <folders…> --out week.html`** renders one run, or merges several nightly runs into a week. A review opened on any night stays open; watches come from the latest run.
- **Human-ceiling scatter** takes accuracy and headshot rate from the same weapon as the lines it is drawn against. It had mixed an smg accuracy with a rifle headshot rate for about one player in ten.
- **`checks` on every case**: machine-readable ids of what fired, so dashboards group on ids instead of reason text. Metrics carry the weapon `key`.
- **dashboards/README.md** maps each panel to the case-JSON fields that drive it, for Grafana, Kibana, Splunk or an in-house console.

### Fixed

- **The band named on a case and its 95th percentile now come from the same band.** When the best human measured sat outside the top band, a case named that band but showed the top band's p95.
- **A review opened on several nights shows the latest night's case** in both the desk and `fpsdet dashboard`. The two had disagreed: one kept the first night, the other the last.
- **Merged field coverage counts each night by the events that could carry the field** (shots, hits for `hitbox`, movement samples), not by all events. `ops.json` coverage rows carry that count as `n`.
- **`vendor_min_z: 0` in a profile is kept.** It had silently become 5.

### Scoring: what the population showed

- **Shared leftover.**
  - The lag term crossed spray boundaries, which left the same spike on every spray's first shot for every player. In the synthetic week that matched 97 honest players with each other.
  - The lag is now in-spray. Signature points need 3 sprays and are scaled by their standard error.
  - A match needs Fisher z ≥ 5 (`vendor_min_z`) as well as r ≥ 0.85. `vendor_min_points` is now 12, since real sprays rarely reach 24 rounds.
  - Accounts are compared per weapon, not per build.
  - Result: 0 honest matches.
- **Medians use a confidence bound.** Distance, recoil and declared-metric medians are tested on a distribution-free bound, as rates are. Nightly batches had put about half the honest players on watch at least once, from small-sample noise.
- **"Past every human" means every band.** For numbers that rank does not order, such as distance, the top band's maximum was not the most extreme human. Ordinary players were "past every human" on engagement distance.
- **Fire rate and metronome are judged per match.** A macro switched on mid-week, or one that fires in bursts with human-length pauses, used to be averaged away.
  - A match needs at least 5 gaps on a gun before its fire-rate violations count. Otherwise one glitched stamp in a match where the gun fired twice was a 50% violation rate, and ten such matches made a review.
  - A match whose cadence averages at the gun's own cycle (within `tick_ms` or the slack) is the server's pace, not a macro. Measuring only the gaps inside bursts had made every held trigger on a full-auto look like a metronome.

The synthetic week, scored nightly and weekly against a frozen baseline:

| Players | Outcome |
| --- | --- |
| Honest (383) | 0 in review, 1 on watch |
| Blatant cheats (12) | All in review |
| Teammate on a wallhacker's calls, boosted account | Watch |
| Closet aimbots tuned under the elite ceiling, the humanizer buyer | One closet on watch; the other and the buyer clean |

The buyer's leftover correlates at r 0.89 over 13 spray positions, under the z bar. Another week of data clears it.

## Earlier in 0.3.0

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
