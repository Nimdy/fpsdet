# Scenario guide

Every scenario is a file in [scenarios/](../scenarios/). Its card is shown before it plays; its expectation is metadata the harness compares with fpsdet's output and never uses as the output. The numbers under *Qualified* are from [result.json](../result.json): one headless run of each scenario with the real topology (a dedicated server and a stock client with the honest autopilot, over loopback), scored live and offline. Every one came out as declared and replayed identically; the digest at the end of result.json binds them to the arena's code.

**Nobody here is a person, and nothing is real cheat software.** The subject is the honest autopilot (scripted). Where a scenario names a stand-in, it is a server-side script that aims the subject the way a reader of hidden state would: the behaviour such software produces, never the software. A fault is something the lab does to the subject's own body or weapon on purpose, standing in for a server that accepted what it should not have. The actor label under each title is the one the lab shows over the security pane while the scenario runs.

## Normal play  (`normal_play`, key `1`)

**Actor.** NO STAND-IN: the person at the keyboard, or in qualification the honest autopilot (scripted, not a human)

**Setup.** Move with W A S D, aim with the mouse, shoot the bot in the lane. Nothing is wrong here.
Bots: `arena-bot-a` at 3.0 m/s (fires). Subject autopilot: `tracker`. 

| | |
| --- | --- |
| What this tests | Ordinary movement and shooting make no case. |
| What the player can know | Yes: the bot is in the open lane, in plain sight. |
| What the server knows | Vision known on every aimed shot; every speed sample under the cap; every accepted shot on the rifle's cycle. |
| Expected fpsdet behavior | insufficient_data: not a clean verdict. One player is not a human baseline, so the aim numbers are never compared; the gear rules and the information checks still run and find nothing. fpsdet writes clean only when at least one number was actually compared against a thick enough cohort. |
| What would make this invalid | Any review or watch appears, or the knowledge chip reads anything but known while the bot is in view. |

**Qualified.** decision `insufficient_data` (not a clean verdict: nothing was comparable); findings none; speed `eligible`; hidden `eligible`; arena-bot-a `known`. Offline replay identical; realization no_challenge.

- With no frozen baseline the aim numbers (accuracy, headshots, distance) are never compared: a server with one player has no humans to compare against, and fpsdet says insufficient_data rather than clean.

## Impossible speed  (`impossible_speed`, key `2`)

**Actor.** NO STAND-IN: the person at the keyboard, or in qualification the honest autopilot (scripted, not a human)

**Setup.** Keep running down the lane. From 5 s to 20 s the server lets your body move at twice the cap.
Bots: `arena-bot-a` at 3.0 m/s. Subject autopilot: `runner`. fault speed_multiplier 2.0 from 5000 to 20000 ms.

| | |
| --- | --- |
| What this tests | A basic server-rule break: ground speed over the declared cap, sustained. |
| What the player can know | Not relevant: this is a gear rule, not an information check. |
| What the server knows | The speed it measured on each sample, the cap it declared on the same sample (5.5 m/s), the ground flag and the displacement cause. |
| Expected fpsdet behavior | The speed check fires: a run of at least 25 consecutive over-cap samples (2.5 s at 10 Hz) with displacement_cause none. Decision review. |
| What would make this invalid | The server tags the samples with an innocence cause, the cap field is missing, or the run is shorter than 25 samples. |

**Qualified.** decision `review`; findings ['speed']; speed `eligible`; arena-bot-a `known`; longest run 151 consecutive samples at the emitted 100 ms cadence, about 15100 ms, against a bar of 25 samples (the bar counts samples, not milliseconds). Offline replay identical; realization no_challenge.

- The speed run is counted in samples: 25 consecutive over-cap samples at this server's 10 Hz movement cadence is 2.5 s. A profile can measure it in milliseconds instead (speed_min_run_ms with movement_clock), which this arena does not set, so it shows the default as it is.
- The lab moves the body past the cap on purpose. It stands in for a server that trusts client positions, or a movement exploit; the server still records the speed it measured and the cap it should have held.

