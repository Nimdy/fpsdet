# Scoring

The reference implementation is `src/fpsdet/`. A port in C#, C++, or Go is compatible when it reproduces `python -m fpsdet demo` and `tests/test_fpsdet.py`.

`automated_action` is always `none`. `recommended_action` is `human_review`, `monitor`, or `none`.

## Cohort

A cohort is one number per player, not one number per shot. The keys are rank band, weapon key, and metric.

Rank comes from the matchmaker (`skill_band`, or `skill_prior` cut at 0.30 / 0.70 / 0.95 into developing, average, advanced, elite). It is the band they queued in. It is not computed from the stats under test.

Weapon key is `weapon_class` unless the profile sets `"aim_group": "weapon_id"`. Recoil and builds use `weapon_id|mod,mod` with mods sorted. That is the build key.

The player being scored is removed from each distribution before the percentile is taken (leave-one-out). A frozen cohort from last week is the production path. Fitting the cohort on the same file as the players under review is allowed for a demo and is stamped with a warning.

Percentile is nearest-rank: sort ascending, rank = ceil(q × n), 1-based, clamped. Median of an even count is the mean of the two middle values.

A distribution is thick enough when it still has `min_cohort_players` values after removal (default 30). A thin distribution does not flag. The build is reported as untrained.

The human ceiling is the highest band that is thick for that metric (elite, then advanced, then average, then developing).

## Rates

Accuracy is hits / shots. Headshot rate is head hits / hits whose hitbox was sent, and it is skipped unless at least `min_hits_for_headshot` hits have a hitbox and those cover at least 90% of hits. Geometry rate is the same shape.

The bound is the one-sided 95% Wilson lower bound, z = 1.6448536269514722.

- Past the rank: lower bound > the rank band's p95.
- Past the best measured human: lower bound > the ceiling band's maximum.

Past the best human is the review-grade flag. Past the rank only is a watch-grade flag. A hot 10-shot game has a wide bound and does not clear a maximum.

## Continuous numbers

Distance and declared `extra_metrics` use the player's median.

- High direction, past the rank: median > own p95. Past humans: median > ceiling p95.
- Low direction, past the rank: median < own p05. Past humans: median < ceiling p05.

View-angle p95 and acquire-time median are supporting tells. Acquire median and acquire standard deviation count as one family, so a single timing habit cannot masquerade as two findings. They are only computed when you send `view_delta_deg` or `acquire_ms`, and `acquire_ms` is only meaningful when the server actually knows the target became visible. Omit it otherwise. Sound, utility, and a pre-aimed angle are not reaction time.

## Decision

Count review-grade primary flags (`beyond_human`) and watch-grade flags (`beyond_band`).

| Condition | Decision |
| --- | --- |
| Sustained gear-rule break (speed, fire interval, blatant or learned recoil, a same-tick recoil mirror, a metronome on a legal cycle, sustained tracking of a hidden mover, sustained aim on a private replay, aim that matches the wire snapshot ahead of the picture, or aim noise that drops only while the server says this client could not have known) | review |
| Two or more review-grade combat flags | review |
| One review-grade combat flag, and the account broke its own history or a supporting tell agrees | review |
| Any watch-grade flag, a single review-grade flag, an account-history break, or two supporting families | watch |
| At least one metric was actually compared, and nothing above fired | clean |
| Nothing was comparable (short sample, thin cohort, no cap) | insufficient_data |

An account-history break is the same weapon and the same rank band: Wilson lower bound of this window minus Wilson upper bound of the player's own earlier windows is at least `self_jump_gap` (default 0.10), and both windows have at least `min_shots`. A rank change does not count, because the band no longer matches.

## Speed

Eligible samples are on the ground, `displacement_cause` is `none`, and a cap exists.

Cap order: `expected_max_ground_speed_mps` on the sample, else the weight-class curve. A class with `max_ground_speed_mps` uses that absolute number. A class with only `speed_multiplier` uses `reference_lightest_speed_mps × multiplier`. If neither resolves, the sample is skipped and the case says the cap is unset. Nothing is flagged.

A sample is over cap when speed > cap × (1 + `speed_over_fraction`). Default fraction is 0.08, for animation jitter.

A run is consecutive over-cap samples in one match with gaps no larger than `speed_run_gap_ms` (default 400). The finding fires when the longest run is at least `speed_min_run` (default 25). Shorter runs are recorded as a possible glitch and do not change the decision.

