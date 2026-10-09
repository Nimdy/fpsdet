extends RefCounted
## The Arena's one rifle, as the server runs it. The client never decides a shot, a hit or a kick.
##
## Fire cycle: the server accepts a shot every CYCLE_TICKS (100 ms); a fault can lower that, which is what
## the fire-rate scenario does. Recoil: each accepted shot kicks the server-side view up by a learnable
## pattern plus a fresh random part; the pattern repeats by spray index, the random part never does, so
## fpsdet's mirror check has something nobody can anticipate. The floor the server declares on every
## shot (expected_min_recoil_pitch_deg) is under the smallest kick a healthy weapon applies.

const CYCLE_TICKS := 6  # 100 ms at 60 Hz: the rifle's own cycle, and the profile's min_shot_interval_ms
const MAGAZINE := 30
const RELOAD_TICKS := 90  # 1.5 s
const SPRAY_RESET_TICKS := 18  # 300 ms without a shot starts a new spray (spray_index back to 0)
const FLOOR_DEG := 0.8  # the declared minimum kick: no healthy shot lands under it
const KICK_BASE := 1.6
const KICK_SWING := 0.5
const KICK_NOISE := 0.3  # the fresh random part, drawn per shot
const RECOVERY_DEG_PER_S := 10.0  # how fast the kicked view settles back
const DAMAGE_TO_BOT := 25
const DAMAGE_TO_PLAYER := 8
const RANGE_M := 100.0


## The learnable part of the kick at one spray index, in degrees.
static func pattern(spray_index: int) -> float:
	return KICK_BASE + KICK_SWING * sin(spray_index * 0.6)


## The kick the server applies on one accepted shot: the pattern plus a fresh draw. ``multiplier`` is 1
## for a healthy weapon; the recoil-floor scenario lowers it to stand in for recoil that lands under the floor.
static func kick(spray_index: int, rng: RandomNumberGenerator, multiplier: float) -> float:
	return snappedf((pattern(spray_index) + rng.randf_range(-KICK_NOISE, KICK_NOISE)) * multiplier, 0.001)
