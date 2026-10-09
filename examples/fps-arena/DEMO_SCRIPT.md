# The fpsdet Arena: a 60 to 90 second recording

Record the lab window at 1920×1080 in demo mode:

```bash
python examples/fps-arena/harness/arena.py run --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64 --mode demo
```

Demo mode shows four panels under the two views: SERVER KNOWLEDGE, DETECTOR ELIGIBILITY, CASE with FINDINGS, and the scenario card with the challenge panel. `m` switches to developer mode at any point for the packet, the graph, the raw telemetry and the timings. Each scenario shows its card for eight seconds and then plays for thirty.

Nothing in the flow is staged: every chip is fpsdet's output over the telemetry the server just wrote. If a step does not come out as written here, the panel says `EXPECTED != ACTUAL`, and that is the take to keep.

## The flow

1. **Normal play** (`1`, 0:00 to 0:12). Shoot the bot in the lane. Point at the right pane: the bot is `KNOWN (seen)`, the vision ray is green, the aim line holds it. The case reads `INSUFFICIENT_DATA`, `automated_action: none`, no findings. Say: one player is not a human baseline, so the aim numbers are not compared; the gear rules and the information checks ran and found nothing.
2. **Hidden enemy, audible** (`6`, 0:12 to 0:24). The bot runs behind the solid wall; sound rings pulse around it in the right pane. The chip says `KNOWN (heard)`. A stand-in tracks it through the wall and fires: the hidden-mover check is `ELIGIBLE` and stays empty. Say: footsteps the server emitted are information the player could have; tracking them is a skill, and fpsdet counts none of it.
3. **Hidden and unknowable** (`7`, 0:24 to 0:34). The bot creeps silently behind the wall; the chip turns `UNKNOWABLE`, the other bot in the lane stays `KNOWN`. Press `t`: the stand-in tracks the hidden one, and within four seconds the FINDINGS panel gets `hidden review` and the case turns `REVIEW`. Say: this is the only knowledge state the information checks act on.
4. **Unknown: audio telemetry missing** (`8`, 0:34 to 0:44). The same aim, the audio query off. The chip reads `UNKNOWN`, the hidden-mover row reads `TELEMETRY_UNAVAILABLE`, nothing fires. Say: unchecked is not absent. Missing telemetry makes fpsdet say less, never more.
5. **Impossible speed** (`2`, 0:44 to 0:56). Run down the lane. At five seconds the strip reads the fault, the speed chip turns `x2.0 FAULT`, and about three seconds later `speed review` appears with the run, the peak and the cap. Say: a gear rule, from numbers the server already has.
6. **Active challenge** (`c`, 0:56 to 1:16). The left pane shows walls; the right pane shows an orange body inside the chamber behind the doorway's frame, labelled with the challenge id and `vision absent · audio absent · body UNKNOWABLE`. The stand-in follows it. Near the end of its window the case turns `REVIEW` with `occluded_motion_replay`, and the orange badge reads `EXPERIMENTAL: NOT PRODUCTION QUALIFIED`. Say: planned from a secret, sent to this client only, verified by the server's own verdict at every moment, bound to the plan's commitment.
7. **Angle hold** (`h`, 1:16 to 1:36). A stand-in holds the doorway's edge, still. The probe rests behind that frame. The same review appears, the same badge. Say: geometric alignment is not the same thing as responding to hidden information. This is kept as the false-positive control, and it is why the human pilot exists.
8. **Evidence and AI** (developer mode, `m`, then `tab`, 1:36 to 1:50). The packet digest, `complete`, the graph with its nodes and edges, the detector and profile digests. `r` replays the last match: scrub to where the observation first appeared on the timeline. Press `i`: the AI reviewer brief, when an endpoint is configured, appears under the case with the packet digest before and after; the decision does not move. Without an endpoint the badge says so, and everything else is already complete.

End on the strip: `OFFLINE REPLAY: IDENTICAL`.

## Screenshot checklist

Developer mode unless noted. `p` saves a still into `<run>/operator/shots`; `--virtual-display --start-scenario <id> --screenshot-every-ms 6000 --quit-after-ms 45000` takes them without a desktop.

1. **Split view with the challenge** (`c`, about 20 s in): walls on the left, the labelled orange probe on the right, the experimental badge on the strip.
2. **The three knowledge states**: `6` at 15 s (`KNOWN (heard)` with sound rings), `7` at 15 s (`UNKNOWABLE` beside `KNOWN`), `8` at 15 s (`UNKNOWN`, `telemetry_unavailable`).
3. **Detector eligibility**: `1` at 20 s, the full two-column table, most rows `insufficient_samples` or `baseline_too_thin`, four `eligible`.
4. **The evidence graph**: `t` or `c` at 25 s, the fourth panel on its first tab.
5. **The experimental angle-hold warning**: `h` at 30 s, the CASE panel.
6. **The replay timeline**: `r` after any review scenario, the fourth panel on its last tab, the red tick where the observation appeared.
7. **AI off and on**: the CASE panel before and after `i`, with the packet digests in the AI line.
8. **Final packet verification**: the PACKET / GRAPH tab after a match ends, `packet and graph verify`, and the strip's `OFFLINE REPLAY: IDENTICAL`.