## Fire rate  (`fire_rate`, key `3`)

**Actor.** NO STAND-IN: the person at the keyboard, or in qualification the honest autopilot (scripted, not a human)

**Setup.** Hold the trigger on the bot. From 5 s to 20 s the server accepts shots every 50 ms, twice the rifle's cycle.
Bots: `arena-bot-a` at 3.0 m/s. Subject autopilot: `trigger`. fault fire_cycle_ticks 3 from 5000 to 20000 ms.

| | |
| --- | --- |
| What this tests | Shots accepted faster than the weapon's declared cycle. |
| What the player can know | Not relevant: a gear rule. |
| What the server knows | The server time of every shot it accepted. The profile declares the rifle's cycle: 100 ms, 15 ms slack. |
| Expected fpsdet behavior | The fire-rate check fires: gaps under 85 ms, at least 20 gaps and 10 violations in matches where they are the habit. Decision review. |
| What would make this invalid | Shot timestamps are not the server's, or the profile's cycle does not match the weapon. |

**Qualified.** decision `review`; findings ['fire_rate']; fire_rate `eligible`; metronome `not_applicable`; arena-bot-a `known`. Offline replay identical; realization no_challenge.

- The metronome check stays quiet on a held trigger: the server paces those shots at the rifle's own cycle, and the profile says shots are stamped on server ticks (shot_clock server_tick, tick_ms 17).
- The lab lowers the server's own cycle enforcement on purpose, standing in for a server that accepts what a macro sends. The detector reads what the server accepted.

## Recoil floor  (`recoil_floor`, key `4`)

**Actor.** NO STAND-IN: the person at the keyboard, or in qualification the honest autopilot (scripted, not a human)

**Setup.** Hold the trigger on the bot. From 5 s to 25 s the kick that lands on your view is a tenth of what the rifle applies.
Bots: `arena-bot-a` at 3.0 m/s. Subject autopilot: `trigger`. fault recoil_multiplier 0.1 from 5000 to 25000 ms.

| | |
| --- | --- |
| What this tests | Recoil far under the minimum the server declares for this weapon (no-recoil). |
| What the player can know | Not relevant: a gear rule. |
| What the server knows | The kick it applied on each shot, the spray index, and the floor it declares for the build: 0.8 degrees. |
| Expected fpsdet behavior | The recoil-floor check fires: a run of at least 10 shots at spray index 3 or more with a kick under 25% of the floor (0.2 degrees). Decision review. |
| What would make this invalid | A compensator or attachment lowers the real floor and the server did not declare the lower floor on the shot. |

**Qualified.** decision `review`; findings ['recoil_floor']; recoil_floor `eligible`; arena-bot-a `known`. Offline replay identical; realization no_challenge.

- The lab scales the kick the server applies on purpose, standing in for recoil the server measured under its own floor.

## Recoil mirror  (`recoil_mirror`, key `5`)

**Actor.** SCRIPTED TEST STAND-IN (same-tick recoil mirror) · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** Hold the trigger on the bot. A stand-in supplies the view command a mirror script would: the kick, flipped, on the same tick, for the whole round.
Bots: `arena-bot-a` at 3.0 m/s. Subject autopilot: `trigger`. stand-in `mirror` from 0 to 30000 ms.

| | |
| --- | --- |
| What this tests | The player's view command cancels the server's kick on the same tick. A person reacts a shot later. |
| What the player can know | A person feels the kick after it lands; the random part of it cannot be known before the shot. |
| What the server knows | The kick it applied (applied_recoil_pitch_deg), the view command on that tick (compensation_pitch_deg), and the spray index, so the learnable pattern can be removed first. |
| Expected fpsdet behavior | The mirror check fires after the spray pattern is removed: at least 16 shots, same-tick correlation at or under -0.90 and at least 0.25 more negative than at a one-shot lag. Decision review. |
| What would make this invalid | The game has a fixed spray pattern and the profile says recoil_pattern random, or the server sends no spray_index. |

