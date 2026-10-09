# The Arena's architecture

Four processes, one machine, loopback only. fpsdet is the fourth, and it is the one that was not written for the arena.

```
                 commands: move, look, fire                  snapshots, sounds, banner
   stock client  ───────────────────────────►  dedicated server  ◄────────────────────  (to every player)
   (left pane)   ◄───────────────────────────  (headless, 60 Hz)
                                                      │  │
                                   operator feed      │  │   events.ndjson, one line per shot and movement sample
                              (token-gated, 20 Hz)    │  │   match.json at the end; plans read at startup
                                                      ▼  ▼
   operator view ◄──────────────────────────  public/matches/<match>/ ─────────►  the scorer (Python, beside the server)
   (right pane and panels)                                                        run_score every second; fpsdet score at the end
          ▲                                                                        │
          └──────────────── public/live/current.json, knowledge-table.json ◄───────┘
```

## Roles

| Role | Script | Trusts | Sends |
| --- | --- | --- | --- |
| Server | `godot/scripts/arena_server.gd` | nothing a client says | snapshots and sounds to players; the feed to operators; NDJSON to disk |
| Client | `godot/scripts/arena_client.gd` | the server | move, look, fire |
| Operator | `godot/scripts/operator_view.gd` | the server and the scorer | scenario requests and options, with the run's token |
| Scorer | `harness/arena.py` (`Scorer`) | the files the server wrote | `live/*.json` for the operator; `live-case.json` per match |
| Lab | `godot/scripts/lab.gd` | | hosts a client and an operator in one window; routes keys |

Godot routes a remote call by node path, so the networked nodes sit at fixed paths on every side: `Main/Game` is the player link (the server script on the server, the client script on a client), `Main/Operator` is the operator link (`server_operator.gd` on the server, the operator view on an operator). A stock client has no `Operator` node and no method that could receive the feed. In the lab the two links share one connection; `run --separate` opens them as two processes.

## The tick

Every physics tick (60 Hz) the server:

