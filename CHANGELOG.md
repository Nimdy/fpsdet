# Changelog

## Unreleased: the fpsdet Arena

Nothing here changes fpsdet. No detector, threshold, packet format, benchmark artifact, pilot or study protocol moved; the arena consumes the scorer as it ships.

### Added

- **The fpsdet Arena** (`examples/fps-arena`): a tiny reference FPS in a Godot 4.7.2 dedicated server, built to show what fpsdet sees. One window holds the stock client's view beside the server's view, with the operator panels under them: server knowledge with fpsdet's `known`, `unknowable` and `unknown` on every body, every detector's eligibility, the findings, the case with `automated_action: none`, the packet and graph, the raw telemetry, and a replay of any match. A Python scorer beside the server runs fpsdet on the match once a second, and again with the `fpsdet` command when it ends, and the panel says `OFFLINE REPLAY: IDENTICAL` or `MISMATCH`.
  - Fourteen scenarios, each with a card (what this tests, what the player can know, what the server knows, the expected fpsdet behaviour, what would make it invalid) and an expectation the harness compares with fpsdet's output: normal play; impossible speed; fire rate; recoil floor; recoil mirror; a hidden enemy that is audible, silent, or unmeasured; the wire against the picture; a real `occluded_motion_replay/2` challenge from a secret-derived plan; the angle-hold false positive, kept and labelled `EXPERIMENTAL: NOT PRODUCTION QUALIFIED`; a fictional external record that makes a watch and cannot make a review; and an exposed positive control the pixel proof needs.
  - `harness/arena.py`: `doctor`, `run`, `scenario`, `qualify`, `verify`, `replay`. The qualification runs every scenario with the real topology, checks packets, graphs and plans, reproduces each realization from the secret, scans every public file for secret material and for addresses or machine identities, and writes `result.json` (`fpsdet.arena/1`, class `interactive_reference_demo`) and the public captures. All 14 came out as declared and replayed identically.
  - An optional AI reviewer brief through fpsdet's own brief path, with the packet digest shown before and after: it summarizes, creates nothing, and changes no decision.
  - **The player pixel proof.** The qualification runs the challenge and an exposed positive control with a pixel-check client (the stock client plus the P12 frame check, in a private virtual display): the probe in the sealed chamber is drawn as a body and contributes no pixel; the same probe placed in the open does. Qualification only.
  - **Labels nobody can miss.** Every scenario names its actor; the security pane, the strip, the card, the replay header, the player's HUD and every qualification row say `SCRIPTED TEST STAND-IN · NOT A HUMAN · NOT REAL CHEAT SOFTWARE` (or `HONEST-STYLE SCRIPTED STAND-IN`, or `HONEST AUTOPILOT`) wherever a script drives the aim. `insufficient_data` is explained as not a clean verdict; a watch from an external record is labelled as caused by it; the speed finding shows the emitted sample cadence beside its sample count.
  - `tests/test_fps_arena.py`: the scenario metadata, the committed captures re-scored offline, the server's knowledge queries byte for byte the pilot's, the stock client's ignorance of challenges and the operator feed, AI and external evidence changing nothing they must not, no secret or personal data in any capture, and the pilots and Benchmark v1 untouched.

## 0.4.0 (2026-10-09): secret turns and declared clocks

Nothing here changes a default. Plans still default to `occluded_motion_replay`/2, profiles without the new fields score as before, and Benchmark v1 and the human pilot reproduce. Challenge reviews remain experimental and not production-qualified, and no person has played the human pilot yet.

### Added

- **`occluded_motion_replay`/3: secret turns.** The body turns at six secret times, each a secret way, and the finding is whether the aim turned with it, against 1,000 shuffled schedules drawn from the challenge id. Followed when p ≤ 0.01 and four turns moved the aim more than 2° their own way. It answers the dry run's doorway holder, which version 2's time-on-body bar cannot. New event fields `view_yaw_deg`, `challenge_turn_index` and `challenge_turn_sign`, digested as `fpsdet.player-events/4`. Versions 1 and 2, Benchmark v1 and the human pilot are unchanged; plans still default to version 2, and the human pilot's server does not send version 3. Tested on simulated aim, and live in Godot by machine stand-ins.
- **Version 3 in the live Godot server** (`examples/turns-pilot`). New files beside the frozen study server: `turns.tscn`, `scripts/turns_server.gd` (extends `study_server.gd`), `scripts/turns_recipe.gd`. The published run: no honest stand-in followed in 24 challenges, the controlled follower in 8 of 8, every turn the secret's. An earlier run had one sweeper followed at p = 0.00999, the 1 in 100 the bar allows; docs/challenges.md reports all three runs and what a stricter bar would cost.

- **Profiles can declare their clocks.** Three opt-in fields; a profile without them scores as before and keeps its digest, so Benchmark v1 still verifies.
  - `speed_min_run_ms` measures a sustained speed run in milliseconds, not samples, which are a different length of time on every emitter. It needs `movement_clock: "server"`; without it the speed rule abstains.
  - `shot_clock: "server_tick"` without `tick_ms` makes the metronome abstain instead of flagging every held trigger on tick-quantised gaps. `server_ms` says shots are stamped to the millisecond. The finding names the clock it assumed.

### Changed

