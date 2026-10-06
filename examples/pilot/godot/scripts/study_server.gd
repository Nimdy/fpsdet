extends Node3D
## The consented human pilot's authoritative server (examples/human-pilot, docs/human-pilot.md).
##
## The P12 pilot's server, unchanged in what matters (scripts/server.gd): the same plan checks and recipe,
## the same vision rays and audio query, the same aim cone, the same NDJSON. What it adds: one human
## participant, whose client sends commands only; the study arena and its mode's bots, which the server
## drives and which never shoot; and, while a challenge runs, the server's own per-tick study telemetry
## (where the aim was relative to the body, how fast it turned, whether a visible bot was in the cone),
## written to the session's private folder and never published as it is.
##
## Options (after "--"): --participant --mode --match --match-ms --plan --secret-file --events --log
## --private --perf --port --bind --wait-ms --follower

const Arena = preload("res://scripts/arena.gd")
const Study = preload("res://scripts/study_arena.gd")
const Recipe = preload("res://scripts/recipe.gd")

const GAME_ID := "fpsdet-godot-study"
const STUDY_VERSION := 1
# The P12 pilot's numbers, unchanged (tests/test_human_pilot.py compares them with scripts/server.gd).
const CONE_DEG := 2.0
const HEARING_RADIUS := 30.0
const SOUND_MEMORY_TICKS := 30
const STEP_EVERY_TICKS := 24
const FIRE_COOLDOWN_TICKS := 6
const RESPONDER_LAG_TICKS := 9
const VISION_MARGIN := 0.25
const DAMAGE := 25

var options := {}
var mode := "free"
var participant := ""
var match_id := "study-local"
var match_ms := 240000
var follower := false
var tick := 0
var started := false
var finished := false
var wait_until_ms := 0

var players := {}  # name -> state: the participant and the bots
var order := []
var bots := {}  # bot name -> {"path", "speed", "pause_ticks", "index", "wait"}
var peers := {}  # peer id -> participant
var entity_ids := {}
var events: FileAccess
var log_file: FileAccess
var study: FileAccess
var private_dir := ""
var rng := RandomNumberGenerator.new()
var noise := RandomNumberGenerator.new()

var challenges := []
var active := {}
var probe_history := []
var sounds := []
var accumulator := {}
var tick_fields := {}
var last_aim := Vector3.ZERO

var perf := {"ticks": 0, "tick_us": 0, "tick_us_max": 0, "challenge_us": 0, "knowledge_us": 0, "study_us": 0, "telemetry_us": 0, "rays": 0,
	"event_bytes": 0, "event_lines": 0, "snapshot_bytes": 0, "probe_ticks": 0, "tracked_ticks": 0, "verdicts": {"vision": {}, "audio": {}}}


func _ready() -> void:
	mode = options.get("mode", mode)
	participant = options.get("participant", "")
	match_id = options.get("match", match_id)
	match_ms = int(options.get("match-ms", str(match_ms)))
	follower = options.get("follower", "false") == "true"
	private_dir = options.get("private", "")
	rng.randomize()
	noise.seed = 20261006
	events = FileAccess.open(options.get("events", "user://events.ndjson"), FileAccess.WRITE)
	log_file = FileAccess.open(options.get("log", "user://server.log"), FileAccess.WRITE)
	if not Study.MODES.has(mode) or not participant.begins_with("hp-"):
		_log({"kind": "error", "detail": "a study session needs a known --mode and an hp- participant id"})
		get_tree().quit(2)
		return
	if private_dir != "":
		study = FileAccess.open(private_dir.path_join("study.ndjson"), FileAccess.WRITE)
	Study.build(self, false)
	order.append(participant)
	players[participant] = _spawn(participant, Study.SPAWN)
	var index := 0
	for spec in Study.MODES[mode]:
		index += 1
		var name := "bot-%d" % index
		order.append(name)
		players[name] = _spawn(name, spec[0][0])
		bots[name] = {"path": spec[0], "speed": float(spec[1]), "pause_ticks": int(round(float(spec[2]) / Arena.TICK_MS)), "index": 0, "wait": 0}
	var used := {}
	for key in order + ["probe"]:
		var id := ""
		while id == "" or used.has(id):
			id = "e%d" % rng.randi_range(100, 999)
		used[id] = true
		entity_ids[key] = id
	_log({"kind": "start", "engine": Engine.get_version_info().string, "study_version": STUDY_VERSION, "mode": mode, "match_id": match_id,
		"match_ms": match_ms, "bots": bots.size(), "controlled_follower": follower})
	_load_plan()
	var bind: String = options.get("bind", "127.0.0.1")
	if not _private(bind):
		_log({"kind": "error", "detail": "the server binds only to loopback or a private LAN address; refusing a public one"})
		get_tree().quit(3)
		return
	var peer := ENetMultiplayerPeer.new()
	peer.set_bind_ip(bind)
	if peer.create_server(int(options.get("port", "24800")), 2) != OK:
		_log({"kind": "error", "detail": "cannot listen"})
		get_tree().quit(3)
		return
	multiplayer.multiplayer_peer = peer
	multiplayer.peer_disconnected.connect(func(id): peers.erase(id))
	wait_until_ms = Time.get_ticks_msec() + int(options.get("wait-ms", "600000"))