1. **steers**: reads each client's last command; drives the bots; in deterministic mode runs the honest autopilot in-process, shown only the picture (what the client would draw); applies the scenario's stand-in over the subject's aim if its window is open;
2. **moves** every body with Godot's own character physics, with the subject's speed fault if open, and lets the kicked view settle back;
3. **updates the challenge**: starts a planned one when its window opens, moves the probe along the realization (a bot's track, turned by the secret heading, from the secret delay back, clamped inside the sealed chamber), ends it when the window closes or the body becomes perceivable;
4. **observes**: for the subject against every bot, its line-of-sight query (rays from five eye points to sixteen body points, now and one interpolation delay ago, against the world only) and its audio query (a sound from that body within 30 m in the last 500 ms), or `unchecked` when the scenario switched the audio query off; whether the aim cone holds the body; the same for the probe, accumulated into the challenge fields;
5. **fires**: enforces the cycle and the magazine, applies the kick (pattern plus a fresh draw), applies the mirror stand-in's command on a shot tick, traces the shot, applies damage, and writes the shot event with the view it was fired from;
6. **emits** a movement event per player every 100 ms;
7. **sends** snapshots at 20 Hz, remembers what it sent the subject (the wire), and derives from that record what the stock client draws (the picture);
8. **feeds** the operator at 20 Hz and writes the same row to `operator/<match>/timeline.ndjson`.

The knowledge queries and the challenge fields are the P12 pilot's, byte for byte; `tests/test_fps_arena.py` compares the function bodies.

## What the server never does

- It never reads what a client claims to have seen, heard or tracked. The command is three things.
- It never reports `absent` for a channel it did not check. With the audio query off, `audio_state` is `unchecked`, and `hidden_track_ms` is not sent, because "neither see nor hear" cannot be measured without it.
- It never applies a kick before the shot that caused it, and it writes the shot's enemy, wire and picture fields with the view the shot was fired from.
- It never binds a public address, logs a peer's address, or writes the secret: the key is read once, the realizations are derived, the key is dropped.

## Scenarios

`scenarios/*.json` (`fpsdet.arena-scenario/1`) say where the subject starts, which bots run with which waypoints and speeds, whether the audio query runs, which fault or stand-in is on and when, whether a challenge is planned and with what schedule, whether a fictional external record is written, the card, the caveats, and the expectation. `scenario.gd` reads them on every side. The expectation is read only by the harness, to compare with fpsdet's output, and by the card.

Faults change what the server does to the subject's own body or weapon: a speed multiplier, a shorter fire cycle, a smaller kick. Stand-ins override the subject's aim from the server, as the pilot's responder did: `follower` (the probe, 150 ms behind), `wallhack` (a hidden bot, 150 ms behind, firing on its own clock), `packet_reader` (the wire position), `mirror` (the kick flipped on the shot's own tick), `holder` (the doorway's edge, still). A stand-in is the behaviour such software produces, never cheat code, and it is always declared on the panel.

## Matches and files

A scenario run is a match: its own id (`arena-<scenario>-<n>`), its own clock from tick 0, its own folder:

```
<run>/
  public/
    plans/arena-active_challenge-1.json ...   the public plans, written before the server starts
    matches/<match id>/
      events.ndjson            the NDJSON fpsdet reads, written as the server accepts each sample
      plan.json                the plan this match ran, copied in by the server
      external.ndjson          the fictional provider's record, written by the scorer for this match id
      match.json               scoreboard, timings and counters, written at the end
      live-case.json           the scorer's final view: the case, the timeline, the offline comparison
    live/                      current.json, <match>.json, status.json, knowledge-table.json
    control/ai.json            the AI toggle
    current.json, server.log, client.jsonl
  operator/<match id>/timeline.ndjson   the feed, row by row, for replay (and shots/ for stills)
  private/                     secret.hex, operator.token, realization-<id>.json: owner-only, never copied
  godot-user/                  Godot's own user data, pointed here
```

Plans are made before the server starts: four per challenge scenario, so a scenario can be run four times in one session. The server derives every realization at startup and drops the key. A scenario button picks the next unused plan.

## Live and offline

The scorer reads the match's events file every second and calls `fpsdet.pipeline.run_score` on it, with the plan and the external record when the match has them, exactly as `fpsdet score` would. It writes the subject's case for the panels: the decision, the reasons, every observation with its id and evidence, every detector's eligibility by unit, the challenge results, the fusion, the packet, the graph's nodes and edges, the provenance digests, and fpsdet's answer to "could this client know" for every enemy the subject's events named, event by event. Each scoring is one point of the case timeline.

When `match.json` appears, the scorer scores the file once more in-process and once with the `fpsdet score` command, compares the decisions, observation ids, graph digests, packet digests, input digests, challenge results, fusion and eligibility, and writes `OFFLINE REPLAY: IDENTICAL` or `MISMATCH`. It also checks the packet and the graph, checks each challenge finding against its plan, reproduces the realization from the secret, and compares the outcome with the scenario's expectation.

## The knowledge table

The 3D labels and the knowledge chips must read at tick rate, before the scorer's next pass. Rather than reimplement the knowledge engine in GDScript, the scorer writes `live/knowledge-table.json` at startup: fpsdet's own `resolve` for every combination of vision, audio and recent-perception channel state, and `body_knowledge` for every vision and audio pair of a challenge body, under the profile's declared channels. The operator view looks up the server's channel words in that table. The only comparison it makes itself is recent perception against the grace, and the grace is the profile's, from the table. The scorer's per-event answer, from `shot_knowledge` over the real events, is shown beside it.

## The player pixel proof

The server's verdict that the probe was unseen is its own raycast. The qualification does not take it on trust: it runs the challenge scenario and the exposed positive control once more with `pixel_check_client.gd`, the stock client plus the P12 pilot's frame check, drawing in a private virtual display. Every two seconds it freezes the frame, draws it again with each remote body hidden in turn, and counts the pixels that changed. The probe is named afterwards from the server's own entity record, never by the client. The proof passes only when the probe in the sealed chamber was drawn as a body and changed no pixel while the same checks found pixels of the visible bots and the frozen frame was stable, and when the probe placed in the open did change pixels. Qualification only: no scoring rule reads it.

## Who is at the keyboard

The launcher tells the server who it put at the keyboard (`--subject-input`), and every scenario file names its actor. The server puts both in the operator feed, the timeline rows and the match record; the operator view shows the label in large type over the security pane, on the strip, on the card and on the replay header; the player's HUD says when a stand-in holds the aim; the qualification result carries it per row. No label is a judgement: it is the launcher's own knowledge of what it started.

## Replay

`operator/<match>/timeline.ndjson` is the feed as the server sent it: every 50 ms, the subject's position, view and aim, every bot's true position with its wire and picture and verdicts, the probe with its verdict. `live/<match>.json` holds the case timeline: fpsdet's decision, findings, observation ids and eligibility after each second. The replay scrubs the first and shows the second as it stood at that time; `arena.py replay` rebuilds the case timeline offline by scoring the events up to each second.

## Performance, as qualified

From [result.json](../result.json): thirteen matches of about 30 s each, run seven at a time on one machine, so the tick times are an upper bound on a quiet box.

| | |
| --- | --- |
| Server tick | 478 µs mean (median over scenarios; 224 to 1,063 µs across them), 3.1 ms worst single tick, against a 16.7 ms budget |
| Knowledge queries | 122 µs and 177 rays per tick (median); 70 to 158 µs per probe tick for the challenge |
| Telemetry | about 10.5 KB/s of events, 3.6 KB/s of snapshots to the client, 78 KB/s of operator feed |
| fpsdet | 8 to 19 ms per scoring pass over 652 to 1,022 events |
| Server memory | 44 MB static peak |
| Headless autopilot client | 145 frames a second; the lab window reports its own rate on the performance tab |

None of these is a target. The operator feed is the one cost that does not exist in a game: it is the security view's, not the server's.
