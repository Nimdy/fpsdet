# Scoring

The reference implementation is `src/fpsdet/`. A port in C#, C++, or Go is compatible when it reproduces `python -m fpsdet demo` and `tests/test_fpsdet.py`.

`automated_action` is always `none`. `recommended_action` is `human_review`, `monitor`, or `none`.

`checks` lists the ids of what fired, so a dashboard can group cases without reading sentences. The ids are:

| Family | Ids |
| --- | --- |
| Gear rules | `speed`, `fire_rate`, `metronome`, `recoil_floor`, `recoil_learned`, `mirror` |
| Human baseline | `accuracy`, `headshot_rate`, `median_distance`, `geometry_rate`, `extra`, `account_jump`, `rank_tail` (the watch-grade tail), `supporting` |
| Information | `hidden`, `quiet_aim`, `private_replay` (a challenge, below), `wire` |
| Batch | `leftover`, `voice` |

The labels and families are `fpsdet.models.CHECKS`.

## Event order

The order lines arrive in a file is not game state. Each player's events are read in one canonical timeline: match, server time, kind (movement before shot), `spray_index`, then the event's own content for events those cannot tell apart. Players are read in id order. Weapons, recoil builds and declared metrics are reported in key order. Events at the same match and time are simultaneous; each check below says what that means to it. [event-normalization.md](event-normalization.md) has the full rules.

## Cohort

A cohort is one number per player, not one number per shot. The keys are rank band, weapon key, and metric.

Rank comes from the matchmaker (`skill_band`, or `skill_prior` cut at 0.30 / 0.70 / 0.95 into developing, average, advanced, elite). It is the band they queued in. It is not computed from the stats under test. A line with neither field is `unrated`. A server with no matchmaker, such as a community server, is one `unrated` band: every player is compared with everyone else on that server, and the case says so.

Weapon key is `weapon_class` unless the profile sets `"aim_group": "weapon_id"`. Recoil and builds use `weapon_id|mod,mod` with mods sorted. That is the build key.

The player being scored is removed from each distribution before the percentile is taken (leave-one-out). A frozen cohort from last week is the production path. Fitting the cohort on the same file as the players under review is allowed for a demo and is stamped with a warning.

Percentile is nearest-rank: sort ascending, rank = ceil(q × n), 1-based, clamped. Median of an even count is the mean of the two middle values.

A distribution is thick enough when it still has `min_cohort_players` values after removal (default 30). A thin distribution does not flag. The build is reported as untrained.

The human ceiling is the highest band that is thick for that metric (elite, then advanced, then average, then developing, then `unrated`).

How many players that takes: `min_cohort_players` (default 30) per band and weapon key, each with at least `min_shots` (default 40) shots on that weapon in the window. A community server with 40 regulars and no ranks trains one band per weapon in a week or two. A ranked game needs 30 per band before that band is its own ceiling. Until then the build is listed as untrained and nothing on it is flagged.

## Rates

Accuracy is hits / shots. Headshot rate is head hits / hits whose hitbox was sent, and it is skipped unless at least `min_hits_for_headshot` hits have a hitbox and those cover at least 90% of hits. Geometry rate is the same shape.

The bound is the one-sided 95% Wilson lower bound, z = 1.6448536269514722.

Shots are not independent. A player who has one hot match moves every shot in it together. When a player has at least 5 matches on that weapon, the per-match rates give a design effect: the Pearson dispersion `sum((k - n·p)^2 / (n·p·(1-p))) / (matches - 1)`, floored at 1. The Wilson bound is taken on `hits / deff` successes out of `shots / deff` trials. One hot match widens the bound; ten steady matches leave it alone. With fewer than 5 matches the plain bound is used. A design effect of 1.5 or more is written on the case.

- Past the rank: lower bound > the rank band's p95.
- Past the best measured human: lower bound > the highest value among all thick bands. For accuracy that is almost always the top band. For numbers that rank does not order, such as distance, it is whichever band holds the most extreme human.

Past the best human is the review-grade flag. Past the rank only is a watch-grade flag. A hot 10-shot game has a wide bound and does not clear a maximum.

## Continuous numbers

