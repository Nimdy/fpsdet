extends RefCounted
## Server-driven bots. A bot walks its scenario's waypoints at its scenario's speed, pauses where told,
## faces the player, and fires at the player only when the server's own line of sight says it can see
## them. A bot with one waypoint stands still. Bots are players to the telemetry: the server emits their
## movement and shots like anyone's.

const Arena = preload("res://scripts/arena_map.gd")

const FIRE_EVERY_MS := 1000


## A bot's runtime state from its scenario program.
static func make(program: Dictionary) -> Dictionary:
	var path := []
	for point in program.get("path", []):
		path.append(Vector3(float(point[0]), 0, float(point[1])))
	return {
		"id": String(program.get("id", "arena-bot")),
		"path": path,
		"speed": float(program.get("speed", 0.0)),
		"pause_ticks": int(round(float(program.get("pause_ms", 0)) / Arena.TICK_MS)),
		"fires": bool(program.get("fires", false)),
		"index": 0,
		"wait": 0,
		"last_fire_ms": -100000,
	}


## The bot's command this tick: where to move, where to look, whether to fire.
static func steer(bot: Dictionary, position: Vector3, eye: Vector3, subject_eye: Vector3, sees_subject: bool, t_ms: int, noise: RandomNumberGenerator) -> Dictionary:
	var path: Array = bot.path
	var move := Vector2.ZERO
	if path.size() > 1 and bot.speed > 0.0:
		if bot.wait > 0:
			bot.wait -= 1
		else:
			var goal: Vector3 = path[bot.index]
			var to := goal - position
			to.y = 0
			if to.length() < 0.3:
				bot.index = (bot.index + 1) % path.size()
				bot.wait = bot.pause_ticks
			else:
				# The command is in the bot's own frame: x right, y forward of where it looks.
				var look_yaw: float = Arena.look_angles(eye, subject_eye).x
				var forward := Arena.aim_direction(look_yaw, 0.0)
				var right := Vector3(-forward.z, 0, forward.x)
				var direction := to.normalized()
				move = Vector2(direction.dot(right), direction.dot(forward)) * minf(1.0, bot.speed / Arena.RUN_SPEED)
	var look := Arena.look_angles(eye, subject_eye) + Vector2(noise.randf_range(-1.5, 1.5), noise.randf_range(-1.0, 1.0))
	var fire := false
	if bot.fires and sees_subject and t_ms - int(bot.last_fire_ms) >= FIRE_EVERY_MS:
		bot.last_fire_ms = t_ms
		fire = true
	return {"move": move, "yaw": look.x, "pitch": look.y, "fire": fire}