## Loopback or RFC 1918 only. The address itself is never written anywhere.
func _private(address: String) -> bool:
	var parts := address.split(".")
	if parts.size() != 4:
		return false
	var a := int(parts[0])
	var b := int(parts[1])
	return a == 127 or a == 10 or (a == 172 and b >= 16 and b <= 31) or (a == 192 and b == 168)


func _load_plan() -> void:
	var path: String = options.get("plan", "")
	if path == "" or not FileAccess.file_exists(path):
		_log({"kind": "plan", "status": "missing", "detail": "no challenge plan: no challenge runs in this session"})
		return
	var plan = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(plan) != TYPE_DICTIONARY or plan.get("format") != Recipe.PLAN_FORMAT or plan.get("game_id") != GAME_ID or plan.get("match_id") != match_id:
		_log({"kind": "plan", "status": "refused", "detail": "not a challenge plan for this game and session: no challenge runs"})
		return
	var loaded := Recipe.load_secret(options.get("secret-file", ""))
	var secret: PackedByteArray = loaded[0]
	if secret.is_empty():
		_log({"kind": "plan", "status": "refused", "detail": loaded[1] + ": no challenge runs"})
		return
	var accepted := []
	var refused := []
	for raw in plan.get("challenges", []):
		var record := Recipe.record(raw)
		if record.subject_id != participant or record.challenge_type != "occluded_motion_replay" or record.end_ms > match_ms:
			refused.append({"challenge_id": record.challenge_id, "detail": "not runnable in this session"})
			continue
		var realized := Recipe.realize(secret, record)
		if realized[1] != "":
			refused.append({"challenge_id": record.challenge_id, "detail": realized[1]})
			continue
		challenges.append({"plan": record, "parameters": realized[0], "ran": false})
		accepted.append({"challenge_id": record.challenge_id, "version": record.version, "start_ms": record.start_ms, "end_ms": record.end_ms})
	secret = PackedByteArray()
	_log({"kind": "plan", "status": "loaded", "accepted": accepted, "refused": refused})


func _spawn(name: String, at: Vector3) -> Dictionary:
	var body := CharacterBody3D.new()
	body.collision_layer = Arena.PLAYER_LAYER
	body.collision_mask = Arena.WORLD_LAYER
	var shape := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = Arena.BODY_RADIUS
	capsule.height = Arena.BODY_HEIGHT
	shape.shape = capsule
	shape.position = Vector3(0, Arena.BODY_HEIGHT / 2, 0)
	body.add_child(shape)
	body.position = at
	add_child(body)
	return {"name": name, "body": body, "spawn": at, "yaw": 0.0, "pitch": 0.0, "cmd": {"move": Vector2.ZERO, "yaw": 0.0, "pitch": 0.0, "fire": false},
		"health": 100, "kills": 0, "deaths": 0, "shots": 0, "hits": 0, "damage": 0, "last_fire": -1000, "last_step": -1000, "track": []}


func _position(name: String) -> Vector3:
	return players[name].body.global_position


