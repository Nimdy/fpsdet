# The live challenge pilot

A Godot 4 dedicated server and stock clients that run fpsdet's active challenge, `occluded_motion_replay/2`, end to end on one machine: a secret-derived plan, a probe the stock client is sent but cannot see or hear, the server's own per-moment proof of that, a controlled stand-in for a hidden-information reader, verifiable evidence, and the same evidence again from the captured telemetry offline.

A controlled integration qualification, not a deployment and not a detection rate. It touches nothing but its own Godot processes, talking over `127.0.0.1`. [docs/pilot.md](../../docs/pilot.md) explains all of it.

| Path | What it is |
| --- | --- |
| `qualification.json` | The scenarios, failures and checks, with their expected outcomes, declared before any live run |
| `pilot.json` | The pilot game's fpsdet profile |
| `godot/` | The Godot project: `scripts/server.gd` (authoritative server, challenge, knowledge queries, telemetry), `scripts/client.gd` (stock client), `scripts/recipe.gd` (fpsdet's challenge recipe in GDScript), `scripts/arena.gd` (the map) |
| `pilot.py` | The harness: plan, run, capture, score, reproduce, leak-check, qualify, verify |
| `captured/<scenario>/` | The server's own telemetry from the qualification run, and its public plan |
| `result.json` | `fpsdet.pilot/1`: what the qualification found, bound by digest |

```bash
python examples/pilot/pilot.py godot --dest ~/godot-4.7.2          # the pinned engine, checked, user-local
python examples/pilot/pilot.py qualify --godot ~/godot-4.7.2/Godot_v4.7.2-stable_linux.x86_64 --out /tmp/pilot
python examples/pilot/pilot.py verify                               # offline, no engine
```

The server secret and the realization the server ran stay in each run's `private/` folder; neither is ever committed.
