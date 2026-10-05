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
- **Where the secret and realizations never appear:** plan files, case JSON, evidence observations, evidence packets, `ops.json`, the dashboard, the review desk, the public pages, AI prompts, golden files, committed fixtures, and command output. `SecretLeakTest` draws a fresh secret, plans two players, scores their events with `fpsdet score --challenges --out`, and searches everything that run wrote and printed: each case file and its HTML page, both indexes, `features.csv`, `ops.json`, the dashboard, the evidence and its packet, the exact body an AI brief would send, `challenge verify` with and without the secret, the error a tampered plan raises, and the review desk. It looks for the secret and every realization, as hex in either case, either half of the hex, and base64. Other tests do the same for plan files, every command's output, and error text. The committed golden files and fixtures hold no planned challenge at all.

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

## Linking a response to its challenge

While a challenge's window is open, the game server adds two fields to every shot and movement event of that player:

```json
{"game_id": "your-game", "match_id": "m-1001", "player_id": "p-7", "t_ms": 160200, "event_type": "movement",
 "challenge_id": "ch-c9822fee5e1a48b270e3a78a", "challenge_track_ms": 100}
```

- **`challenge_id`** is the planned id. It is server-side telemetry: never send it to the client.
- **`challenge_track_ms`** is milliseconds, since the previous event that named the same challenge (or since the window opened), that the aim cone contained that challenge's body. Send 0 when the aim was elsewhere: those events are the challenge's eligible samples.

Score with the plans:

```bash
fpsdet score events.ndjson --profile profiles/your-game.json --challenges m-1001.plan.json
```

A sample is read only from an event that names its challenge. Nothing is inferred: the legacy `private_track_ms` on an event that names a challenge is not read, and `challenge_track_ms` without a `challenge_id` is not read either. This closes the old ambiguity about which target a time belonged to. Ordinary hidden-enemy fields are never turned into challenge evidence.

A sample counts only when all of these hold:

| Check | Otherwise, recorded as |
| --- | --- |
| The challenge is in the plans given to this run | `unplanned` |
| It was planned for this player | `other_subject` |
| The event is in the challenge's match | `other_match` |
| The event is inside the window, ends included | `outside_window` |
| The event carries `challenge_track_ms` | `no_measurement` |
| Every event at that moment says the same thing; duplicates count once | `disagreed` |
| The time is above 0, after it is cut to the time since the previous moment that named the challenge (or since the window opened) | nothing tracked |
| No real enemy on the same event explains the aim. If the event names one, it must be unknowable | `seen`, `heard`, `recent`, `unchecked`, `conflict` |
| The challenge's target is unknowable to this client (below) | `unchecked` |

Every gap in the telemetry and every contradiction can only take samples away.

### What the case shows

`evidence.challenges` lists one result per challenge, sorted by id: the challenges this player's events named, and the challenges planned for this player in the matches scored, even with no response. Each result has the challenge id, its plan digest and match, a status, the eligible and tracked samples, the tracked time, and the samples left out by cause:

| Status | Meaning |
| --- | --- |
| `followed` | The challenge's own samples clear the bar (below) |
| `not_followed` | Eligible samples, below the bar |
| `no_samples` | No eligible sample: no response, or none that could be read |
| `abstained` | The challenge could not count: its target was not unknowable (`cause: unchecked`), or it was another player's (`cause: other_subject`) |
| `unplanned` | No plan in this run has this id |

The key is there only when there is a result. The case also says, once, how many events carried challenge time with no id, named unplanned challenges, or named another player's.

## The knowledge requirement

A challenge counts only when the knowledge engine ([knowledge-engine.md](knowledge-engine.md)) says its target was `unknowable` to this client. It does not get around the engine. Each type declares the channels it defeats:

| Type | Defeats | Not applicable | Everything else the profile declares |
| --- | --- | --- | --- |
| `occluded_motion_replay`/1 | `vision`, `audio`: placed where this client's line-of-sight and audio queries fail for the whole window, silent | `recent_perception`: the body was never perceivable | `unchecked`, so the target is `unknown` and the challenge abstains |

A game that declares `radar`, `team_share`, `ability`, `objective` or `spectator` gets no challenge evidence from this type. `fpsdet challenge plan` refuses to plan it, and scoring abstains with `cause: unchecked` if a plan exists anyway. Only a future type, or version, that defeats or checks those channels can count there.

## Scoring

**The bar** is the hidden-mover bar, applied to each challenge on its own: at least `hidden_track_min_samples` (default 8) counted samples above 0, and an exact total of at least `hidden_track_min_ms` (default 1200 ms). One crossing is not a review. A short window cannot reach the bar, and planning refuses windows shorter than `hidden_track_min_ms`.

