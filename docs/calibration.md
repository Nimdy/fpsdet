# Measuring the detectors

## What the labels mean

Every rate fpsdet publishes about real play is a rate against someone else's labels. None of those labels is ground truth. This section says exactly what each one is, before any number is computed from it. It was written at `2a6dc90`, and `tests/test_calibration.py` (`LabelAuditTest`) pins the label sets and the published results it describes.

### TF2: RGL bans

Source: RGL's public ban list, joined to server-logged league matches kept by logs.tf (`examples/tf2`).

| Label | Who | What it does not mean |
| --- | --- | --- |
| `cheater`, published as **RGL cheating-ban labelled** | RGL banned the account, and the reason mentions cheating, but not assisting, distributing or developing cheats | That the player cheated in every scored match, or in any particular one. A ban is a league decision, sometimes mirroring another league's |
| `not banned`, published as **never-banned comparison** | RGL never banned the account for anything, and it played in the same lobbies as labelled accounts | That the player never cheated. A few unlabelled cheaters are likely among them |
| `other ban` | RGL banned the account for something other than cheating, such as an alt account | Anything about cheating |
| `vac` | RGL mirrored a Valve ban, possibly from another game | That the player cheated in TF2 |

**How the matches are chosen.**
- **Ban date:** a banned account's matches count only before the ban date.
- **Equal evidence:** every player keeps at most their 20 latest team matches (6v6, prolander or highlander, 12 or more players).
- **Eligibility:** a cheating-ban labelled account needs at least 20 team matches before its ban. A never-banned player needs at least 8 matches in the fetched logs.
- **Baseline split:** never-banned players are split by a fixed bit of their keyed pseudonym. Half build the baseline, and only the other half are scored, so nobody is judged against a baseline that includes them.
- **What is scored:** only aimed weapons are converted (scatterguns, pistols, shotguns, sniper rifles, SMGs and revolvers).

**Counts in the published run (2026-10-04).**

| | Labelled on the scored side | With a scored case |
| --- | --- | --- |
| RGL cheating-ban labelled | 228 | 189 |
| Never-banned comparison | 2,158 | 1,746 |
| Other ban | 1,184 | 821 |
| VAC, mirrored | 20 | 8 |

A labelled player with no scored case had no aimed-weapon shots in their kept matches. They are outside every denominator: fpsdet had nothing to judge.

**Known uncertainty.**
- **Exposure is unequal.** Most never-banned players appear in only a few matches; labelled accounts have up to 20. Rates are therefore also reported at equal evidence.
- **The positive label is about an account, not a match.** A labelled account may have played clean in some, or most, of its scored matches.
- **The comparison is not proven clean.**
- **What the data can show.** Per-match totals only: shots, hits and sniper headshots per weapon, kills per minute. No timing, positions, view angles or movement. Every detector that needs those is not observable here.
- **Timing is not real.** The converter spreads each weapon's shots evenly through the match, so no timing-based detector could be meaningful even if the profile asked for one.

### CS2: the CS2CD dataset

Source: CS2CD, 795 CS2 matchmaking matches parsed from server-recorded demos (`examples/cs2`). Its labels are the dataset authors', and they are not the same kind of label as RGL's.

| Label | Who | What it does not mean |
| --- | --- | --- |
| `cheater`, published as **hand-labelled cheater** | A player the authors labelled as cheating after reviewing a match that contained a VAC-banned player | A ban; the labels are the authors' judgement |
| `clean, reviewed match`, published as **hand-reviewed, not labelled** | A player in a reviewed match whom the authors did not label | That they are proven clean; it is a reviewer's call, but a direct one |
| `clean, unreviewed match`, published as **unreviewed match** | A player in a match with no VAC-banned player. Not reviewed; the authors' spot check of 50 such matches found 97.2% of players showing no cheating | That anyone looked at this player |

