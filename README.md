# detect-FPS-hackers

An answer key, and the scorer that grades it. Thirty-two fake players. A known decision on each one. The tool reads JSON a dedicated server already can write and writes a case a person opens. It does not install on a player's PC. It does not ban.

```bash
PYTHONPATH=src python3 -m fpsdet demo
```

Expect `weight-cheat` as review and `blasted` as clean. The last line is `Planted cases matched profiles/example-loadout.json.` Then open `demo/board.html`. The header is 11 to open, 4 to watch, 16 clean, 1 held. Tape 01 is a 10 kg kit at 7.1 m/s beside the same sprint tagged as a blast. If those two decisions move, the scorer changed. The later pair is `wire-lock` as review and `picture-track` as clean: same enemy, same hits, one crosshair on the snapshot and one on the picture the client draws.

```bash
PYTHONPATH=src python3 -m fpsdet score examples/shot.jsonl --profile profiles/example-loadout.json
```

That file is one legal shot, one legal step, and one 18 m/s step tagged as an explosion. Expect `p-1044` as `insufficient_data`. The explosion is excluded. One fast sample is not a case.

Gear checks are ordinary: the server's own speed cap, the gun's own cycle, the recoil floor of that build. They catch blatant cheats and miss anything that stays inside the cap and jitters its timing. The clock catches a command that cancels the server's own kick on the same tick, and stays quiet when the same pull happens one shot later. You need both numbers logged or that check does nothing. The third check is sustained aim on a body this client was not allowed to see or hear. It can catch a cheat that aims at every body in the snapshot. It cannot catch a DMA read of the drawn frame or a capture card. The fourth check is the two positions of a player they can see. The wire is the snapshot the server just sent. The picture is where the official client draws them, one interpolation delay later. A person aims at the picture. Sustained aim that is closer to the wire, before that delay has elapsed, is a review. The same hits on the picture stay clean. Standing still is not a signal. A capture card, a cheat that waits out the interpolator, a server that scores the wrong timeline, and a listen server still get through. A person still reviews. `automated_action` is `none`.

The 2021 essay is archived at [docs/archive/2021-whitepaper.md](docs/archive/2021-whitepaper.md). The z-score sum, the universal weapon tables, and DynamoDB-as-the-lake are retired.

The desk is one HTML file and does not need a network. It shares type with the site when `site/fpsdet.css` is available. j and k move through the tapes. The row at the top is the rest of the site. The first tape stays the 10 kg sprint. Eleven of the thirty-two players are a review: the weight break, the stock rifle with no kick, a legal camera whose command is the server kick flipped on the same tick, the fire-rate macro, a legal cycle with no variation, aim that tracked a hidden mover, the aimer past every measured elite, aim noise that drops only while the server says this client could not have known, a second same-tick cancel whose leftover is what another account is carrying, aim that stayed on a private replay while every public chart stayed ordinary, and aim that matched the wire snapshot ahead of the picture. The same kick pulled one shot late, a corner that was only pre-aimed, aim that is smooth all the time, aim that got quiet because the player could hear the target, a callout slow enough to have been a voice, a fight on the enemy this client could see, the same hits stuck to the picture, the blast, the two-frame glitch, the adrenaline pen, and the modded gun stay clean. A second account with that leftover, and a teammate who swings the hidden enemy faster than a voice, are watches.

## The public pages

`site/index.html` says what this is. `site/scoring.html` is how a line becomes a case. `site/wire.html` is the JSON a dedicated server writes. `site/games.html` is Tarkov, Call of Duty, Battlefield, and WARDOGS. `site/source.html` is the files and the commands. From the repo, each of them links to `demo/board.html`. The desk links back. The pages say why a dedicated server can see a cheat that left the PC, what still gets through, and ask people to break the planted cases. They do not ban anyone. This does not end cheating.

```bash
PYTHONPATH=src python3 -m fpsdet pages
```

That writes those pages and a fresh `board.html` into `_site/`. `.github/workflows/pages.yml` publishes that folder to GitHub Pages on a push to `main`. Turn the source on once: repository Settings → Pages → Build and deployment → Source → GitHub Actions. The address is `https://nimdy.github.io/detect-FPS-hackers/`.

## The signal