func _eye(name: String) -> Vector3:
	return _position(name) + Vector3(0, Arena.EYE_HEIGHT, 0)


# RPCs, the same on the client (study_client.gd).


@rpc("any_peer", "reliable")
func hello(player_name: String) -> void:
	if player_name == participant and peers.is_empty():
		peers[multiplayer.get_remote_sender_id()] = participant
		_log({"kind": "joined"})


@rpc("any_peer", "unreliable_ordered")
func input_cmd(_seq: int, move: Vector2, yaw: float, pitch: float, fire: bool) -> void:
	if peers.get(multiplayer.get_remote_sender_id()) != participant:
		return
	players[participant].cmd = {"move": move.limit_length(1.0), "yaw": wrapf(yaw, -180.0, 180.0), "pitch": clampf(pitch, -89.0, 89.0), "fire": fire}


@rpc("authority", "unreliable_ordered")
func snapshot(_tick: int, _you: Array, _entities: Array) -> void:
	pass


@rpc("authority", "reliable")
func sound(_kind: String, _position: Vector3, _source: String) -> void:
	pass


@rpc("authority", "reliable")
func scoreboard(_rows: Array) -> void:
	pass


@rpc("authority", "reliable")
func match_over() -> void:
	pass


func _physics_process(_delta: float) -> void:
	if finished:
		return
	if not started:
		if not peers.is_empty() or Time.get_ticks_msec() > wait_until_ms:
			started = true
			_log({"kind": "session_start", "client": not peers.is_empty()})
			if peers.is_empty():
				_finish("no client joined")
		return
	tick += 1
	tick_fields = {}
	var begin := Time.get_ticks_usec()
	_steer()
	for name in order:
		_move(name)
	var mark := Time.get_ticks_usec()
	_update_challenge()
	perf.challenge_us += Time.get_ticks_usec() - mark
	mark = Time.get_ticks_usec()
	_observe()
	perf.knowledge_us += Time.get_ticks_usec() - mark
	for name in order:
		_fire(name)
		_footsteps(name)
	mark = Time.get_ticks_usec()
	if tick % Arena.EVENT_EVERY_TICKS == 0:
		for name in order:
			_movement_event(name)
	perf.telemetry_us += Time.get_ticks_usec() - mark
	if tick % Arena.SNAPSHOT_EVERY_TICKS == 0:
		_snapshots()
	if tick % Arena.TICK_HZ == 0:
		_scoreboard()
	var spent := Time.get_ticks_usec() - begin
	perf.ticks += 1
	perf.tick_us += spent
	perf.tick_us_max = max(perf.tick_us_max, spent)
	if Arena.t_ms(tick) >= match_ms:
		_finish("session over")


func _steer() -> void:
	for name in bots:
		var bot: Dictionary = bots[name]
		var state: Dictionary = players[name]
		var path: Array = bot.path
		var move := Vector2.ZERO
		if path.size() > 1:
			if bot.wait > 0:
				bot.wait -= 1
			else:
				var goal: Vector3 = path[bot.index]
				var to := goal - _position(name)
				to.y = 0
				if to.length() < 0.3:
					bot.index = (bot.index + 1) % path.size()
					bot.wait = bot.pause_ticks
				else:
					move = Vector2(to.x, to.z).normalized() * minf(1.0, bot.speed / Arena.RUN_SPEED)
		var look := Arena.look_angles(_eye(name), _eye(participant))
		state.cmd = {"move": move, "yaw": look.x, "pitch": look.y, "fire": false}
	if follower and not active.is_empty() and probe_history.size() > 0:
		# The controlled follower (P12's stand-in), for the comparison only: aim where the probe was 150 ms ago.
		var seen: Vector3 = probe_history[max(0, probe_history.size() - 1 - RESPONDER_LAG_TICKS)][1]
		var angles := Arena.look_angles(_eye(participant), seen + Vector3(0, Arena.CHEST_HEIGHT, 0))
		var cmd: Dictionary = players[participant].cmd
		players[participant].cmd = {"move": cmd.move, "yaw": angles.x + noise.randf_range(-0.3, 0.3), "pitch": angles.y + noise.randf_range(-0.3, 0.3), "fire": false}


