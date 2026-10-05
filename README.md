# detect-FPS-hackers

**fpsdet** is server-side anti-cheat evidence for first-person shooters. Your dedicated game server writes one JSON line per shot and per movement sample. fpsdet scores each player against the game's own rules, against the best humans you have measured, and against what that player's client could have known, then writes a case file for a person to review.

- It runs on the server's logs. Nothing is installed on a player's PC.
- It never bans. Every case has `automated_action: "none"`; a person decides.
- It needs a dedicated server that decides hits itself. A listen server or client-side hit detection can forge every field.
- It is plain Python 3.11+ with no dependencies.

It is for indie and small studios, community server operators, and anyone who wants to check how a server can see a cheat that left the PC.

## View the Demo
https://nimdy.github.io/detect-FPS-hackers/

## Try it in two minutes

```bash
PYTHONPATH=src python3 -m fpsdet demo
```

That scores 32 planted players with a known answer each. Eleven are cheats that should be reviewed, four should be watched, sixteen are honest players (most of them look suspicious at first glance), and one has too little data. The last line is `Planted cases matched profiles/example-loadout.json.` If a decision moves, the scorer changed.

Then open `demo/board.html` in a browser, the review desk. It works offline and has two tabs, plus a link to the real CS2 matches (see below):

- **Operations** is what a week looks like to the people who run the queue. It covers 400 synthetic players and 17 planted cheats, scored every night against last week's frozen baseline:
  - the open reviews, and the queue night by night
  - which checks fired, and where players sit against the best humans
  - whether reports track the evidence
  - a sortable queue with a case drawer
  - which fields the server is actually sending

  An answer check shows that every blatant cheat reached review and no honest player did. The two closet aimbots tuned under the ceiling, and one shared-humanizer buyer, got through. All of it is invented, and the page says so.
- **Answer key** is the 32 planted players, one worked example ("tape") per check. Its header reads 11 to open, 4 to watch, 16 clean, 1 held. Press j and k to move between tapes. Tape 01 is a 10 kg kit sprinting at 7.1 m/s beside the same sprint tagged as an explosion: the first is a review, the second is clean.

## Try it on real Counter-Strike 2 matches

[examples/cs2](examples/cs2/README.md) converts CS2CD, a public CC BY 4.0 dataset of CS2 matchmaking matches with hand-labelled cheaters, into fpsdet events, then scores them against a baseline built from clean matches. The README there has the results, including what one match per player cannot show.

