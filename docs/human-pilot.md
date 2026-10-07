# The consented honest-human pilot

**Status: designed, instrumented, dry-run with machines, and amended three times before collection. No person has played yet, and there are no human results.** Everything below that is a number comes from machine stand-ins and says so.

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

Amendment 1 ([below](#amendment-1)) adds instruments and collection rules, and changes none of this.

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

Amendment 1 adds, for analysis only:

| Metric | Meaning |
| --- | --- |
| probe distance | from the eye to the body's chest, each tick |
| probe ground speed | the body's ground speed, in m/s |
| probe and aim motion | in the world: how fast the body, and the point the aim passes at the body's distance, moved over the last 250 ms, as angles at that distance |
| overlap, split four ways | **both still** (each under 2°/s); **holding**: the aim point still while the body moved; **following**: both moving, the aim point within 45° of the body's direction of motion; **otherwise**: the aim point moving in any other way |

**How the motion is measured:**

- **In the world, not as seen from the eye:** a player's own movement turns the aim and the body's direction together, which would look like following.
- **Over 250 ms, the episode gap:** per tick, a hand's jitter hides where the aim went.
- **The 2°/s line:** under it, the aim point moves less than 0.5° in that window, a quarter of the cone's 2° margin.

Two earlier drafts were replaced after machine smoke runs, before any person played. Per-tick motion hid the follower's tracking. Motion as seen from the eye made a strafing, pre-aiming stand-in look like it followed the probe.

**The primary question (amendment 2):** did at least one protocol-valid honest session produce an unexplained review-grade challenge finding? The study stops on its first finding and continues past 4 participants only without one. So it is a falsification test, not a way to estimate a rate.

Findings are counted at three levels, because one person's sessions and challenges are not independent:

- **Participants:** the primary unit.
- **Sessions.**
- **Challenges.**

What each answer allows:

- **A finding:** the finding and descriptive counts. No rate and no interval, at any level.
- **No finding, and the planned group completed:** an exact one-sided 95% upper bound (1 − 0.05^(1/n)) at each level, labelled as following the pre-declared continuation rule. At the target size, that would be 0.31 per participant (8), 0.052 per session (56) and 0.013 per challenge (224). None of these is a false-positive rate.
- **No finding yet, and the group incomplete:** descriptive counts only.

The study stops on:

- a body reported known, or an unexplained avatar or sound reported by a participant;
- a leak, or personal data;
- replay differing from live scoring;
- a packet or graph that does not verify;
- review-grade evidence on an honest session.

A stop needs an investigation, recorded with `study.py clear-stop`, before anyone plays again. A stop for review-grade evidence on a protocol-valid honest session cannot be cleared: collection ends there (amendments 1 and 2). A crossing on a session already invalid before it was scored still stops the study, but it does not answer the primary question, and that stop may be cleared.

## Amendment 1

Declared in [examples/human-pilot/amendment-1.json](../examples/human-pilot/amendment-1.json) on 2026-10-06, after the machine dry run and before any human session. It binds `design.json` by digest.

**Unchanged:** the participants, sessions, modes, challenges, schedule, rooms, the bar, the cone, the episode gap, placement, scoring, the analysis and the stop conditions.

**Added:**

- **Staging:** the first 4 participants finish all seven sessions before anyone else is enrolled. Collection continues toward 8, at most 12, only if none of them has an unexplained review-grade finding. `study.py enroll` enforces the order and the maximum.
- **On the first such finding:**
  1. Stop at once.
  2. Preserve the session and replay it offline.
  3. Verify its packet and graph, and inspect its overlap episodes.
  4. Classify the honest behaviour that produced it.
  5. Change no threshold, and propose a separate discrimination research phase.
- **Controls check:** before any participant, `study.py controls-check` drives the unchanged human client with injected keyboard and mouse events under a virtual display. It checks the consent key, mouse look, the arrow keys, W and the fire button, and that the server saw the player move and fire.
- **Practice:** 90 seconds per participant, in free mode with no challenge, in a data folder of its own that is never analysed. It confirms the controls and a client frame rate of at least 30. If the mouse cannot be captured, the participant plays with the arrow keys.
- **A fifth question,** "Did the controls behave normally?". Each answer may carry an optional short comment, kept only in the session's private folder.
- **Motion metrics** (above) and closeness:
  - per participant, whether they ever reached any overlap, 500 ms counted, 1,200 ms counted, or review-grade;
  - per session, any overlap, repeated overlap, and review-grade.
- **Build binding:** each session records the engine binary's SHA-256 and the study code's identity.
- **The regression fixture:** the angle-holding case below is kept permanently as a false-positive control. Any future challenge detector must show that it is no longer review-grade, and why.
- **The dry run stays as it is:** bound to the study code it was made with (commit `68a6fa3`) and never rewritten. Since then, only two files have changed: the server, which writes the new telemetry rows, and `study.py`.

## Amendment 2

Declared in [examples/human-pilot/amendment-2.json](../examples/human-pilot/amendment-2.json) on 2026-10-06, before anyone enrolled. It binds `design.json` and amendment 1 by digest. Outside review of the collection protocol found holes that could deadlock, bias or overclaim the study, and this closes them. Nothing that decides a challenge changed.

- **Participants have states:** enrolled, practice passed, practice failed, withdrawn, discontinued; completed and incomplete follow from their valid sessions.
  - At most 4 are in play until 4 have completed every mode with no finding, then at most 12.
  - Someone who withdraws, is discontinued or fails practice frees a place for a replacement.
- **Withdrawal:** `study.py withdraw`.
  - **At the participant's request:** it deletes every session, the practice and the comments, and keeps only an anonymous record that someone withdrew.
  - **Unable to continue:** it keeps their sessions and frees the place.
  - **After publication:** the recordings are still deleted, but published totals cannot be recalled.
- **Consent version 2** asks participants not to try to find, guess or follow the probes, adds the sixth question, and says what withdrawal after publication means. Nobody enrolled under version 1.
- **Bindings:** enrollment records the digests of the consent notice, the design and both amendments. A session refuses to start, and the analysis refuses to run, when they differ.
- **Before a session:** a passed practice, which now also checks that Esc releases the mouse. Sessions follow the participant's pre-registered order, and a mode is played again only when its last session did not count.
- **Validity is fixed before scoring:**
  - **The participant:** a sixth question asks whether they tried to find, guess or follow the probes. A yes makes the session a protocol deviation.
  - **The operator:** declares whether the session ran as the protocol says, with a reason from a closed list: controls_failure, participant_withdrew, technical_failure, visibility_failure, audio_failure, protocol_deviation, privacy_failure.
  - **Automatic:** some reasons follow from facts that do not depend on the score.
  - **Never a reason:** a review-grade crossing.
- **Privacy:** the whole session tree is scanned, operational files are deleted, and what is kept is scanned again. The same happens after practice. Any address or machine identity left in a kept file stops the study.
- **Statistics:** the falsification question above is the headline. Intervals are gone.
- **The motion split, exactly:** with aw and pw the aim point's and the body's movement over the last 250 ms as degrees a second at the body's distance, and co the angle between them:

  | Part | Rule |
  | --- | --- |
  | both still | aw < 2.0 and pw < 2.0 |
  | holding | aw < 2.0 and pw ≥ 2.0 |
  | following | aw ≥ 2.0 and pw ≥ 2.0 and co ≤ 45.0 |
  | otherwise | any other aw ≥ 2.0 |

  Tests hold each boundary.
- **A known confound:** probes in this arena replay a bot's route inside sealed rooms 1.2 to 2.3 m wide, and in the stress and sweep modes that bot stands still, so probes here are often near-still. A finding may say as much about this placement as about the challenge type, and the next phase must separate the two.

## Amendment 3

Declared in [examples/human-pilot/amendment-3.json](../examples/human-pilot/amendment-3.json) on 2026-10-06, before anyone enrolled. It binds the design and both earlier amendments by digest. It settles one case amendment 2 left open: the participant whose session stopped the study for good asks for their data to be deleted.

- **Deletion always wins.**
- **Collection stays ended.** The final stop cannot be cleared, with or without its data: the operator saw it happen, and continuing without investigation would not be safe.
- **The stop no longer names them.** `study.py withdraw` rewrites `STOP` and `stops.log`, so no line holds their random id. A final stop keeps its words and gains the note "deleted at the participant's request: withdrawn evidence, not reportable". Consent version 2's promise still holds, so the notice is unchanged.
- **The answer is indeterminate.** A withdrawn finding is not a positive human finding, and the study cannot claim a clean result either. Nothing from the deleted session is reported: no metric, rate, interval or bound. A finding still on record answers "yes" first.

## Running it

Follow the operator's checklist in [examples/human-pilot/README.md](../examples/human-pilot/README.md):

1. Check the controls on the machine participants will use.
2. Enroll each participant, and run their practice.
3. Run their seven sessions: the first 4 participants first.
4. Analyze.

Each session:

- plans with the real planner;
- runs the study scene (`res://study.tscn` in the P12 project; P12's own files are untouched);
- asks six yes-or-no questions, each with an optional comment kept private, and the operator declares whether the session ran as the protocol says;
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