func _move(name: String) -> void:
	var state: Dictionary = players[name]
	var body: CharacterBody3D = state.body
	state.yaw = float(state.cmd.yaw)
	state.pitch = float(state.cmd.pitch)
	var move: Vector2 = state.cmd.move
	body.velocity = Vector3(move.x * Arena.RUN_SPEED, body.velocity.y - 9.8 / Arena.TICK_HZ, move.y * Arena.RUN_SPEED)
	if body.is_on_floor() and body.velocity.y < 0:
		body.velocity.y = 0
	body.move_and_slide()
	state.track.append([tick, body.global_position, state.yaw])
	if state.track.size() > 25 * Arena.TICK_HZ:
		state.track.pop_front()


# The challenge: the realization the secret derived, in one of the sealed probe rooms.


func _update_challenge() -> void:
	var now := Arena.t_ms(tick)
	if active.is_empty():
		for entry in challenges:
			if not entry.ran and entry.plan.start_ms <= now and now <= entry.plan.end_ms:
				_begin(entry)
				break
		if active.is_empty():
			return
	if now > active.plan.end_ms:
		_end("window over")
		return
	var then: int = tick - int(active.delay_ticks)
	var source: Dictionary = players[active.source]
	var position: Vector3 = active.anchor + _turn(_track_at(source, then) - active.base, active.heading)
	position = Vector3(clampf(position.x, active.low.x, active.high.x), 0, clampf(position.z, active.low.y, active.high.y))
	active.position = position
	active.yaw = wrapf(_yaw_at(source, then) + active.heading, -180.0, 180.0)
	probe_history.append([tick, position])
	active.ticks += 1
	perf.probe_ticks += 1


func _begin(entry: Dictionary) -> void:
	var parameters: Dictionary = entry.parameters
	var sources: Array = bots.keys()
	sources.sort()
	var source: String = sources[int(parameters.route_pick) % sources.size()]
	var room: Array = Study.ROOMS[int(parameters.placement_pick) % Study.ROOMS.size()]
	var low: Vector2 = room[1]
	var high: Vector2 = room[2]
	var anchor := Vector3((low.x + high.x) / 2, 0, (low.y + high.y) / 2)
	var delay_ticks := int(round(int(parameters.replay_delay_ms) / Arena.TICK_MS))
	entry.ran = true
	active = {"entry": entry, "plan": entry.plan, "source": source, "room": room[0], "low": low, "high": high, "anchor": anchor,
		"heading": float(parameters.heading_offset_deg), "delay_ticks": delay_ticks, "base": _track_at(players[source], tick - delay_ticks),
		"position": anchor, "yaw": 0.0, "ticks": 0, "first_ms": Arena.t_ms(tick)}
	accumulator = {}
	_log({"kind": "challenge_start", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick)})


func _end(why: String) -> void:
	var entry: Dictionary = active.entry
	_log({"kind": "challenge_end", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick), "why": why, "ticks": active.ticks})
	if private_dir != "":
		# What ran, for the operator's check after the session and for the study's per-room tables. Never public.
		var record := {"challenge_id": entry.plan.challenge_id, "start_ms": entry.plan.start_ms, "end_ms": entry.plan.end_ms,
			"parameters": entry.parameters, "room": active.room, "source": active.source, "first_ms": active.first_ms,
			"last_ms": Arena.t_ms(tick - 1), "ticks": active.ticks, "ended": why}
		var out := FileAccess.open(private_dir.path_join("realization-%s.json" % entry.plan.challenge_id), FileAccess.WRITE)
		out.store_string(JSON.stringify(record, "", true) + "\n")
		out.close()
	active = {}
	probe_history = []


func _track_at(state: Dictionary, at_tick: int) -> Vector3:
	var track: Array = state.track
	var index := track.size() - 1 - (tick - at_tick)
	if index >= 0 and index < track.size():
		return track[index][1]
	return track[0][1] if track.size() > 0 else state.spawn


func _yaw_at(state: Dictionary, at_tick: int) -> float:
	var track: Array = state.track
	var index := track.size() - 1 - (tick - at_tick)
	if index >= 0 and index < track.size():
		return track[index][2]
	return 0.0


