# The consented honest-human pilot

**Status: designed, instrumented and dry-run with machines. No person has played yet, and there are no human results.** Everything below that is a number comes from machine stand-ins and says so.

**The question:** when honest people play the repository's own Godot pilot ([pilot.md](pilot.md)), normally and aggressively, how often and for how long does their aim behave like challenge tracking? How close do they come to producing challenge evidence?

**What this is:** a small, consented behavioural baseline. It is not a population, a calibration, a validation, or a false-positive rate, and no result from it changes a threshold.

## Consent

Participants read [CONSENT.md](../examples/human-pilot/CONSENT.md) before anything starts. The client shows the essentials again and waits for them to press Y. They are told:

- **What it is:** a research playtest of a practice game, studying honest aim.
- **Hidden probes:** they exist behind walls; they will not see or hear them and need not look for them.
- **What is recorded:** the game server records their movement, aim and shots in this game, and nothing else.
- **No commercial games:** no game, anti-cheat or account is involved.
- **Their identity:** they are a random code.
- **Their choice:** taking part is voluntary, they may stop at any time, and their sessions are deleted on request.

They are not told where or when probes appear, or their ids. That keeps their play honest without hiding the study.

## Privacy

- **The id:** `hp-` and 8 random hex digits, drawn at enrollment. The tool stores no name, email, game account, device id or network address, and never writes one.
- **The network:**
  - The server binds `127.0.0.1`, or a private LAN address the operator gives; it refuses a public one.
  - Neither side logs a peer's address.
- **What the client keeps:** its own frame rate and what the game server sent it. It reads no screen, file, process or memory.
- **After every session:** each publishable file is scanned for IP addresses and the operator's host name, user name and home folder. Any hit stops the study.
- **What is committed:** the design, the consent notice, the aggregate artifact, and replayable telemetry only from participants who ticked the separate publication box.
  - Per-tick study telemetry is never committed: it holds the aim relative to each probe.
  - Secrets are never committed; each is deleted right after its session's post-match check.
- **Retention:** raw session folders are deleted once results are published, at most 90 days after the last session.

## Study design

Fixed in [examples/human-pilot/design.json](../examples/human-pilot/design.json) (`fpsdet.human-pilot-design/1`) before any human session. A change after collection starts is a new study version.

| | |
| --- | --- |
| Participants | target 8, at least 4 to report, at most 12 |
| Sessions | 7 per participant, 4 minutes each, one per mode; free play first, the stress session last, the rest in an order fixed by the participant's id |
| Modes | free play; combat; angle holding; sweep and search; tracking a visible bot; high motion; and the stress session (clear every corner, pre-aim every doorway, flick between likely spots, spray hiding places) |
| Bots | server-driven, visible, never shoot back; how many and how they move is the mode's (`study_arena.gd`) |
| Challenges | `occluded_motion_replay/2` from fpsdet's planner with a fresh secret per session; 4 per session, 8–16 s each, 30 s apart, between 20 s and 230 s |
| Placement | secret-derived: one of four sealed probe rooms, tagged by what players aim at nearby: `door_edge`, `corner`, `chokepoint`, and `long_wall` as the control. Nothing is placed by hand, and nothing avoids or seeks a crosshair |
| Frozen | the P12 pilot's thresholds (8 moments and 1,200 ms per challenge), its aim cone (2° plus the body's angular radius) and its knowledge rules; a 250 ms episode gap |

The probe rooms are sealed and 4 m tall, and no one can jump. So a probe's inside is never in anyone's line of sight. The server still checks that for every tick, as in P12, and would end a challenge whose body became perceivable.

## What is measured

For every challenge, from the server's own per-tick study telemetry and fpsdet's result (never from the client, never inferred intent):

| Metric | Meaning |
| --- | --- |
| eligible, counted moments; counted ms | fpsdet's own numbers, after every exclusion: what the bar is compared with |
| overlap ms | time the aim cone held the body, by tick |
| explained ms | overlap while a bot the participant could see was in the same cone |
| challenge-only ms | the rest |
| episodes | runs of overlap joined across gaps of at most 250 ms; their durations and the longest |
| time to first overlap | from the window's start |
| aim error | the aim's angle to the body: median and lowest |
| turn rate | the aim's angular speed during overlap: median and 95th percentile |
| enemy separation | the angle between the body and the nearest visible bot, during overlap |

The primary outcome is review-grade challenge findings on honest sessions. They are counted at three levels, because one person's sessions and challenges are not independent:

- **Participants:** the primary unit.
- **Sessions.**
- **Challenges.**

