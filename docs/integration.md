# Integration

Emit from the dedicated server, at the moment the server accepts the shot or the movement sample, using the server's hit result and the server's clock. One JSON object per line.

Required on every line: `game_id`, `match_id`, `player_id`, `t_ms`, and a rank (`skill_band` or `skill_prior`). `event_type` defaults to `shot`.

## Shot

```json
{
  "game_id": "your-game",
  "match_id": "m-1001",
  "player_id": "stable-pseudonym",
  "t_ms": 18420,
  "utc": "2026-10-02T18:04:11Z",
  "event_type": "shot",
  "skill_band": "average",
  "map_id": "customs",
  "party_id": "party-9",
  "weapon_class": "rifle",
  "weapon_id": "ak-74",
  "mod_set": ["compensator", "vertical_grip"],
  "hit": true,
  "hitbox": "head",
  "distance_m": 42.5,
  "through_geometry": false,
  "spray_index": 6,
  "recoil_pitch_deg": 1.4,
  "expected_min_recoil_pitch_deg": 1.1,
  "applied_recoil_pitch_deg": 1.8,
  "compensation_pitch_deg": -0.4,
  "hidden_track_ms": 0,
  "private_track_ms": 0,
  "wire_error_deg": 1.8,
  "picture_error_deg": 0.4,
  "interp_delay_ms": 100,
  "information_state": "visible",
  "aim_jitter_deg": 0.42,
  "enemy_id": "enemy-4",
  "loadout_weight_kg": 14.2,
  "on_ground": true,
  "speed_mps": 5.4,
  "expected_max_ground_speed_mps": 6.1,
  "displacement_cause": "none"
}
```

`hitbox` is `head`, `upper_torso`, `lower_torso`, `limbs`, or omitted on a miss. `through_geometry` is the server trace, not the client's claim. Omit `recoil_pitch_deg` until the server can measure view pitch across the spray. Omit `acquire_ms` unless the server has a real visibility test. A client-reported "I saw them" is forgeable.

`applied_recoil_pitch_deg` is the kick the server applied on that shot. `compensation_pitch_deg` is the signed player view command on the same tick. Negative is a pull-down. Omit either one and the mirror check does nothing. The leftover check uses the same pair, after the kick and one lagged kick are subtracted, and only compares that leftover across accounts. `hidden_track_ms` is milliseconds that shot's aim cone contained an enemy the server had not made visible. Omit it, or send 0, when you do not have that query. A query that marks a visible enemy as hidden manufactures reviews. Corner pre-aim belongs in `acquire_ms`, not here.

`private_track_ms` is milliseconds that shot's aim cone contained the server's private replay: another player's motion, on a different heading, where this client's line-of-sight and audio queries both fail. Do not set an invisible or decoy bit on that body. The stock client drops it before draw because it is occluded. A snapshot parser that keeps every player still sees a person moving. Omit the field, or send 0, when this shot was not on that path. A short crossing is not a review. Labeling a body this client could see as private manufactures the case.

`wire_error_deg` is the angular error from this aim to the quantized snapshot the server just sent. `picture_error_deg` is the angular error to the position the official client is drawing, one interpolation delay earlier. `interp_delay_ms` is that delay. All three are server-computed. Omit any of them when you cannot measure it, and that shot does not count. `view_delta_deg` is a cohort statistic. It is not this pair. A legal example sits closer to the picture than to the wire. Standing still makes the two errors match. Scoring against a timeline the client was not shown manufactures the case.

`information_state` is `visible`, `audio`, or `unknowable`. Unknowable means this client's own line-of-sight and audio queries both failed for that enemy. `aim_jitter_deg` is the aim noise on that shot, in degrees, and it is not negative. `enemy_id` is the server's id for the enemy that shot was about. Omit any of the three and that sample is skipped. Audio is knowable: footsteps and a legal callout have to be labeled, or a quiet aim on a heard target becomes a radar case. Labeling a visible enemy as unknowable manufactures a smoothness review, a hidden-track review, and a teammate watch. That is an emitter bug.

`player_id` should be a stable pseudonym. Keep the map from pseudonym to account inside your own network.

## Movement

Send movement on a fixed cadence (10 Hz is enough). Shots can also carry speed, but a speedhack between shots is invisible if you only sample on the trigger.

```json
{
  "game_id": "your-game",
  "match_id": "m-1001",
  "player_id": "stable-pseudonym",
  "t_ms": 19000,
  "event_type": "movement",
  "skill_band": "average",
  "loadout_weight_kg": 14.2,
  "speed_mps": 5.5,
  "expected_max_ground_speed_mps": 6.1,
  "on_ground": true,
  "displacement_cause": "none"
}
```

When a grenade, a vehicle, a parachute, a ladder, a zipline, an ability, or an admin teleport moves the body, set `displacement_cause` for every sample in that window. If you do not know, send `unknown`. Unknown is dropped. A long untagged throw can look like a speedhack, and the case will say the server did not mark a cause.

## Unity

Dedicated server script, not a client MonoBehaviour. `examples/unity/BaselineEmitter.cs` writes a line. Ship the lines to your collector. Do not have the client POST its own stats.

## Unreal

From the server GameMode, after the server confirms the hit (post lag-compensation rewind, using the server's hit result):

```cpp
// Pseudocode. Run only when HasAuthority().
FString Line = FString::Printf(
    TEXT("{\"game_id\":\"%s\",\"match_id\":\"%s\",\"player_id\":\"%s\",\"t_ms\":%lld,"
         "\"event_type\":\"shot\",\"skill_band\":\"%s\",\"weapon_class\":\"%s\","
         "\"weapon_id\":\"%s\",\"hit\":%s,\"distance_m\":%.2f,"
         "\"expected_max_ground_speed_mps\":%.2f,\"on_ground\":%s,"
         "\"displacement_cause\":\"%s\"}"),
    *GameId, *MatchId, *PlayerPseudonym, ServerTimeMs, *Band, *WeaponClass,
    *WeaponId, bHit ? TEXT("true") : TEXT("false"), DistanceM,
    ExpectedSpeed, bOnGround ? TEXT("true") : TEXT("false"), *Cause);
```

## Godot

```gdscript
# Dedicated server peer only.
var line = JSON.stringify({
    "game_id": "your-game",
    "match_id": match_id,
    "player_id": pseudonym,
    "t_ms": Time.get_ticks_msec(),
    "event_type": "shot",
    "skill_band": band,
    "weapon_class": weapon_class,
    "hit": hit,
    "distance_m": distance_m,
    "on_ground": body.on_floor,
    "displacement_cause": cause,
    "expected_max_ground_speed_mps": expected_speed
})
```

## Collector

Appending lines to a file is enough for a solo dev. `fpsdet ingest` partitions them. A studio can point the same stream at a bus they already run. The scorer does not care how the line arrived.

Call `run_score` in-process if you would rather not shell out:

```python
from fpsdet.parse import load_profile, parse_event
from fpsdet.pipeline import run_score

profile = load_profile("profiles/example-loadout.json")
events = [parse_event(obj) for obj in batch]
cases = run_score(events, profile, cohort, history, reports)
```

Pass a cohort built from an earlier window. Omit it only when you accept the in-file warning.