**Challenges never add up.** Five samples on one challenge and five on another are two challenges below the bar, not one above it. Each result is kept, and every challenge the player was planned is listed, so a reviewer sees "followed 1 of 3" rather than one sum. Weighing repeated, independent challenges together needs calibration, and is left for later.

**A followed challenge is a review.** It adds the reason

```text
aim stayed on challenge ch-c9822fee5e1a48b270e3a78a (occluded motion replay in m-1001) for 1600 ms across 16 samples
```

and the case check `private_replay`, which dashboards already group and the review desk already explains. Like the other information checks, it counts even when the player has too few shots for aim to be scored.

## Evidence

Each followed challenge is one observation:

| Field | Value |
| --- | --- |
| `family` | `challenge` |
| `kind` | `occluded_motion_replay` |
| `role` | `review` |
| `key` | the challenge id |
| `match_ids` | the challenge's match |
| `evidence.challenge` | `challenge_id`, `origin: "planned"`, `type`, `version`, `commitment`, `plan` (the plan digest), `window` (`start_ms`, `end_ms`) |
| `evidence.linkage`, `evidence.scope` | `challenge_id`; `challenge`, meaning this challenge's samples alone |
| `evidence.eligible_samples`, `tracked_samples`, `total_ms` | what the window held and what counted |
| `evidence.knowledge` | the channels the profile declares (`required`), the ones the type defeats, the ones that cannot apply |
| `evidence.thresholds` | `min_samples`, `min_total_ms` |
| `context.knowledge` | the knowledge state, its basis, and the samples left out by cause |
| `context.series` | how many challenges this player was planned in the run, by status |

The evidence binds the challenge by id, plan digest and commitment. It holds no secret and no realization, and nothing in it helps predict another challenge: the next challenge's id, window and realization come from HMAC outputs this one says nothing about. The observation id covers all of it, so the evidence packet binds the challenge through the observation, as it binds every other finding. No new packet recipe was needed.

### Checking a case against its plan

```bash
fpsdet challenge verify m-1001.plan.json --cases review/review-index.json
```

For each planned challenge finding, this checks that the challenge is in the plans given, that its commitment, plan digest, type, version and window match its plan, that it is cited for its own player and match, and that the case's evidence packet verifies. This is the public check. Adding `--secret-file` also shows the secret plans those plans: that check is privileged.

## The legacy private replay

`private_track_ms` still works, on events that name no challenge, exactly as before: the same cut, the same agreement rule at one moment, the same bar, on one aim key, across every match in the window, and the same reason text. The adapter makes its finding the same kind of observation as a planned challenge, with an identity that says what it is:

| Field | Legacy value |
| --- | --- |
| `key`, `evidence.challenge.challenge_id` | `legacy_private_replay:<aim key>`, a label and not a planned id |
| `evidence.challenge.origin` | `legacy_private_replay` |
| `evidence.challenge.type` | `occluded_motion_replay`, what the field always described |
| `version`, `commitment`, `plan`, `window` | `null`: no plan existed, and none is pretended |
| `evidence.linkage`, `evidence.scope` | `private_track_ms`; `aim_key`, every match on one aim key added together |
| `evidence.eligible_samples` | `null`: the legacy path never counted them |
| `context.legacy` | says so in words |

The legacy path is for compatibility and tests. It keeps the weaknesses the audit below lists: no target, no window, and crossings in unrelated matches add up. New integrations should send `challenge_id`.

## False positives

`FalsePositiveControlsTest` holds the honest cases. None is a review:

- a player who never tracks a challenge;
- one accidental crossing;
- brief crossings on four challenges that would clear the bar if added together. They are not added together; the legacy field adds the same crossings up and reviews them;
- the aim on an enemy the client sees, hears, or saw a moment ago, on the same events;
- a profile that declares `radar`, `team_share`, `ability`, `objective` or `spectator`;
- contradictory telemetry: events at one moment that disagree, or a shot whose labels contradict each other;
- events before or after the window, a stale id from another match, an unknown id, another player's challenge, and a run scored without the plans;
- duplicated events, which count once;
- malformed linkage: time with no id, an id with no time, the legacy field on a linked event, and an id with characters an id cannot have, which is a parse error.

`test_missing_or_bad_telemetry_never_strengthens` takes a followed challenge and damages its telemetry six ways: half the ids dropped, half the measurements dropped, a visible enemy on some events, disagreeing duplicates, claims longer than the time between events, some events moved outside the window. In every one, the counted samples and the total stay at or below the undamaged ones.

## Cheats that know about challenges

`AttackerAwareTest` gives one player four challenges in a match, and stands in for six behaviours at the event level. It is not cheat software. It is what each behaviour looks like to the server.