Only `displacement_cause` of `none` is scored. Innocent causes, `unknown`, any cause the profile has not listed, missing `on_ground`, and airborne samples break a run and are counted in the case so a reviewer can see them. They are not violations. A new traversal gadget is safe until you decide it is a normal sprint.

## Recoil

Samples count when `spray_index` is at least `recoil_min_spray_index` (default 3) and the cause is not an innocence tag. Taps and the first shots of a spray are ignored. The floor is `expected_min_recoil_pitch_deg` on the shot, else the profile row for that build.

Blatant: a run of at least `recoil_min_run` shots (default 10) whose pitch is under `recoil_floor_fraction` of the floor (default 25%). That is a review by itself. A compensated gun has a lower floor, so the same pitch can be legal.

No floor and no thick cohort: untrained, not flagged.

No floor, thick cohort, and the player's median pitch is under the lowest measured human and under `recoil_floor_fraction` of that cohort's median: learned floor break, review. Sitting in the low tail (under p05) without clearing that gap is only a watch-grade flag. The steadiest legal player on a build is not a review.

## Fire interval

Per match, per weapon id (or weapon class when the id is absent). Gaps under `min_shot_interval_ms - interval_slack_ms` are violations. The finding fires when there are at least `min_intervals` gaps, at least `min_violations` violations, and the violation rate is at least `min_violation_rate`. This is checked even when the aim sample is still too small to score.

Set the interval to the minimum legal gap between accepted shots. For a burst weapon that is the intra-burst gap, or leave the rule off for that weapon. Slack covers one tick of timestamp jitter. Timestamps are server time.

## Same-tick recoil mirror

The no-recoil rule sees the camera. A script can cancel the kick and write a legal pitch back, so `recoil_pitch_deg` stays above the floor. Send `applied_recoil_pitch_deg` (the kick the server applied) and `compensation_pitch_deg` (the signed player command on that tick, negative for a pull-down). Both are required. Spray index does not gate this pair. The first shots count.

Review when there are at least `mirror_min_shots` (default 16), the Pearson correlation of the command against the applied kick is at or below `mirror_max_r` (default −0.90), and that same-tick correlation is at least `mirror_lag_gap` (default 0.25) more negative than the correlation at `mirror_lag_shots` (default 1). A flat kick has no variance and is not a mirror. A person who pulls down does it on a later shot, so the lagged correlation is the tighter one and the case stays quiet. A kick pattern that barely changes from shot to shot cannot separate the two lags.

## Metronome

Fire interval catches gaps under the legal line. A macro that fires on the legal line, with no motor noise, does not. Review when there are at least `metronome_min_gaps` (default 40), the mean gap is at or above the legal line, and the sample standard deviation is at or under `metronome_max_std_ms` (default 1.0 ms).

If the mean is under the legal line, this rule returns nothing and the fire-interval rule owns the case. `WeaponRule.server_paced` true opts that weapon out: the server fired it. If `tick_ms` is set on the profile and every gap equals that tick, the stamps are quantized and the rule stays quiet.

## Hidden tracking

`hidden_track_ms` is time, on that shot, that the aim stayed in a tight cone of an enemy the server had not made visible. Omit the field, or send 0, and nothing happens. Review when at least `hidden_track_min_samples` (default 8) shots have time above 0 and the sum is at least `hidden_track_min_ms` (default 1200). One long sample is not enough. Corner pre-aim is `acquire_ms`, not this. A visibility query that marks a visible enemy as hidden manufactures the case. That is an emitter bug. This check runs even when the aim sample is still too small to score.

## Private replay

`private_track_ms` is time, on that shot, that the aim cone contained a body the server built by replaying another player's motion on a different heading, in a volume this client could not see or hear. The official client is not given a decoy bit. Omit the field, or send 0, and nothing happens. The bar is the hidden-mover bar: at least `hidden_track_min_samples` (default 8) shots above 0, and a sum of at least `hidden_track_min_ms` (default 1200). One crossing is not a review. One long sample is not enough. A body this client could actually perceive, labeled private, manufactures the case. That is an emitter bug. The public speed, recoil, and aim charts are allowed to stay ordinary. This check runs even when the aim sample is still too small to score. The review desk draws that route from the planted case. The score still reads only `private_track_ms`.

## Wire and picture

Every moving player has two positions. The wire is the quantized snapshot the server just sent. The picture is where the official client draws that player, the wire position from one interpolation delay ago. A person aims at the picture. A packet or memory aimbot aims at the wire, often on the tick the snapshot arrives.