Cheats change the software. The player still has to move and shoot inside the gear they have equipped. Once a baseline exists for that rank, that weapon, and that build, a sustained number is evidence.

Two checks, in order:

1. **Gear rules, from day one.** The server already knows the speed cap for this weight and stance, the refire time of this weapon, and the recoil floor of this mod set. A 10 kg kit that sprints like a 3 kg kit, a rifle that cycles faster than its own bolt, or a stock gun with no kick, held long enough to be a pattern, is a case. A blast, a ragdoll, a vehicle, a parachute, an ability, or a two-frame glitch is not.
2. **Human baselines, after you have players.** Accuracy, headshot rate, engagement distance, and any extra number you add (ADS time, sway, stamina) are scored against people in the same rank and against the best humans you have actually measured. Beating your rank while staying inside those humans is a watch: a smurf or a good player. Clearing the best measured humans on more than one rate is a review.

Player reports do not add to the score. They move that player to the front of the scan queue, clean numbers included. A brigade can make you look today. It cannot convict anyone.

## What the kernel cannot see

A kernel driver watches a process on the player's PC. A DMA card, a second PC, and a capture card put the cheat on a machine that driver is not running on. The dedicated server never sees that process. It sees the command stream it accepted, and it already knows facts that client was not supposed to have: who was visible, who made a sound, and when.

Another cutoff on speed, recoil, or accuracy will be humanized. The part worth keeping is the clock. A person acts late. A script acts on the server's own kick, on a target the server has not revealed yet, or on the snapshot before the picture has caught up. Five checks use that, and only when the server already knows the fact:

1. **Aim noise, only while nothing was knowable.** If the aim gets quieter only on shots the server marks `unknowable`, that is a review. Visible and audio are the knowable baseline. A player who is smooth the whole time stays clean. Omit the label and the sample is skipped. A wrong `unknowable` label manufactures the case.
2. **The same leftover, across customers.** Subtract the server kick and the previous kick. What remains is the humanizer. The same leftover on two accounts does not convict anyone. If one of them is already a review, the other moves up the scan as a watch. If neither is a review, both are watches.
3. **A teammate who moves before a voice could.** Only after someone in the party is already a review for tracking a hidden mover. Swings on that same still-hidden enemy, faster than `voice_min_ms` (default 350), are a watch. A human callout delay stays clean.
4. **A private replay.** The server plays someone else's real movement on a different heading, where this client has no sight and no audio, and it does not mark the body invisible. Sustained aim on that path is a review. The speed, recoil, and accuracy charts are allowed to look ordinary. A short crossing stays clean. A body this client could see, labeled private, manufactures the case.
5. **The picture is late.** The server measures angular error to the snapshot it just sent, angular error to the position the official client is drawing, and the interpolation delay. Sustained aim that explains the wire and not the picture is a review. The same hits on the picture stay clean. Standing still is nothing. There is no new accuracy cutoff and no new profile knob. A server that scores the wrong timeline manufactures the case.

A person still opens the case. `automated_action` stays `none`. This does not end cheating. It is the evidence that is left once the cheat has left the PC.

## Run the planted matches

```bash
PYTHONPATH=src python3 -m fpsdet demo
PYTHONPATH=src python3 -m unittest tests.test_fpsdet
```

`fpsdet demo` writes `demo/board.html` and prints the same decisions in the terminal. That planted set is the spec: a rage aimer, a 10 kg player sprinting at the 3 kg cap, a no-recoil stock rifle, a legal camera whose command cancels the server kick on the same tick, the same kicks pulled one shot late, a fire-rate macro, a legal cycle with zero variation, aim that tracked a hidden mover, a pre-aimed corner with none of that time, aim noise that drops only while the target was unknowable, the same drop while the target was audible, aim that is smooth on both sides, a same-tick cancel whose leftover another account is carrying, that second account, a teammate who swings the hidden enemy 40 ms later, a teammate whose swings are slow enough to have been a callout, aim that stayed on a private replay of someone else's movement, a fight on a visible enemy that only clips that path, aim that matched the wire snapshot ahead of the picture on a visible enemy, the same hits stuck to that picture, a legal heavy runner, an explosion throw, a two-frame glitch, an adrenaline-style server cap, a modded gun whose lower recoil is legal, a brand-new gun with no baseline yet, a reported player whose aim is ordinary, a rank outlier, an account that stopped looking like itself, and a 10-shot sample that is refused.