func _turn(offset: Vector3, degrees: float) -> Vector3:
	return Vector3(offset.x, 0, offset.z).rotated(Vector3.UP, deg_to_rad(degrees))


# Knowledge, as P12: rays from five eye points to sixteen body points, now and one interpolation delay ago,
# against the world only; a sound from that body within range in the last 500 ms.


func _observe() -> void:
	if active.is_empty():
		last_aim = Arena.aim_direction(players[participant].yaw, players[participant].pitch)
		return
	var before: Vector3 = probe_history[max(0, probe_history.size() - 1 - int(Arena.INTERP_DELAY_MS / Arena.TICK_MS))][1]
	var vision := _vision(participant, active.position, before)
	var audio := _audio(participant, "probe")
	perf.verdicts.vision[vision] = perf.verdicts.vision.get(vision, 0) + 1
	perf.verdicts.audio[audio] = perf.verdicts.audio.get(audio, 0) + 1
	var eye := _eye(participant)
	var aim := Arena.aim_direction(players[participant].yaw, players[participant].pitch)
	var chest: Vector3 = active.position + Vector3(0, Arena.CHEST_HEIGHT, 0)
	var tracked := _in_cone(eye, aim, chest)
	var mark := Time.get_ticks_usec()
	_study_row(eye, aim, chest, tracked, vision, audio)
	perf.study_us += Time.get_ticks_usec() - mark
	last_aim = aim
	if vision == "known" or audio == "known":
		_end("perceivable")  # the type's rule: a body the client could perceive is no longer a challenge
		return
	accumulator["ticks"] = int(accumulator.get("ticks", 0)) + 1
	accumulator["tracked"] = int(accumulator.get("tracked", 0)) + (1 if tracked else 0)
	perf.tracked_ticks += 1 if tracked else 0
	accumulator["vision"] = _worst(accumulator.get("vision", "absent"), vision)
	accumulator["audio"] = _worst(accumulator.get("audio", "absent"), audio)


## The study's own row for this tick: angles only, no position. Private; the analysis publishes aggregates.
func _study_row(eye: Vector3, aim: Vector3, chest: Vector3, tracked: bool, vision: String, audio: String) -> void:
	if study == null:
		return
	var to := chest - eye
	var visible_in_cone := false
	var separation := -1.0
	for name in bots:
		var bot_chest := _position(name) + Vector3(0, Arena.CHEST_HEIGHT, 0)
		var trail: Array = players[name].track
		var before: Vector3 = trail[max(0, trail.size() - 1 - int(Arena.INTERP_DELAY_MS / Arena.TICK_MS))][1]
		if _vision(participant, _position(name), before) != "known":
			continue
		var apart := rad_to_deg(to.angle_to(bot_chest - eye))
		separation = apart if separation < 0 else minf(separation, apart)
		visible_in_cone = visible_in_cone or _in_cone(eye, aim, bot_chest)
	var row := {"t": Arena.t_ms(tick), "c": active.plan.challenge_id, "in": tracked, "ang": snappedf(rad_to_deg(aim.angle_to(to)), 0.01),
		"lim": snappedf(CONE_DEG + rad_to_deg(atan(Arena.BODY_RADIUS / maxf(to.length(), 0.01))), 0.01),
		"turn": snappedf(rad_to_deg(last_aim.angle_to(aim)) * Arena.TICK_HZ if last_aim != Vector3.ZERO else 0.0, 0.1),
		"vis_in": visible_in_cone, "sep": snappedf(separation, 0.01) if separation >= 0 else null, "v": vision, "a": audio}
	study.store_string(JSON.stringify(row, "", true) + "\n")


func _worst(so_far: String, now: String) -> String:
	if so_far == "known" or now == "known":
		return "known"
	if so_far == "unchecked" or now == "unchecked":
		return "unchecked"
	return "absent"