The scored run is in the desk too: **C · Real CS2 matches** in the [review desk](https://nimdy.github.io/detect-FPS-hackers/board.html), or `demo/cs2.html` after `fpsdet demo`. Every player has fpsdet's decision, the reason, and the dataset's label beside it.

Run it yourself, on more matches or on your own server's logs, and share what you find with the [result form](https://github.com/Nimdy/detect-FPS-hackers/issues/new?template=real_data_result.yml).

## Try it on real Team Fortress 2 matches

[examples/tf2](examples/tf2/README.md) scores players banned for cheating by RGL, a competitive TF2 league, on their server-logged matches from logs.tf, beside the honest players from the same lobbies. From the servers' shot counts alone, fpsdet picked 97 of 2,764 players for a person to look at, and 51 of them (53%) are banned cheaters: nearly 8 times better than picking at random. With the same evidence per player, it flagged 37.5% of banned cheaters and 2.6% of never-banned players. Every review it opened was a banned cheater, and none of 1,746 never-banned players went to review. Standard library only. The scored run is in the desk as **D · Real TF2 matches**.

## Run the whole pipeline on a sample week

```bash
PYTHONPATH=src python3 -m fpsdet sample --out week.ndjson
PYTHONPATH=src python3 -m fpsdet ingest week.ndjson --lake ./lake --dt 2026-10-02
PYTHONPATH=src python3 -m fpsdet baseline --lake ./lake \
  --profile profiles/example-loadout.json \
  --out baselines/week.json \
  --history baselines/week-players.json
PYTHONPATH=src python3 -m fpsdet score --lake ./lake \
  --profile profiles/example-loadout.json \
  --cohort baselines/week.json \
  --reports examples/reports.json \
  --out cases/
```

1. `sample` writes a synthetic population of 144 players plus the 32 planted ones, about 32,500 events.
2. `ingest` files them into a lake.
3. `baseline` learns what humans on each rank and weapon look like. It prints one `POISON RISK` line, for match `raid-9`: a planted aimbot's lobby that hit 92% of its shots. On a real week, review that match, or rebuild with `--screen-matches` to leave it out.
4. `score` writes the cases.

`cases/` gets one JSON file and one offline HTML page per player. Each case JSON has the sentences a reviewer reads and, under `evidence`, the same findings as data: which check fired, the role it played in the decision, and the numbers behind it ([docs/observations.md](docs/observations.md)). It also gets:
- `scan-index.json` (reported players first)
- `review-index.json` (strongest evidence first)
- `features.csv`
- `ops.json` and `dashboard.html`, the operations view of this batch

Score each night into its own folder and `fpsdet dashboard cases/2026-09-26 cases/2026-09-27 ... --out week.html` merges them into one week. A review opened on any night stays open. To build the same panels in Grafana, Kibana or Splunk instead, [dashboards/README.md](dashboards/README.md) maps each panel to the case-JSON fields that drive it.

Next week, score the new events against this week's baseline and pass `--history baselines/week-players.json`. An account whose accuracy jumps well past its own history then becomes a watch. This sample has no earlier week, so `account-changed` stays clean here.

`--reported-only` scores just the reported accounts, for a quick pass between batches. `features.csv` is there for whatever model your team trains once reviewers have labelled cases. Labels come first: an unsupervised model on unlabelled play mostly rediscovers your best humans.

## What it checks

**Gear rules**, from day one, from numbers the server already has:

1. **Speed.** Ground speed over the cap for this loadout weight, held for 25 consecutive samples.
2. **Fire rate.** Shots faster than the gun's own cycle.
3. **Metronome.** Legal shot timing with no human variation (a macro).
4. **Recoil floor.** Recoil far under the minimum for that weapon and attachments (no-recoil).
5. **Recoil mirror.** The player's view command cancels the server's kick on the same tick. A person reacts a shot later. In games with a fixed spray pattern, the pattern is removed first, because practiced players pull it on time.

**Human baselines**, once you have players: accuracy, headshot rate, engagement distance, shots through geometry, and any number you declare. These are compared with the player's rank and with the best humans measured. Better than your rank but inside the best humans is a **watch** (a smurf or a good player). Past every measured human on two kinds of number is a **review**.

**Information checks** use what the server knows about what this client could see and hear:

1. **Hidden mover.** Sustained aim on an enemy this client could neither see nor hear.
2. **Quiet aim.** Aim noise that drops only while the target is unknowable to this client.
3. **Private replay, now an active challenge.** A body the server plays only where this client cannot perceive it. Aim that stays on it is a review. The server can plan each one with a secret, for one player, in one match, in one window, and fpsdet then binds the finding to that exact challenge ([docs/challenges.md](docs/challenges.md)).
4. **Wire, not picture.** The client draws enemies one interpolation delay late. A person aims at the drawn picture; a packet aimbot aims at the newer snapshot.

**Batch checks** look across players:

1. **Shared leftover.** Two accounts whose recoil command, after the kick is removed, carries the same humanizer signature become watches.
2. **Faster than a voice.** A teammate of a confirmed wallhacker who swings the same hidden enemy within 350 ms, faster than a callout, becomes a watch.

Player reports never add to the score. They only move a player to the front of the scan queue. A brigade can make you look; it cannot convict anyone.

[docs/scoring.md](docs/scoring.md) has every bar precisely enough to reimplement.

## Protecting honest players

Most of this code exists so that an honest player is not flagged. Each rule below has a planted honest twin in the demo or the tests.

- **Physics you forgot to tag.** Explosions, knockback, vehicles, ladders and similar causes are excluded. An untagged one-frame spike is a glitch, not a case.
- **Perks and attachments.** If the server sends the cap or floor it actually used, that wins over any table in the profile.
- **A memorised spray pattern.** The mirror check uses only the part of the kick that changes between sprays.
- **Sound.** Tracking footsteps through a wall is a skill. Shots labeled `audio` never count as hidden tracking.
- **A target who just ducked out of sight.** Within `hidden_grace_ms` (default 1 s) of last seeing or hearing them, it is not hidden tracking.
- **The best player in the game.** A number counts as "past every human" only beyond the best human measured, not the top 5%. The same number on several guns counts once.
- **One hot match.** Shots in one match are not independent. A rate that swings between matches gets a wider bound.
- **A short night.** Medians (distance, recoil, declared metrics) are tested on a confidence bound, as rates are. Thirty shots cannot put a player past a line that three hundred would not.
- **Many players, many pairs.** The shared-leftover check scales each spray position by its own noise and needs a Fisher z of 5. Four hundred honest players produce no matches.
- **New guns and thin data.** A weapon or build with fewer than 30 measured humans is listed as untrained, and its human-baseline numbers are not flagged.

## What still gets through

This does not end cheating:

- A DMA read of the frame the game actually drew.
- A capture-card aimbot looking at pixels.
- A cheat that waits out the interpolation delay.
- An assist that copies human timing, human noise and human lag.
- A wallhack that never aims at a hidden enemy.
- A server that labels a visible enemy as hidden manufactures cases. That is an emitter bug, and the docs call it out field by field.

## Pair it with server-side culling

If the server never sends an enemy's position to a client that cannot see or hear them, wallhacks, ESP and radars have nothing to draw, even on a second PC. That is prevention. fpsdet is detection, and its information checks score what culling cannot remove:

- the moment before an enemy rounds a corner,
- sound positions,
- what teammates share.

[docs/culling.md](docs/culling.md) explains how the two fit together.

## Add one thing: decoys

Statistics catch a cheater who is past every human. A careful one stays inside the human range: on the real TF2 matches, 131 of 189 banned cheaters still looked clean. A decoy is evidence that does not depend on skill. The server sends one client a body that client cannot see or hear, replaying another player's real movement; the game never draws it, and only software reading memory or packets can follow it. fpsdet scores the tracking from one field, `private_track_ms`, or, for a planned challenge, from `challenge_id` and `challenge_track_ms`: `fpsdet challenge plan` derives each player's challenges from a server-held secret and writes only ids, windows and commitments, so the code can be public and the next challenge still cannot be predicted ([docs/challenges.md](docs/challenges.md)). Challenge-aware cheats can ignore probes, and pixel aimbots never see them; that page lists what gets through. The [decoys page](https://nimdy.github.io/detect-FPS-hackers/decoys.html) explains it with diagrams, lists the rules that keep honest players safe, and answers whether it can be countered.

## Wire it to your game

The dedicated server writes one JSON object per shot and per movement sample.

- [docs/integration.md](docs/integration.md) has the fields and Unity, Unreal and Godot emitters. [examples/unity/BaselineEmitter.cs](examples/unity/BaselineEmitter.cs) is a complete Unity one.
- [docs/games.md](docs/games.md) maps the fields onto well-known games.
- [profiles/](profiles/) holds game profiles. Replace the numbers with your server's.

The smallest possible input is [examples/shot.jsonl](examples/shot.jsonl): one legal shot, one legal step, and one 18 m/s step tagged as an explosion.

```bash
PYTHONPATH=src python3 -m fpsdet score examples/shot.jsonl --profile profiles/example-loadout.json
```

Expect `p-1044` as `insufficient_data`. The explosion is excluded, and one sample is not a case.

Start with the gear rules; they need an afternoon. The information checks need visibility and audio queries on the server, which is real engine work. Send each field only when you can measure it honestly. A missing field turns a check off. A wrong one frames players.

Rules of thumb:

- Send the cap the server used on each sample: `expected_max_ground_speed_mps` and `expected_min_recoil_pitch_deg`.
- Tag `displacement_cause` whenever something other than the player moved the body.
- Send `spray_index` with recoil fields, so the pattern can be removed and humanizers line up.
- Send a rank (`skill_band` or `skill_prior`) if you have a matchmaker. Without one, everyone on the server is one population, which is fine for a community server.
- Set `recoil_pattern` in the profile: `learnable` (the default) for fixed spray patterns, `random` only if every kick is drawn fresh.

## How many players you need

A human baseline needs `min_cohort_players` (default 30) players per rank and weapon, each with at least 40 shots on that weapon in the window. A community server with 40 regulars and no ranks trains in a week or two. Until then, aim numbers are not scored and only the gear rules and information checks run.

Freeze the baseline on a window you trust. If a rank is already full of cheaters, the ceiling becomes the cheat. `baseline` sets each match beside the others in the window and flags a lobby far past the median match, the kind where cheaters played each other. `--screen-matches` leaves those out. Pass the previous baseline with `--previous`, and a ceiling that jumps is stamped `poison_risk` too. Add a number without code by declaring it under `extra_metrics` in the profile.

## AI briefs, if you want them

`score --ai` asks any OpenAI-compatible endpoint (a hosted API, or vLLM, Ollama or LM Studio on your own box) for a plain-language brief on cases that are already `review` or `watch`, and on reported players.

```bash
export FPSDET_AI_BASE_URL=http://127.0.0.1:11434/v1
export FPSDET_AI_MODEL=your-model
export FPSDET_AI_API_KEY=          # omit for a local server
PYTHONPATH=src python3 -m fpsdet score ... --ai
```

The model receives the aggregated case only. Every player id in it, including other accounts named by the batch checks, is replaced with an alias. Party ids, match ids and the seal are removed. It never sees raw events, and it cannot change the decision.

## Where the data goes

The lake is append-only NDJSON, partitioned `game=<id>/dt=<day>/events.ndjson`. A solo developer can stop there. A studio can land the same lines in object storage and aggregate with DuckDB or ClickHouse. Elasticsearch and Splunk are fine viewers for the case JSON. They are not the detector; see [dashboards/README.md](dashboards/README.md).

Every pair of accounts on a weapon is compared for shared leftovers. That is fine for a community server and slow for a large population; [docs/scoring.md](docs/scoring.md) says how to split it.

## Glossary

| Term | Meaning |
| --- | --- |
| case | One player's decision, reasons, and numbers, as JSON plus an offline HTML page |
| review / watch / clean / insufficient_data | A person should open it / monitor it / nothing found / too little data or no baseline yet |
| held | The desk's word for `insufficient_data` |
| cohort, baseline | The distribution of one number per player, per rank and weapon |
| ceiling | The best humans measured: the highest rank band with enough players |
| kick | The recoil the server applied to the camera on a shot |
| command | The player's own view input on that tick |
| leftover | The command after the kick and the previous kick are subtracted |
| humanizer | Noise a cheat adds to look human |
| wire / picture | The newest snapshot the server sent / where the client draws that enemy, one interpolation delay later |
| unknowable | The server's visibility and audio checks both say this client could not perceive that enemy |
| private replay | A body the server plays only where this client cannot see or hear it |
| challenge | A private replay the server planned with a secret: one player, one match, one window, one id. See [docs/challenges.md](docs/challenges.md) |
| `displacement_cause` | Why the body moved, when the player did not move it: `explosion`, `vehicle`, `ladder`, … |
| seal | SHA-256 of the player, game, decision, and reasons. It identifies the packet a reviewer saw |
| tape | A worked example on the review desk |

## Docs

- [docs/scoring.md](docs/scoring.md): every bar, precisely enough to port
- [docs/integration.md](docs/integration.md): the event fields and engine emitters
- [docs/challenges.md](docs/challenges.md): active challenges, the server secret, and what they do not catch
- [docs/culling.md](docs/culling.md): server-side culling, and how it fits
- [docs/games.md](docs/games.md): mapping the fields onto well-known games
- [docs/operations.md](docs/operations.md): lake, priority, AI, privacy, appeals
- [docs/players.md](docs/players.md): a page operators can link for their players

The public site is `site/*.html`. `fpsdet pages` writes it, plus a fresh desk, into `_site/`, and `.github/workflows/pages.yml` publishes it to <https://nimdy.github.io/detect-FPS-hackers/>. The 2021 essay is archived at [docs/archive/2021-whitepaper.md](docs/archive/2021-whitepaper.md).

## Layout

```
src/fpsdet/        the scorer, no runtime dependencies
tests/             behaviour locks, including the planted demo and the recorded cases in tests/golden/
tools/regress.py   snapshot a scored run and diff two snapshots case by case
profiles/          game profiles; replace the numbers with your server's
schema/            event and profile JSON Schemas
examples/          a shot and movement file, report counts, a Unity emitter, real CS2 and TF2 matches
docs/              the docs listed above
site/              the public pages
demo/board.html    the review desk, regenerated by `fpsdet demo`
dashboards/        the built-in dashboard, and the case-JSON fields for your own tools
```

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md). The rule is to plant the cheater and the honest twin first, then write the rule that separates them. False-positive reports are the most useful thing you can send.

Report a bypass or a way to frame an honest player privately; see [SECURITY.md](SECURITY.md). Do not post working cheat code in issues.

## License

[PolyForm Small Business 1.0.0](LICENSE.md). It is source-available, not OSI open source. Use for the benefit of a company with fewer than 100 people and less than US$1M revenue in the prior tax year (in 2019 dollars, adjusted for inflation) is permitted: you can use it, change it, and ship it in your game. Past that, you need a commercial license from ZeroBandwidth. If you are unsure whether your community or project fits, [open an issue](https://github.com/Nimdy/detect-FPS-hackers/issues) and ask. The fonts in `site/fonts/` are under the SIL Open Font License, which sits beside them.