`review` means a person should open it. `watch` means monitor, and look now if they were reported. `insufficient_data` means the window is too small or the build has no humans yet. `automated_action` is always `none`.

## Wire it to a game

The dedicated server writes one JSON object per shot and, for movement, per sample. [docs/integration.md](docs/integration.md) has Unity, Unreal, and Godot shapes. [docs/games.md](docs/games.md) maps Escape from Tarkov, Call of Duty, Battlefield, and WARDOGS onto the same fields. [profiles/example-loadout.json](profiles/example-loadout.json) is a synthetic weight-and-recoil game. [profiles/wardogs.json](profiles/wardogs.json) holds the public WARDOGS weight classes with sprint speed left unset until your server fills it.

Preferred over any curve we ship: send the cap the server used for that sample.

- `expected_max_ground_speed_mps` wins over the weight table. That is how an adrenaline pen, a perk, tac-sprint, or a slide stays legal.
- `expected_min_recoil_pitch_deg` wins over the mod table. A new attachment does not need a profile edit before it is safe.
- `displacement_cause` is `none` during a normal sprint, and `explosion`, `knockback`, `ragdoll`, `vehicle`, `parachute`, `ability`, `launch`, `ladder`, `zipline`, `teleport_volume`, or `admin` when the game already knows why the body moved. `unknown` is dropped, not flagged.
- `on_ground` must be true for a sprint to count. Jumps and throws through the air are not ground speed.

A listen server whose host is the client can forge every one of these fields. Run this on a dedicated server that decides hits itself. Client-authoritative hit detection will lie, and the baseline will learn the lie.

## Train a baseline, then score

```bash
PYTHONPATH=src python3 -m fpsdet ingest week.ndjson --lake ./lake --dt 2026-10-02
PYTHONPATH=src python3 -m fpsdet baseline --lake ./lake \
  --profile profiles/example-loadout.json \
  --out baselines/week.json \
  --history baselines/week-players.json
PYTHONPATH=src python3 -m fpsdet score --lake ./lake \
  --profile profiles/example-loadout.json \
  --cohort baselines/week.json \
  --history baselines/previous-players.json \
  --reports reports.json \
  --out cases/
```

`cases/` gets one JSON file and one offline HTML page per player, plus `scan-index.json` (reported players first) and `review-index.json` (evidence first) and `features.csv` for whatever model you want to train later.

`--reported-only` scores the reported accounts and leaves the rest of the lake for the batch. That is the expensive pass. The statistical pass itself is a count, not a model call.

Add a stat by sending the number and declaring it. No code change:

```json
"extra_metrics": [
  {
    "name": "ads_ms",
    "source": "ads_ms",
    "kind": "supporting",
    "direction": "low",
    "min_samples": 30,
    "group_by": ["skill_band", "weapon_class", "weight_class"]
  }
]
```

A new weapon or mod set with no curve and fewer humans than `min_cohort_players` is listed as untrained and is not flagged. The lake is the training set. After the cohort fills in, that build starts scoring. Set `"aim_group": "weapon_id"` when each gun should have its own aim baseline instead of sharing a class.

Freeze the cohort on a window you still trust. If a rank is already mostly cheating, the ceiling becomes the cheat and the detector goes quiet. Refit after you remove those accounts. Scoring a file against itself prints a warning, because the players under review are inside the baseline. Leave-one-out keeps a single outlier from hiding inside their own number. It does not save a poisoned population.

Pass the last trusted cohort when you build the next one:

```bash
PYTHONPATH=src python3 -m fpsdet baseline --lake ./lake \
  --profile profiles/example-loadout.json \
  --previous baselines/week.json \
  --out baselines/next.json
```

A thick ceiling that jumps by `poison_jump` (default 0.08) on accuracy, headshot rate, or geometry rate is stamped `poison_risk`. Score prints that and does not change a decision. No `--previous` leaves the stamp `unchecked`.

Each case carries `seal`, a SHA-256 of the player, the game, the decision, and the reasons. Reports and the brief are not in the hash. It identifies the packet a reviewer saw. It is not a ban.

## AI, if you want it

Any OpenAI-compatible chat endpoint. That covers hosted APIs and a box you run yourself (vLLM, Ollama, LM Studio, a studio gateway).

