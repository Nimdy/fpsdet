# Try fpsdet on logs you already have

fpsdet scores a copy of your dedicated server's logs, on any machine with Python 3.11. Nothing is installed on your servers or your players' PCs, and scoring makes no network connection unless you ask for an AI brief with `--ai`. It bans no one: every case says `automated_action: "none"`. Production never knows it ran.

The same steps, with pictures, are on the [Try it page](https://nimdy.github.io/detect-FPS-hackers/try.html).

## 1. Get fpsdet

```bash
git clone https://github.com/Nimdy/detect-FPS-hackers
cd detect-FPS-hackers
PYTHONPATH=src python3 -m fpsdet demo     # the last line should say the planted cases matched
```

## 2. Export a week or more of shots and movement

From wherever your server logs land, export one row per shot and one per movement sample (10 per second is plenty), as CSV with a header row or as JSON lines. The more of these the rows carry, the more checks can run:

| You have | fpsdet field | Turns on |
| --- | --- | --- |
| match, account, time | `match_id`, `player_id`, `t_ms` | Required on every row |
| shots and hits per weapon | `event_type: shot`, `weapon_id`, `hit`, `hitbox`, `distance_m` | Accuracy, headshots and distance against the best humans you have measured (once a weapon has 30 players) |
| ground speed and the cap your server applied | `event_type: movement`, `speed_mps`, `on_ground`, `expected_max_ground_speed_mps` | Speed over the gear cap, from the first match |
| why the body moved, when the player did not | `displacement_cause` | Keeps blasts, vehicles and ladders out of speed cases |
| shot timing per weapon | `t_ms` on each shot, cycles in the profile | Fire rate and metronome |
| the kick and the view command on the same tick | `applied_recoil_pitch_deg`, `compensation_pitch_deg`, `spray_index` | No-recoil and the recoil mirror |
| what this client could see and hear | `information_state`, `hidden_track_ms`, and the rest on [Wire a game](https://nimdy.github.io/detect-FPS-hackers/wire.html) | The information checks |

A field you do not have just turns its check off. A wrong one frames players: send only what your server measured.

## 3. Convert it, with pseudonyms

`convert.py` copies only fields fpsdet knows, replaces account ids with salted pseudonyms, and checks every value against the schema. [sample.csv](sample.csv) is a made-up export with its own column names, a player name and an address; [map.json](map.json) maps its columns.

```bash
python3 examples/historic/convert.py export.csv --map my-map.json --game my-game --out events.ndjson
```

```
108 of 108 rows written to events.ndjson.
  columns not copied (fpsdet has no field for them): ip, name
  fields present: displacement_cause, distance_m, event_type, expected_max_ground_speed_mps, game_id, hit, ...
```

- **The salt** goes in `fpsdet-salt.hex`, owner-only. Keep it private, and keep it: the same salt gives the same pseudonyms next time.
- **Which pseudonym is an account?** `python3 examples/historic/convert.py --who ACCOUNT_ID`.
- **No map is needed** for columns already named like fpsdet's fields, and `--set event_type=shot` fills a column your export does not have.

## 4. Score it

```bash
PYTHONPATH=src python3 -m fpsdet score events.ndjson --profile examples/historic/profile.json --out cases/
```

[profile.json](profile.json) is the smallest profile: every default. It is enough when your logs carry the server's own caps. To declare weight classes, weapon cycles and recoil floors instead, copy [profiles/example-loadout.json](../../profiles/example-loadout.json).

Run on the sample, this gives one review: a player held 7.0 m/s against the server's own 5.6 m/s cap for 30 samples. The player who moved just as fast because an explosion moved them is not a case.

**A fairer baseline.** For a fairer comparison with your best players, build the baseline from an earlier window you trust, then score a later one:

```bash
PYTHONPATH=src python3 -m fpsdet baseline last-month.ndjson --profile my-profile.json --out baseline.json --screen-matches
PYTHONPATH=src python3 -m fpsdet score this-week.ndjson --profile my-profile.json --cohort baseline.json --out cases/
```

## 5. Read what came out

- **`cases/dashboard.html`:** the batch in a browser. Its "What the server sends" panel shows how many events carried each field, and which check each field turns on.
- **`cases/review-index.json`:** the queue, strongest evidence first.
- **Each case,** as `.json` and `.html`: the decision, the reasons, the numbers, and under `evidence` which checks could run at all.

A review is a case for a person to open, not a verdict.

## 6. Tell us what you found

Use the [result form](https://github.com/Nimdy/detect-FPS-hackers/issues/new?template=real_data_result.yml). Counts are what helps:

- how many players;
- which fields you had;
- what went to review and watch;
- above all, any honest player who came out as a case.

Never post account ids, pseudonyms or names.

## Then, a live test

If the results hold up, the next step is a live test on a dedicated server you run, with players told what is logged. The checks that need it most have only run in fixtures and in one Godot pilot:

- hidden-mover tracking;
- quiet aim;
- the wire against the picture;
- planned challenges.

If you run a server and want to try that, [open an issue](https://github.com/Nimdy/detect-FPS-hackers/issues).