**How the matches are chosen.** Each match is pseudonymised on its own, so a player is one match and cannot be followed across matches. The run fetched the first 180 no-cheater and the first 120 with-cheater matches and kept the complete ones. The first 120 complete no-cheater matches built the baseline (after `--screen-matches` left 20 lobby-wide outliers out). The other 45 complete no-cheater matches and the 108 complete with-cheater matches are scored. Players of the baseline matches are labelled but never scored.

**Counts in the published run (2026-10-04).** 504 hand-labelled cheaters, 575 hand-reviewed players not labelled, and 450 players in unreviewed matches have scored cases (1,529 in all).

**Known uncertainty.**
- **One match per player.** fpsdet is built to judge an account over many matches. Here most players are held for too little data, and that is the dominant effect on every rate.
- **The comparison groups differ by construction.** Reviewed players shared a lobby with a cheater; unreviewed players did not.
- **Unreviewed is not reviewed.** A few cheaters are likely in the unreviewed group, and the baseline screen found whole lobbies of them in the baseline's own window.
- **What the data can show.** Server hits, hitgroups, penetration, distance, view turn before a shot, and movement speed. No recoil kick, no player command, no visibility or audio, no wire timing, no challenges, no parties.

### Synthetic: planted by construction

The planted demo and the synthetic week are built by fpsdet's own code, and each player's behaviour is known because it was planted. They qualify detectors: does a planted behaviour trip the detector it was built for, and does its honest twin stay clean? They are never mixed into a real-world rate. A synthetic rate says how the code behaves on data it was designed for, not how often anything fires on real play.

## The question

On one labelled population, among the players a detector had enough evidence to run on, how often did it fire in the positive-labelled group, how often in the comparison group, and how sure is that?

`fpsdet evaluate` answers exactly that, from finished cases and a label file (`src/fpsdet/calibration.py`). It is measurement only:
- The scorer never imports it. `tests/test_calibration.py` checks that no detection module does.
- No threshold, role or decision was changed in this work, or chosen from these numbers.
- No rate becomes a weight or a likelihood ratio anywhere.

## Who counts

A player enters a detector's rate only when the case shows the detector could run on them. Everyone else is coverage, never a miss.

| Detector | Could run on a player when the case shows |
| --- | --- |
| `accuracy`, `headshot_rate`, `median_distance`, `geometry_rate` | that metric was compared with a thick cohort on at least one weapon (`evidence.eligibility.compared`) |
| `rank_tail` | any compared metric whose tail it reads: those four, recoil, or a primary declared metric |
| `extra`, `supporting_extra` | a declared metric of that kind was compared |
| `view_snaps`, `acquire_timing` | the player's `view_p95` or `acquire_median` row was compared, not skipped |
| `speed` | at least `speed_min_run` eligible ground samples: fewer could never fire |

**Splitting the rest.** Coverage splits everyone else in two:
- **below the sample minimum:** too few shots or samples to compute the number at all;
- **thin baseline:** the number was computed, but the cohort to compare it with was too thin.

**A disagreement stops the run.** If a finding ever lands on a player or weapon the case does not record as compared, the evaluation stops: the eligibility rules would disagree with the scorer.

**Detectors the cases cannot give a denominator for.** The other detectors do not record who they could have run on. That covers the fire-rate rule, metronome, recoil floor and mirror, the learned recoil floor, account history, the information checks, the challenge and the relationship checks. A dataset that cannot feed one of them reports it as `not_observable`, with the reason, in machine-readable form:

| Reason | Meaning |
| --- | --- |
| `missing_telemetry` | an event field it reads is not in the data (listed) |
| `no_server_shot_timing` | shot times were made by a converter, not the server |
| `profile_declares_none` | the profile has no rule for it, such as a weapon's fire interval |
| `no_history_input` | the run was given no account history |
| `eligibility_not_recorded` | the data could feed it, but cases do not record which players it could run on, so its firings are counted with no rate |

A detector that is not observable is never reported as 0%. The dataset definition declares the event fields the data carries. `--events` checks that declaration against every line, and a field declared but absent, or present but undeclared, stops the run.

## The numbers

