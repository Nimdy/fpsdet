# Active challenges

## Why active challenges

Most checks ask whether a player behaved strangely. An elite human can, and fpsdet will not accuse a player for being good. A challenge asks a different question: did this player react to something the legitimate client could not know?

The server places a probe that the official client never draws or plays, and records where the aim went. Following a probe that no legal channel showed is a different kind of evidence from an outlier. It does not depend on skill.

A challenge never decides anything about an account. Its finding is a review, every case still has `automated_action: "none"`, and a person decides.

## What exists

| | Status |
| --- | --- |
| `occluded_motion_replay`, version 1: the private replay as a planned challenge | Implemented |
| Planning with the server secret, `fpsdet challenge keygen`, `plan`, `verify`, `types` | Implemented |
| Other challenge types | Not built. The framework allows them; none is claimed |
| Running a challenge inside a game | The game server's job. This repository plans, links and judges; it never touches a live server |

## Three things kept apart

- **The spec** of a challenge type: what it is, which legal channels it defeats, what its realization is made of, what the game server must keep true, and what beats it. Public, in `fpsdet/challenge.py`. `fpsdet challenge types` prints it.
- **The realization** of one challenge: which recorded route the body replays, how far back, on what heading, and where. Derived from the server secret. The game server holds it while the challenge runs. fpsdet never writes it anywhere.
- **The plan**: the challenge id, the subject, the match, the window, and a commitment to the realization. Public in the sense that it holds no key and no realization. Before its match, its windows still say when each challenge runs, so it stays on the server until the match is over.

## Security model

- **The source is public, this recipe included.** Nothing depends on keeping the code secret. Unpredictability rests on one thing, the server secret: at least 32 random bytes that never leave the game server.
- **Every challenge is keyed** to the game, the match, the player, the plan's nonce, the challenge type and version, and the challenge's place among that player's challenges in that match. Without the secret, none of a challenge's id, window or realization can be computed from those. Knowing past challenges, their plans or even their realizations does not help predict the next one. That is what HMAC-SHA256 as a pseudorandom function gives, and nothing more is claimed.
- **The secret is read from a file the operator names** (`--secret-file`; `/dev/fd/N` works for a descriptor), **or from `FPSDET_CHALLENGE_SECRET`**, as hex. It never goes in a game profile: profiles are public configuration. `fpsdet challenge keygen` writes a new one to a new owner-only file and never overwrites one.
- **Only `fpsdet/challenge_plan.py` reads the secret.** Detection code never imports it; a provenance test fails if it does. Scoring, reviewing and verifying a case never need the secret.
- **The secret object prints as `ServerSecret(<redacted>)`**, cannot be pickled, and no error message contains any of it.
- **Where the secret and realizations never appear:** plan files, case JSON, evidence observations, evidence packets, `ops.json`, the dashboard, the review desk, the public pages, AI prompts, golden files, committed fixtures, and command output. Tests generate a fresh secret, write a plan and run the commands, and search what they wrote and printed for the secret and each realization, as hex in either case, either half of the hex, and base64.

What the model does not protect against:

- **A compromised game server.** Whoever has the secret can compute every challenge.
- **A plan leaked before its match.** It gives the windows, though not where or what.
- **A body the client can actually see or hear.** If the game server places it badly, it manufactures evidence. That is an emitter bug, the same as labelling a visible enemy hidden.
- **A game server that lies.** fpsdet judges what the server reports.

## The recipe: `fpsdet.challenge/1`

HMAC-SHA256 (RFC 2104), keyed with the server secret, used as a pseudorandom function. Every text input is UTF-8, framed as its byte length in 4 bytes, big-endian, followed by the bytes:

```text
field(x) = uint32_be(len(x)) || x

message(purpose, index) =
    field("fpsdet.challenge/1") || field(purpose) ||
    field(game_id) || field(match_id) || field(subject_id) || field(nonce) ||
    field(challenge_type) || field(decimal version) || field(decimal index)
```