| Behaviour | Result | Why |
| --- | --- | --- |
| A naive packet or memory reader follows every challenge | Review, 4 findings | Each challenge clears the bar alone |
| Follows the first probe, then ignores every later one | Review, 1 finding | It cannot tell a probe from a player until it has followed it |
| Reacts to every probe for about 300 ms, then lets go | **Not caught** | 1,200 ms across four challenges, but never the bar on one. Challenges are not added together |
| Waits 3 s into each window before following | Review | Windows are at least 8 s, leaving time to clear the bar |
| Waits until the last second of the window | **Not caught** | Too little window left to clear the bar |
| Follows every second challenge | Review, 2 findings | Each followed challenge stands alone |
| Follows probes only while a visible enemy is in the same aim cone | **Not caught** | Aim on an enemy the client can see explains every sample |

The three that get through are real limits. Weighing many short, independent reactions together could catch the first two; it needs calibration on real data before it can be trusted with an honest player's crossings, and it is left for later.

## Capability and limits

The type carries a machine-readable entry for the capability matrix (`OCCLUDED_MOTION_REPLAY.capability`; `fpsdet challenge types` prints it):

| | |
| --- | --- |
| Technique | A packet or memory reader that shows, or aims at, players the client was sent but does not draw |
| Evidence | `challenge.occluded_motion_replay` |
| Strength | Strong when every knowledge channel the game declares is one the challenge defeats, and the plan, window and linkage all check out |
| Needs | `challenge_id` and `challenge_track_ms` on the player's events; the plan file at scoring time; `knowledge_channels` no wider than vision and audio |

It is not unbeatable. Challenges do not catch:

- **pixel-only aimbots**, which react to the rendered frame, where the body never appears;
- **DMA or other software that deliberately ignores hidden players**, or drops any player it cannot verify;
- **software that has learned a fixed pattern** in how a game places or routes its challenges. The plan varies when, where, which route and which heading, but the game's own placement rules can still have a shape;
- **information use that never turns into measurable aim toward the probe**: callouts, map awareness, timing;
- **reactions too brief, too late or too rare** to clear the bar on any one challenge;
- **following a probe only when a real visible enemy is in the same aim cone**.

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

## What changed when the engine arrived

Every case was compared from the knowledge engine (`eabd87c`) to the challenge engine with `tools/regress.py migrate`, which names this move `observation-migrated-to-challenge` and checks that the migrated finding kept its role, matches, sample count, total and reason line.

| | Cases | Decisions | Reasons | Seals | Observations migrated | Other observations | Observation ids moved | Input digests | Packets |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Planted | 32 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 32 |
| Synthetic weekly | 400 | 0 | 0 | 0 | 1 | 0 | 1 | 0 | 400 |
| Synthetic nightly | 2,669 | 0 | 0 | 0 | 4 | 0 | 4 | 0 | 2,669 |
| CS2 | 1,529 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1,529 |
| TF2 | 2,764 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2,764 |

- **The six migrated findings** are the planted `replay-lock` and the synthetic `p-0024` (weekly, and nights 2, 3, 5 and 6). Each moved from `information` / `private_replay`, key the aim key, to `challenge` / `occluded_motion_replay`, key `legacy_private_replay:rifle`, with the new evidence shape, so each id moved. Each kept its decision (review), reason, sample count, total, matches and seal. No id was aliased.
- **Packets** all moved because the detector digest moved: the scorer now imports `fpsdet.challenge`, the 15th detector module. No profile or input digest moved. Every CS2 and TF2 packet verifies, and so do the packets written before: the retired kind is still readable.
- **CS2 and TF2** carry no challenge or private replay fields, so nothing else could move.
- **The golden lock** was re-recorded for exactly the six migrated findings.
- **Python 3.11 and 3.12** give the same packet digests and the same plans.

**Cost**, measured back to back against `eabd87c` on one machine:

| | Before | After |
| --- | --- | --- |
| Planning 100 / 1,000 / 10,000 challenges | — | 2 ms / 22 ms / 0.22 s (writing the 5 MB file for 10,000: 0.08 s; checking it without the secret: 0.08 s; reproducing it with the secret: 0.23 s) |
| Synthetic week, summarize (178,020 events) | 0.43 s | 0.435 s |
| Synthetic week, full scoring | 1.23 to 1.24 s | 1.24 to 1.25 s |
| CS2 scoring (4.07 M events) | 11.8 s | 12.2 s |
| TF2 scoring (4.14 M events) | 13.3 s | 13.7 s |
| Peak memory, CS2 / TF2 | 5.83 GB / 5.68 GB | +62 MB / +65 MB, the two new optional event fields |
| 400,000 events, every one naming a challenge | 0.80 s without the fields | 1.56 s with them, about 1.9 µs per challenge event |

Events without challenge fields pay one check each; nothing is built for them.

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