Distance and declared `extra_metrics` use the player's median, tested on a one-sided 95% distribution-free bound from order statistics: the value `floor((n - 1.645·√n) / 2)` places below the middle (or above it, for the low direction). A night of 30 shots gets a wide bound. A week of 300 gets a tight one.

- High direction, past the rank: lower bound > own p95. Past humans: lower bound > the highest value among all thick bands.
- Low direction, past the rank: upper bound < own p05. Past humans: upper bound < the lowest value among all thick bands.

Recoil medians use the upper bound in the same way, because low recoil is the finding.

Past humans means past every human measured, the same bar the rates use. The top band's p95 is crossed by one player in twenty on every metric, and the best players are good at all of them at once. An `extra_metrics` row has one distribution (its `group_by`), so its tail is p95/p05 and past humans is its maximum/minimum.

View-angle p95 and acquire-time median are supporting tells. Acquire median and acquire standard deviation count as one family, so a single timing habit cannot masquerade as two findings. They are only computed when you send `view_delta_deg` or `acquire_ms`, and `acquire_ms` is only meaningful when the server actually knows the target became visible. Omit it otherwise. Sound, utility, and a pre-aimed angle are not reaction time.

## Decision

Count the kinds of number that are past every human (accuracy, headshot rate, median distance, geometry rate, each primary extra) and the watch-grade flags (`beyond_band`). The same kind on two weapons is one kind. The best human in a population is usually the best on every gun, and leave-one-out compares them to the second best each time.

| Condition | Decision |
| --- | --- |
| Sustained gear-rule break (speed, fire interval, blatant or learned recoil, a same-tick recoil mirror, a metronome on a legal cycle, sustained tracking of a hidden mover, sustained aim on a private replay, aim that matches the wire snapshot ahead of the picture, or aim noise that drops only while the server says this client could not have known) | review |
| Two or more kinds of number past every human | review |
| One kind past every human, and the account broke its own history or a supporting tell agrees | review |
| Any watch-grade flag, one kind past every human, an account-history break, or two supporting families | watch |
| At least one metric was actually compared, and nothing above fired | clean |
| Nothing was comparable (short sample, thin cohort, no cap) | insufficient_data |

Those are the native decisions. Then, only when the run was given external records, they meet fpsdet's own decision by explicit rules ([external-evidence.md](external-evidence.md)): a qualifying external signal makes a clean or `insufficient_data` case a watch; a watch stays a watch; a review stays a review. External evidence never makes a review, and reports never take part. A record signed by a registered provider key ([external-authentication.md](external-authentication.md)) is treated exactly like an unsigned one by these rules: a signature says who made the claim, not that it is true.

Every case also carries an evidence graph ([evidence-graph.md](evidence-graph.md)): what each observation depends on and what sources observations share. It is built after the decision, from the finished case, and nothing reads it to decide anything.

An account-history break is the same weapon and the same rank band: Wilson lower bound of this window (with its design effect) minus Wilson upper bound of the player's own earlier windows is at least `self_jump_gap` (default 0.10), and both windows have at least `min_shots`. A rank change does not count, because the band no longer matches.

## Speed

Eligible samples are on the ground, `displacement_cause` is `none`, and a cap exists.

Cap order: `expected_max_ground_speed_mps` on the sample, else the weight-class curve. A class with `max_ground_speed_mps` uses that absolute number. A class with only `speed_multiplier` uses `reference_lightest_speed_mps × multiplier`. If neither resolves, the sample is skipped and the case says the cap is unset. Nothing is flagged.

A sample is over cap when speed > cap × (1 + `speed_over_fraction`). Default fraction is 0.08, for animation jitter.

A run is consecutive over-cap samples in one match with gaps no larger than `speed_run_gap_ms` (default 400). The finding fires when the longest run is at least `speed_min_run` (default 25). Shorter runs are recorded as a possible glitch and do not change the decision.

A count of samples is a different length of time on every emitter: 25 samples are 2.5 s at 10 Hz and 0.4 s at 64 Hz. A profile can measure the run in time instead. Set `speed_min_run_ms`, and the finding fires when the longest run spans at least that many milliseconds from its first moment to its last; `speed_min_run` is then not read. It needs `movement_clock: "server"`, the emitter's word that movement `t_ms` is the server's own clock. Without it the speed rule abstains (`disabled`) rather than measure time on a clock nobody declared. Neither field set is today's sample count, and such a profile keeps its digest.