The server emits `wire_error_deg`, `picture_error_deg`, and `interp_delay_ms`, or it omits them. Omit any of the three, send a delay of 0 or less, or send a negative error, and that shot does not count. A shot is wire-led only when the delay has started, the picture error is at least 0.20° larger than the wire error, and the wire error is at most 0.35 of the picture error. Those two numbers are a separation floor. They are not an accuracy ceiling. A legal aim can sit as close to the picture as a cheat sits to the wire. Standing still, and noise that does not separate the two positions, do not count.

The bar is the hidden-mover bar, with no new profile knobs: at least `hidden_track_min_samples` (default 8) wire-led shots, and a sum of `interp_delay_ms` over those shots of at least `hidden_track_min_ms` (default 1200). Eight shots at 100 ms is 800 ms and does not fire. One shot with a 5000 ms delay does not fire. Twelve shots at 100 ms does. The same hit rate on the picture stays clean. This check runs even when the aim sample is still too small to score.

A server that compares the aim against a position that client was not shown manufactures the case. A capture card, a DMA read of the frame that was actually drawn, and a cheat that reimplements the official interpolator and waits out the delay still get through. The planted pair is `wire-lock` and `picture-track`. The desk draws both error series. The score reads the three fields.

## Unknowable smoothness

`information_state` is the server's statement about what this client could have known on that shot. `visible` and `audio` are knowable. `unknowable` means the server's own line-of-sight and audio queries say this client had neither. `aim_jitter_deg` is the aim noise on that shot, in degrees.

Review when both sides have at least `unknowable_min_samples` (default 12), the knowable median is at least 0.05°, and the unknowable median is at or under `unknowable_jitter_ratio` (default 0.35) times the knowable median. A player who is smooth on both sides has no drop. Samples with either field omitted are ignored. A tape that is only unknowable does not fire. Audio that gets quieter is knowable and does not fire. This check runs even when the aim sample is still too small to score. A wrong `unknowable` label manufactures the case.

## Shared leftover

After the case decisions exist, each player's command is fit to `1 + applied kick + previous kick`. What remains is the leftover. Two leftovers are compared on their overlapping prefix when both are at least `vendor_min_shots` long (default 32). Pearson correlation at or above `vendor_min_r` (default 0.85) is a match.

A match does not make a review. If either account is already a review, the other becomes a watch and moves up the non-reported scan (`queue_rank` 2). The confirmed account gets an observation only, so its seal stays. If neither is a review, both become watches and the reason says nobody in the pair is a review yet. A flat leftover, a series shorter than the minimum, and independent noise do not match. Comparing the raw command to the raw kick correlates two humans through the kick itself, so that comparison is not used.

## Voice-speed teammate

Only a player who is already a review for a hidden mover can start this. For each party member, each of their unknowable contacts on the same `enemy_id` is lagged against the latest cheater contact at or before that time. Every such lag is stored, including the ones that are not a finding.

A watch fires when at least `inherit_min_events` (default 4) of those lags are at least 0 and under `voice_min_ms` (default 350). The teammate who was clean or `insufficient_data` becomes a watch (`queue_rank` 1). A player who is already a review is not changed. A lag long enough for a voice stays stored and stays clean. No confirmed hidden-mover partner means no lags and no watch.

## Cohort poison

`fpsdet baseline --previous <prior cohort json>` compares ceilings. For accuracy, headshot rate, and geometry rate, when both sides have at least `min_cohort_players` and the new maximum is at least `poison_jump` (default 0.08) above the old maximum, the cohort file is stamped `integrity.status = poison_risk` and each alarm is printed. Score reads that stamp, prints it, and does not change decisions. Freeze the last cohort you still trust. No `--previous` leaves the status `unchecked`.

## Evidence seal

After the per-player reasons are final, the case gets `seal`, the SHA-256 of canonical JSON `{v:1, player_id, game_id, decision, reasons}`. A later batch pass that appends a watch reason reseals that case. An observation on someone who is already a review does not. Observations, reports, party notes, and the brief are not in the hash. The same case hashes the same. Edit a reason and the hash changes. It identifies the packet a reviewer saw. It is not a ban.

## Reports and parties

`reports.json` is either `{"player-id": 4}` or `{"players": {"player-id": {"reports": 4}}}`. The count is copied onto the case and used only for sort order. Scan order is reported players first, highest count first, then a higher `queue_rank`, then player id. Review order is review, then watch, then clean, then insufficient, and within a decision the higher report count comes first. `queue_rank` does not change review order.

When two or more players in the same `party_id` are watch or review in the batch, each case names the others. That note is written after the leftover and voice-speed passes, so an upgraded teammate is named. The note does not change the decision and is not in the seal.