**Rates.**
- **What a rate shows:** the count, the denominator, the rate and a two-sided 95% Wilson interval (z = 1.95996). The scorer's own bounds are one-sided, and nothing here changes them.
- **The minimums:** below 20 players a rate is not shown ("insufficient calibration sample"). A detector is `measured` when the positive group and the primary comparison group each have at least 100 evaluated players, `descriptive_only` from 20, and `insufficient_sample` below that.
- **When they were set:** both minimums were set before any rate was computed, and have not moved. No status is ever "calibrated".

**Ratios between groups.** A ratio is the positive group's rate over the comparison group's. Its range is the positive group's low end over the comparison's high end, and its high end over the comparison's low end. When the groups are independent samples, the range covers the true ratio at least about 90% of the time (0.95 squared). It is not itself a 95% interval. No pseudo-count or continuity correction is added. Zeros:
- **No fires in either group:** "no fires in either group", with no ratio.
- **No comparison fires:** "no comparison fires observed", with the low end only. There is never an infinity.
- **No positive fires:** a ratio of 0, with its high end.

**Queues.**
- **Who is in a queue:** each queue (review, watch, both) and each detector's firings are split by label, with the share that is label-positive and its interval.
- **The chance level:** the positive share of the players who could have been in the queue. For the decision queues that means every player fpsdet could compare. The share drawn at random from every scored player is given too, because the READMEs use it.

**Decisions.** Review, watch and both, as a share of the players fpsdet could compare, and as a share of every scored player (the published framing).

**Evidence amount.** The same, by matches per player: 1, 2–5, 6–10, 11–14, 15–20 and 21 or more. Most never-banned TF2 players have a few matches, so the fair comparison is in equal bins.

**Strata.** Each detector by weapon (or declared-metric group), by skill band and by evidence amount, with the same 20-player minimum per cell.

**Families.** Any finding in the family, among players at least one of its detectors could run on.

**Overlap.**
- **Counts:** how often two detectors fired on the same player, among players both could run on. That is co-occurrence, not independence.
- **What the graph says they share:** for each pair that fired together, the case's evidence graph says whether the two were measured against the same cohort, in the same match, on the same weapon or group, through a recorded dependency, or about the same partner.
- **One domain:** every native finding is server behaviour, one telemetry domain.

**Splits.**
- **How the halves are made:** `fpsdet.evaluation-split/1` splits players into two fixed halves by a hash of the pseudonym alone. No label, decision or number goes in.
- **What they show:** both halves are reported side by side, to show how far a rate moves within one population. No threshold was set on either.

**Observation and pair level.**
- **Observation level:** a unit is a weapon, or a metric on a weapon. These are counts with no interval, because one player's weapons are not independent.
- **Relationship checks:** they keep the player as the main unit. Their pair counts are reported separately.

**No p-values.** There are no hypothesis tests here.

## The artifact

`fpsdet.evaluation/1` is one JSON file. It holds:
- the dataset definition;
- the inputs: how many cases, an identity digest over each player's packet digest, and the shared detector, profile and cohort digests;
- the label digest and counts, and the telemetry declaration with its census;
- the evaluator's digest: this module's and `statsutil`'s source;
- the configuration;
- each detector's observability;
- one row per scored player;
- every statistic;
- the published-decision check.

**What stops a run.** The evaluation refuses:
- cases whose evidence packet or graph does not verify;
- cases from more than one run;
- a profile other than the one the cases were scored with;
- labels the dataset definition does not explain;
- published decisions that no longer reproduce.

**The digest.** One digest binds all of it. It is not signed, and nothing of it goes into a case or its packet.

**Verifying.** `fpsdet evaluate verify` recomputes every statistic from the rows and checks the digest. An artifact written by another version of the evaluator still verifies when this one computes the same statistics.

**After a detection change.** An artifact names the detector it measured. After a detection change, the run is re-scored and re-evaluated, and the regression diff says what moved.

## Results