func _vision(viewer: String, now: Vector3, before: Vector3) -> String:
	var space := get_world_3d().direct_space_state
	var eye := _eye(viewer)
	for eye_offset in [Vector3.ZERO, Vector3(0.3, 0, 0), Vector3(-0.3, 0, 0), Vector3(0, 0.2, 0), Vector3(0, -0.2, 0)]:
		for base in [now, before]:
			for point in _body_points(base):
				perf.rays += 1
				var query := PhysicsRayQueryParameters3D.create(eye + eye_offset, point, Arena.WORLD_LAYER)
				if space.intersect_ray(query).is_empty():
					return "known"
	return "absent"


func _body_points(base: Vector3) -> Array:
	var radius := Arena.BODY_RADIUS + VISION_MARGIN
	var out := [base + Vector3(0, Arena.BODY_HEIGHT + VISION_MARGIN, 0)]
	for height in [0.1, 0.9, 1.7]:
		out.append(base + Vector3(0, height, 0))
		for direction in [Vector3(radius, 0, 0), Vector3(-radius, 0, 0), Vector3(0, 0, radius), Vector3(0, 0, -radius)]:
			out.append(base + Vector3(0, height, 0) + direction)
	return out


func _audio(listener: String, source: String) -> String:
	var here := _position(listener)
	for entry in sounds:
		if entry[1] == source and tick - int(entry[0]) <= SOUND_MEMORY_TICKS and here.distance_to(entry[2]) <= HEARING_RADIUS:
			return "known"
	return "absent"


func _in_cone(eye: Vector3, aim: Vector3, target: Vector3) -> bool:
	var to := target - eye
	var limit := CONE_DEG + rad_to_deg(atan(Arena.BODY_RADIUS / maxf(to.length(), 0.01)))
	return rad_to_deg(aim.angle_to(to)) <= limit


func _aimed_enemy(name: String) -> Dictionary:
	var eye := _eye(name)
	var aim := Arena.aim_direction(players[name].yaw, players[name].pitch)
	var best := {}
	var best_angle := 1e9
	for other in order:
		if other == name:
			continue
		var chest := _position(other) + Vector3(0, Arena.CHEST_HEIGHT, 0)
		if _in_cone(eye, aim, chest):
			var angle := rad_to_deg(aim.angle_to(chest - eye))
			if angle < best_angle:
				best_angle = angle
				var trail: Array = players[other].track
				var before: Vector3 = trail[max(0, trail.size() - 1 - int(Arena.INTERP_DELAY_MS / Arena.TICK_MS))][1]
				best = {"enemy_id": other, "vision_state": _vision(name, _position(other), before), "audio_state": _audio(name, other)}
	return best


func _fire(name: String) -> void:
	var state: Dictionary = players[name]
	if not state.cmd.fire or tick - int(state.last_fire) < FIRE_COOLDOWN_TICKS:
		return
	state.last_fire = tick
	state.shots += 1
	var eye := _eye(name)
	var aim := Arena.aim_direction(state.yaw, state.pitch)
	var hit := get_world_3d().direct_space_state.intersect_ray(
		PhysicsRayQueryParameters3D.create(eye, eye + aim * 100.0, Arena.WORLD_LAYER | Arena.PLAYER_LAYER, [state.body.get_rid()]))
	var victim := ""
	for other in order:
		if not hit.is_empty() and hit.collider == players[other].body:
			victim = other
	var fields := {"event_type": "shot", "weapon_class": "rifle", "weapon_id": "pilot_rifle", "hit": victim != ""}
	if victim != "":
		var height: float = hit.position.y - _position(victim).y
		fields["hitbox"] = "head" if height > 1.5 else ("upper_torso" if height > 1.0 else ("lower_torso" if height > 0.6 else "limbs"))
		fields["distance_m"] = snappedf(eye.distance_to(hit.position), 0.01)
		fields["through_geometry"] = false
		state.hits += 1
		state.damage += DAMAGE
		players[victim].health -= DAMAGE
		if players[victim].health <= 0:
			state.kills += 1
			players[victim].deaths += 1
			players[victim].health = 100
			players[victim].body.global_position = players[victim].spawn
	_sound("gunshot", _position(name), name)
	_emit(name, fields)


func _footsteps(name: String) -> void:
	var body: CharacterBody3D = players[name].body
	if Vector2(body.velocity.x, body.velocity.z).length() > 2.0 and tick - int(players[name].last_step) >= STEP_EVERY_TICKS:
		players[name].last_step = tick
		_sound("footstep", _position(name), name)


