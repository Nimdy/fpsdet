# How this maps to your game

```
your dedicated server
        ↓
movement + shot telemetry           one JSON line per accepted sample, on the server's clock
        ↓
visibility / audio queries          the server's own, against its own geometry and its own sound events
        ↓
fpsdet                              fpsdet score events.ndjson --profile game.json [--challenges plan.json] [--external records.ndjson]
        ↓
case JSON                           decision, reasons, observations, eligibility, graph, packet, provenance
        ↓
human reviewer                      automated_action is always none
```

You do not need all of it on day one. The arena emits levels 1, 3 and 4; it has no population for level 2.

| Level | Needs | Turns on | Where the arena does it |
| --- | --- | --- | --- |
| 1 · basic rules | `speed_mps`, `on_ground`, `expected_max_ground_speed_mps`, `displacement_cause`; shot `t_ms` on the server clock, `weapon_class`, `weapon_id`; `spray_index`, `recoil_pitch_deg`, `applied_recoil_pitch_deg`, `compensation_pitch_deg`, `expected_min_recoil_pitch_deg` | speed, fire rate, metronome, recoil floor, mirror | `_movement_event`, `_fire` in `arena_server.gd` |
| 2 · human baselines | enough players, a baseline frozen from a trusted window with `fpsdet baseline`, `skill_band` or `skill_prior` if you have a matchmaker, `hitbox`, `distance_m`, `through_geometry` | accuracy, headshots, distance, geometry, the rank tail, declared metrics | the hit fields are emitted; there is no cohort to compare against here |
| 3 · knowledge checks | authoritative `vision_state` and `audio_state` for the enemy a shot was about, `enemy_id`, `since_perceived_ms`, `hidden_track_ms`; `wire_error_deg`, `picture_error_deg`, `interp_delay_ms` | hidden mover, quiet aim, wire | `_observe`, `_aimed_enemy`, `_wire_and_picture`, `_vision`, `_audio` |
| 4 · active challenges | a plan from `fpsdet challenge plan` and a server-held secret; the recipe in your server; the body as an ordinary replicated player, to one client, with no collider and no sound; `challenge_id`, `challenge_track_ms`, `challenge_vision_state`, `challenge_audio_state` on that client's events | occluded motion replay (experimental) | `_load_plans`, `_update_challenge`, `_begin`, `_challenge_fields`, `recipe.gd` |

## What the arena's lines look like

A movement sample of the subject while a challenge runs (level 1 fields, then the level 4 fields the server adds on every event of the subject while the window is open):

```json
{"challenge_audio_state": "absent", "challenge_id": "ch-ae22262e1a1925366f715a75", "challenge_track_ms": 66.667, "challenge_vision_state": "absent",
 "displacement_cause": "none", "event_type": "movement", "expected_max_ground_speed_mps": 5.5, "game_id": "fpsdet-arena", "loadout_weight_kg": 12.0,
 "map_id": "arena", "match_id": "arena-active_challenge-1", "on_ground": true, "player_id": "arena-player", "speed_mps": 2.0, "t_ms": 12700}
```

A shot at a visible bot (levels 1 and 3: the recoil pair, the enemy and its channels, the hidden time since the previous shot, the wire and the picture):

```json
{"applied_recoil_pitch_deg": 1.461, "audio_state": "known", "compensation_pitch_deg": 0.0, "distance_m": 11.7, "enemy_id": "arena-bot-a",
 "event_type": "shot", "expected_min_recoil_pitch_deg": 0.8, "game_id": "fpsdet-arena", "hidden_track_ms": 0.0, "hit": true, "hitbox": "upper_torso",
 "interp_delay_ms": 100.0, "map_id": "arena", "match_id": "arena-active_challenge-1", "picture_error_deg": 0.288, "player_id": "arena-player",
 "recoil_pitch_deg": 1.461, "since_perceived_ms": 0, "spray_index": 0, "t_ms": 250, "through_geometry": false, "vision_state": "known",
 "weapon_class": "rifle", "weapon_id": "arena-rifle", "wire_error_deg": 0.512}
```

The same shot at a bot the client could neither see nor hear, with the aim cone having held it since the last shot:

```json
{"applied_recoil_pitch_deg": 1.314, "audio_state": "absent", "compensation_pitch_deg": -0.765, "enemy_id": "arena-bot-b", "event_type": "shot",
 "expected_min_recoil_pitch_deg": 0.8, "game_id": "fpsdet-arena", "hidden_track_ms": 16.667, "hit": false, "interp_delay_ms": 100.0, "map_id": "arena",
 "match_id": "arena-unknowable_hidden_tracked-1", "picture_error_deg": 0.309, "player_id": "arena-player", "recoil_pitch_deg": 1.314, "spray_index": 0,
 "t_ms": 3000, "vision_state": "absent", "weapon_class": "rifle", "weapon_id": "arena-rifle", "wire_error_deg": 0.21}
```