| Purpose | Index | Output |
| --- | --- | --- |
| `id` | the challenge's counter | `challenge_id` = `ch-` + the first 24 hex digits of HMAC(secret, message). Public |
| `realization` | the challenge's counter | `material` = HMAC(secret, message), 32 bytes. Secret |
| `schedule` | draw number 0, 1, 2… | one draw, HMAC(secret, message) as a big-endian integer |

Each realization parameter is `low + P mod (high - low + 1)`, where P is HMAC(material, field("fpsdet.challenge/1") ‖ field("parameter") ‖ field(name)) as a big-endian integer. With 256-bit draws the modulo bias is far below anything measurable.

```text
commitment = "sha256:" + hex(SHA-256(
    field("fpsdet.challenge-commitment/1") ||
    field(canonical JSON of the plan's public fields, without the commitment) ||
    field(material)))
```

Canonical JSON is sorted keys, no spaces, UTF-8. Each prefix is used for one purpose only, and the purpose is inside every message, so no output can stand in for another. A game server in another language can implement the recipe from this section. `DerivationTest.test_the_recipe_is_what_the_docs_say` rebuilds it from `hmac` and `hashlib` alone, and `test_the_recipe_is_pinned` pins its output for a public test key (the bytes 0 to 31) so it cannot drift silently.

## Ids, realizations and commitments

- **`challenge_id`** is the challenge's public name, carried by events and evidence. It is 96 bits of HMAC output, so it cannot be guessed without the secret, and it is stable: the same secret, game, match, player, nonce, type, version and counter always give the same id. Changing the budget moves the windows, never the id.
- **The realization** is the material and the parameters made from it. The game server gets it from `challenge_plan.realize(secret, plan, budget)` in its own process, or from its own implementation of the recipe. It has no serialized form, and it prints without its values.
- **The commitment** is SHA-256 over the plan's public fields and the realization material.
  - **It proves** that the plan was made for one particular realization. Nobody, the operator included, can later claim a different one, because SHA-256 is collision-resistant. Opening it means revealing that one challenge's material. That gives away no other challenge and not the secret.
  - **It does not reveal** the realization: the material is 256 bits nobody can guess.
  - **It does not prove who made it.** It is not signed: anyone can commit to anything.
  - **It does not prove when it was made.** To show a plan existed before its match, publish or escrow its digest somewhere timestamped before the match starts.
  - **It does not prove the game server ran that realization,** or that the body was invisible.
- **The plan digest** (`fpsdet.challenge-plan/1`) is SHA-256 over the recipe name, a zero byte, and the canonical JSON of every public field, the commitment included. It shows a record was not edited after it was digested. It is not a signature either.

## Planning

```bash
fpsdet challenge keygen --out /srv/game/challenge.key        # once; owner-only, never overwritten
fpsdet challenge plan --profile profiles/your-game.json --match m-1001 \
    --player p-7 --player p-9 --to-ms 1500000 \
    --secret-file /srv/game/challenge.key --out m-1001.plan.json
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--player` | required | Each player to challenge; repeat it |
| `--to-ms` | required | The latest a window may end, in match time |
| `--from-ms` | 60000 | No challenge before this: the player has to be playing first |
| `--count` | 1 | Challenges per player in this match, at most 4 |
| `--cooldown-ms` | 60000 | At least this long between one window's end and the next one's start |
| `--min-duration-ms`, `--max-duration-ms` | 8000, 16000 | Each window's length is drawn between these |
| `--nonce` | fresh random | Pass a plan's nonce to reproduce it exactly |
| `--secret-file` | `FPSDET_CHALLENGE_SECRET` | Hex secret |

The plan file holds the format (`fpsdet.challenge-plans/1`), a notice, the game, match and nonce, the type and version, the budget, the type's requirements for the game server, and one record per challenge:

```json
{
  "challenge_id": "ch-2167d11b4beef7b5f1d4a268",
  "challenge_type": "occluded_motion_replay",
  "version": 1,
  "game_id": "g",
  "match_id": "m1",
  "subject_id": "x",
  "counter": 0,
  "nonce": "00112233445566778899aabbccddeeff",
  "start_ms": 397250,
  "end_ms": 409594,
  "commitment": "sha256:e53f8ea7…",
  "plan": "sha256:5ba47fb1…"
}
```

(That record is the public test vector, not a real plan.)

Planning refuses:

- more than 4 challenges for one player in one match;
- a play window too short for the count, durations and cooldown;
- windows shorter than the profile's `hidden_track_min_ms`, which could never reach the review bar;
- a profile that declares a knowledge channel the type does not defeat, such as `radar`. Its results could never count, so the player is not probed for nothing.

## Scheduling

Each player's windows in one match come from that player's own `schedule` draws. The lengths are drawn first. The time left over in the play window, after the lengths and the cooldowns, is cut at sorted random points, so every gap is as likely as any other, and nothing about one window follows from the last. The result:

- **Spacing.** Every window is inside the play window, and windows are at least the cooldown apart.
- **Unique ids.** Counters are 0, 1, 2… in time order, so ids are unique per player and per match. A different player, match or nonce gives unrelated ids.
- **No repeats.** Two challenges never share a realization. Planning stops if two ever derived the same material; a test derives 10,000 and finds 10,000 distinct ones.
- **No reuse across plans.** A match id used twice gets a fresh nonce, so its challenges are not repeated.
- **A small budget.** The cap is deliberate. More probes per player make a challenge-aware cheat's job easier, and add nothing a reviewer needs.

## Verifying a plan

```bash
fpsdet challenge verify m-1001.plan.json                                    # anyone
fpsdet challenge verify m-1001.plan.json --secret-file /srv/game/challenge.key  # the server
```

- **Without the secret,** it checks the format, every record's digest, that every record matches the file's game, match, nonce and type, that the ids are unique, and that the schedule keeps its budget. This shows the file is consistent. It does not show who wrote it.
- **With the secret,** it derives every record again and reports `reproduced from the secret, N of N`, or, for each record that differs, its id and which field: `challenge_id`, `start_ms`, `end_ms` or `commitment`. It never prints a value that came from the secret. A record edited and digested again passes the public check and fails this one.

## Gameplay safety

The game server must keep these true for `occluded_motion_replay`. They are in every plan file under `requirements`:

- place the body only where this client's line-of-sight and audio queries both fail, for the whole window, and end it early if either would succeed;
- send it to this client only;
- never draw it in the stock client, and never show it in the scoreboard, kill feed, radar, minimap or any other UI;
- it makes no sound;
- it has no collision: it blocks no player, projectile or trace;
- it cannot be hit, damaged or killed, and deals no damage;
- it changes no movement, score, objective, economy or match result;
- send it with the same fields as a real player, with no decoy bit;
- keep it away from any enemy this client can see or hear, so aim on a real enemy does not cross it.

fpsdet cannot check any of these. It never runs inside a game.

## Before the challenge engine: the private replay

This is the private replay as it stood at `eabd87c`, written down before anything about it changed. `tests/test_challenge.py` (`LegacyPrivateReplaySemanticsTest`) pins it.

### The field

`private_track_ms` on a shot: milliseconds that shot's aim cone contained the server's private replay, another player's motion on a different heading, placed where this client's line-of-sight and audio queries both fail. It is the only field. It carries no target, no challenge, no time window and no plan.