Samples at the same match and time are one moment. A moment extends a run by its number of samples only when every sample in it is an eligible ground sample over its cap. Any other sample at that moment breaks the run there, whichever order the file listed them in.

Only `displacement_cause` of `none` is scored. Innocent causes, `unknown`, any cause the profile has not listed, missing `on_ground`, and airborne samples break a run and are counted in the case so a reviewer can see them. They are not violations. A new traversal gadget is safe until you decide it is a normal sprint.

## Recoil

Samples count when `spray_index` is at least `recoil_min_spray_index` (default 3) and the cause is not an innocence tag. Taps and the first shots of a spray are ignored. The floor is `expected_min_recoil_pitch_deg` on the shot, else the profile row for that build.

Blatant: a run of at least `recoil_min_run` shots (default 10) whose pitch is under `recoil_floor_fraction` of the floor (default 25%). That is a review by itself. A compensated gun has a lower floor, so the same pitch can be legal.

Shots on one build are ordered by match, time and `spray_index`. Shots that share all three are one moment: it extends a low run only when every shot in it is an eligible low one.

No floor and no thick cohort: untrained, not flagged.

No floor, thick cohort, and the upper bound of the player's median pitch is under the lowest measured human and under `recoil_floor_fraction` of that cohort's median: learned floor break, review. Sitting in the low tail (under p05) without clearing that gap is only a watch-grade flag. The steadiest legal player on a build is not a review.

## Fire interval

Per match, per weapon id (or weapon class when the id is absent). Gaps under `min_shot_interval_ms - interval_slack_ms` are violations. A match counts when it has at least 5 gaps and at least `min_violation_rate` of them are violations. The finding fires when the matches that count hold at least `min_intervals` gaps and `min_violations` violations between them. One jittery timestamp in an honest match does not make the match count, even in a match where the gun fired only two or three times. Two shots of one gun at the same time are a zero gap, whichever the file listed first. A macro switched on in the middle of a week is not diluted by the honest matches before it. This is checked even when the aim sample is still too small to score.

Set the interval to the minimum legal gap between accepted shots. For a burst weapon that is the intra-burst gap, or leave the rule off for that weapon. Slack covers one tick of timestamp jitter. Timestamps are server time.

## Same-tick recoil mirror

The no-recoil rule sees the camera. A script can cancel the kick and write a legal pitch back, so `recoil_pitch_deg` stays above the floor. Send `applied_recoil_pitch_deg` (the kick the server applied) and `compensation_pitch_deg` (the signed player command on that tick, negative for a pull-down). Both are required. Spray index does not gate this pair. The first shots count.

Review when there are at least `mirror_min_shots` (default 16), the Pearson correlation of the command against the applied kick is at or below `mirror_max_r` (default −0.90), and that same-tick correlation is at least `mirror_lag_gap` (default 0.25) more negative than the correlation at `mirror_lag_shots` (default 1). A flat kick has no variance and is not a mirror. A person who pulls down does it on a later shot, so the lagged correlation is the tighter one and the case stays quiet.

That is only true of a kick nobody can predict. In a game with a fixed spray pattern (CS-style, Valorant-style), practiced players memorise it and pull on the same tick. On the raw kick they look exactly like the script. So the test runs on what is left after the pattern is removed:

1. Send `spray_index` (0 for the first round of a spray) with the kick and the command.
2. For every spray index reached by at least 3 sprays, subtract the mean kick and the mean command at that index.
3. Run the test above on those leftovers, in shot order, when at least `mirror_min_shots` remain.

The leftover kick is the part that changes from spray to spray. Nobody can anticipate it. A script that reads the kick still cancels it on the same tick, so its correlation stays near −1. A pure fixed pattern with no randomness leaves nothing to test and the check stays quiet; that game has to rely on the recoil floor.

When the sprays are too few, or `spray_index` is missing, the profile decides. `recoil_pattern: "learnable"` (the default) skips the check and writes why on the case. `recoil_pattern: "random"` runs the test on the raw kick, which is right only when every kick is drawn fresh (Tarkov-style). A wrong `random` frames every player who learned the pattern.