func _sound(kind: String, where: Vector3, source: String) -> void:
	sounds.append([tick, source, where, kind])
	while sounds.size() > 0 and tick - int(sounds[0][0]) > SOUND_MEMORY_TICKS:
		sounds.pop_front()
	var heard_by: String = entity_ids.get(source, "")
	for peer in peers:
		if source != participant and _position(participant).distance_to(where) <= HEARING_RADIUS:
			sound.rpc_id(peer, kind, where, heard_by)


func _movement_event(name: String) -> void:
	var body: CharacterBody3D = players[name].body
	_emit(name, {"event_type": "movement", "speed_mps": snappedf(Vector2(body.velocity.x, body.velocity.z).length(), 0.001),
		"on_ground": body.is_on_floor(), "expected_max_ground_speed_mps": Arena.RUN_SPEED + 1.5})


func _emit(name: String, fields: Dictionary) -> void:
	var line := {"game_id": GAME_ID, "match_id": match_id, "player_id": name, "t_ms": Arena.t_ms(tick), "map_id": "study-arena"}
	line.merge(fields)
	line.merge(_aimed_enemy(name))
	if name == participant and not active.is_empty():
		line.merge(_challenge_fields())
	var text := JSON.stringify(line, "", true) + "\n"
	events.store_string(text)
	perf.event_bytes += text.to_utf8_buffer().size()
	perf.event_lines += 1


func _challenge_fields() -> Dictionary:
	if tick_fields.is_empty():
		var ticks := int(accumulator.get("ticks", 0))
		tick_fields = {"challenge_id": active.plan.challenge_id,
			"challenge_track_ms": snappedf(int(accumulator.get("tracked", 0)) * Arena.TICK_MS, 0.001),
			"challenge_vision_state": accumulator.get("vision", "unchecked") if ticks > 0 else "unchecked",
			"challenge_audio_state": accumulator.get("audio", "unchecked") if ticks > 0 else "unchecked"}
		accumulator = {}
	return tick_fields


func _snapshots() -> void:
	for peer in peers:
		var entities := []
		for name in order:
			if name != participant:
				var position := _position(name)
				entities.append([entity_ids[name], position.x, position.y, position.z, players[name].yaw, players[name].pitch])
		if not active.is_empty():
			entities.append([entity_ids["probe"], active.position.x, active.position.y, active.position.z, active.yaw, 0.0])
		entities.sort_custom(func(a, b): return a[0] < b[0])
		var me := _position(participant)
		var you := [me.x, me.y, me.z, players[participant].yaw, players[participant].pitch, players[participant].health]
		perf.snapshot_bytes += var_to_bytes([tick, you, entities]).size()
		snapshot.rpc_id(peer, tick, you, entities)


func _scoreboard() -> void:
	var rows := [["you", players[participant].kills, players[participant].hits]]
	for peer in peers:
		scoreboard.rpc_id(peer, rows)


func _log(entry: Dictionary) -> void:
	entry["server_ms"] = Time.get_ticks_msec()
	log_file.store_string(JSON.stringify(entry, "", true) + "\n")
	log_file.flush()


func _finish(why: String) -> void:
	finished = true
	if not active.is_empty():
		_end("session over")
	_log({"kind": "session_end", "why": why, "ticks": tick, "t_ms": Arena.t_ms(tick), "kills": players[participant].kills if players.has(participant) else 0,
		"entities": {"probe": entity_ids.get("probe", "")}})
	perf["memory_static_peak_bytes"] = OS.get_static_memory_peak_usage()
	perf["match_ms"] = Arena.t_ms(tick)
	var perf_path: String = options.get("perf", "")
	if perf_path != "":
		var out := FileAccess.open(perf_path, FileAccess.WRITE)
		out.store_string(JSON.stringify(perf, "", true) + "\n")
		out.close()
	events.close()
	if study != null:
		study.close()
	for peer in peers:
		match_over.rpc_id(peer)
	await get_tree().create_timer(0.5).timeout
	log_file.close()
	get_tree().quit(0)