- **What the numbers can and cannot carry.**
  - Challenge reviews are now labelled experimental and not production-qualified on every public page, in the README and in docs/challenges.md. Scoring and case text are unchanged: the study keeps them frozen, and case text is sealed.
  - The home page's 8.9× says most of it is the rank-tail check, a watch-level signal. Its "0 never-banned to review" tile says how rarely anyone reaches review on match totals: 1 to 10 banned accounts per draw.
  - docs/calibration.md names possible label-selection confounding: league cheating decisions may take suspicious performance into account. Benchmark v1's frozen claims are unchanged.
  - The GitHub description, topics and the package description no longer say "ML and AI".

- **The public pages and the README say where the work stands.** The site predated the evidence work; it now matches the committed artifacts, and tests tie its numbers to them.
  - **The home page** leads with FPSDET Benchmark v1: TF2 over nine predeclared baseline draws (a median 8.9×, 7.7× to 10.4×, and no never-banned player sent to review in any draw) and CS2. The first TF2 run, one draw chosen by a private key, is linked only as a historical record.
  - **A new evidence page** (`site/evidence.html`) covers:
    - what a case carries;
    - probes planned with a secret;
    - how other systems' records stay a watch at most;
    - what has been tested, by class: fixtures, public real data, the live Godot pilot, and the human pilot no person has played yet.
  - **The decoys page no longer says "only a cheat follows it".** The human pilot's machine dry run showed an honest-style stand-in holding a doorway's edge reaching the challenge bar, so the page says a challenge review is a case to open. It also covers planned challenges and their fields, and labels its TF2 chart as the historical run's.
  - **The scoring, wire and source pages** add the challenge fields and their per-moment verdicts, the evidence packet, external records, the Godot reference server and the benchmark.
  - **The README** gains a status table, what a case carries, the challenge caveat, and a complete docs list.

### Fixed

- **File order is no longer game state.** The same server events listed in a different order in a file could produce a different case. Same-time events could decide a hidden-mover, floor-run or speed review, and on Python 3.11 a float sum could cross a line in one order and not the other.
  - **One order:** each player's events are now read in one canonical timeline: match, server time, kind, `spray_index`, then content for events those cannot tell apart. Players are read in id order, and weapons, builds and declared metrics are reported in key order.
  - **Simultaneous events have explicit rules:**
    - a movement moment extends a run only when every sample is over;
    - shots at one moment add one hidden or private sample when they agree and none when they disagree;
    - recoil moments are ordered by spray index;
    - the mirror and leftover checks do not run on kicks the server did not order.
  - **Exact sums:** material float sums use `math.fsum`.
  - **What moved:** across the planted demo, the synthetic week and the CS2 and TF2 reruns, no decision and no observation changed. Lists were re-ordered, and two TF2 legacy seals moved with their reason order.
  - **Docs:** see [docs/event-normalization.md](docs/event-normalization.md).
- **A teammate timed against two wallhackers kept the wrong lags.** `inherit_lags_ms` kept whichever partner was processed last. It now keeps the strongest relationship: one that reaches the watch bar, then the most swings faster than a voice, then the faster median, then the partner id. Each qualifying partner still has its own voice observation. No planted, synthetic or real case moved.
- **An event could write outside the lake.** `fpsdet ingest` used `game_id` as a folder name, so a game id such as `x/../../elsewhere` appended `events.ndjson` outside `--lake`. A game id with a path separator, and a `utc` whose first ten characters are not a `YYYY-MM-DD` date, are now skipped and counted. `--dt` and `--game` must be safe too, or the command stops with a message.

### Added

- **Amendment 3 to the human pilot, before anyone enrolled** ([examples/human-pilot/amendment-3.json](examples/human-pilot/amendment-3.json)). If the participant whose session stopped the study for good asks for deletion:
  - deletion is honoured, and collection stays ended;
  - `withdraw` rewrites `STOP` and `stops.log` so no line names them, and marks the final stop as withdrawn evidence;
  - the analysis answers the primary question "indeterminate", with nothing from the deleted session.

  Consent version 2 is unchanged.

- **Amendment 2 to the human pilot, before anyone enrolled** ([examples/human-pilot/amendment-2.json](examples/human-pilot/amendment-2.json)). Outside review found collection holes that could deadlock, bias or overclaim the study. Nothing that decides a challenge changed.
  - **Participant states and replacements:**
    - at most 4 in play until 4 have completed, then at most 12;
    - `study.py withdraw` deletes a participant's data on request and keeps only an anonymous record, or frees the place of someone unable to continue.
  - **Consent version 2:** please don't try to find the probes, a sixth question, and what withdrawal after publication means.
    - Enrollment binds the consent notice, the design and both amendments.
    - Sessions and the analysis refuse mixed or changed versions.
  - **Before each session:** a passed practice, now with an Esc check, and the participant's pre-registered order.
  - **Validity fixed before scoring:** from the participant's answer, the operator's declaration with a closed list of reasons, and score-independent facts. A crossing is never a reason, and only the stop on a protocol-valid session is final.
  - **Privacy:** the whole session tree is swept, operational files are deleted, and what is kept is scanned again; practice too.
  - **Falsification is the headline.**
    - A finding gives counts and no interval.
    - Zero-event bounds appear only once the planned group completes.
    - Motion-split boundaries are tested exactly.
    - The study arena's near-still probes are named as a confound.