**Qualified.** decision `review`; findings ['mirror']; mirror `eligible`; arena-bot-a `known`. Offline replay identical; realization no_challenge.

- The pattern is learnable, so the mean kick and command at each spray index are subtracted first; only the fresh random part, which nobody can anticipate, is tested.

## Hidden enemy, audible  (`audible_hidden_enemy`, key `6`)

**Actor.** SCRIPTED TEST STAND-IN (reader of hidden positions) · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** A bot runs behind the solid wall; you can hear its footsteps. A scripted stand-in (not a human, not cheat software) aims your avatar at it through the wall and fires, as a reader of hidden positions would.
Bots: `arena-bot-b` at 4.5 m/s. Subject autopilot: `tracker`. stand-in `wallhack` on `arena-bot-b` from 3000 to 30000 ms.

| | |
| --- | --- |
| What this tests | Legal tracking through sound: a critical false-positive control. |
| What the player can know | Yes: the server emitted footsteps the client could hear. |
| What the server knows | Vision absent (its rays hit the wall), audio known (a footstep from that body within hearing range in the last 500 ms). |
| Expected fpsdet behavior | KnowledgeState known (heard). The hidden-mover check is eligible, counts nothing, and makes no finding. No review. |
| What would make this invalid | Audio telemetry is missing or wrong: a server that never reports footsteps turns this into the unknowable case. |

**Qualified.** decision `insufficient_data` (not a clean verdict: nothing was comparable); findings none; hidden `eligible`; arena-bot-b `known`. Offline replay identical; realization no_challenge.

- Hearing is modelled as a server sound event within 30 m in the last 500 ms. A real game's audio query is engine work; a wrong one frames players.

## Hidden and unknowable  (`unknowable_hidden_enemy`, key `7`)

**Actor.** NO STAND-IN: the person at the keyboard, or in qualification the honest autopilot (scripted, not a human)

**Setup.** A bot creeps silently behind the solid wall while another patrols the lane in view. Shoot the one you can see. Press T to switch on the scripted stand-in that aims at it through the wall.
Bots: `arena-bot-a` at 3.0 m/s (fires), `arena-bot-b` at 1.5 m/s. Subject autopilot: `tracker`. 

| | |
| --- | --- |
| What this tests | An enemy this client could neither see nor hear: the information checks may act, and only then. |
| What the player can know | No: no line of sight, no sound, never perceived. |
| What the server knows | Vision absent and audio absent for the hidden bot, both checked every tick; vision known for the bot in the lane. |
| Expected fpsdet behavior | KnowledgeState unknowable for the hidden bot. The hidden-mover check is eligible. Honest play makes no finding; a stand-in that tracks the hidden bot does. |
| What would make this invalid | The server reports the hidden bot visible, or the audio query is switched off (that is the next scenario). |

**Qualified.** decision `insufficient_data` (not a clean verdict: nothing was comparable); findings none; hidden `eligible`; arena-bot-a `known`; arena-bot-b `unknowable`. Offline replay identical; realization no_challenge.

- No finding is manufactured: the honest autopilot never aims at what it cannot see. The tracked variant (scenario T) shows the finding.

## Hidden and unknowable, tracked  (`unknowable_hidden_tracked`, key `t`)

**Actor.** SCRIPTED TEST STAND-IN (reader of hidden positions) · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** The same silent bot behind the wall. A scripted stand-in (not a human, not cheat software) aims your avatar at it through the wall and fires, as a reader of hidden positions would.
Bots: `arena-bot-b` at 1.5 m/s. Subject autopilot: `tracker`. stand-in `wallhack` on `arena-bot-b` from 3000 to 30000 ms.