## Metronome

Fire interval catches gaps under the legal line. A macro that fires on the legal line, with no motor noise, does not. Only the cadence counts: gaps no longer than 2.5 times `min_shot_interval_ms`. Longer gaps are pauses between bursts. They vary the way a person's do, and they would hide a perfect cadence. Each match is judged on its own. A match counts when it has at least 5 cadence gaps, their mean is above the server's pace, and their sample standard deviation is at or under `metronome_max_std_ms` (default 1.0 ms). Review when the matches that count hold at least `metronome_min_gaps` (default 40) cadence gaps between them.

The server's pace is `min_shot_interval_ms` plus `tick_ms` or `interval_slack_ms`, whichever is larger. A match whose cadence averages at or under it is the server firing as fast as the gun cycles, which is what a held trigger on a full-auto does, and the rule leaves it alone. Under the legal line the fire-interval rule owns it. Set `tick_ms` when the server stamps shots on ticks: a 90 ms cycle on a 20-tick server fires every 100 ms. `WeaponRule.server_paced` true opts that weapon out entirely.

A profile can declare the clock that stamps shots with `shot_clock`. `server_tick` says shots land on ticks; without `tick_ms` beside it the metronome abstains (`disabled`), because every held trigger lands on the same tick multiple and its gaps have no spread for a reason that is not the player's. `server_ms` says shots are stamped to the millisecond. Either way the finding's thresholds name the clock and `tick_ms` it assumed. No `shot_clock` is today's behaviour, and such a profile keeps its digest.

## What the client could know

