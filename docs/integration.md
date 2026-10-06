# Integration

Emit from the dedicated server, at the moment the server accepts the shot or the movement sample, using the server's hit result and the server's clock. One JSON object per line.

Required on every line: `game_id`, `match_id`, `player_id`, and `t_ms` (milliseconds since the match started). Send a rank (`skill_band` or `skill_prior`) when you have a matchmaker. Without one the player is `unrated`, and everyone unrated on the server is one population. That is right for a community server. `event_type` defaults to `shot`.

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

`applied_recoil_pitch_deg` is the kick the server applied on that shot. `compensation_pitch_deg` is the signed player view command on the same tick. Negative is a pull-down. Omit either one and the mirror check does nothing. Send `spray_index` with them (0 for the first round of each spray). In a game with a fixed spray pattern, practiced players pull it on time, so the check removes the average kick and command at each spray index and tests what is left. Set `recoil_pattern` in the profile: `learnable` (the default) when the kick repeats by spray index, `random` only when every kick is drawn fresh. The leftover check uses the same pair, after the kick and one lagged kick are subtracted, and compares that leftover across accounts on the same build, lined up by spray index.

`hidden_track_ms` is milliseconds, since this player's previous shot in the match, that the aim cone contained an enemy this client could neither see nor hear. Omit it, or send 0, when you do not have that query. If you run server-side culling, count only enemies that were actually sent to this client (see [culling.md](culling.md)). A query that marks a visible enemy as hidden manufactures reviews. Corner pre-aim belongs in `acquire_ms`, not here. A larger value is cut to the time since the previous shot, so a running total is harmless but wasted.

`since_perceived_ms` is the time since this client last saw or heard the enemy that shot was about. Send it with `hidden_track_ms` and `information_state`. Under `hidden_grace_ms` (default 1000) the shot is not hidden tracking and not an unknowable sample. Tracking a body that just ducked behind cover, or spraying where they went, is human.

`private_track_ms` is milliseconds that shot's aim cone contained the server's private replay: another player's motion, on a different heading, where this client's line-of-sight and audio queries both fail. Do not set an invisible or decoy bit on that body. The stock client drops it before draw because it is occluded. A snapshot parser that keeps every player still sees a person moving. Omit the field, or send 0, when this shot was not on that path. A short crossing is not a review. Labeling a body this client could see as private manufactures the case.

`challenge_id` and `challenge_track_ms` are the planned form of the same thing ([challenges.md](challenges.md)). `challenge_id` names one challenge from a plan written by `fpsdet challenge plan`. `challenge_track_ms` is milliseconds, since the previous event that named the same challenge (or since its window opened), that the aim cone contained that challenge's body. Send both on every shot or movement event for that player while the window is open, with 0 when the aim was elsewhere, and score with `--challenges` and the plan file. A response counts only for the exact challenge it names, planned for this player, in its match, inside its window. An unknown, stale or foreign id is reported and not read. On an event that names a challenge, `private_track_ms` is not read. Keep the challenge id on the server: never send it to the client. If the same event names a real enemy (`enemy_id` with `information_state`, `vision_state`, `audio_state` or `since_perceived_ms`), that enemy must be unknowable for the sample to count, because aim on an enemy this client could see or hear explains it.

`wire_error_deg` is the angular error from this aim to the quantized snapshot the server just sent. `picture_error_deg` is the angular error to the position the official client is drawing, one interpolation delay earlier. `interp_delay_ms` is that delay. All three are server-computed. Omit any of them when you cannot measure it, and that shot does not count. `view_delta_deg` is a cohort statistic. It is not this pair. A legal example sits closer to the picture than to the wire. Standing still makes the two errors match. Scoring against a timeline the client was not shown manufactures the case.

`information_state` is `visible`, `audio`, or `unknowable`. Unknowable means this client's own line-of-sight and audio queries both failed for that enemy. If your server runs those queries separately, or sometimes runs only one, send `vision_state` and `audio_state` instead, each `known`, `absent` or `unchecked`. A channel you did not check is `unchecked`, or left out. Never send it as `absent`: fpsdet treats an unchecked channel as "could have known", and the information checks abstain ([knowledge-engine.md](knowledge-engine.md)). If your game has another legal way to know where an enemy is, such as a radar or teammate callouts the server can see, declare it in the profile's `knowledge_channels`; until your events report it, the information checks will not act. `aim_jitter_deg` is the aim noise on that shot, in degrees, and it is not negative. `enemy_id` is the server's id for the enemy that shot was about. Omit any of the three and that sample is skipped. Audio is knowable: footsteps and a legal callout have to be labeled, or a quiet aim on a heard target becomes a radar case. A shot labeled `audio` never counts as hidden tracking. Labeling a visible enemy as unknowable manufactures a smoothness review, a hidden-track review, and a teammate watch. That is an emitter bug.

`player_id` should be a stable pseudonym. Keep the map from pseudonym to account inside your own network.