| | |
| --- | --- |
| What this tests | Sustained aim on an enemy this client could neither see nor hear. |
| What the player can know | No: no line of sight, no sound, never perceived. |
| What the server knows | Vision absent and audio absent for the hidden bot, both checked every tick; the aim cone held it between shots. |
| Expected fpsdet behavior | KnowledgeState unknowable. The hidden-mover check fires: at least 8 shots with hidden time and 1200 ms in all. Decision review. |
| What would make this invalid | The bot made a sound, was seen, or was perceived within the last second: any of those makes it known. |

**Qualified.** decision `review`; findings ['hidden']; hidden `eligible`; arena-bot-b `unknowable`. Offline replay identical; realization no_challenge.

- A server-side stand-in aims the subject: the behaviour such software produces, not cheat code.

## Unknown: audio telemetry missing  (`unchecked_audio_channel`, key `8`)

**Actor.** SCRIPTED TEST STAND-IN (reader of hidden positions) · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** The same silent bot and the same wallhack stand-in, but the server's audio query is switched off.
Bots: `arena-bot-b` at 1.5 m/s. Subject autopilot: `tracker`. stand-in `wallhack` on `arena-bot-b` from 3000 to 30000 ms; audio query off.

| | |
| --- | --- |
| What this tests | Missing telemetry does not mean absent information. |
| What the player can know | Nobody checked whether they could hear it, so fpsdet cannot say they could not. |
| What the server knows | Vision absent. Audio unchecked: the query did not run, and the server says so instead of saying absent. It cannot measure hidden time either, so it sends none. |
| Expected fpsdet behavior | KnowledgeState unknown. The hidden-mover check abstains (telemetry_unavailable). No evidence, whatever the aim did. |
| What would make this invalid | A server reports unchecked as absent. That manufactures the previous scenario's review from nothing. |

**Qualified.** decision `insufficient_data` (not a clean verdict: nothing was comparable); findings none; hidden `telemetry_unavailable`; arena-bot-b `unknown`. Offline replay identical; realization no_challenge.

- Unchecked is not absent. A channel the server did not report makes the answer unknown, and the information checks abstain.

## Wire vs picture  (`wire_vs_picture`, key `9`)

**Actor.** SCRIPTED TEST STAND-IN (reader of the newest snapshot) · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** A bot strafes across the lane. From 5 s to 25 s a scripted stand-in (not a human, not cheat software) aims at the newest snapshot the server sent, not at the body your client draws.
Bots: `arena-bot-a` at 4.5 m/s. Subject autopilot: `tracker`. stand-in `packet_reader` on `arena-bot-a` from 5000 to 25000 ms.

| | |
| --- | --- |
| What this tests | Aim that matches the wire snapshot ahead of the picture the official client draws, one interpolation delay later. |
| What the player can know | A person sees the picture: the body drawn 100 ms behind the newest snapshot. |
| What the server knows | The angular error from the aim to the snapshot it just sent (wire), to the position the client draws (picture), and the delay between them, all from its own record of what it sent. |
| Expected fpsdet behavior | The wire check fires: at least 8 wire-led shots whose delays add to 1200 ms (12 shots at 100 ms). Decision review. The honest autopilot in normal play aims at the picture and never leads. |
| What would make this invalid | The server scores the aim against a timeline the client was never shown, or the body is standing still (the two positions coincide and nothing counts). |

**Qualified.** decision `review`; findings ['wire']; wire `eligible`; arena-bot-a `known`. Offline replay identical; realization no_challenge.

- This does not cover every packet reader: a cheat that waits out the interpolation delay, or reads the drawn frame, aims at the picture too.

## Active challenge  (`active_challenge`, key `c`)