Every information check below asks `fpsdet.knowledge` one question: could this client know this enemy at this moment? The answer is `known` (a legal channel exposed it), `unknowable` (every channel in the profile's `knowledge_channels`, default vision and audio, was checked and failed), or `unknown` (anything else: an unchecked declared channel, or telemetry that contradicts itself). The checks act only on `unknowable`. Recent perception (`since_perceived_ms` under `hidden_grace_ms`) makes an enemy known. [knowledge-engine.md](knowledge-engine.md) has the full rules.

## Hidden tracking

`hidden_track_ms` is time, since this player's previous shot in the match, that the aim stayed in a tight cone of an enemy this client could neither see nor hear. Omit the field, or send 0, and nothing happens.

A sample counts only when the tracked enemy is `unknowable`. Four things are not a wallhack, and the scorer drops them:

- **Sound.** A shot labeled `information_state: "audio"` does not count. Following footsteps through a wall is a skill.
- **A body that just broke line of sight.** Send `since_perceived_ms`, the time since this client last saw or heard that enemy. Under `hidden_grace_ms` (default 1000) the shot does not count. Tracking where someone just went, or spraying the cover they ducked behind, is human. The same grace keeps those shots out of the unknowable smoothness sample and the teammate check.
- **A running total.** Each value is cut to the time since the player's previous shot in that match. An emitter that sends a running total cannot count the same second once per shot in a spray.
- **An enemy the shot says was visible.** Hidden time on it contradicts the label. It is not counted, and the case names the emitter problem.

Shots at the same match and time are one aim, so a moment adds at most one sample: once when its shots that report a hidden time agree on it (and on `information_state` and `since_perceived_ms`), and nothing when they disagree. The sum is exact (`math.fsum`), so it does not depend on order or Python version.

Review when at least `hidden_track_min_samples` (default 8) shots have time above 0 and the sum is at least `hidden_track_min_ms` (default 1200). One long sample is not enough. Corner pre-aim is `acquire_ms`, not this. A visibility query that marks a visible enemy as hidden manufactures the case. That is an emitter bug. This check runs even when the aim sample is still too small to score.

## Private replay

The private replay is now an active challenge: the server plans it with a secret, the events name it, and each one is judged on its own samples. [challenges.md](challenges.md) has the plan, the fields, the rules and the evidence. A planned challenge counts only for events that carry its `challenge_id`, inside its window, and only while every knowledge channel the profile declares is one the challenge defeats. Its bar is the one below, per challenge. The legacy field still scores as it always did:

`private_track_ms` is time, on that shot, that the aim cone contained a body the server built by replaying another player's motion on a different heading, in a volume this client could not see or hear. The official client is not given a decoy bit. Omit the field, or send 0, and nothing happens. The bar is the hidden-mover bar: at least `hidden_track_min_samples` (default 8) shots above 0, and a sum of at least `hidden_track_min_ms` (default 1200), with the same rule for shots at one moment: they count once when they agree and not at all when they disagree. One crossing is not a review. One long sample is not enough. A body this client could actually perceive, labeled private, manufactures the case. That is an emitter bug. The public speed, recoil, and aim charts are allowed to stay ordinary. This check runs even when the aim sample is still too small to score. The review desk draws that route from the planted case. The score still reads only `private_track_ms`. On an event that names a challenge it is not read.

## Wire and picture

Every moving player has two positions. The wire is the quantized snapshot the server just sent. The picture is where the official client draws that player, the wire position from one interpolation delay ago. A person aims at the picture. A packet or memory aimbot aims at the wire, often on the tick the snapshot arrives.

The server emits `wire_error_deg`, `picture_error_deg`, and `interp_delay_ms`, or it omits them. Omit any of the three, send a delay of 0 or less, or send a negative error, and that shot does not count. A shot is wire-led only when the delay has started, the picture error is at least 0.20° larger than the wire error, and the wire error is at most 0.35 of the picture error. Those two numbers are a separation floor. They are not an accuracy ceiling. A legal aim can sit as close to the picture as a cheat sits to the wire. Standing still, and noise that does not separate the two positions, do not count.

The bar is the hidden-mover bar, with no new profile knobs: at least `hidden_track_min_samples` (default 8) wire-led shots, and a sum of `interp_delay_ms` over those shots of at least `hidden_track_min_ms` (default 1200). Eight shots at 100 ms is 800 ms and does not fire. One shot with a 5000 ms delay does not fire. Twelve shots at 100 ms does. The same hit rate on the picture stays clean. This check runs even when the aim sample is still too small to score.

A server that compares the aim against a position that client was not shown manufactures the case. A capture card, a DMA read of the frame that was actually drawn, and a cheat that reimplements the official interpolator and waits out the delay still get through. The planted pair is `wire-lock` and `picture-track`. The desk draws both error series. The score reads the three fields.

## Unknowable smoothness

`information_state` is the server's statement about what this client could have known on that shot. `visible` and `audio` are knowable. `unknowable` means the server's own line-of-sight and audio queries say this client had neither. `aim_jitter_deg` is the aim noise on that shot, in degrees.

Review when both sides have at least `unknowable_min_samples` (default 12), the knowable median is at least 0.05°, and the unknowable median is at or under `unknowable_jitter_ratio` (default 0.35) times the knowable median. A player who is smooth on both sides has no drop. Samples with either field omitted are ignored. A tape that is only unknowable does not fire. Audio that gets quieter is knowable and does not fire. This check runs even when the aim sample is still too small to score. A wrong `unknowable` label manufactures the case.

## Shared leftover

After the case decisions exist, each player's command is fit to `1 + applied kick + previous kick`. That needs to know which kick came first. A build where the server sent different kicks at one time and spray index has no such order, so neither the leftover nor the same-tick mirror check runs on it, and the case says why. With `spray_index`, the previous kick is the previous shot of the same spray, and 0 on a spray's first shot. A person reacts to the kick they just felt, not to the end of a spray seconds ago; lagging across that gap leaves the same spike at every spray's first shot, for every player. What remains is the leftover. A player needs at least `vendor_min_shots` (default 32) leftover samples on a weapon.

Accounts are compared per weapon key. A humanizer table belongs to the tool, so the same table on a stock and a modded rifle still lines up. With `spray_index`, each account's signature is its mean leftover at each spray index reached by at least 3 sprays, divided by that mean's standard error. Each point then carries equal weight, and a late index reached by two sprays cannot decide the correlation alone. Two accounts are compared on the spray indices both have, when there are at least `vendor_min_points` (default 12).

Without `spray_index`, or with too few qualifying indices (one long spray, say), leftovers are compared on their overlapping prefix of at least `vendor_min_shots`. That only lines up a replay that started on the same shot. When a weapon has at least `min_cohort_players` accounts, the mean signature is removed first, so a habit every human on that gun shares is not a match.

A match needs Pearson r at or above `vendor_min_r` (default 0.85) and a Fisher z, `atanh(r)·√(n−3)`, of at least `vendor_min_z` (default 5). Every pair on a weapon is a test, so the bar has to hold across thousands of them. At 12 points that takes r ≈ 0.93. From about 24 points on, the r floor of 0.85 is the stricter bar. In the synthetic week, 400 honest players produce no matches.

Every pair inside a weapon is compared. That is fine for a community server and slow for a studio's whole population. Run it per region, or only against accounts that are already a review.

A match does not make a review. If either account is already a review, the other becomes a watch and moves up the non-reported scan (`queue_rank` 2). The confirmed account gets an observation only, so its seal stays. If neither is a review, both become watches and the reason says nobody in the pair is a review yet. A flat leftover, a series shorter than the minimum, and independent noise do not match. Comparing the raw command to the raw kick correlates two humans through the kick itself, so that comparison is not used.

## Voice-speed teammate

Only a player who is already a review for a hidden mover can start this. For each party member, each of their unknowable contacts on the same `enemy_id`, in the same `match_id`, is lagged against the latest cheater contact at or before that time. `t_ms` restarts each match, so contacts in different matches are never compared. Every such lag is stored, including the ones that are not a finding.

A watch fires when at least `inherit_min_events` (default 4) of those lags are at least 0 and under `voice_min_ms` (default 350). The teammate who was clean or `insufficient_data` becomes a watch (`queue_rank` 1). A player who is already a review is not changed. A lag long enough for a voice stays stored and stays clean. No confirmed hidden-mover partner means no lags and no watch.

## Cohort poison

`fpsdet baseline` first sets every match in the window beside the others. Each match is one point: every shot fired in it, by everyone. The median match and a robust spread (1.4826 times the median absolute deviation) come from matches with at least 5 × `min_shots` shots. A match with at least `min_shots` shots is out of line when the Wilson lower bound of its hit rate, or of its rate of shots through geometry, is more than `match_outlier_sd` (default 5) spreads above the median. That is a lobby where cheaters played each other, the kind nobody reviews before a baseline is frozen. By default each one is an alarm, the cohort is stamped `poison_risk`, and the baseline keeps the match. `--screen-matches` leaves those matches out and lists them under `integrity.left_out`. With fewer than 10 matches big enough to set the line, the screen does not run. It finds whole lobbies, not one quiet cheater in an honest one.

`fpsdet baseline --previous <prior cohort json>` also compares ceilings. For accuracy, headshot rate, and geometry rate, when both sides have at least `min_cohort_players` and the new maximum is at least `poison_jump` (default 0.08) above the old maximum, the cohort file is stamped `integrity.status = poison_risk` and each alarm is printed. Score reads that stamp, prints it, and does not change decisions. Freeze the last cohort you still trust. With no `--previous` and too few matches to screen, the status stays `unchecked`.

## Evidence seal

After the per-player reasons are final, the case gets `seal`, the SHA-256 of canonical JSON `{v:1, player_id, game_id, decision, reasons}`. A later batch pass that appends a watch reason reseals that case. An observation on someone who is already a review does not. Observations, reports, party notes, and the brief are not in the hash. The same case hashes the same. Edit a reason and the hash changes. It identifies the packet a reviewer saw. It is not a ban.

## Reports and parties

Reports are not external evidence and never meet it: the fusion rules read no report count.

`reports.json` is either `{"player-id": 4}` or `{"players": {"player-id": {"reports": 4}}}`. The count is copied onto the case and used only for sort order. Scan order is reported players first, highest count first, then a higher `queue_rank`, then player id. Review order is review, then watch, then clean, then insufficient, and within a decision the higher report count comes first. `queue_rank` does not change review order.

When two or more players in the same `party_id` are watch or review in the batch, each case names the others. That note is written after the leftover and voice-speed passes, so an upgraded teammate is named. The note does not change the decision and is not in the seal.