Records from other integrity systems, such as a client anti-cheat, an attestation service, a league's rulings or your own detector, are not events. Send them beside the events with `fpsdet score --external` in the `fpsdet.external/1` format, or through an adapter, using the same player pseudonym. [external-evidence.md](external-evidence.md) has the format and the rules: an external record can make a watch, never a review.

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

Use [`examples/unity/BaselineEmitter.cs`](../examples/unity/BaselineEmitter.cs). It runs on the dedicated server, never on a client: it only compiles into dedicated server builds and the editor (`#if UNITY_SERVER || UNITY_EDITOR`), so wrap your calls in the same `#if`. It appends one line per call to a file under `Application.persistentDataPath/fpsdet/`, escapes every string, writes numbers with a `.` in any locale, and leaves out any argument passed as `null` and any number that is NaN or infinite. `BeginMatch` sets the game and match ids and starts the match clock. `t_ms` is `MatchTimeMs`: milliseconds since the match started, on the server, not wall time.

```csharp
#if UNITY_SERVER || UNITY_EDITOR
using Fpsdet;

// At match start, on the main thread.
BaselineEmitter.BeginMatch("your-game", "m-1001");

// Where the server's own trace has decided the hit.
BaselineEmitter.Shot(pseudonym, BaselineEmitter.MatchTimeMs, "average", "rifle", hit: true,
    weaponId: "ak-74", modSet: new[] { "compensator", "vertical_grip" },
    hitbox: "head", distanceM: 42.5);

// From a server loop, about 10 times a second per player. Horizontal speed only.
Vector3 v = controller.velocity;
BaselineEmitter.Movement(pseudonym, BaselineEmitter.MatchTimeMs, "average",
    speedMps: new Vector2(v.x, v.z).magnitude, onGround: controller.isGrounded,
    displacementCause: DisplacementCause.None,
    loadoutWeightKg: kitKg, expectedMaxGroundSpeedMps: capMps);
#endif
```

Ship the file to your collector, or point `BaselineEmitter.Sink` at your own. Do not have the client POST its own stats.

## Unreal

Emit where the server confirms the hit: after the lag-compensation rewind, from the server's own hit result. In most projects that is weapon or character code running on the server, such as the implementation of the fire Server RPC behind a `HasAuthority()` check. It is usually not the GameMode.

The snippet builds the line with `FJsonObject` and a condensed JSON writer, which escapes every string, and appends it with `FFileHelper::SaveStringToFile(..., FILEWRITE_Append)`. Add `"Json"` to the module's dependencies in your `Build.cs`. Unreal units are centimetres, so divide distances and speeds by 100. `t_ms` is milliseconds since the match started: `MatchStartSeconds` is `GetWorld()->GetTimeSeconds()`, saved when the match starts. Check the first line you write: `t_ms` must come out as a whole number such as `18420`, not `18420.0`, or the parser rejects the line. `SetFinite` leaves out a number that is NaN or infinite, because that is not valid JSON.

```cpp
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "GameFramework/CharacterMovementComponent.h"

using FFpsdetWriter = TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>;
using FFpsdetWriterFactory = TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>;

// Appends one event as one line of UTF-8 (no BOM) under Saved/fpsdet/.
static void FpsdetAppend(const TSharedRef<FJsonObject>& Event)
{
    FString Line;
    TSharedRef<FFpsdetWriter> Writer = FFpsdetWriterFactory::Create(&Line);
    FJsonSerializer::Serialize(Event, Writer);
    Line.AppendChar(TEXT('\n'));
    const FString Path = FPaths::ProjectSavedDir() / TEXT("fpsdet") / TEXT("events.ndjson");
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
    FFileHelper::SaveStringToFile(Line, *Path, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM,
                                  &IFileManager::Get(), FILEWRITE_Append);
}

// Leaves the field out when the number is NaN or infinite.
static void SetFinite(const TSharedRef<FJsonObject>& Event, const TCHAR* Key, double Value)
{
    if (FMath::IsFinite(Value))
    {
        Event->SetNumberField(Key, Value);
    }
}

// MatchId, PlayerPseudonym, SkillBand, MatchStartSeconds, WeaponClass, WeaponId,
// DisplacementCause, and KitKg are your own members, set by your server code.
TSharedRef<FJsonObject> AMyCharacter::NewFpsdetEvent(const TCHAR* EventType) const
{
    TSharedRef<FJsonObject> Event = MakeShared<FJsonObject>();
    Event->SetStringField(TEXT("game_id"), TEXT("your-game"));
    Event->SetStringField(TEXT("match_id"), MatchId);
    Event->SetStringField(TEXT("player_id"), PlayerPseudonym);
    // Milliseconds since match start.
    Event->SetNumberField(TEXT("t_ms"),
        FMath::FloorToDouble((GetWorld()->GetTimeSeconds() - MatchStartSeconds) * 1000.0));
    Event->SetStringField(TEXT("event_type"), EventType);
    Event->SetStringField(TEXT("skill_band"), SkillBand);
    return Event;
}

// Call from the server's fire path, after the server's trace decided bHit.
void AMyCharacter::EmitShot(bool bHit, const FHitResult& Hit)
{
    if (!HasAuthority())
    {
        return;
    }
    TSharedRef<FJsonObject> Event = NewFpsdetEvent(TEXT("shot"));
    Event->SetStringField(TEXT("weapon_class"), WeaponClass);
    Event->SetStringField(TEXT("weapon_id"), WeaponId);
    Event->SetBoolField(TEXT("hit"), bHit);
    if (bHit)
    {
        SetFinite(Event, TEXT("distance_m"), Hit.Distance / 100.0);
    }
    FpsdetAppend(Event);
}

// Call from a server-side timer, about 10 times a second.
void AMyCharacter::EmitMovement()
{
    if (!HasAuthority())
    {
        return;
    }
    const UCharacterMovementComponent* Move = GetCharacterMovement();
    TSharedRef<FJsonObject> Event = NewFpsdetEvent(TEXT("movement"));
    SetFinite(Event, TEXT("speed_mps"), Move->Velocity.Size2D() / 100.0);
    Event->SetBoolField(TEXT("on_ground"), Move->IsMovingOnGround());
    Event->SetStringField(TEXT("displacement_cause"), DisplacementCause); // "none" for a normal sprint
    SetFinite(Event, TEXT("loadout_weight_kg"), KitKg);
    // The cap the movement component is using now. Already includes your weight and perk math
    // if that math sets MaxWalkSpeed.
    SetFinite(Event, TEXT("expected_max_ground_speed_mps"), Move->GetMaxSpeed() / 100.0);
    FpsdetAppend(Event);
}
```

