# fpsdet on real Counter-Strike 2 matches

The planted demo and the synthetic week show what fpsdet does when the answer is known by construction. This folder runs it on real play instead: Counter-Strike 2 matchmaking matches from the public **CS2CD** dataset, where the cheaters were labelled by hand.

Not affiliated with Valve or with the dataset's authors. Nothing from the dataset is committed here; the script downloads it when you run it.

## The data

[CS2CD](https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection) (Counter-Strike 2 Cheat Detection) by Mille Mei Zhen Loo and Gert Lužkov, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Paper: [arXiv:2508.06348](https://arxiv.org/abs/2508.06348).

- 795 CS2 matchmaking matches (Premier and Competitive), parsed from server-recorded demos with demoparser2. Each match is one table of 64-tick snapshots per player plus one file of game events.
- 317 matches contain at least one VAC-banned player. In those, every player was reviewed by hand and the cheaters are listed.
- 478 matches contain no VAC-banned player. These were **not** reviewed. The authors' spot check of 50 found 97.2% of players showing no cheating behaviour, so a few cheaters are in there unlabelled.
- Players are pseudonymised by the authors (`Player_1` to `Player_10` per match). This script renames them `nc012-p3` (no-cheater match 12, player 3) or `wc029-p3`.

Changes made here: events are converted to fpsdet's format as described below. Nothing else is altered.

## Why CS2 fits fpsdet

A CS2 demo is recorded by the server (SourceTV), not by a player's client. Hits, hitgroups, wall penetrations, positions and view angles in it are what the server decided. That is the data fpsdet is built for.

## Run it

```bash
pip install -r examples/cs2/requirements.txt
PY="python examples/cs2/cs2cd.py"

# Download. Only the events and tick columns the converter reads: about 12 MB a match.
$PY fetch no_cheater_present   --first 0 --count 180 --out ~/cs2cd
$PY fetch with_cheater_present --first 0 --count 120 --out ~/cs2cd

# The baseline: what humans look like. The first 120 complete no-cheater matches, no movement needed.
# --screen-matches leaves out lobbies where cheaters played each other (see Results).
$PY convert ~/cs2cd/no_cheater_present --count 120 --movement-hz 0 --out baseline.ndjson
PYTHONPATH=src python -m fpsdet baseline baseline.ndjson --profile examples/cs2/cs2.json --screen-matches --out cs2-cohort.json

# Scored against it: the remaining no-cheater matches, and the matches with labelled cheaters.
$PY convert ~/cs2cd/no_cheater_present --skip-first 120 --out holdout.ndjson
$PY convert ~/cs2cd/with_cheater_present --out cheaters.ndjson
cat holdout.ndjson cheaters.ndjson > scored.ndjson
PYTHONPATH=src python -m fpsdet score scored.ndjson --profile examples/cs2/cs2.json --cohort cs2-cohort.json --out cs2-cases/

# Compare the decisions with the labels. fpsdet never sees the labels.
$PY labels ~/cs2cd --out labels.json
$PY report cs2-cases/ --labels labels.json
```

`cs2-cases/dashboard.html` is the operations view of the scored matches.

## What the converter sends

| fpsdet field | From the demo |
| --- | --- |
| `match_id`, `player_id`, `map_id` | CS2CD match number, its `Player_N`, the map |
| `t_ms` | tick × 15.625 (64-tick server) |
| `skill_band` | rank at the start of the match. Premier: under 5,000 developing, under 10,000 average, under 20,000 advanced, then elite. Competitive: Silver developing, Gold Nova average, Master Guardian to DMG advanced, Legendary Eagle and up elite. No rank is `unrated` |
| `weapon_id`, `weapon_class` | the `weapon_fire` event's gun; class is rifle, sniper, smg, pistol, shotgun or mg. Knives, grenades, the taser and the bomb are left out |
| `hit` | the server hurt an enemy with this shooter's gun on the same tick. Team damage is not a hit |
| `hitbox` | the hitgroup: head; neck and chest are upper torso; stomach is lower torso; arms and legs are limbs |
| `distance_m`, `through_geometry` | the server's `bullet_damage` distance and penetration count. Hits only: a miss has no target |
| `spray_index` | the server's shots-fired counter, minus one |
| `view_delta_deg` | how far the view turned in the two ticks (31 ms) before the shot |
| movement | ground speed from the server's velocity at 4 Hz, `on_ground`, ladders tagged. Every sample is capped at 6.35 m/s (250 units/s, knife out), the fastest anything moves on foot |

Not sent, because a demo cannot say it honestly: recoil kick and the player's compensation, what each client could see or hear (`information_state`, hidden tracking), what each client was sent (`wire_error_deg`), private replays, and parties.

## Results

Run on 2026-10-04 with the commands above: 165 complete no-cheater matches (the first 180 by number, minus abandoned ones) and 108 complete with-cheater matches (the first 120).

**fpsdet caught its own poisoned baseline.** `fpsdet baseline` sets every match beside the others in the window. Of the first 120 no-cheater matches, it flagged 20 where the whole lobby was far past the median match, for example:

```
match nc104: the lobby hit 49% of 334 shots (lower bound 44%); the median match hits 19% and the line is 35%;
70% of 162 traced shots went through geometry (lower bound 64%); the median match is 8% and the line is 29%
```

These look like hack-vs-hack games where nobody had been banned yet, which is what "not reviewed" allows. Left in, they would make "past every measured human" mean "past a rage cheater". `--screen-matches` left them out, keeping 100 matches and 996 players. The lines come from the window itself, not from CS2: none of the 45 held-out no-cheater matches crosses them, and 47 of the 108 with-cheater matches would. In this run, leaving them out changed no decision, because the players in those lobbies fired too few shots to enter the baseline. With more play per account it would matter.

**What the labels look like in the data.** The cheaters are not subtle:

| | Labelled cheaters | Clean, reviewed matches | Clean, unreviewed matches |
| --- | --- | --- | --- |
| Players | 504 | 575 | 450 |
| Median shots in the match | 38 | 72 | 258 |
| Reach 40 shots on one weapon class | 30% | 56% | 98% |
| Median accuracy (20+ shots) | 45% | 17% | 20% |
| Median share of hits through walls (10+ hits) | 47% | 6% | 5% |
| Players with over 40% of hits through walls | 222 | 1 | 0 |

**What fpsdet decided,** scored against the screened baseline:

| | Review | Watch | Clean | Held for too little data |
| --- | --- | --- | --- | --- |
| Labelled cheaters (504) | 0 | 14 | 92 | 398 |
| Clean, reviewed matches (575) | 0 | 0 | 302 | 273 |
| Clean, unreviewed matches (450) | 0 | 4 | 435 | 11 |

- **No honest player was put in review,** and none of the 575 hand-reviewed clean players was even watched.
- **No cheater was put in review either.** Most were held: a cheater fires a median 38 shots in a match, fpsdet waits for 40 on one weapon class, and many rage cheaters use auto-snipers, where too few clean players fire 40 shots to make a baseline.
- **Those it could score mostly sat inside the human range.** The clearest, `wc002-p2`, hit 92.5% of rifle shots (lower bound 83%, best human 45%) but only 19% of hits were headshots. One number past every human is a watch. A review needs two.
- **Shots through walls never counted.** A demo only records penetration for hits, and the wall check waits for 40 shots with a known answer. Few cheaters land 40 hits in one match.
- **Speed stayed clean** across 3.85 million movement samples. The server enforces it.

Halving the per-player minimums (20 shots, 12 hits for headshot rate) as an experiment moved 35 cheaters and 9 clean players to watch, and still put nobody in review. The limit is evidence per account, not the thresholds.

**What this shows.** On real CS2 play, fpsdet did not frame anyone, and it said "not enough data" instead of guessing when one match was all it had. The labelled cheaters differ from clean players by a wide margin on exactly the numbers fpsdet scores. What one match per account cannot show is detection: fpsdet is built to judge an account over a week of play, and CS2CD cannot link a player across matches.

## Limits

- **One match per player.** CS2CD pseudonymises each match separately, so nobody can be followed across matches. A player fires around 70 shots a match, and fpsdet waits for 40 shots on a weapon class (and 25 hits for headshot rate) before it scores aim. Many players are held for too little data. A studio scores a week.
- **The baseline is unreviewed.** The match screen catches whole lobbies of cheaters, not one quiet cheater in an honest lobby. "Past every measured human" is past the most extreme player in the baseline, so one unlabelled cheater left in it raises the bar for everyone. A baseline frozen from reviewed play does not have that problem.
- **Gear rules cannot fire.** The CS2 server enforces movement speed and fire rate, and the spray pattern is fixed, so humans learn to cancel it. Speed is checked and should stay clean. Fire rate, metronome and recoil checks are off in this profile.
- **No information checks.** Wallhacks are what most labelled cheaters run. Catching them needs a visibility query (what each client could see), which a demo does not contain. Approximating it from map geometry is possible and is the riskiest part to get right.
- **Labels are the dataset's.** A VAC ban plus a reviewer's judgement. Some labelled cheaters may not have cheated in this match.