Both runs are from 2026-10-04, scored by detector `sha256:fb30982d…`. The full reports are [examples/tf2/evaluation.md](../examples/tf2/evaluation.md) and [examples/cs2/evaluation.md](../examples/cs2/evaluation.md), with every number in the JSON beside them.

### TF2: RGL cheating-ban labelled against the never-banned comparison

| Detector | Status | RGL cheating-ban labelled | Never-banned comparison | Ratio |
| --- | --- | --- | --- | --- |
| accuracy | measured | 11/182 = 6.0% (3.4%–10.5%) | 0/1605 = 0.0% (0.0%–0.2%) | no comparison fires observed; at least 14.3x |
| headshot_rate | measured | 5/101 = 5.0% (2.1%–11.1%) | 2/407 = 0.5% (0.1%–1.8%) | 10.1x (1.2–82.1) |
| extra | descriptive_only | 2/81 = 2.5% (0.7%–8.6%) | 0/385 = 0.0% (0.0%–1.0%) | no comparison fires observed; at least 0.7x |
| rank_tail | measured | 44/182 = 24.2% (18.5%–30.9%) | 28/1605 = 1.7% (1.2%–2.5%) | 13.9x (7.4–25.5) |
| supporting_extra | descriptive_only | 1/28 = 3.6% (0.6%–17.7%) | 0/104 = 0.0% (0.0%–3.6%) | no comparison fires observed; at least 0.2x |

**Decisions.** Review or watch, among the players fpsdet could compare:
- RGL cheating-ban labelled: 51/182 = 28.0% (22.0%–34.9%);
- never-banned comparison: 30/1605 = 1.9% (1.3%–2.7%);
- ratio: 15.0x (8.3–26.6).

At equal evidence (15–20 matches) it was 30 of 80 against 6 of 228. The 97 players in the queues held 51 cheating-ban labelled accounts: 51/97 = 52.6% (42.7%–62.2%), against a chance level of 7.2%.

**Not observable here (17).** speed, fire_rate, metronome, recoil_floor, mirror, recoil_learned, median_distance, geometry_rate, view_snaps, acquire_timing, account_jump, hidden, quiet_aim, wire, occluded_motion_replay, leftover and voice. The logs carry per-match totals only, and shot times made by the converter.

**What it says.** On this population, among accounts it could compare, the rank tail fired on about one RGL cheating-ban labelled account in four, and on fewer than one never-banned account in fifty. The past-every-human accuracy check fired on 11 labelled accounts and no never-banned one. That is the narrow claim. It is not a detection rate for TF2 cheating: most labelled accounts look clean on hit rates, and the labels are bans, not observed cheating.

### CS2: hand-labelled cheaters against hand-reviewed players

| Detector | Status | Hand-labelled cheater | Hand-reviewed, not labelled | Ratio |
| --- | --- | --- | --- | --- |
| speed | measured | 0/504 = 0.0% (0.0%–0.8%) | 0/575 = 0.0% (0.0%–0.7%) | no fires in either group |
| accuracy | measured | 1/106 = 0.9% (0.2%–5.2%) | 0/302 = 0.0% (0.0%–1.3%) | no comparison fires observed; at least 0.1x |
| headshot_rate | descriptive_only | 2/36 = 5.6% (1.5%–18.1%) | 0/69 = 0.0% (0.0%–5.3%) | no comparison fires observed; at least 0.3x |
| median_distance | insufficient_sample | 0 of 11, too few to rate | 0/21 = 0.0% (0.0%–15.5%) | insufficient calibration sample |
| geometry_rate | insufficient_sample | 0 of 11, too few to rate | 0/21 = 0.0% (0.0%–15.5%) | insufficient calibration sample |
| rank_tail | measured | 12/106 = 11.3% (6.6%–18.8%) | 0/302 = 0.0% (0.0%–1.3%) | no comparison fires observed; at least 5.3x |
| view_snaps | measured | 1/106 = 0.9% (0.2%–5.2%) | 1/302 = 0.3% (0.1%–1.9%) | 2.8x (0.1–88.1) |