**Actor.** CONTROLLED FOLLOWER · SCRIPTED TEST STAND-IN · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** A planned challenge runs inside the sealed chamber behind the doorway's left frame. You cannot see or hear it. A controlled follower, a scripted stand-in that is not a human and not cheat software, aims your avatar at it, as a reader of the body it was sent would.
Bots: `arena-bot-a` at 3.0 m/s (fires), `arena-bot-b` at 1.5 m/s. Subject autopilot: `tracker`. stand-in `follower` from 0 to 32000 ms; one occluded_motion_replay/2 challenge of 14 to 16 s planned between 6 and 30 s.

| | |
| --- | --- |
| What this tests | A real occluded_motion_replay/2 challenge: planned from the server secret, sent to this client only, hidden by geometry, verified by the server's own vision and audio verdict at every moment. |
| What the player can know | No: the chamber is sealed, the body makes no sound, and the stock client draws nothing of it. |
| What the server knows | Its per-moment verdict on the body (challenge_vision_state, challenge_audio_state), how long the aim cone held it since the last event, the challenge id and window. |
| Expected fpsdet behavior | Challenge followed: at least 8 verified moments and 1200 ms on the body. Decision review, bound to the plan's id, digest and commitment. |
| What would make this invalid | The server reports the body seen or heard at any moment (the challenge voids), or the events name an id the plan does not have. |

**Qualified.** decision `review`; findings ['occluded_motion_replay']; occluded_motion_replay `eligible`; arena-bot-a `known`; arena-bot-b `unknowable`; challenge `followed`, 133 of 147 moments counted, 147 verified, 13300 ms, not counted {'seen': 13}. Offline replay identical; realization reproduced.

**EXPERIMENTAL CHALLENGE RESULT: NOT PRODUCTION QUALIFIED.** Controlled behaviour from a labelled script; not a cheat-detection rate.

- Challenge reviews are experimental and not production-qualified. The bar counts time on the body, not intent.
- One chamber: the secret's placement draw always lands here. The secret still picks the route, the heading and the replay delay, and the commitment binds all of it.

## Angle hold: experimental challenge failure  (`angle_hold_false_positive`, key `h`)

**Actor.** HONEST-STYLE SCRIPTED STAND-IN (holds an angle) · NOT A HUMAN

**Setup.** An honest-style scripted stand-in (not a human) holds the doorway's left frame from across the room, as a player waiting for a push would. A near-still probe rests in the chamber behind that frame. Nobody is following anything.
Bots: `arena-bot-b` at 0.0 m/s, `arena-bot-a` at 2.0 m/s. Subject autopilot: `holder`. stand-in `holder` from 0 to 32000 ms; one occluded_motion_replay/2 challenge of 14 to 16 s planned between 6 and 30 s.

| | |
| --- | --- |
| What this tests | The known weakness of the time-on-body bar: ordinary angle holding over a probe that happens to rest behind the held angle. |
| What the player can know | No. And the player is not responding to anything: the aim never moves. |
| What the server knows | Vision absent and audio absent for the body at every moment; the aim cone held the body because the chamber is behind the frame. |
| Expected fpsdet behavior | Challenge followed, decision review: the same evidence as the follower. EXPERIMENTAL, NOT PRODUCTION QUALIFIED. Geometric alignment is not the same thing as responding to hidden information. |
| What would make this invalid | The holder's aim moves with the body, which would make it a follower. Here the body is near-still and the aim is still. |

**Qualified.** decision `review`; findings ['occluded_motion_replay']; occluded_motion_replay `eligible`; challenge `followed`, 145 of 145 moments counted, 145 verified, 14416 ms. Offline replay identical; realization reproduced.

**EXPERIMENTAL CHALLENGE RESULT: NOT PRODUCTION QUALIFIED.** Controlled behaviour from a labelled script; not a cheat-detection rate.

- Challenge reviews are experimental and not production-qualified. The bar counts time on the body, not intent.
- This failure is preserved on purpose: it is why the consented human pilot exists and why occluded_motion_replay/3 asks whether the aim turns with secret turns instead.

## External evidence  (`external_record`, key `e`)

