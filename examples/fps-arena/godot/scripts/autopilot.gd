extends RefCounted
## Honest scripted players, used where no person is at the keyboard: the stock client runs one when the
## harness qualifies a scenario, and the server runs the same code in its deterministic mode. An autopilot
## aims only at bodies the caller says a person could see: on the client, drawn bodies with a clear line
## from the eye; on the server, true bodies with a clear line. It never reads a hidden body, the probe,
## or anything a stock client does not show.
##
## decide() is a pure function of what it is shown. ``view`` carries the own position, the current look
## (yaw, pitch) and recoil offset, the bodies with their chest positions and visibility, and the elapsed
## match time. ``memory`` is the behaviour's own scratch dictionary.

const Arena = preload("res://scripts/arena_map.gd")

const TURN_RATE := 360.0  # degrees a second, at most
const ON_TARGET_DEG := 1.5
# The runner's loop around the south room, at full speed.
const RUN_LOOP := [Vector3(-9, 0, 12), Vector3(-9, 0, 4), Vector3(6, 0, 4), Vector3(6, 0, 12)]


static func decide(behaviour: String, view: Dictionary, memory: Dictionary, delta: float) -> Dictionary:
	var position: Vector3 = view.position
	var eye := position + Vector3(0, Arena.EYE_HEIGHT, 0)
	var look: Vector2 = view.look
	var offset: float = view.get("recoil_offset", 0.0)
	var elapsed: int = view.elapsed_ms
	var visible: Variant = _nearest_visible(eye, view.bodies)
	var target: Variant = null
	var move := Vector2.ZERO
	var fire := false
	match behaviour:
		"holder":
			# Hold the doorway's left frame from across the room, as a player waiting for a push would; a short
			# burst at it now and then, so the case has shots.
			target = Arena.look_angles(eye, Arena.FRAME_EDGE)
			fire = elapsed % 6000 < 300
		"runner":
			target = visible if visible != null else Vector2(0.0, 0.0)
			var index: int = int(memory.get("waypoint", 0))
			var to: Vector3 = RUN_LOOP[index] - position
			to.y = 0
			if to.length() < 0.6:
				# Turn the corner without stopping: a runner does not pause at a waypoint.
				index = (index + 1) % RUN_LOOP.size()
				memory["waypoint"] = index
				to = RUN_LOOP[index] - position
				to.y = 0
			move = _local_move(look.x, to.normalized())
			fire = visible != null and _on_target(look, visible, offset) and elapsed % 1500 < 300
		"trigger":
			target = visible if visible != null else Vector2(0.0, 0.0)
			fire = visible != null and _on_target(look, visible, offset)
		"idle":
			target = look
		_:  # tracker: strafe gently, aim at the nearest visible body, fire in bursts when on it
			if position.x > 1.5:
				memory["strafe"] = -1.0
			elif position.x < -1.5:
				memory["strafe"] = 1.0
			move = _local_move(look.x, Vector3(float(memory.get("strafe", 1.0)), 0, 0)) * 0.4
			target = visible if visible != null else Vector2(0.0, 0.0)
			fire = visible != null and _on_target(look, visible, offset) and elapsed % 1500 < 300
	# A person pulls the kicked view back onto the target: the command compensates what the view shows, which
	# is the kick already landed, never the one about to land.
	var want := Vector2(float(target.x), float(target.y) - offset)
	var turn := TURN_RATE * delta
	look = Vector2(
		wrapf(look.x + clampf(wrapf(want.x - look.x, -180.0, 180.0), -turn, turn), -180.0, 180.0),
		clampf(look.y + clampf(want.y - look.y, -turn, turn), -89.0, 89.0),
	)
	return {"move": move, "look": look, "fire": fire}


## The look angles to the nearest body a person could see, or null.
static func _nearest_visible(eye: Vector3, bodies: Array) -> Variant:
	var best := 1e9
	var found: Variant = null
	for body in bodies:
		if not body.visible:
			continue
		var chest: Vector3 = body.chest
		var distance := eye.distance_to(chest)
		if distance < best:
			best = distance
			found = Arena.look_angles(eye, chest)
	return found


static func _on_target(look: Vector2, target: Vector2, offset: float) -> bool:
	return absf(wrapf(target.x - look.x, -180.0, 180.0)) < ON_TARGET_DEG and absf(target.y - (look.y + offset)) < ON_TARGET_DEG


## A world direction as the client's move command (x right, y forward), relative to where the player looks.
static func _local_move(yaw: float, world: Vector3) -> Vector2:
	var forward := Arena.aim_direction(yaw, 0.0)
	var right := Vector3(-forward.z, 0, forward.x)
	return Vector2(world.dot(right), world.dot(forward)).limit_length(1.0)