```bash
export FPSDET_AI_BASE_URL=http://127.0.0.1:11434/v1
export FPSDET_AI_MODEL=your-model
export FPSDET_AI_API_KEY=          # omit for a local server
PYTHONPATH=src python3 -m fpsdet score ... --ai
```

The model writes a short brief on cases that are already `review` or `watch`, and on anyone who was reported. It receives the aggregate case with the player id, party, and match ids stripped. It does not receive the lake. It cannot change the decision. Sending raw shots to an API is the slow, expensive, leaky path. Aggregating first, then asking for language on the queue, is the fast one.

`features.csv` is there for a gradient-boosted model or whatever stack your team already trains, once reviewers have labeled cases. Unsupervised models on unlabeled play mostly rediscover your best humans. Labels first.

## Where the bytes go

Append-only lake, partitioned `game=<id>/dt=<day>/events.ndjson`. A solo dev can stop there. A studio can land the same lines in object storage and aggregate with DuckDB or ClickHouse. A bus (Kafka, Redpanda, NATS) is worth adding only when you already run one.

Elasticsearch and Splunk remain fine viewers if the building already pays for them. They are not the detector. The case JSON is what you index. See [dashboards/README.md](dashboards/README.md).

## What still gets through

A wallhack that never moves the aim early, never spends time on a mover the server still has hidden, never stays on the private replay, aims at the picture rather than the wire, and does not get quieter when that mover exists, leaves no gear-rule break. `hidden_track_ms`, `private_track_ms`, `information_state`, `wire_error_deg`, `picture_error_deg`, and `interp_delay_ms` only exist when the queries that produce them are honest. A bad query, a visible enemy labeled `unknowable`, a visible body labeled private, or a comparison against a timeline the client was not shown, manufactures the case. A DMA read of the frame the game actually drew, a capture-card aimbot looking at pixels, and a cheat that reimplements the official interpolator and waits out the delay, do not have to lock the snapshot. Standing still is not a signal. Shots the server can prove went through geometry catch some of the rest, once you send `through_geometry`. Reports still jump the queue. They still do not convict anyone.

An assist that matches the human mean, the human variance, a human lag, and a noise sequence of its own stays inside the rules above. Matching only the average is no longer enough for recoil or for fire rate. A shared leftover moves the second account up the scan. It does not convict them, and a unique leftover does not match. A sudden jump against that account's own history becomes a watch. Reports make it the next file you open.

A callout at a human voice lag stays clean. The watch is the swing that lands before a voice could have carried the name.

A server-paced full-auto (`server_paced`) and timestamps that all land on `tick_ms` are not metronomes. A kick that barely changes from shot to shot cannot separate a same-tick cancel from a one-shot-late pull.

A speedhack that flickers for a few frames on purpose can hide in the glitch filter. The filter exists so a ragdoll you forgot to tag does not ban someone. Tag the cause. The bar is a sustained run (`speed_min_run`, default 25 ground samples), not a single velocity spike.

A pulsed macro and a one-off physics glitch are the same shape. Sustained means consecutive samples, because a long untagged explosion would also trip a "percent of the window" rule. Fix the emitter rather than lowering the bar.

A listen server whose host is the client, or a game that lets the client decide hits, can forge every field on this page. The contract is a dedicated server.

## Docs

- [docs/scoring.md](docs/scoring.md) — the bars, precisely enough to reimplement
- [docs/integration.md](docs/integration.md) — dedicated server, Unity, Unreal, Godot
- [docs/games.md](docs/games.md) — Tarkov, Call of Duty, Battlefield, WARDOGS
- [docs/operations.md](docs/operations.md) — lake, priority, AI, privacy, appeals

## Layout

```
profiles/          game curves. Replace the numbers with your server's.
src/fpsdet/        reference scorer, zero runtime dependencies
tests/             the behavior lock, including the planted demo
schema/            event and profile shapes
examples/          one shot, one movement sample, a Unity emitter
cases/             gitignored output
```

## Before other people send patches

There is no license yet. Add one before you take contributions. Patches that describe how to build a cheat, or that weaken the innocence rules so a blast becomes a ban, are off the point of this repo. Patches that add a game's server-side fields, a profile, or a failing test for a false positive are the point.
