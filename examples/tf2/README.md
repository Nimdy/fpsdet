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
python examples/tf2/tf2logs.py fetch --out $DATA           # each cheater's recent team matches before the ban
python examples/tf2/tf2logs.py convert $DATA               # baseline.ndjson, scored.ndjson, labels.json

PYTHONPATH=src python -m fpsdet baseline $DATA/baseline.ndjson --profile examples/tf2/tf2.json --screen-matches --out $DATA/cohort.json
PYTHONPATH=src python -m fpsdet score $DATA/scored.ndjson --profile examples/tf2/tf2.json --cohort $DATA/cohort.json --out $DATA/cases
python examples/tf2/tf2logs.py report $DATA/cases --labels $DATA/labels.json

# Optional: rebuild the desk's TF2 page from your run, then regenerate the desk.
python examples/tf2/tf2logs.py desk $DATA/cases --labels $DATA/labels.json
PYTHONPATH=src python -m fpsdet demo
```

## Who is in it

- **Labelled cheaters:** RGL accounts banned for cheating (not for helping a cheater, or for selling cheats) with at least 20 team matches on logs.tf before the ban. For each, the most recent team matches before the ban: 6v6, prolander or highlander, 12 or more players.
- **Honest players:** everyone in those same matches whom RGL never banned for anything, with at least 8 matches in the sample. They played the same lobbies at the same level. A fixed bit of each pseudonym sends half of them to the baseline and half to be scored, so nobody is judged against a baseline that includes them.
- A banned player's matches count only before the ban date.

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

Run on 2026-10-04: 4,567 RGL bans, of which 314 accounts banned for cheating. 228 had at least 20 team matches on logs.tf before the ban, and their 20 most recent were fetched: 4,482 matches. 85% of them carry the server's headshot counts. The baseline came from 914 honest players in those matches (670 with enough shots on some weapon); the lobby screen found nothing to leave out. The other half of the honest players, the cheaters, and everyone RGL banned for something else were scored against it.

**fpsdet separated the cheaters from the honest players, and put no honest player in review.**

| | Review | Watch | Clean | Held for too little data | Flagged |
| --- | --- | --- | --- | --- | --- |
| Banned for cheating (192 with aimed shots) | 3 | 50 | 135 | 4 | 28% |
| Never banned (677) | 0 | 12 | 588 | 77 | 1.8% |
| Banned for something else (701) | 0 | 22 | 595 | 84 | 3.1% |

Honest players had fewer matches in the sample (median 5) than the cheaters (median 14), and more matches mean tighter bounds. At equal evidence, players with 15 to 20 matches each:

| | Players | Flagged | Review |
| --- | --- | --- | --- |
| Banned for cheating | 64 | 41% | 2 |
| Never banned | 34 | 2.9% | 0 |
| Banned for something else | 22 | 9.1% | 0 |

- **Every review is a labelled cheater.** All three have revolver or Ambassador accuracy confidently past the best human measured, for example a lower bound of 62% against a best human of 46%.
- **Most catches are watches.** One number past every human is a watch; a review needs two. Many cheaters were past every human on one weapon, or above the rank's range and inside the best humans.
- **The weapons behind the flags** are the ones aim cheats help most: sniper rifles (24 cheaters, counting the AWPer Hand), the scattergun (12), the Ambassador (5), and shotguns, SMGs and revolvers.
- **Most cheaters still look clean (135 of 192).** A cheat that does not move hit rates, such as a wallhack or ESP, leaves nothing in a per-match count. So does a cheat used in some matches and not others, or one tuned to stay inside human aim.

**What this shows.** With many server-logged matches per player, fpsdet does what it says on real play: it flags labelled cheaters at many times the rate of honest players, and reserves review for the confident cases. It is not a complete detector. On these logs it sees hit rates only, so it catches cheaters whose aim is better than humans', and it waits for two kinds of evidence before asking a person to review.

## Limits

- **Per-match totals, not single shots.** The log has each weapon's shots and hits for the match, so fpsdet sees the right counts per match but no timing, positions or view angles. There are no gear rules, distance or information checks here.
- **Competitive TF2 is its own population.** Players in league lobbies are better than the average public-server player, and cheaters there tend to be careful. That makes this a hard test.
- **Labels are RGL's.** A cheating ban is a league decision, sometimes a mirror of another league's. Some banned players may not have cheated in every match before the ban.
- **Unbanned is not proven honest.** A few unlabelled cheaters are likely among the honest players.