**Actor.** NO STAND-IN: the person at the keyboard, or in qualification the honest autopilot (scripted, not a human)

**Setup.** Normal play, while a fictional provider sends one adverse record about this player in this match.
Bots: `arena-bot-a` at 3.0 m/s (fires). Subject autopilot: `tracker`. a fictional `example-lab-integrity` record (client_integrity, adverse) for this match.

| | |
| --- | --- |
| What this tests | A record from another integrity system can move a clean case to watch. It cannot create a review. |
| What the player can know | Not relevant. |
| What the server knows | Nothing new: the record comes from outside the server, in the fpsdet.external/1 format, and is carried exactly as given. |
| Expected fpsdet behavior | Native decision clean or insufficient_data; fused decision watch (rule A). An external observation with its provider, class, direction and confidence; automated_action still none. |
| What would make this invalid | The record names another match (then it is context), or anyone treats a provider's claim as fpsdet's finding. |

**Qualified.** decision `watch`; findings ['external_signal']; arena-bot-a `known`; native fpsdet result `insufficient_data` → external evidence → final `watch` by rule A; external evidence caused this watch and cannot create a review. Offline replay identical; realization no_challenge.

- No adapter or signing key for any commercial anti-cheat exists; this provider is fictional. A signature, when there is one, proves who signed a record, not that it is true.

## Exposed challenge: positive control  (`exposed_challenge_control`, key `o`)

**Actor.** CONTROLLED FOLLOWER · SCRIPTED TEST STAND-IN · NOT A HUMAN · NOT REAL CHEAT SOFTWARE

**Setup.** The positive control of the pixel proof: the server breaks the placement rule on purpose and puts the probe body in the open lane, in plain sight. You will see a red body nobody can shoot. The server's own verdict says seen, and fpsdet must abstain. A controlled follower, a scripted stand-in that is not a human and not cheat software, aims your avatar at it.
Bots: `arena-bot-a` at 3.0 m/s, `arena-bot-b` at 1.5 m/s. Subject autopilot: `tracker`. stand-in `follower` from 0 to 32000 ms; one occluded_motion_replay/2 challenge of 14 to 16 s planned between 6 and 30 s, placed in the open on purpose.

| | |
| --- | --- |
| What this tests | That the player's renderer draws a probe placed in the open (so the pixel proof's zero in the sealed chamber means something), and that a body the client could see voids the challenge. |
| What the player can know | Yes: the body stands in the open lane and the stock client draws it like any body. |
| What the server knows | Vision known on the body at every moment of the window; it does not end the challenge, so that only the per-moment verdict stands between a visible body and evidence. |
| Expected fpsdet behavior | The challenge abstains (seen). No finding, no review, whatever the aim did. |
| What would make this invalid | The renderer draws no pixel of a body in the open, or fpsdet counts a moment the server reported seen. |

**Qualified.** decision `insufficient_data` (not a clean verdict: nothing was comparable); findings none; occluded_motion_replay `not_applicable`; arena-bot-a `known`; arena-bot-b `unknowable`; challenge `abstained` (seen), 0 of 0 moments counted, 0 verified, 0 ms, not counted {'seen': 149}. Offline replay identical; realization reproduced.

**EXPERIMENTAL CHALLENGE RESULT: NOT PRODUCTION QUALIFIED.** Controlled behaviour from a labelled script; not a cheat-detection rate.

- A qualification control, kept as a scenario so the rule it shows is visible: one moment where the server reports the body seen or heard voids the whole challenge. Challenge reviews are experimental and not production-qualified.

## The player pixel proof

Two of these scenarios ran once more with the pixel-check client, drawing in a private virtual display: `active_challenge` as the hidden control and `exposed_challenge_control` as the positive control.

```text
PLAYER PIXEL PROOF
hidden:          0 challenge pixels in 7 checks (the same checks found pixels of visible bots 14 times; the frozen frame was stable)
visible control: 1536 changed pixels in 8 checks
PASS
```