**Decisions.**
- **Of the players fpsdet could compare:** 14/106 = 13.2% (8.0%–21.0%) of hand-labelled cheaters went to watch, against 0/302 hand-reviewed players and 4/439 = 0.9% (0.4%–2.3%) players in unreviewed matches.
- **Held for too little data:** most of each group was. 398 of 504 hand-labelled cheaters were held for too little data in their one match.
- **The watch queue:** 18 players, 14 of them hand-labelled cheaters. That is too few for a rated share: the interval runs from 55% to 91%.

**Not observable here (15).** fire_rate, metronome, recoil_floor, mirror, recoil_learned, extra, acquire_timing, supporting_extra, account_jump, hidden, quiet_aim, wire, occluded_motion_replay, leftover and voice.

**What it says.**
- **Speed:** it could run on every player and fired on none, labelled or not.
- **Aim:** the aim checks fired on a few hand-labelled cheaters and no hand-reviewed player.
- **What one match cannot give:** one match per player is too little evidence for most players, so this is not a measure of what fpsdet does over an account's week.

## Controlled fixtures

`fpsdet evaluate fixtures` runs two worlds:
- **The planted demo.**
- **The controlled fixtures beside it (`fpsdet.fixtures`):** 40 humans frozen into a baseline, plus players who each differ from a human in one deliberate way.

It asks two things of each detector: does it trip on the behaviour planted for it, and does it stay quiet on an honest twin built to look like that behaviour? This is controlled-fixture qualification of code on data built for it. It is never a rate, and never a real-world measurement.

| Outcome | Detectors |
| --- | --- |
| passes_controlled_fixture | speed, fire_rate, metronome, recoil_floor, mirror, recoil_learned, accuracy, headshot_rate, median_distance, geometry_rate, extra, rank_tail, view_snaps, acquire_timing, supporting_extra, account_jump, hidden, quiet_aim, wire, occluded_motion_replay, leftover, voice |

**What P9 added.** P8 found six detectors with no plant: recoil learned, geometry rate, the primary and supporting declared numbers, view snaps and acquire timing. The controlled fixtures plant each one, beside a twin who is good but human. They also add honest twins that fire rate, metronome and account jump lacked: a fast but legal trigger, a steady hand that still varies, a held full-auto at the gun's own cycle, and an account that improved inside what it had shown.

**Eligibility probes.** The same world carries a probe for each eligibility status the demo never reaches. Together with the demo, every detector is shown eligible and firing, eligible and quiet, and in every status its rules allow (`tests/test_eligibility.py`).

All 16 honest fixtures in the demo have no finding at all.

**The challenge detector.** The occluded-motion replay has no real-world measurement (`real_world_calibration: unavailable`): no public dataset carries planned challenges. Its fixture here is the legacy private replay. Planned challenges are tested in `tests/test_challenge.py`.

## Running it

```bash
# A scored run's cases, one per line, then the evaluation.
PYTHONPATH=src python tools/regress.py snapshot scored.ndjson --profile examples/tf2/tf2.json --cohort cohort.json --out cases.jsonl
PYTHONPATH=src python -m fpsdet evaluate run cases.jsonl --labels labels.json \
    --dataset examples/tf2/evaluation.dataset.json --profile examples/tf2/tf2.json \
    --events scored.ndjson --out evaluation.json --report evaluation.md
PYTHONPATH=src python -m fpsdet evaluate verify evaluation.json
PYTHONPATH=src python -m fpsdet evaluate fixtures
```

`run` also reads the `review-index.json` that `fpsdet score --out` writes. On the 2026-10-04 runs, an evaluation takes under a second, and under 105 MB. The optional `--events` census reads every event once, about 9 to 10 s for 4 million.

## What this does not do

- **Change scoring:** it does not change a threshold, role, rule, watch or review. It does not choose any of them from these numbers.
- **Turn a rate into evidence:** no rate here is a likelihood ratio, a weight, or a probability that a player cheated.
- **Combine datasets:** TF2, CS2 and the planted fixtures are never combined into one number.
