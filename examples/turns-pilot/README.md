# Secret turns in the live server

`occluded_motion_replay/3` ([docs/challenges.md](../../docs/challenges.md#version-3-secret-turns)) run in the human pilot's Godot arena by machine stand-ins and the controlled follower. Not people, not a deployment, not a rate.

The human pilot's dry run found a stand-in holding the doorway's frame, over a probe in the sealed room behind it, reaching version 2's bar without knowing anything. This runs the same arena, bots, stock client, stand-ins and follower under version 3 and records what each challenge showed, beside the time the aim sat on the body anyway: what version 2 would have counted.

| Path | What it is |
| --- | --- |
| `turns.py` | The harness: plan version 3 with fpsdet, run the server and a stand-in client, score, check every turn against the secret, scan the public files |
| `result.json` | `fpsdet.turns-pilot/1`: every session's challenges, the turn check, the leak scan, and the code it ran |
| `captured/<scenario>-<n>/` | The public half of the first holder and follower sessions: the plan and the server's telemetry. Every session's events are in `result.json` by digest |

The Godot side is new files only, so the human pilot's frozen server and the P12 pilot stay byte for byte what they were:

- `examples/pilot/godot/turns.tscn` and `turns.gd`: the entry scene;
- `scripts/turns_server.gd`: extends `study_server.gd` and replaces only the probe's motion and the participant's challenge fields;
- `scripts/turns_recipe.gd`: version 3's turn parameters and schedule, beside the untouched `recipe.gd`.

**The probe.** It runs along a lane through a sealed probe room's centre, across the participant's line of sight to the room when the challenge starts, as far as the room allows. Before each turn it stands still at the end it will leave from. At the turn it sets off along the lane, the way that turns this client's bearing to it the turn's way, for the 600 ms reaction window; then it eases back to where the next turn starts. So a turn is the only abrupt change in how its bearing moves. It is never drawn, makes no sound and has no collision, as in version 2.

```bash
python examples/pilot/pilot.py godot --dest ~/godot-4.7.2
python examples/turns-pilot/turns.py run --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64 --data /tmp/turns --repeat 2 --publish
```

Each session lasts four minutes of server time; scenarios run in parallel. The secret is deleted after each session's checks, and the private folders stay under `--data`.

## What it found

The published run (`result.json`): 18 to 24 s windows, two sessions per stand-in, four challenges each.

| Stand-in | Challenges | Followed | Notes |
| --- | --- | --- | --- |
| Holder (doorway frame) | 8 | 0 | Aim on the body up to 5.7 s in one challenge, beyond version 2's bar; there p = 0.003 with 3 turns followed, short of 4 |
| Sweeper | 8 | 0 | |
| Tracker | 8 | 0 | All abstained: a bot it could see was in its aim cone around the turns |
| Controlled follower | 8 | 8 | p ≤ 0.002, 5 or 6 turns of 6 followed |

Every turn the server ran and every turn event matched the secret within one tick, no public file held the secret or a realization, and every case's packet, graph and plan check verified.

Two runs came before it, and both are in [docs/challenges.md](../../docs/challenges.md#version-3-secret-turns): one with 12 to 16 s windows (followers 5 of 8), and one in which the probe still stopped dead after each turn, where a sweeper's challenge was followed at p = 0.00999: the 1-in-100 the bar allows.

`tests/test_turns.py` re-scores the two published captures offline and checks them, and the code, against `result.json`.