`SaveStringToFile` opens and closes the file for every line. That is fine at 10 Hz. At high fire rates, collect a tick's lines and append them once, or hand them to your log shipper.

## Godot

Godot 4. `begin_match` opens the file once and moves to its end, so earlier lines are kept. `emit_event` drops null values and any float that is NaN or infinite, turns the Dictionary into JSON with `JSON.stringify`, and appends it as one line. `t_ms` comes from `match_ms()`: milliseconds since `begin_match`, as an integer. `is_on_floor()` is a method on `CharacterBody3D`.

```gdscript
# An autoload Node on the server. Run it on a dedicated server export, not on a listen-server host.
const EVENTS_PATH := "user://fpsdet/events.ndjson"

var match_id := ""
var match_start_ms := 0
var _events: FileAccess

func begin_match(id: String) -> void:
    match_id = id
    match_start_ms = Time.get_ticks_msec()
    DirAccess.make_dir_recursive_absolute("user://fpsdet")
    _events = FileAccess.open(EVENTS_PATH, FileAccess.READ_WRITE)  # existing file: keep its lines
    if _events == null:
        _events = FileAccess.open(EVENTS_PATH, FileAccess.WRITE)  # first run: create it
    if _events == null:
        push_error("fpsdet: cannot open %s (error %d)" % [EVENTS_PATH, FileAccess.get_open_error()])
        return
    _events.seek_end()

func match_ms() -> int:
    return Time.get_ticks_msec() - match_start_ms  # milliseconds since match start

func emit_event(event: Dictionary) -> void:
    if _events == null or not multiplayer.is_server():
        return
    for key in event.keys():
        var value = event[key]
        if value == null or (value is float and not is_finite(value)):
            event.erase(key)
    _events.store_line(JSON.stringify(event))  # store_line adds the newline
    _events.flush()

func emit_shot(pseudonym: String, band: String, weapon_class: String, weapon_id: String,
        hit: bool, distance_m: float) -> void:
    var event := {
        "game_id": "your-game",
        "match_id": match_id,
        "player_id": pseudonym,
        "t_ms": match_ms(),
        "event_type": "shot",
        "skill_band": band,
        "weapon_class": weapon_class,
        "weapon_id": weapon_id,
        "hit": hit,
    }
    if hit:
        event["distance_m"] = distance_m
    emit_event(event)

# Call about 10 times a second per player, from the server's physics loop.
func emit_movement(pseudonym: String, band: String, body: CharacterBody3D, cause: String,
        kit_kg: float, expected_speed: float) -> void:
    var v := body.velocity
    emit_event({
        "game_id": "your-game",
        "match_id": match_id,
        "player_id": pseudonym,
        "t_ms": match_ms(),
        "event_type": "movement",
        "skill_band": band,
        "speed_mps": Vector2(v.x, v.z).length(),
        "on_ground": body.is_on_floor(),
        "displacement_cause": cause,  # "none" for a normal sprint
        "loadout_weight_kg": kit_kg,
        "expected_max_ground_speed_mps": expected_speed,
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
