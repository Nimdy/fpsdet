# fpsdet on real Team Fortress 2 matches

The CS2 example could not show detection: CS2CD has one match per player, and fpsdet judges an account over many. This one can try. It uses two public sources:

- **[logs.tf](https://logs.tf)** keeps the match logs that TF2 community servers upload. With the server's supplemental stats plugin, each log has every player's shots and hits per weapon, and headshots on sniper rifles. These are counted by the server.
- **[RGL](https://rgl.gg)**, a competitive TF2 league, publishes [its bans](https://api.rgl.gg/docs) with a reason. 314 accounts are banned for cheating.

Together they give players banned for cheating, each with dozens of server-logged matches before the ban, and the honest players they shared lobbies with.

**See it:** open the [review desk](https://nimdy.github.io/detect-FPS-hackers/board.html) and pick **D · Real TF2 matches**, or run `fpsdet demo` and open `demo/tf2.html`. Every scored player is there with fpsdet's decision, the reason, and RGL's label beside it. Players are keyed pseudonyms.

**Try it, and tell us what you find,** with the [result form](https://github.com/Nimdy/detect-FPS-hackers/issues/new?template=real_data_result.yml).

Not affiliated with Valve, logs.tf or RGL. Neither site publishes terms for its data. logs.tf's robots.txt asks crawlers to stay off its API, so this script behaves like the community stats tools that use it: it asks for a bounded sample, one or two requests at a time with a pause after each, names this project in its User-Agent, and caches every response so nothing is fetched twice. Nothing downloaded is committed, and player ids are replaced by keyed pseudonyms whose key never leaves your machine, so published results name nobody. If you run logs.tf or RGL and want this changed, open an issue.

## Run it

Standard library only.

```bash
DATA=~/tf2
python examples/tf2/tf2logs.py cohort --out $DATA          # RGL's ban list
python examples/tf2/tf2logs.py fetch --out $DATA --plan    # match lists only: how much would be fetched
python examples/tf2/tf2logs.py fetch --out $DATA --per-account 20   # each cheater's 20 latest team matches before the ban
python examples/tf2/tf2logs.py honest --out $DATA          # 150 honest players per half, 20 matches each
python examples/tf2/tf2logs.py convert $DATA               # baseline.ndjson, scored.ndjson, labels.json

# The same steps, with every parameter pinned and every output checked: fpsdet benchmark fetch/prepare/run --dataset tf2
# (docs/benchmark.md). The published run fetched 20 matches per account; the fetcher's default is 40.

PYTHONPATH=src python -m fpsdet baseline $DATA/baseline.ndjson --profile examples/tf2/tf2.json --screen-matches --out $DATA/cohort.json
PYTHONPATH=src python -m fpsdet score $DATA/scored.ndjson --profile examples/tf2/tf2.json --cohort $DATA/cohort.json --out $DATA/cases
python examples/tf2/tf2logs.py report $DATA/cases --labels $DATA/labels.json

# Optional: measure each detector against the labels (writes examples/tf2/evaluation.json and .md).
PYTHONPATH=src python -m fpsdet evaluate run $DATA/cases/review-index.json --labels $DATA/labels.json \
    --dataset examples/tf2/evaluation.dataset.json --profile examples/tf2/tf2.json --events $DATA/scored.ndjson \
    --out examples/tf2/evaluation.json --report examples/tf2/evaluation.md

# Optional: rebuild the desk's TF2 page from your run, then regenerate the desk.
python examples/tf2/tf2logs.py desk $DATA/cases --labels $DATA/labels.json
PYTHONPATH=src python -m fpsdet demo
```

## Who is in it

- **Labelled cheaters:** RGL accounts banned for cheating (not for helping a cheater, or for selling cheats) with at least 20 team matches on logs.tf before the ban. For each, the most recent team matches before the ban: 6v6, prolander or highlander, 12 or more players.
- **Honest players:** everyone in those same matches whom RGL never banned for anything, with at least 8 matches in the sample. They played the same lobbies at the same level. A fixed bit of each pseudonym sends half of them to the baseline and half to be scored, so nobody is judged against a baseline that includes them. On top of them, `honest` takes 150 players from each half, in an order fixed by their pseudonyms, and fetches each one's 20 most recent team matches, so cheaters have an honest comparison with as much evidence.
- **Equal evidence:** every player keeps at most their 20 latest matches, and a banned player's count only before the ban date.

## What the converter sends

| fpsdet field | From the log |
| --- | --- |
| `match_id`, `map_id` | the log id and map |
| `player_id` | a keyed hash of the SteamID |
| `weapon_class`, `weapon_id` | the TF2 class and the weapon |
| one shot event per shot | the weapon's `shots`, with the first `hits` of them marked as hits |
| `hitbox` | sniper rifles and the Ambassador only, when the server recorded headshots: the player's `headshots_hit` are head, the rest of the hits body |
| `skill_band` | `unrated`: the logs carry no division |

Only aimed, hitscan weapons are sent: scatterguns, pistols, shotguns, sniper rifles, SMGs and revolvers. Rockets, stickies and pipes hit by splash, flamethrowers by stream, and a minigun's bullet count would swamp everything else.

## Results

Run on 2026-10-04: 4,567 RGL bans, of which 314 accounts banned for cheating. 228 had at least 20 team matches on logs.tf before the ban. Their 20 most recent, plus 20 each for 300 honest players, came to 10,218 matches; 84% carry the server's headshot counts. The baseline came from 2,095 honest players (1,662 with enough shots on some weapon), and the lobby screen found nothing to leave out. The cheaters, the other half of the honest players, and everyone RGL banned for something else were scored against it.

**What fpsdet added.** From the servers' shot and hit counts alone, without the labels, fpsdet picked 97 of the 2,764 scored players for a person to look at: 3 to review and 94 to watch. 51 of the 97 are banned cheaters (53%). 97 players picked at random would hold about 7, so its picks were nearly 8 times better than chance. All 3 reviews were banned cheaters, and none of the 1,746 never-banned players went to review. Every decision, reason, bound and baseline line here is fpsdet's; the shot counts are the servers', and the labels are RGL's.

**One draw of a random split.** Which half of the never-banned players builds the baseline is decided by the pseudonym key. The benchmark rebuilds five other draws from the same downloads, with public keys ([docs/benchmark.md](../../docs/benchmark.md)): the labelled accounts flagged hardly move, but more never-banned players are flagged in every other draw, and the ratio between the groups is lower. The numbers here are reproducible exactly, and they are the most favourable of the six draws.

| | Review | Watch | Clean | Held for too little data | Flagged |
| --- | --- | --- | --- | --- | --- |
| Banned for cheating (189 with aimed shots) | 3 | 48 | 131 | 7 | 27% |
| Never banned (1,746) | 0 | 30 | 1,575 | 141 | 1.7% |
| Banned for something else (821) | 0 | 16 | 729 | 76 | 1.9% |

Most never-banned players appear in only a few of these matches, and fewer matches mean wider bounds, so the fair comparison is at equal evidence. Players with 15 to 20 matches each:

| | Players | Flagged | Review |
| --- | --- | --- | --- |
| Banned for cheating | 80 | 37.5% | 2 |
| Never banned | 228 | 2.6% | 0 |
| Banned for something else | 61 | 3.3% | 0 |

- **Every review is a banned cheater.** Two have Ambassador or revolver accuracy confidently past the best human measured (a lower bound of 62% against 52%, and 52% against 51%). The third has an AWPer Hand headshot rate with a lower bound of 70% against a best human of 59%.
- **Most catches are watches.** One number past every human is a watch; a review needs two kinds. Most flagged cheaters were above their rank's range and inside the best humans, or past every human on one weapon.
- **The weapons behind the flags** are the ones aim cheats help most: sniper rifles (21 cheaters, counting the AWPer Hand), the scattergun (12), the Ambassador and Enforcer (5), and SMGs, shotguns and revolvers.
- **Most cheaters still look clean (131 of 189).** A cheat that does not move hit rates, such as a wallhack or ESP, leaves nothing in a per-match count. So does a cheat used in some matches and not others, or one tuned to stay inside human aim.

**Headshot counts that do not add up are dropped.** In about 1 in 80 sniper matches, the log's headshot count is larger than the player's hits with every gun that can headshot. Capped, those matches read as "every hit was a head" and set a best human of 100% on the sniper rifle that nobody could pass. The converter now sends no hitbox for those matches.

**Kills per minute helps a little.** Across classes, kills per minute and, for snipers, headshot kills per minute rank cheaters above honest players far more often than not (83% of the time for snipers). Declared as extra numbers, they added one review and two watches, all banned cheaters, and moved no honest player. They add little because fpsdet flags a number only past the best human measured, and most cheaters, though better than average, stay inside that range. Better than average is not evidence fpsdet acts on; that is what keeps honest players out of review.

**What this shows.** With many server-logged matches per player, fpsdet does what it says on real play: it flags banned cheaters at about 14 times the rate of never-banned players with the same evidence, and keeps review for the confident cases. It is not a complete detector. On these logs it sees hit rates and kill rates only, so it catches cheaters whose aim is better than every human's, and it waits for two kinds of evidence before asking a person to review.

## Detector by detector

`fpsdet evaluate` measures each detector on this run, among the accounts it could run on. The full report is [evaluation.md](evaluation.md); [docs/calibration.md](../../docs/calibration.md) explains the method. It changes no threshold.

| Detector | RGL cheating-ban labelled | Never-banned comparison |
| --- | --- | --- |
| rank tail (above the rank's range) | 44 of 182 (24.2%, 95% CI 18.5–30.9%) | 28 of 1,605 (1.7%, 1.2–2.5%) |
| accuracy past every human | 11 of 182 (6.0%, 3.4–10.5%) | 0 of 1,605 (0.0%, 0.0–0.2%) |
| headshot rate past every human | 5 of 101 (5.0%, 2.1–11.1%) | 2 of 407 (0.5%, 0.1–1.8%) |

Kills per minute and headshot kills per minute fired too rarely to measure, so they are descriptive only. The other 17 detectors are not observable on per-match totals: no timing, positions, view angles, movement or visibility. They are reported that way, never as 0%.

An offline research layer, [strength.md](strength.md), fits label-conditioned evidence ratios on a frozen half of these accounts and checks them on the other half. It changes nothing here, and the measurement above stays the result ([docs/calibration.md](../../docs/calibration.md#evidence-strength-results)).

## Limits

- **Per-match totals, not single shots.** The log has each weapon's shots and hits for the match, so fpsdet sees the right counts per match but no timing, positions or view angles. There are no gear rules, distance or information checks here.
- **Competitive TF2 is its own population.** Players in league lobbies are better than the average public-server player, and cheaters there tend to be careful. That makes this a hard test.
- **Labels are RGL's.** A cheating ban is a league decision, sometimes a mirror of another league's. Some banned players may not have cheated in every match before the ban.
- **Unbanned is not proven honest.** A few unlabelled cheaters are likely among the honest players.