- **Parsing.** A number becomes a float. `null` or a missing key is not sent. A string is a parse error, and the line is dropped. A negative number is accepted, although the schema says at least 0, and it adds nothing.
- **Summarizing** (`summarize._track_times`). The player's canonical timeline is read one server moment (match and `t_ms`) at a time. When the shots at a moment that carry the field agree on its value, it adds one sample, cut to the time since the player's previous moment in the same match. The first moment of a match is not cut. When they disagree, the moment adds nothing and the weapon's `knowledge_skipped["private_replay"]["disagreed"]` counts it. A value of 0 or less adds nothing. The sample goes to the shot's aim key.
- **Knowledge.** Every sample has the same knowledge, `knowledge.private_knowledge(profile)`: vision and audio absent, recent perception not applicable. With the default channels it is `unknowable`. A game that declares any other channel gets `unknown`, and every sample is left out as `unchecked`. The shot's own `information_state` is about `enemy_id`, another target, and is not read.
- **The bar** (`signals.private_finding`, the hidden-mover bar). On one aim key, at least `hidden_track_min_samples` (8) samples above 0, and an exact sum of at least `hidden_track_min_ms` (1200). Samples from every match in the window add to one total. Aim keys are not pooled.
- **The reason.** `{key} aim stayed on a private replay for {total} ms across {n} shots`. The check id is `private_replay`, in the dashboards' information group, explained by `tape-replay`. The v1 seal hashes the reason.
- **The observation.** Family `information`, kind `private_replay`, role `review`, key the aim key. `match_ids` is every match the aim key was used in, not only the ones with replay time. Evidence: `shots`, `total_ms`, and `thresholds` (`min_shots`, `min_total_ms`). Context: the line, and the knowledge state with its basis and what it left out.
- **The decision.** A review, even when the weapon has too few shots for aim to be scored.

### Samples in the data

- **Planted.** `replay-lock` has 20 samples of 80 ms, 1,600 ms in all, and is a review. `real-fight` crosses the replay on 4 shots, 160 ms, and is clean.
- **Synthetic week.** About 20% of matches carry a replay. On those matches ESP cheaters lock onto it on 40% of their shots for 80 ms, and anyone crosses it on 3% of shots for 20 to 60 ms. One weekly case and four nightly cases are reviews on it, and in each it is the only finding.
- **CS2 and TF2.** No field, so no sample.

### What it assumed about the target

- The field names no target. Two replays in one match, or a replay and an ordinary hidden enemy, add into one number.
- The body's knowledge comes from the field's definition: the server placed it where this client's line-of-sight and audio queries fail, and it was never perceivable. Nothing checks it.
- Nothing says when the replay was active, so a sample counts at any time.
- Nothing says the server meant to run a replay for this player in this match, or lets anyone reproduce one.

### Where the documentation says it is invisible

- README, "Add one thing: decoys".
- `docs/integration.md`, the `private_track_ms` paragraph: no decoy bit; the stock client drops the body before draw because it is occluded.
- `docs/scoring.md`, "Private replay".
- `docs/games.md`, the field table.
- `docs/culling.md`: a body sent into a culled region is never drawn by the stock client.
- `docs/players.md`: the general disclosure to players.
- `docs/knowledge-engine.md`: the replay body's row.
- `site/decoys.html`: the loop and the rules that keep honest players safe.

### What the tests proved

- The bar: 20 samples of 80 ms fire; 4 of 40 ms and one of 5,000 ms do not (`tests/test_fpsdet.py`).
- The planted pair's decisions, and the desk's drawing of the replay: the rotated, delayed route behind the wall, and the aim on it or away from it.
- The shot's labels and the grace window are not read, and a declared extra channel abstains (`tests/test_knowledge.py`).
- Every planted and synthetic case, field by field (`tests/test_golden.py`).
- Parsing, the cut, a match boundary, simultaneous shots, the bar's edges, aim keys not pooled, matches pooled, the case and the planted ids (`tests/test_challenge.py`).

### What they did not prove

- That the body was invisible and silent to this client. That is the server's claim.
- Which replay a sample came from, or that the samples came from one replay.
- That a replay was running when the sample was taken.
- That the server planned it for this player, or that anyone could reproduce it.
- That a cheat could not predict it.
- That crossings in many unrelated matches cannot add up to the bar. They can: matches are pooled.
- That a real enemy the client could see, in the same aim cone, did not explain the sample.
- That the replay did not change the game. That is the engine's side, outside this repository.
