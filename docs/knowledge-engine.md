# The knowledge engine

Some of fpsdet's strongest checks are about information. Aim that stays on an enemy this client could not see or hear is a review. Aim that goes quiet only while the enemy is unknowable is a review. All of them rest on one question:

**Could this legitimate client actually know about this enemy at this moment?**

`fpsdet.knowledge` is the one place that answers it. Every information check asks it, rather than each reading the event's labels its own way.

## What knowledge is

Knowledge is information a legitimate player, using the official client, could lawfully have. That includes seeing the enemy, hearing them, or having seen or heard them a moment ago. It is not what a cheat could extract. The server sent many things the player was never shown.

## Three answers

| Answer | Meaning |
| --- | --- |
| `known` | At least one legal channel exposed the enemy to this client. |
| `unknowable` | Every legal channel the game declares was checked by the server, and none exposed the enemy. |
| `unknown` | Anything else: a declared channel nobody checked, or telemetry that contradicts itself. |

Information checks act only on `unknowable`. `unknown` never counts as `unknowable`.

## The rule that matters most

**Unchecked is not absent.**

If the game has a legal way for a player to know something, such as a teammate's callout or a radar, and the server does not report whether it did, fpsdet cannot say the player did not know. The answer is `unknown`, and the checks abstain. Missing telemetry can only make fpsdet say less. It never makes a finding stronger.

## Channels

Each channel says one of `known`, `absent` (checked, did not expose the enemy), `unchecked`, or `not_applicable` (cannot carry this enemy, as for the private replay body below).

| Channel | Status | Where it comes from |
| --- | --- | --- |
| `vision` | Implemented | `information_state`, or `vision_state` |
| `audio` | Implemented | `information_state`, or `audio_state` |
| `recent_perception` | Implemented | `since_perceived_ms` against the profile's `hidden_grace_ms` |
| `team_share`, `radar`, `objective`, `ability`, `spectator` | Can be declared; nothing reports them yet | — |

A game says which channels it has in its profile:

```json
"knowledge_channels": ["vision", "audio"]
```

That is the default, and it is exactly what `information_state: "unknowable"` has always meant: the server's line-of-sight and audio queries both failed.

A game with a radar declares `["vision", "audio", "radar"]`. Until its server reports radar, every information check abstains on it. That is deliberate. Declaring a channel is the honest statement that the game has it; reporting it is the work that lets fpsdet use it.

The answer, given the declared channels:

- any channel `known` → `known`;
- every declared channel `absent` (or `not_applicable`) → `unknowable`;
- otherwise → `unknown`, and also whenever the telemetry contradicts itself.

### Reading the legacy label

| `information_state` | Channels | Answer |
| --- | --- | --- |
| `visible` | vision known | known |
| `audio` | audio known | known |
| `unknowable` | vision absent, audio absent | unknowable (with the default channels) |
| not sent | none | unknown |

`visible` says nothing about audio, and `audio` says nothing about vision. The mapping claims no more than the integration docs promise.

### Reporting channels one at a time

A server that runs some queries and not others can send `vision_state` and `audio_state`, each `known`, `absent` or `unchecked`. For example, `"vision_state": "absent"` with no audio report means the enemy was not visible and nobody listened, so the answer is `unknown`. Where these fields and `information_state` disagree, the shot is a conflict and the answer is `unknown`.

## Recently perceived

A player who saw an enemy duck behind a wall a moment ago knows where they went. Tracking that spot, or spraying it, is human.

When the server sends `since_perceived_ms`, the time since this client last saw or heard the enemy:

- under `hidden_grace_ms` (default 1000), the enemy is `known` through `recent_perception`;
- at or over it, that channel is `absent`.

The boundary is strict: 999 ms is known, 1000 ms is not.

Recent perception only adds knowledge. When it is not sent, it decides nothing, because the vision and audio verdicts already describe the moment of the shot. A game that wants it reported for every shot declares `recent_perception` in `knowledge_channels`.

The quiet-aim check reads something more specific. It compares aim on an enemy the client is seeing or hearing with aim on an enemy it cannot know. An enemy known only from memory is neither, so those shots join neither side, as before.

## Which enemy

A shot can be about more than one target, and each has its own knowledge:

| Target | Its knowledge |
| --- | --- |
| The enemy the shot was about (`enemy_id`) | `information_state`, `vision_state`, `audio_state`, recent perception |
| The enemy `hidden_track_ms` says the aim stayed on | By that field's definition the server's line-of-sight and audio queries both failed for it, so it supplies vision and audio absent where the shot says nothing. If the shot says the enemy was seen or heard, it was known. If the shot says a channel was not checked, the two contradict each other. Recent perception applies |
| The private replay body, or a challenge's | Placed where this client's line-of-sight and audio queries fail, and never perceivable, so recent perception is not applicable. The shot's own labels are about another enemy. For a planned challenge, the channels come from its type ([challenges.md](challenges.md)), and an enemy the same event names must itself be unknowable |

## Wire and picture

The wire check is not about whether the enemy was known. It is about which version of the enemy the aim followed.

- **The wire** is the snapshot the server just sent. It is data in the client's memory, available to any software running there.
- **The picture** is where the official client draws that enemy, one interpolation delay earlier. It is what a person can see.

A person aims at the picture. A packet or memory aimbot aims at the wire. Data in memory is not information a person perceived, and the check measures exactly that gap. `fpsdet.knowledge.presentation` keeps the two apart, and the wire finding's context names them.

## What each check reads

| Check | Counts a sample when |
| --- | --- |
| Hidden mover | the tracked enemy is `unknowable` |
| Private replay, and planned challenges | the replay body is `unknowable`: every declared channel is one the replay is known to defeat |
| Quiet aim | the shot's enemy is seen or heard (the knowable side), or `unknowable` (the unknowable side). Everything else joins neither |
| Teammate (voice) contacts | the shot's enemy is `unknowable` |
| Wire | the server sent the wire, the picture and the delay |

The teammate check stays a relationship check. It times a teammate's swings against a confirmed wallhacker's, and it never assumes the teammate could not have been told. If a game declares `team_share`, contacts become `unknown` until the server reports it, and the check abstains.

## When the emitter is wrong

A server that marks a visible enemy as hidden manufactures cases. The integration docs say so field by field. The engine cannot see the world, but it does notice telemetry that contradicts itself:

- a shot whose `information_state` and `vision_state` or `audio_state` disagree;
- hidden-mover time on an enemy the shot also marks visible;
- a channel the hidden-mover field says was checked, but `vision_state` or `audio_state` says was not;
- shots at the same server moment that disagree about a track time or its knowledge.

None of these counts toward a finding. The case says how many there were, on which weapon, and asks for the emitter to be checked. A contradiction never makes evidence stronger. A declared channel that no event reports is also said once on the case.

## In the evidence

Each information finding carries the knowledge that let it count, in its observation's `context`:

- the status;
- the declared channels;
- the basis;
- for the hidden mover, the recent-perception state of the samples it counted;
- for each check, how many samples it kept out, and why: `seen`, `heard`, `recent`, `unchecked`, `conflict` or `disagreed`.

Context is not part of an observation's identity, so adding it moved no observation id.

## What changed when the engine arrived

Every case was compared from just before the engine (event normalization, `7ef9c1b`) to after it (`tools/regress.py migrate`). The data sets were the planted demo, the synthetic week (weekly and nightly), and the real CS2 and TF2 reruns.

| | Cases | Decisions | Reviews | Watches | Reasons | Context lines | Observation ids | Seals | Observation context | Packets |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Planted | 32 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 32 |
| Synthetic weekly | 400 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 400 |
| Synthetic nightly | 2,669 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 14 | 2,669 |
| CS2 | 1,529 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1,529 |
| TF2 | 2,764 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2,764 |

What moved:

- **Observation context.** The information findings gained their knowledge context: the planted hidden mover, private replay, quiet aim, wire and teammate watch, and the same findings in the synthetic week.
- **Packets.** Every packet moved because two of the digests it binds moved. The profile digest moved because profiles gained `knowledge_channels`, and the detector digest moved because the code changed. The input digests did not move: no event's bytes changed.
- **Real data.** CS2 and TF2 carry no information fields, so nothing but provenance could move there.

All 1,529 CS2 and 2,764 TF2 packets verify.

**The one deliberate change:** a shot labelled `visible` that also carries hidden-mover time is no longer counted. It contradicts itself, and a visible enemy is known. No case in any data set had one.

**Cost**, measured back to back against `7ef9c1b` on one machine:

- On the synthetic week (178,020 events, most of them labelled), summarizing went from 0.37 s to 0.43 s. Each distinct combination of labels is worked out once and shared.
- Scoring the real runs, which carry no labels, did not change beyond run-to-run noise.
- Peak memory rose by about 58 MB on 4 million events, for the two new optional event fields.
