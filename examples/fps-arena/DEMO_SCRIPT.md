# The fpsdet Arena: a 90 second recording

Record the lab window at 1920×1080 in demo mode, with the honest autopilot at the keyboard so the label on every pane reads `HONEST AUTOPILOT AT THE KEYBOARD · SCRIPTED · NOT A HUMAN`, or play it yourself and the label reads `A PERSON AT THE KEYBOARD`:

```bash
python examples/fps-arena/harness/arena.py run --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64 --mode demo [--behaviour tracker]
```

Demo mode shows four panels under the two views: SERVER KNOWLEDGE, DETECTOR ELIGIBILITY, CASE with FINDINGS, and the scenario card with the challenge panel. `m` switches to developer mode for the packet, the graph, the raw telemetry and the timings. Each scenario shows its card for eight seconds and then plays for thirty.

Nothing in the flow is staged: every chip is fpsdet's output over the telemetry the server just wrote. Wherever a script holds the aim, the security pane says so in large type. If a step does not come out as written here, the panel says `EXPECTED != ACTUAL`, and that is the take to keep. Do not try to show every detector: ten steps, one idea each.

## The flow

1. **Normal gameplay** (`1`, 0:00 to 0:10). Shoot the bot in the lane. Left pane: a game. Right pane: the same room as the server sees it.
2. **Server evidence** (same scenario, 0:10 to 0:18). Point at the right pane: the bot reads `KNOWN (seen)`, the vision ray is green, the aim line holds it. The CASE reads `INSUFFICIENT DATA` with the line under it: *this is not a clean verdict*. Say: one player is not a human baseline, so the aim numbers are not compared; the gear rules and the information checks ran and found nothing, and fpsdet refuses to call that clean.
3. **Audible hidden enemy, known, abstain** (`6`, 0:18 to 0:28). The bot runs behind the solid wall; sound rings pulse around it in the right pane; the chip says `KNOWN (heard)`. The security pane's label says a scripted stand-in, not a human and not cheat software, aims at it through the wall. The hidden-mover row is `ELIGIBLE` and stays empty. Say: footsteps the server emitted are information the player could have; tracking them is a skill, and fpsdet counts none of it.
4. **Missing channel, unknown, abstain** (`8`, 0:28 to 0:37). The same aim, the audio query off. The chip reads `UNKNOWN`, the hidden-mover row `TELEMETRY_UNAVAILABLE`, nothing fires. Say: unchecked is not absent. Missing telemetry makes fpsdet say less, never more.
5. **Hidden and unheard, unknowable** (`7`, 0:37 to 0:47). The bot creeps silently; the chip turns `UNKNOWABLE` while the lane bot stays `KNOWN`. The honest autopilot shoots only what it can see: no finding. Press `t`: the scripted stand-in aims through the wall, and within four seconds FINDINGS gets `hidden review`. Say: this is the only knowledge state the information checks act on, and this is a labelled script, not a cheat.
6. **A basic deterministic violation** (`2`, 0:47 to 0:57). Run down the lane. At five seconds the speed chip turns `x2.0 FAULT`, and about three seconds later `speed review` appears with the run, the peak and the cap. Say: a gear rule, from numbers the server already has.
7. **Active challenge, split view** (`c`, 0:57 to 1:12). Left pane: walls. Right pane: an orange body inside the sealed chamber, `vision absent · audio absent · body UNKNOWABLE`, and the label `CONTROLLED FOLLOWER · SCRIPTED TEST STAND-IN · NOT A HUMAN · NOT REAL CHEAT SOFTWARE`. Near the end of its window the case turns `REVIEW` with the orange badge `EXPERIMENTAL: NOT PRODUCTION QUALIFIED`. Say: planned from a secret, sent to this client only, verified by the server's own verdict at every moment, and the player's renderer drew no pixel of it, which the qualification measured.
8. **The experimental false positive** (`h`, 1:12 to 1:24). `HONEST-STYLE SCRIPTED STAND-IN (holds an angle) · NOT A HUMAN` holds the doorway's edge, still. The probe rests behind that frame. The same review, the same badge. Say: geometric alignment is not the same thing as responding to hidden information. This is kept as the false-positive control, and it is why the human pilot exists.
9. **Packet and replay** (`m`, `tab`, then `r`, 1:24 to 1:36). The packet digest, `complete`, the graph with its nodes and edges, the detector and profile digests. `r` replays the last match; scrub to where the observation first appeared; the replay header carries the same actor label. End on the strip: `OFFLINE REPLAY: IDENTICAL`.
10. **Optional AI** (`i`, 1:36 to 1:45). With an endpoint configured, a plain-language brief appears under the case with the packet digest before and after; the decision does not move. Without one, the badge says `AI brief off (not configured)` and everything on screen is already complete. Say: AI summarizes evidence, creates none, and changes no decision.

## Screenshot checklist

Developer mode unless noted. `p` saves a still into `<run>/operator/shots`; `--virtual-display --start-scenario <id> --screenshot-every-ms 6000 --quit-after-ms 45000` takes them without a desktop. Every still must show the actor label on the security pane; if it does not, it is not from this build.

1. **Split view with the challenge** (`c`, about 20 s in): walls on the left, the labelled orange probe on the right, `CONTROLLED FOLLOWER · SCRIPTED TEST STAND-IN` over the pane, the experimental badge on the strip.
2. **The three knowledge states**: `6` at 15 s (`KNOWN (heard)` with sound rings), `7` at 15 s (`UNKNOWABLE` beside `KNOWN`), `8` at 15 s (`UNKNOWN`, `telemetry_unavailable`).
3. **Insufficient data is not clean**: `1` at 20 s, the CASE panel with its explanation line.
4. **Detector eligibility**: `1` at 20 s, the full two-column table, most rows `insufficient_samples` or `baseline_too_thin`, four `eligible`.
5. **The evidence graph**: `t` or `c` at 25 s, the fourth panel on its first tab.
6. **The experimental angle-hold warning**: `h` at 30 s, the CASE panel, with `HONEST-STYLE SCRIPTED STAND-IN` over the pane.
7. **External evidence**: `e` after the match ends, the CASE panel reading `EXTERNAL EVIDENCE CAUSED THIS WATCH`.
8. **The replay timeline**: `r` after any review scenario, the fourth panel on its last tab, the red tick where the observation appeared.
9. **AI off and on**: the CASE panel before and after `i`, with the packet digests in the AI line.
10. **Final packet verification**: the PACKET / GRAPH tab after a match ends, `packet and graph verify`, and the strip's `OFFLINE REPLAY: IDENTICAL`.