And the same aim when the server's audio query was switched off. The channel says `unchecked`, not `absent`, and `hidden_track_ms` is gone, because a server that did not listen cannot say the client could not hear:

```json
{"applied_recoil_pitch_deg": 1.833, "audio_state": "unchecked", "compensation_pitch_deg": -0.765, "enemy_id": "arena-bot-b", "event_type": "shot",
 "expected_min_recoil_pitch_deg": 0.8, "game_id": "fpsdet-arena", "hit": false, "interp_delay_ms": 100.0, "map_id": "arena",
 "match_id": "arena-unchecked_audio_channel-1", "picture_error_deg": 0.309, "player_id": "arena-player", "recoil_pitch_deg": 1.833, "spray_index": 0,
 "t_ms": 3000, "vision_state": "absent", "weapon_class": "rifle", "weapon_id": "arena-rifle", "wire_error_deg": 0.21}
```

The telemetry tab in the lab shows these as they are written, filtered by movement, shot, challenge or knowledge, and `c` copies the last one.

## The decisions the arena made, and why

- **The clock.** `t_ms` is the server tick, rounded to the millisecond, and the profile says so (`shot_clock: "server_tick"`, `tick_ms: 17`). Without that, a held trigger paced by the server at exactly its cycle would have no timing spread for a reason that is not the player's, and the metronome rule would be wrong to read it. With it, the metronome abstains on the arena's held triggers (`not_applicable`), as it should.
- **The cap on the sample.** Every movement sample carries `expected_max_ground_speed_mps` (5.5 for a 5.0 m/s sprint). The speed rule reads the cap the server declared, never a table.
- **The speed run in samples.** The profile does not set `speed_min_run_ms`, so the run is 25 consecutive samples, which at this server's 10 Hz is 2.5 s. The panel says so. A profile can measure it in milliseconds instead (`speed_min_run_ms` with `movement_clock: "server"`); the arena shows the default as it is.
- **The recoil pair.** `applied_recoil_pitch_deg` is the kick the server applied; `compensation_pitch_deg` is the change in the client's commanded pitch on that same tick; `spray_index` restarts at 0 after 300 ms without a shot. The pattern is learnable (the profile's default), so fpsdet removes the mean kick and command per spray index before it tests what is left.
- **Hidden time only with both queries.** The server adds a tick to `hidden_track_ms` only when its vision query and its audio query both failed for a body in the aim cone, and sends the field (0 included) only when the audio query ran. The field's definition is "neither see nor hear"; a server that did not listen cannot measure it.
- **The picture from the server's own record.** The server keeps the snapshots it sent the subject's client. The wire is the newest; the picture is the interpolation one delay behind it, which is exactly what the stock client draws. A game would use the client's acknowledged snapshot; scoring aim against a timeline the client was never shown would manufacture the case.
- **The probe is a player on the wire.** It is replicated with the same fields as any body, to the subject's client only, with no collider and no sound, inside a sealed chamber. The stock client draws it as it draws any body, behind the wall. The server queries vision and audio against it every tick and ends it the moment either would succeed.
- **The server's view is the one fpsdet reads.** A shot's enemy, wire and picture fields are computed from the view the shot was fired from; the kick lands on the view after the event is written.

## What the arena deliberately does not emit

`skill_band` (no matchmaker: everyone is `unrated`, as on a community server), `aim_jitter_deg` (so the quiet-aim check reads `telemetry_unavailable`), `acquire_ms` and `view_delta_deg` (no supporting tells), `party_id` (no teams), and nothing a client reports about itself. Each missing field turns its check off, and the eligibility panel says which.

## The pitfalls the scenarios are built around

- A channel you did not check must be `unchecked`, or left out. Reporting it `absent` turns the unknowable scenario's review into something you manufactured.
- Audio is knowledge. If your server emits footsteps a client can hear, say so on the shot, or legal tracking through sound becomes a wallhack case.
- A visible enemy labelled hidden is an emitter bug, and fpsdet names it on the case.
- A challenge body your server reports seen or heard at any moment voids the challenge; place it where the queries fail for the whole window, and query every tick.
- A single honest shot inside a stream of mirrored ones is enough to pull the same-tick correlation under the bar. The check is strict on purpose.