- **Try it on your own logs** ([examples/historic](examples/historic/README.md), [the Try it page](https://nimdy.github.io/fpsdet/try.html)). An operator can score a copy of logs their server already wrote, offline. Nothing goes on their servers or players' PCs, and nobody is banned.
  - **`convert.py`** turns a CSV or JSON-lines export into fpsdet events:
    - it copies only the schema's fields, so names and addresses are dropped;
    - account ids become HMAC pseudonyms under a salt kept in an owner-only file;
    - every value is checked against the schema;
    - `--who` finds an account's pseudonym again.
  - **Example files:** a made-up sample export, its column map, and a one-line profile. On the sample, they give one speed review and keep an explosion out of the case, as the page says.
  - **Every page asks.** A "Try it" nav link and a banner above every footer, a banner at the top of the home page, and the desk's nav. The second ask is a dedicated server for a live test, through its own issue form (`live_test.yml`).
  - **The setup as a cheesy game quest** (`site/build_quest.py` draws `site/img/setup-quest.svg` and a tall version for phones):
    - a HUD with prod HP 100/100, players banned 0, and the network offline;
    - five levels from `git clone` to reading the cases;
    - an achievement toast and a loading tip.

    The jokes carry only the page's own facts. It sits on the Try it page, under the home page's top banner, and in the READMEs.

- **Amendment 1 to the human pilot, before any person plays** ([examples/human-pilot/amendment-1.json](examples/human-pilot/amendment-1.json)). It changes nothing that decides a challenge: the bar, the cone, the episode gap, placement and scoring are as pre-registered.
  - **Staging:** the first 4 participants finish before anyone else is enrolled. Review-grade evidence on an honest session ends collection, and `clear-stop` refuses it.
  - **Before the sessions:**
    - `study.py controls-check` drives the unchanged human client with injected keyboard and mouse events;
    - `study.py practice` runs 90 seconds without challenges, in a folder that is never analysed.
  - **Questions and comments:** a fifth question asks whether the controls behaved normally. Optional comments stay in the session's private folder.
  - **Measured:**
    - the probe's distance and motion, and whether the aim held still or moved with it, as study telemetry only;
    - per-participant and per-session closeness to the bar;
    - each session's engine binary and code identity.
  - **The machine dry run stays as committed,** bound to the code that made it. Its angle-holding case is now a permanent regression fixture: under the current rule it is review-grade.
- **The consented honest-human pilot: designed, instrumented, dry-run; not yet played by people** ([docs/human-pilot.md](docs/human-pilot.md)). It asks how often, and for how long, honest players' aim behaves like challenge tracking in the repository's own Godot pilot.
  - **Pre-registered** in `examples/human-pilot/design.json` before any session:
    - 8 participants, seven 4-minute sessions each across honest-play modes up to an aggressive pre-aim stress session;
    - challenges from the real planner;
    - P12's thresholds and cone, frozen, and a 250 ms episode gap;
    - every metric and the analysis at participant, session and challenge level with exact bounds;
    - exclusions, stop conditions, and the data and retention policy.
  - **Consent and privacy:** a plain-language notice says that hidden probes exist and what is and is not recorded.
    - Participants are random ids.
    - The server binds loopback or a private LAN address only.
    - Every publishable file is scanned for addresses and machine names.
    - Only participants who opt in have gameplay published.
  - **Instruments:**
    - A study arena (doorway, corner, passage, and sealed probe rooms near each) in the P12 project, with P12's own files untouched.
    - A study server keeping P12's challenge and knowledge rules (a test compares them).
    - A human-playable client with a consent gate and no screen capture.
    - `study.py`: enroll, sessions with live and offline scoring, reproduction and secret deletion, the questionnaire, stop conditions, and `fpsdet.human-pilot/1`.
    - A `consented_human_pilot` benchmark addendum that refuses anything but people.
  - **Machine dry run** (`examples/human-pilot/dry-run`, scripted stand-ins, never people): every runtime check held. Honest-style aiming crossed the frozen bar: a stand-in holding a doorway's frame while a probe sat behind it counted 5,500 ms (bar 1,200 ms). The controlled follower counted a median of 13,258 ms. No threshold changed; the human sessions will measure how often people do this.
- **The live challenge pilot** ([docs/pilot.md](docs/pilot.md), [examples/pilot](examples/pilot/README.md)): fpsdet's active challenge run end to end in a real game for the first time. It runs in a Godot 4.7.2 dedicated server with stock clients, on one machine, over loopback only. A controlled integration qualification, not a deployment and not a detection rate.
  - **The path:**
    - `fpsdet challenge plan` writes the plan with a server secret.
    - The server derives each realization with fpsdet's recipe in GDScript and refuses any record its secret did not make.
    - It sends the probe to the subject's client only, with the same fields as a real player.
    - It proves at every tick, with its own line-of-sight and audio queries, that this client could neither see nor hear it.
    - fpsdet turns the server's NDJSON into verifiable evidence.
  - **Scenarios:** eight, with their outcomes declared before any live run, and every one came out as declared.
    - Only the server-side stand-in for a hidden-information reader produced evidence (review).
    - Honest play, an accidental crossing and aim on a visible enemy in line with the probe did not.
    - A body the server saw or heard, a missing audio verdict and contradictory verdicts each abstained, for their own cause.
  - **The stock client** received the probe and drew no pixel of it while the server called it hidden. When it was placed in the open on purpose, the client drew it and the server called it seen. The enemy's client never received it.
  - **Proven alongside:**
    - Gameplay was identical, tick for tick, with and without the probe.
    - The captured telemetry scores the same offline.
    - The secret reproduces the plan and the realization the server ran.
    - No secret or realization material appears in any public output.
  - **Committed:** the result (`fpsdet.pilot/1`), the captured telemetry and the plans. It joins the benchmark as an addendum after Benchmark v1 (`fpsdet.benchmark-addendum/1`) with its own class, `live_controlled_pilot`; Benchmark v1 is unchanged.
- **`occluded_motion_replay/2`: the server's verdict at every moment** ([docs/challenges.md](docs/challenges.md#version-2-the-servers-verdict-at-every-moment)). Version 1 took the body as hidden because the type requires it.
  - **New fields:** version 2 reads `challenge_vision_state` and `challenge_audio_state`, the server's own verdict on the challenge's body over each event, never on the event's enemy.
  - **What counts:**
    - A moment counts only when that verdict makes the body unknowable.
    - One moment seen or heard voids the challenge, and so does a contradiction, with a note.
    - Missing verdicts abstain.
  - **Defaults:** `fpsdet challenge plan` writes version 2 (`--version 1` reproduces old plans). Version 1 is judged exactly as before, so Benchmark v1 reproduces; its synthetic class reports PROVENANCE_ONLY.
  - **Input recipe:** a timeline that sets the new fields is digested as `fpsdet.player-events/3`; `/2` keeps its meaning.
- **FPSDET Benchmark v1** ([docs/benchmark.md](docs/benchmark.md), [benchmark/release.json](benchmark/release.json)): the benchmark rebuilt so a third party can reconstruct it from public definitions, with no private key, and so its TF2 result reports how much it depends on the baseline draw instead of quoting one draw. Benchmark v1 is a version of the benchmark, not of fpsdet.
  - **`tf2-rgl-v2`**, built by `examples/tf2/tf2bench.py` with no key: sources frozen at 2026-10-03, extra never-banned players taken in a public hash order, public benchmark ids, and nine baseline draws (`fpsdet.tf2-baseline-draw/1`, draws 0-8). The draws, the draw count and the headline rule were committed before any draw was run. A benchmark id is a public hash of a SteamID, not pseudonymization, so nothing per player is committed for it: per draw, a result, the aggregate statistics without rows, and the strength estimates.
  - **The TF2 result is the spread** (`fpsdet.benchmark-sensitivity/2`): every draw's inputs, cohort, decisions, eligibility, evaluation and strength by digest, its numbers, and the median, lowest and highest of each, per detector too. Nothing is averaged, and verify fails if a draw is dropped, added, reordered or edited. Over the nine draws, fpsdet flagged 28.0–29.1% of RGL cheating-ban labelled accounts (median 28.6%) and 2.8–3.7% of never-banned ones (median 3.2%): a ratio of 8.9x at the median, from 7.7x to 10.4x. The rank tail replicated on one split in 9 of 9 draws, accuracy in 5, headshot rate in 3. The draws vary only the baseline half, not which 300 extra never-banned players were chosen, and the report says so: tf2-rgl-v1, with another such set, ran from 9.9x to 15.0x.
  - **`cs2cd-v2`** fetches CS2CD at one pinned Hugging Face revision with `examples/cs2/cs2fetch.py`, checks every event file byte for byte and every tick table's LFS digest against `benchmark/sources/cs2cd-v2.json`, and stops if the revision is missing instead of taking the latest. A rebuild from an empty cache gave every committed input and output byte for byte.
  - **SOURCE_CHANGED:** prepare compares a download with the frozen source part by part (the bans, the logs, the selections; the CS2 fetch receipts) and names the digest it expected and the one it found. Drift is classed as code, source, selection or identity changed.
  - **Claims and release:** every public statement is generated with one class (`controlled_fixture`, `benchmark_v1_real_data`, `historical_real_data`, `architecture_limit`, `unmeasured`) in `benchmark/claims.json`; the README's TF2, detector and limits blocks print them with their class. `benchmark/release.json` (`fpsdet.benchmark-release/1`) binds the dataset versions, expectations, spreads, capability matrix, claims and report by digest. The capability matrix says in how many draws a TF2 replication held.
  - **Kept:** `tf2-rgl-v1` and `cs2cd-v1` stay as the historical record and still verify; their pages say so. The published TF2 draw (15.0x) is quoted only as one historical draw.
  - **CI:** ordinary CI adds the key-free reproduction test. A manual workflow (`.github/workflows/benchmark-cs2.yml`) rebuilds CS2 from an empty cache. TF2 has none: it takes about 11,000 requests to logs.tf, whose robots.txt asks crawlers to stay off its API.
- **A reproducible benchmark** (`fpsdet benchmark`, [docs/benchmark.md](docs/benchmark.md)). The fixtures, scenarios and both labelled real-data runs as one chain a third party can rebuild or check: source data, prepared input, profile and cohort, scored packets, evaluation, strength, report. Three classes, never one score.
  - **Manifests** (`fpsdet.benchmark-dataset/1`) record each dataset's source, what in it can change, licence, pseudonymization, label semantics, the exact fetch and prepare commands, the frozen selection rules with the converter's digest, and the digests the prepared inputs must have. The TF2 pseudonym key is bound by a commitment, never published.
  - **Commands:** `fetch` (network, the converters' rate limits), `prepare` (offline, rebuilds the inputs and reports any difference), `run` (offline: scores, verifies every packet and graph, evaluates, estimates strength, writes a split manifest and a result), `verify` (every committed artifact and its chain, without scoring) and `report`.
  - **Results** (`fpsdet.benchmark-result/1`) bind every step by digest; environment and timings are reported, never part of the identity. Against the pin (`fpsdet.benchmark-expected/1`) a run is MATCH, ENVIRONMENT_ONLY, PROVENANCE_ONLY, PRESENTATION_ONLY, DRIFT, INPUT_CHANGED or NOT_RUN, with the categories that explain it.
  - **Generated, not maintained:** detector qualification, the coverage matrix (observable or not, never a zero), the capability matrix (each claim tied to fixtures, scenarios, tests or an architecture statement, its evidence worked out), and the README's "What still gets through".
  - **Controlled scenarios as data:** ten challenge-aware behaviours and ten external-signature states, deterministic, labelled controlled synthetic qualification.
  - **Reproduced:** the TF2 and CS2 converters rebuild every published prepared input byte for byte from the cached downloads, and both runs give the published decisions, evaluation rows, statistics and strength estimates exactly. The synthetic class has one identity on Python 3.11 and 3.12. CI runs `benchmark verify`, the synthetic run against its pin, and `benchmark report --check`; the real-data runs need data CI does not have.
  - **What moved:** the published evaluation and strength files now bind the benchmark's own run, so their provenance digests moved; no number did. The CS2 baseline is the one `benchmark prepare` rebuilds (`sha256:143ce6f0…`): the earlier one had been screened with settings the README did not record. The TF2 README's fetch command now passes `--per-account 20`, as the published run did.
- **Detector eligibility** ([docs/observations.md](docs/observations.md#detector-eligibility)). Every case now says why each native detector could or could not run, per unit, in `evidence.detector_eligibility`, so a detector that ran and found nothing is no longer indistinguishable from one that never could.
  - **Closed statuses:** `eligible`, `baseline_too_thin`, `insufficient_samples`, `conflict`, `telemetry_unavailable`, `disabled`, `not_applicable`. Firing is read from the observations: every native finding must name an eligible unit.
  - **Per unit:** a weapon, a recoil build, a declared metric and group, a rank comparison, a challenge or a party, and `""` for the account. A player-level rollup is the unit that came closest to running.
  - **Recorded by the scorer as it goes**, from the state each check reads. Nothing reads it back. The reducer gained counters for the information checks and recoil pitch reports; no finding uses them.
  - **`fpsdet.packet/5`** binds it, and checking one also checks every finding against it. Every new case uses it. `packet/1` to `/4` keep their meaning, and `tests/fixtures/historical-packets-p8.json` keeps a `packet/4` case with signed external records verifying. The graph, observation ids and seals do not include eligibility.
  - **What moved:** no decision, reason, observation id or seal in the planted demo, the synthetic week or either real-data run. Every case gained the block, and every packet moved to `packet/5`. On TF2 and CS2, the units eligibility records as able to run are exactly the ones P8's evaluation inferred, player for player.
- **Offline evidence strength** (`fpsdet evaluate strength`, [docs/calibration.md](docs/calibration.md#evidence-strength-offline-research)). Research only: the scorer never imports it, and nothing it writes is a threshold, a weight or a probability that anyone cheated.
  - **The question:** using only a frozen development half (`fpsdet.calibration-split/1`, by account for TF2 and by match for CS2), how much more often did a detector fire for positive-labelled players than for comparison players, among players it could run on? And did the untouched evaluation half agree?
  - **Declared before any estimate:** a Jeffreys prior per group; the label-conditioned evidence ratio of posterior means, finite with no comparison fires; a credible set of at least 95% from two √0.95 intervals; P8's gates. Stability is `unsupported`, `unstable`, `tentative` or `replicated` against the development posterior predictive. A uniform prior and the reverse split are reported as sensitivity only.
  - **Never combined:** estimates are per detector, or per family as its own event. Co-occurring pairs are tagged from the evidence graph instead.
  - **Not calibrated:** challenges, external providers, human reviewers, and anything a dataset cannot observe.
  - **`fpsdet.strength/1`** binds its evaluation by digest, the dataset definition, split, estimator and code. `examples/tf2/strength.json` and `examples/cs2/strength.json` are published beside reports. On TF2 the rank tail and the human-baseline family replicate (rank tail 14.3, 95% credible 5.2–42); accuracy replicates on 5 and 6 fires with a prior-set far end; headshot rate and kills per minute are unsupported. On CS2 the rank tail replicates on 7 and 5 fires; everything else is unsupported or too thin.
- **`fpsdet.evaluation/2`.** `fpsdet evaluate` reads each detector's denominator from the case's own eligibility instead of inferring it, and counts coverage by why a player was not eligible. `/1` still verifies. On both published runs every rate, interval, ratio and table is identical to P8's; coverage is finer, splitting P8's "below the sample minimum" into too few samples and no telemetry.
- **Controlled fixtures for every detector** (`fpsdet.fixtures`, [docs/calibration.md](docs/calibration.md#controlled-fixtures)). A second fixture world beside the planted demo: 40 humans frozen into a baseline, a plant and an honest twin for each detector the demo never planted (recoil learned, geometry rate, both declared numbers, view snaps, acquire timing), honest twins for fire rate, metronome and account jump, and a probe for each eligibility status the demo never reaches. `fpsdet evaluate fixtures` runs both worlds: all 22 native detectors now pass controlled-fixture qualification. It is never a rate.
- **Detector measurement** (`fpsdet evaluate`, [docs/calibration.md](docs/calibration.md)). On one labelled run: how often each detector fired in the positive-labelled group and in the comparison group, among the players it could run on, and how sure that is. Measurement only: no threshold, role, rule or decision moved, and none was chosen from these numbers.
  - **Labels first:** [docs/calibration.md](docs/calibration.md) says what TF2's RGL labels and CS2CD's hand labels mean and do not mean, before any number. Each dataset definition (`fpsdet.evaluation-dataset/1`) says the same, and every report prints it first. Synthetic players are never mixed into a real-world rate.
  - **Who counts:** a player enters a detector's rate only when the case shows it could run on them. Coverage splits the rest into below the sample minimum and thin baseline. A detector the data cannot feed is `not_observable` with a machine-readable reason (missing telemetry, no server shot timing, no profile rule, no history, eligibility not recorded), never 0%.
  - **Numbers:** count, denominator, rate and a two-sided 95% Wilson interval; no rate under 20 players. Ratios against each comparison group have no infinity and no pseudo-count. Also: queue share against the chance level, decisions by evidence amount, families, strata by weapon, skill band and matches, co-occurrence with what the evidence graph says two findings share, and fixed pseudonym splits. Statuses are `not_observable`, `insufficient_sample`, `descriptive_only` and `measured`, never "calibrated". No p-values.
  - **The artifact** (`fpsdet.evaluation/1`): one unsigned digest binds the cases' packet digests, the labels, the profile, detector and cohort digests, the configuration, the evaluator's own source and every statistic. `fpsdet evaluate verify` recomputes them from the per-player rows. Cases that do not verify, mixed runs, the wrong profile, unexplained labels, a telemetry declaration the event census contradicts and published decisions that no longer reproduce all stop the run.
  - **Published:** `examples/tf2/evaluation.json` and `examples/cs2/evaluation.json`, with Markdown reports. Tests tie the READMEs' numbers and the desks to them.
  - **Controlled fixtures:** `fpsdet evaluate fixtures` checks that each planted behaviour trips its detector and each honest twin stays quiet. It is never a rate.
  - **What moved:** no case. Re-scoring both real-data runs moved only the detector fingerprint, because `provenance.py` lists the new module as not detection, and the packet digest that binds it.
- **Authenticated external evidence** (`fpsdet.auth`, [docs/external-authentication.md](docs/external-authentication.md)). fpsdet can tell a record that says it is from a provider from one that provider's registered key signed.
  - **Ed25519, optional:** verification uses the `cryptography` package (42 or later) as the `auth` extra. Everything else stays standard library only; without it, signatures are reported as not checked.
  - **The registry** (`fpsdet.provider-registry/1`): the operator's public keys by provider and key id, each `active`, `retired` or `revoked`, read strictly and with no private material. Its digest ignores order and moves with any change of trust. A record never brings its own key.
  - **Signed records:** a `fpsdet.external-signed/1` envelope around the provider's claim. The signed bytes are `fpsdet.external-signature/1`, a zero byte, and the canonical claim. They are verified before any adapter maps the claim, as the provider it will be read as, so no adapter can change a signed field or carry a signature into another provider's identity.
  - **States:** `unsigned`, `not_checked`, `unknown_key` and `verified` are read; `invalid`, `revoked_key` and `unsupported_algorithm` are refused and never become evidence. A retired key still verifies what it signed. The same claim keeps one identity and its strongest signature, and each signature is checked once.
  - **In the case:** each external observation's `authenticity` names the state, key and registry; `provenance.external` names the registry and policy; `fpsdet.graph/2` adds `provider_key` nodes with `authenticated_by` and `belongs_to` edges; `fpsdet.packet/4` binds the registry and policy. Older graphs and packets keep their meaning and verify.
  - **Commands:** `fpsdet score --external-registry` and `--require-signed-external`; `fpsdet external verify`.
  - **Policy unchanged:** a verified record makes the same watch an unsigned one does, and never a review. Unsigned records still work by default.
  - **What moved:** with no external input, no decision, reason, observation, id or seal in any data set. Every graph moved to `graph/2` and every packet to `packet/4`. In the external fixtures, every decision, reason, seal and fusion block is the same; external observation ids moved, because `authenticity` now says `unsigned` instead of `unverified`.
- **The evidence graph** (`fpsdet.graph`, [docs/evidence-graph.md](docs/evidence-graph.md)). Every case carries `evidence.graph`: what each piece of evidence is, where it came from, what it depends on, and what it shares with the rest.
  - **Closed model, standard library only:** ten node types (case, player, match, observation, challenge, external record, provider group, telemetry domain, cohort, history) and ten relations (`about`, `occurred_in`, `supports`, `depends_on`, `derived_from`, `names_partner`, `provided_by`, `uses_telemetry_domain`, `compared_against`, `uses_history`), each between fixed node types. Nothing in data can add a type, a relation or a node; provider text never enters the graph.
  - **Built from the case:** `depends_on` exactly from `Observation.depends_on` (across cases as out-of-case references), partners from relationship evidence, a challenge's public identity (never its secret), external records with their provider groups and telemetry domains, and cohort and history identities from provenance. Reports stay out.
  - **A summary, not a score:** what each observation is and comes from, and every provider group, telemetry domain, record, challenge, dependency and partner that observations share. It is descriptive and changes no decision. Different names, groups or domains are not assumed independent.
  - **Identity and verification:** `fpsdet.graph/1`, order-independent and the same on every Python. `verify_graph` recomputes observation ids, rebuilds the graph from the case and checks every relationship against its evidence and provenance; `verify_graphs` checks dependencies across a run.
  - **`fpsdet.packet/3`:** `packet/2` plus the graph's recipe and digest, checked with `verify_graph`. Every new case is written with it; `packet/1` and `packet/2` keep their meaning and old packets verify.
  - **What moved:** no decision, reason, observation, id or seal in any data set. Every case gained its graph, and every packet moved once, to `packet/3`.
- **External evidence** (`fpsdet.external`, [docs/external-evidence.md](docs/external-evidence.md)). Records from other integrity systems can sit beside fpsdet's own evidence without pretending to be it.
  - **The record:** provider and group, source class (`client_integrity`, `platform_attestation`, `account_status`, `tournament_finding`, `human_review`, `custom_detector`), telemetry domain, the provider's own kind, direction (adverse, favorable, context), the player, an optional match and window, and the provider's confidence exactly as given with its declared scale and meaning. Nothing is turned into an fpsdet probability. Every record is `unverified`: a digest is not a signature.
  - **Formats:** the native `fpsdet.external/1` (with a JSON Schema), and data-only adapters (`fpsdet.external-adapter/1`) of key paths, constants and an allowlist that cannot run code or transform values. Three fictional examples are in `examples/external/`. No adapter for any commercial anti-cheat exists.
  - **Untrusted input:** bounded files, lines, strings, metadata and record counts; strict JSON; bad lines skipped and named by file, line and reason, never by value; `--external-strict` stops instead. Native scoring is never affected.
  - **Fusion, by explicit rules:** an adverse signal scoped to a scored match moves a clean or `insufficient_data` case to watch; a watch stays a watch; a review stays a review. Any number of providers is still a watch, account status is context only, and a person's ruling is kept as theirs. Reports are never read. The native decision is recorded beside the final one, and `implied_decision` rebuilds both.
  - **Its own family, source and roles:** `external`, with `external_watch` and `external_context`, so no external record ever joins a native rule. No provider string reaches case text, dashboards or an AI prompt.
  - **Provenance and packets:** a per-player `fpsdet.external-input/1` digest and the run's sources; `fpsdet.packet/2` binds them and the fusion state. Every new case is written with `packet/2`; `packet/1` keeps its meaning, and old packets verify.
  - **What moved:** with no external input, no decision, reason, observation, id or seal moved in any data set. Every packet moved once, to `packet/2`.
- **Active challenges** (`fpsdet.challenge`, [docs/challenges.md](docs/challenges.md)). The private replay is now a server-planned challenge: one player, one match, one window, one id.
  - **Planning:** `fpsdet challenge plan` derives each challenge's public id, its window and its secret realization from a server-held key with HMAC-SHA256 (`fpsdet.challenge/1`, framed and domain-separated). It writes only ids, windows and a SHA-256 commitment to each realization. `keygen` writes a new owner-only key; `verify` checks a plan file without the key, and with it shows the key plans exactly that file; `types` prints the challenge types.
  - **The secret:** read from a file the operator names or `FPSDET_CHALLENGE_SECRET`, never from a profile. Only `fpsdet.challenge_plan` reads it, and scoring never imports that module. It prints as redacted, is never pickled, and is never in a plan, case, packet, dashboard, AI prompt, error or command output; tests search all of them.
  - **Budget:** at most 4 challenges per player per match, a cooldown between them, windows long enough to reach the bar, and no challenge planned against a knowledge channel it does not defeat.
  - **Event fields `challenge_id` and `challenge_track_ms`:** a response counts only for the exact challenge it names, planned for this player, in its match, inside its window, when no real enemy the client could know explains the aim, and when the knowledge engine says the target was unknowable. Every gap or contradiction only takes samples away.
  - **Evidence:** a followed challenge is a review, one observation in the new `challenge` family, kind `occluded_motion_replay`, bound to its plan by id, plan digest, commitment and window. Challenges never add up: each is judged alone, and every planned one is listed in `evidence.challenges`. `fpsdet score --challenges` reads the plans; `fpsdet challenge verify --cases` checks findings against them.
  - **The legacy field:** `private_track_ms` scores exactly as before on events that name no challenge, and its finding is now an `occluded_motion_replay` observation with a visibly legacy identity (`legacy_private_replay:<aim key>`, no plan, window or commitment).
  - **What moved:** the six private replay observations in the planted demo and the synthetic week changed family, kind, key, evidence and id, deliberately. No decision, reason or seal moved anywhere, and CS2 and TF2 moved only their packets. The retired `private_replay` kind stays readable, so earlier packets still verify.
  - **Limits, said plainly:** pixel aimbots, software that ignores hidden players, reactions too brief to clear the bar on one challenge, and following only next to a visible enemy all get through.
- **The client knowledge engine** (`fpsdet.knowledge`, [docs/knowledge-engine.md](docs/knowledge-engine.md)). Every information check now asks one question: could this client know this enemy at this moment? The answer is `known`, `unknowable` (every channel the game declares was checked and failed) or `unknown`.
  - **The rule:** unchecked is never absent. A declared channel nobody reported, or telemetry that contradicts itself, means `unknown`, and the checks abstain. Missing telemetry can only make fpsdet say less.
  - **Profile field `knowledge_channels`:** defaults to vision and audio, which is what `unknowable` has always meant. A game with a radar or teammate callouts can declare those, and the checks then wait until its server reports them.
  - **Event fields `vision_state` and `audio_state`:** report one channel at a time, including "not checked".
  - **Checks routed through it:** hidden mover, private replay, quiet aim and teammate contacts. The legacy `information_state` maps onto it exactly.
  - **Evidence:** each information finding's context records the knowledge behind it and the samples kept out.
  - **Contradictions:** contradictory telemetry is named on the case.
  - **One change on purpose:** a shot labelled visible that also carries hidden-mover time no longer counts; no case in any data set had one.
  - **Otherwise unchanged:** no decision, reason, observation id or seal moved. Profile digests, and so packets, moved because profiles gained the field.
- **Input provenance follows the scorer: `fpsdet.player-events/2`.**
  - **What changed:** the input digest is now taken over each player's canonical timeline, the same list the scorer reads, so the same events in any file order have one digest. Duplicates still count.
  - **Old packets:** `fpsdet.player-events/1`, the arrival-order recipe, keeps its meaning (`arrival_digest`). Packets written with it still verify, and nothing writes it any more.
  - **Packet recipe unchanged:** `fpsdet.packet/1` binds the input recipe by name, so it stays.
  - **Proof at scale:** shuffling all 4.1 M TF2 events gives 2,764 of 2,764 identical cases, digests and packets included.
- **One evidence-packet digest per case, and the account history behind it.**
  - **`evidence.provenance.history`:** the account-history windows the scorer could read for this player, as a sorted multiset (`fpsdet.history/1`), or `{"mode": "none"}` when the run had no history. The account check and the digest share one rule, `score.history_for`, so they cannot drift apart.
  - **`evidence.packet`:** a SHA-256 over the subject, game, decision, eligibility, every observation id, and the detector, profile, cohort (with its mode and integrity stamp), input and history digests (`fpsdet.packet/1`). Reason wording, context lines, the AI brief, reports and queue state are not in it.
  - **No placeholders:** a case scored outside a run gets `"status": "incomplete"` and no digest.
  - **`fpsdet.provenance.verify_packet`** checks a serialized case on its own. It recomputes every observation id from its contents first, so an edited value is caught even if the id is copied, then rebuilds the packet. `tools/regress.py verify` runs it on every case: all 1,529 CS2 and 2,764 TF2 packets verify.
  - **Not a signature:** anyone who edits a case can recompute the digest. It proves content identity against a stored digest, not who produced the case.
  - **Unchanged:** the seal and every other field.
- **Evidence provenance: which baseline and which events.** `evidence.provenance` now also has `cohort` and `inputs`.
  - **`cohort`:** a SHA-256 of every band, key, metric, player id and value in the baseline the run used. Its `mode` is `external` for a baseline given to the run, or `in_file` for one fitted on the events being scored. Its advisory integrity stamp (status, alarms, matches left out) has a separate digest and its status in plain text.
  - **Cohort files:** `fpsdet baseline` writes both digests into the cohort file. Loading recomputes them, and a file whose values or integrity stamp were edited afterwards is refused. Older files still load and are marked `stored_digest: absent`.
  - **`inputs`:** a SHA-256 of the subject player's own parsed events, in the order the scorer read them, with event and match counts. Each case has its own; one changed event moves only that player's digest.
  - **Not yet covered:** other players' events behind a relationship observation, player history, and one identity over the whole packet.
  - **Nothing else moves:** observation ids, the seal and every other field are unchanged.
  - **Cost:** input digests cost about 2 µs per event, about 4.5 s on the 4.1 M-event TF2 run.
- **Evidence provenance: which detector and which profile.** Each case's `evidence` now carries `provenance`, shared by every case of a run.
  - **Profile:** a SHA-256 of the game profile as the scorer parsed it. Spacing, key order, notes and values the parser reads as the default do not change it.
  - **Detector:** a SHA-256 of the source of the 12 modules that can change a finding (the package `__init__` included, since it runs on every import), listed by name. Dashboards, the desk, the site, the command line and AI prose are outside it, and a test checks the list against the scorer's imports so a new detection module cannot be left out.
  - **Identical everywhere:** both digests are the same on Python 3.11 and 3.12 and in any checkout folder.
  - **Nothing else moves:** observation ids, the seal and every other field are unchanged.
  - **Not yet bound:** the events scored and the cohort. [docs/provenance.md](docs/provenance.md) says what it proves and what it does not.
- **Structured evidence on every case.** Case JSON has a new key, `evidence`. Every finding that feeds a decision is also kept as data there: the detector, its evidence family, the role the scorer gives it, the matches, and the numbers it compared (the bound and its method, the rank's line and the best human's, sample and cohort sizes, the thresholds). Nothing else changed:
  - The sentences, check ids, decisions and seals are unchanged.
  - The decision the evidence implies matches the scorer's on every planted case, on all 400 synthetic-week players (weekly and nightly), and on the real CS2 (1,529) and TF2 (2,764) reruns.
  - The 78 TF2 watches that have no reason line are now explained by their evidence.
  - `ops.json`, the desk and the AI brief do not read it yet.

  The model is `fpsdet.evidence`; [docs/observations.md](docs/observations.md) maps every detector. `tools/regress.py verify` runs the same check on a real-data rerun.
- **Architecture map** (`docs/architecture-v1.md`): the code as it is, every check by evidence family, the trust boundaries, what the seal can and cannot answer, the debt that blocks structured evidence, the proposed interfaces (observation, provenance, knowledge state, challenge, external evidence, evidence graph), and the order of work. Proposed parts are marked proposed; none of them is in the code yet.
- **Whole-case behaviour lock** (`tests/test_golden.py`). Every field of every planted case, and of all 400 players in the synthetic week, weekly and nightly, is recorded in `tests/golden/`; so are the committed CS2 and TF2 results by label. A change that moves any of them fails and names the player. CI runs it on Python 3.11 and 3.12.
- **`tools/regress.py`** snapshots a scored run and diffs two snapshots case by case: a new field is allowed, a moved one fails. It is the regression check for the real-match runs, which are too large to commit. Re-scoring the local CS2 and TF2 inputs reproduces both committed desks player for player.
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