With no finding, each level gets an exact one-sided 95% upper bound (1 − 0.05^(1/n)). With any, it gets counts and two-sided Clopper–Pearson intervals. At the target size, zero findings would bound the share at 0.31 per participant (8), 0.052 per session (56) and 0.013 per challenge (224). None of these is a false-positive rate.

The study stops on:

- a body reported known, or an unexplained avatar or sound reported by a participant;
- a leak, or personal data;
- replay differing from live scoring;
- a packet or graph that does not verify;
- review-grade evidence on an honest session.

A stop needs an investigation, recorded with `study.py clear-stop`, before anyone plays again.

## Running it

Follow the operator's checklist in [examples/human-pilot/README.md](../examples/human-pilot/README.md):

1. Enroll each participant.
2. Run their seven sessions.
3. Analyze.

Each session:

- plans with the real planner;
- runs the study scene (`res://study.tscn` in the P12 project; P12's own files are untouched);
- asks four yes-or-no questions;
- scores the telemetry live and offline and checks every packet and graph;
- reproduces the realization with the secret, then deletes the secret;
- scans for leaks and personal data, and checks the stop conditions.

## The machine dry run (not people)

Before any person plays, scripted stand-ins played every mode. This checks the instruments end to end, and checks whether honest-style aiming in this arena already reaches the bar. They are not people and say nothing about how people aim; they are labelled `machine_standin` everywhere, and they can never become the human result or its benchmark addendum.

**The stand-ins:**

| Stand-in | Used for | Behaviour |
| --- | --- | --- |
| tracker | free, combat, tracking | aims at the nearest bot it can see |
| holder | angle holding | holds the doorway's left frame from the spawn |
| sweeper | sweep and search | sweeps the room side to side at head height |
| flicker | high motion | flicks between common angles and random directions |
| prefire | stress | pre-aims nine common angles in turn |

**The comparison:** the controlled follower (P12's stand-in for a reader) played the same arena twice.

The committed dry run is [examples/human-pilot/dry-run/result.json](../examples/human-pilot/dry-run/result.json): 2 stand-in "participants", 14 sessions, 56 challenges, plus 8 follower challenges.

| | Honest-style stand-ins | Controlled follower |
| --- | --- | --- |
| Counted ms per challenge | median 67, 95th percentile 950, max 5,500 | median 13,258, max 15,467 |
| Longest episode | median 117 ms, max 2,634 ms | median 13,417 ms |
| Review-grade challenges | 1 of 56 (1 of 14 sessions) | 8 of 8 |

**The strongest honest-style case crossed the bar.**

- **Who:** the holder stand-in, holding the doorway's left frame from the spawn.
- **What happened:** for that session the secret placed a probe in the sealed room behind that frame. The aim never turned (0°/s) and stayed 2.3–2.6° from the probe's chest, inside the cone.
- **What the server counted:** three episodes of 1.1, 2.6 and 1.8 s, for 56 counted moments and 5,500 ms against a bar of 8 moments and 1,200 ms. No visible bot explained it.

That is ordinary angle holding: a person holding a doorway for a few seconds while a probe happens to sit behind its frame would produce the same review-grade evidence.

**An earlier, exploratory dry run** used code that differed only in how one line was written, and is not committed. In it, the prefire stand-in crossed the bar 3 times in 56 challenges. Each time it held its aim on a corner of the north room's structure from the south room: through the middle wall, at a remembered spot, with the probe behind it.

**The rest of the dry run behaved as designed:**

- The server reported no probe seen or heard.
- Visible bots explained 55% of all overlap, and fpsdet left those moments out.
- Every session replayed offline identically.
- Every packet and graph verified, and every realization reproduced.
- There were no leaks and no personal data.

**What it means:** the P12 bar counts time, not intent. A probe placed behind an angle that someone holds still for more than about a second can be "followed" by honest play. The bar stays frozen for this study: the human sessions will measure how often people actually do this. The study's own rules stop it at the first such case among people, for investigation.

## What this study cannot show

- **Not a rate:** 8 people are not a population; no rate, calibration or validation comes from them.
- **Not about cheats:** nothing about how cheats behave. The follower is a clean stand-in.
- **Not other games:** nothing about other maps, games, latencies, or players who know where probes are.
- **No machine-to-people extrapolation:** the stand-ins' numbers say nothing about people. They show what the instruments measure and what kind of honest behaviour can reach the bar.

## Human results

None yet. When people have played, `study.py analyze` writes `examples/human-pilot/result.json`. `fpsdet benchmark report` then adds it to the benchmark as an addendum after Benchmark v1, with its own class, `consented_human_pilot`, and this section reports it.
