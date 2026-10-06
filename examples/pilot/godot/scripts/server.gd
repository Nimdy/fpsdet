extends Node3D
## The pilot's authoritative dedicated server.
##
## It simulates every player from the commands clients send, decides every hit with its own trace,
## runs the challenge from the plan and the secret, answers every knowledge question with its own
## queries, and writes the NDJSON fpsdet reads. A client never reports what it saw, heard or tracked.
##
## Options (after "--"): --port --match --scenario --match-ms --plan --secret-file --events --log
## --private --perf --deterministic --no-challenge --ab-out --wait-ms

const Arena = preload("res://scripts/arena.gd")
const Recipe = preload("res://scripts/recipe.gd")

const GAME_ID := "fpsdet-godot-pilot"
const SUBJECT := "pilot-subject"
const ENEMY := "pilot-enemy"
const PILOT_VERSION := 1
const CONE_DEG := 2.0  # the aim cone's half-angle, before the body's own angular radius is added
const HEARING_RADIUS := 30.0
const SOUND_MEMORY_TICKS := 30  # a sound counts as heard for 500 ms
const STEP_EVERY_TICKS := 24  # a footstep every 400 ms while moving faster than 2 m/s
const FIRE_COOLDOWN_TICKS := 6
const RESPONDER_LAG_TICKS := 9  # the stand-in reader aims where the probe was 150 ms ago
const VISION_MARGIN := 0.25
const DAMAGE := 25
# Scenarios where a server-side responder aims the subject at the probe: the behaviour of software that
# reads the body it was sent. A controlled stand-in, never a client program.
const RESPONDER := ["exposed_vision", "exposed_audio", "missing_audio_channel", "contradictory_channels", "illicit_follower"]

var options := {}
var scenario := "honest"
var match_id := "pilot-local"
var match_ms := 40000
var deterministic := false
var tick := 0
var started := false
var wait_until_ms := 0
var finished := false

var players := {}  # name -> state
var order := [SUBJECT, ENEMY]
var peers := {}  # peer id -> name
var entity_ids := {}  # SUBJECT, ENEMY, "probe" -> the public entity id clients see
var events: FileAccess
var log_file: FileAccess
var private_dir := ""
var rng := RandomNumberGenerator.new()
var noise := RandomNumberGenerator.new()

var challenges := []  # [{plan, parameters, ran}] for the subject, accepted from the plan
var active := {}  # the running challenge, or empty
var probe_history := []  # [tick, position] while the probe runs
var sounds := []  # [tick, source, position, kind]
var accumulator := {}  # challenge fields since the last event that named the challenge
var tick_fields := {}  # the challenge fields of this tick, once computed
var emissions_in_window := 0

var perf := {"ticks": 0, "tick_us": 0, "tick_us_max": 0, "challenge_us": 0, "knowledge_us": 0, "telemetry_us": 0, "rays": 0,
	"event_bytes": 0, "event_lines": 0, "snapshot_bytes": {}, "probe_ticks": 0, "tracked_ticks": 0, "verdicts": {"vision": {}, "audio": {}}}
var ab_hash := HashingContext.new()
var ab_log := []


func _ready() -> void:
	scenario = options.get("scenario", scenario)
	match_id = options.get("match", match_id)
	match_ms = int(options.get("match-ms", str(match_ms)))
	deterministic = options.get("deterministic", "false") == "true"
	private_dir = options.get("private", "")
	rng.randomize()
	noise.seed = 20261006  # the responder's aim noise: fixed, so a scenario's behaviour is the same each run
	Arena.build(self, false)
	events = FileAccess.open(options.get("events", "user://events.ndjson"), FileAccess.WRITE)
	log_file = FileAccess.open(options.get("log", "user://server.log"), FileAccess.WRITE)
	ab_hash.start(HashingContext.HASH_SHA256)
	for name in order:
		players[name] = _spawn(name)
	# Public entity ids, drawn at random: nothing in an id says whether it is a player or the probe.
	var used := {}
	for key in [SUBJECT, ENEMY, "probe"]:
		var id := ""
		while id == "" or used.has(id):
			id = "e%d" % rng.randi_range(100, 999)
		used[id] = true
		entity_ids[key] = id
	_log({"kind": "start", "engine": Engine.get_version_info().string, "pilot_version": PILOT_VERSION, "scenario": scenario,
		"match_id": match_id, "match_ms": match_ms, "deterministic": deterministic, "tick_hz": Arena.TICK_HZ})
	_load_plan()
	if deterministic:
		started = true
		return
	var peer := ENetMultiplayerPeer.new()
	peer.set_bind_ip("127.0.0.1")  # local only: the pilot never listens beyond this machine
	var error := peer.create_server(int(options.get("port", "24680")), 4)
	if error != OK:
		_log({"kind": "error", "detail": "cannot listen", "code": error})
		get_tree().quit(3)
		return
	multiplayer.multiplayer_peer = peer
	multiplayer.peer_disconnected.connect(func(id): peers.erase(id))
	wait_until_ms = Time.get_ticks_msec() + int(options.get("wait-ms", "20000"))


# The plan and the secret. Only challenges this secret made, for this game, match and subject, ever run.


func _load_plan() -> void:
	if options.get("no-challenge", "false") == "true":
		_log({"kind": "plan", "status": "none", "detail": "run without a challenge (control run)"})
		return
	var path: String = options.get("plan", "")
	if path == "" or not FileAccess.file_exists(path):
		_log({"kind": "plan", "status": "missing", "detail": "no challenge plan: no challenge runs in this match"})
		return
	var plan = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(plan) != TYPE_DICTIONARY or plan.get("format") != Recipe.PLAN_FORMAT:
		_log({"kind": "plan", "status": "refused", "detail": "not a challenge plan file"})
		return
	if plan.get("game_id") != GAME_ID or plan.get("match_id") != match_id:
		_log({"kind": "plan", "status": "refused", "detail": "the plan is for game %s match %s, this is %s %s: no challenge runs" % [plan.get("game_id"), plan.get("match_id"), GAME_ID, match_id]})
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
		if record.subject_id != SUBJECT:
			refused.append({"challenge_id": record.challenge_id, "detail": "planned for a player not in this match"})
			continue
		if record.challenge_type != "occluded_motion_replay":
			refused.append({"challenge_id": record.challenge_id, "detail": "a challenge type this server cannot run"})
			continue
		if record.end_ms > match_ms:
			refused.append({"challenge_id": record.challenge_id, "detail": "its window ends after the match"})
			continue
		var realized := Recipe.realize(secret, record)
		if realized[1] != "":
			refused.append({"challenge_id": record.challenge_id, "detail": realized[1]})
			continue
		challenges.append({"plan": record, "parameters": realized[0], "ran": false})
		accepted.append({"challenge_id": record.challenge_id, "version": record.version, "start_ms": record.start_ms, "end_ms": record.end_ms})
	secret = PackedByteArray()  # the realizations are derived; the key is not kept
	_log({"kind": "plan", "status": "loaded", "accepted": accepted, "refused": refused,
		"file_sha256": Recipe.sha256(FileAccess.get_file_as_bytes(path)).hex_encode()})


# Players.


func _spawn(name: String) -> Dictionary:
	var body := CharacterBody3D.new()
	body.collision_layer = Arena.PLAYER_LAYER
	body.collision_mask = Arena.WORLD_LAYER  # players never block each other, and nothing else is a player
	var shape := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = Arena.BODY_RADIUS
	capsule.height = Arena.BODY_HEIGHT
	shape.shape = capsule
	shape.position = Vector3(0, Arena.BODY_HEIGHT / 2, 0)
	body.add_child(shape)
	var spawn: Vector3 = Arena.SUBJECT_SPAWN if name == SUBJECT else Arena.ENEMY_SPAWN
	if name == ENEMY and scenario in ["ab_with", "ab_without"]:
		spawn = Vector3(-6, 0, -6)  # the A/B runs walk the enemy straight through the probe's zone
	body.position = spawn
	add_child(body)
	return {"name": name, "body": body, "spawn": spawn, "yaw": 180.0 if name == SUBJECT else 0.0, "pitch": 0.0,
		"cmd": {"move": Vector2.ZERO, "yaw": 180.0, "pitch": 0.0, "fire": false}, "health": 100, "kills": 0, "deaths": 0,
		"shots": 0, "hits": 0, "damage": 0, "last_fire": -1000, "last_step": -1000, "track": [], "patrol": 0}


func _position(name: String) -> Vector3:
	return players[name].body.global_position


# RPCs. The same names and annotations are on the client (client.gd).


@rpc("any_peer", "reliable")
func hello(player_name: String) -> void:
	var peer := multiplayer.get_remote_sender_id()
	if players.has(player_name) and not peers.values().has(player_name):
		peers[peer] = player_name
		_log({"kind": "joined", "player": player_name})


@rpc("any_peer", "unreliable_ordered")
func input_cmd(_seq: int, move: Vector2, yaw: float, pitch: float, fire: bool) -> void:
	var name = peers.get(multiplayer.get_remote_sender_id())
	if name == null:
		return
	# A command, and nothing else: where to move, where to look, whether to fire. Never what was seen.
	players[name].cmd = {"move": move.limit_length(1.0), "yaw": wrapf(yaw, -180.0, 180.0), "pitch": clampf(pitch, -89.0, 89.0), "fire": fire}


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


# The tick.


func _physics_process(delta: float) -> void:
	if finished:
		return
	if not started:
		if peers.size() >= 2 or Time.get_ticks_msec() > wait_until_ms:
			started = true
			_log({"kind": "match_start", "players": peers.values()})
		return
	tick += 1
	tick_fields = {}
	var begin := Time.get_ticks_usec()
	_steer()
	for name in order:
		_move(name, delta)
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
	if not deterministic and tick % Arena.SNAPSHOT_EVERY_TICKS == 0:
		_snapshots()
	if not deterministic and tick % Arena.TICK_HZ == 0:
		_scoreboard()
	_ab_record()
	var spent := Time.get_ticks_usec() - begin
	perf.ticks += 1
	perf.tick_us += spent
	perf.tick_us_max = max(perf.tick_us_max, spent)
	if Arena.t_ms(tick) >= match_ms:
		_finish()


## Where each player looks and moves this tick: a client's last command, or a server-side controller.
func _steer() -> void:
	var subject: Dictionary = players[SUBJECT]
	var enemy: Dictionary = players[ENEMY]
	# The enemy is a server-side bot in every scenario: it patrols the open side of the wall.
	var goal: Vector3 = Arena.PATROL[enemy.patrol]
	if scenario == "visible_enemy_cover" and not active.is_empty():
		goal = _cover_point()
	elif scenario in ["ab_with", "ab_without"]:
		goal = Vector3(-6 + 12 * absf(fmod(tick / 600.0, 2.0) - 1.0), 0, -6)  # back and forth through the probe's zone
	elif _position(ENEMY).distance_to(goal) < 0.5:
		enemy.patrol = (enemy.patrol + 1) % Arena.PATROL.size()
		goal = Arena.PATROL[enemy.patrol]
	var to_goal := goal - _position(ENEMY)
	to_goal.y = 0
	var speed := 1.0 if scenario == "visible_enemy_cover" else 0.6
	enemy.cmd = {"move": Vector2(to_goal.x, to_goal.z).limit_length(speed) if to_goal.length() > 0.1 else Vector2.ZERO,
		"yaw": Arena.look_angles(_position(ENEMY), _position(SUBJECT)).x, "pitch": 0.0, "fire": false}
	if deterministic:
		# The subject, scripted: aim at the probe's anchor area and fire every half second, in both A/B runs.
		subject.cmd = {"move": Vector2.ZERO, "yaw": Arena.look_angles(_eye(SUBJECT), Vector3(0, 1.3, -5)).x + 6.0 * sin(tick / 40.0),
			"pitch": Arena.look_angles(_eye(SUBJECT), Vector3(0, 1.3, -5)).y, "fire": tick % 30 == 0}
	if scenario in RESPONDER and not active.is_empty() and probe_history.size() > 0:
		# The stand-in for a reader of the body it was sent: aim where the probe was 150 ms ago, with a little noise.
		var seen: Vector3 = probe_history[max(0, probe_history.size() - 1 - RESPONDER_LAG_TICKS)][1]
		var angles := Arena.look_angles(_eye(SUBJECT), seen + Vector3(0, Arena.CHEST_HEIGHT, 0))
		subject.cmd = {"move": Vector2.ZERO, "yaw": angles.x + noise.randf_range(-0.3, 0.3), "pitch": angles.y + noise.randf_range(-0.3, 0.3), "fire": false}


func _cover_point() -> Vector3:
	# On the line from the subject to the probe, in the open 2 m south of the wall.
	var from := _position(SUBJECT)
	var to: Vector3 = active.position
	var t := (from.z - 2.0) / maxf(from.z - to.z, 0.01)
	return Vector3(from.x + (to.x - from.x) * t, 0, 2.0)


func _move(name: String, _delta: float) -> void:
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


func _eye(name: String) -> Vector3:
	return _position(name) + Vector3(0, Arena.EYE_HEIGHT, 0)


# The challenge: the probe follows the realization the secret derived, and nothing else.


func _update_challenge() -> void:
	var now := Arena.t_ms(tick)
	if active.is_empty():
		for entry in challenges:
			var plan: Dictionary = entry.plan
			if not entry.ran and plan.start_ms <= now and now <= plan.end_ms:
				_begin(entry)
				break
		if active.is_empty():
			return
	var plan: Dictionary = active.plan
	if now > plan.end_ms:
		_end("window over")
		return
	var source: Dictionary = players[active.source]
	var then: int = tick - int(active.delay_ticks)
	var position: Vector3 = active.anchor + _turn(_track_at(source, then) - active.base, active.heading)
	var low: Vector2 = Arena.EXPOSED_MIN if scenario == "exposed_vision" else Arena.ZONE_MIN
	var high: Vector2 = Arena.EXPOSED_MAX if scenario == "exposed_vision" else Arena.ZONE_MAX
	position = Vector3(clampf(position.x, low.x, high.x), 0, clampf(position.z, low.y, high.y))
	active.position = position
	active.yaw = wrapf(float(_yaw_at(source, then)) + active.heading, -180.0, 180.0)
	probe_history.append([tick, position])
	active.path.update(var_to_bytes([tick, snappedf(position.x, 0.001), snappedf(position.z, 0.001)]))
	active.ticks += 1
	perf.probe_ticks += 1
	if scenario == "exposed_audio" and tick % STEP_EVERY_TICKS == 0:
		_sound("footstep", position, "probe")


func _begin(entry: Dictionary) -> void:
	var parameters: Dictionary = entry.parameters
	var sources := order.filter(func(name): return name != SUBJECT)
	var source: String = sources[int(parameters.route_pick) % sources.size()]
	var placement := int(parameters.placement_pick) % Arena.PLACEMENTS.size()
	var anchor: Vector3 = Arena.EXPOSED_PLACEMENT if scenario == "exposed_vision" else Arena.PLACEMENTS[placement]
	var delay_ticks := int(round(int(parameters.replay_delay_ms) / Arena.TICK_MS))
	var path := HashingContext.new()
	path.start(HashingContext.HASH_SHA256)
	entry.ran = true
	active = {"entry": entry, "plan": entry.plan, "source": source, "placement": placement, "anchor": anchor,
		"heading": float(parameters.heading_offset_deg), "delay_ticks": delay_ticks,
		"base": _track_at(players[source], tick - delay_ticks), "position": anchor, "yaw": 0.0, "path": path,
		"ticks": 0, "first_ms": Arena.t_ms(tick), "ended": ""}
	accumulator = {}
	emissions_in_window = 0
	_log({"kind": "challenge_start", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick)})


func _end(why: String) -> void:
	var entry: Dictionary = active.entry
	_log({"kind": "challenge_end", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick), "why": why, "ticks": active.ticks})
	if private_dir != "":
		# What ran, for the operator's own check after the match (pilot.py reproduce). Never a public file.
		var record := {"challenge_id": entry.plan.challenge_id, "start_ms": entry.plan.start_ms, "end_ms": entry.plan.end_ms,
			"parameters": entry.parameters, "placement_index": active.placement, "source": active.source,
			"first_ms": active.first_ms, "last_ms": Arena.t_ms(tick - 1), "ticks": active.ticks, "ended": why,
			"path_sha256": active.path.finish().hex_encode()}
		var out := FileAccess.open(private_dir.path_join("realization-%s.json" % entry.plan.challenge_id), FileAccess.WRITE)
		out.store_string(JSON.stringify(record, "", true) + "\n")
		out.close()
	active = {}
	probe_history = []


func _track_at(state: Dictionary, at_tick: int) -> Vector3:
	var track: Array = state.track
	for index in range(track.size() - 1, -1, -1):
		if track[index][0] <= at_tick:
			return track[index][1]
	return track[0][1] if track.size() > 0 else state.spawn


func _yaw_at(state: Dictionary, at_tick: int) -> float:
	var track: Array = state.track
	for index in range(track.size() - 1, -1, -1):
		if track[index][0] <= at_tick:
			return track[index][2]
	return 0.0


func _turn(offset: Vector3, degrees: float) -> Vector3:
	return Vector3(offset.x, 0, offset.z).rotated(Vector3.UP, deg_to_rad(degrees))


# Knowledge: the server's own queries, every tick. Vision is line of sight from this client's eye to the
# body, against the world's geometry; audio is a sound from that body this client was in range of.


func _observe() -> void:
	var subject := _eye(SUBJECT)
	var aim := Arena.aim_direction(players[SUBJECT].yaw, players[SUBJECT].pitch)
	if active.is_empty():
		return
	var before: Vector3 = probe_history[max(0, probe_history.size() - 1 - int(Arena.INTERP_DELAY_MS / Arena.TICK_MS))][1]
	var vision := _vision(SUBJECT, active.position, before)
	var audio := "unchecked" if scenario == "missing_audio_channel" else _audio(SUBJECT, "probe")
	perf.verdicts.vision[vision] = perf.verdicts.vision.get(vision, 0) + 1
	perf.verdicts.audio[audio] = perf.verdicts.audio.get(audio, 0) + 1
	if (vision == "known" or audio == "known") and scenario not in ["exposed_vision", "exposed_audio"]:
		# The type's rule: end a challenge the client could perceive. The exposed scenarios switch it off on
		# purpose, so that only the per-moment verdict stands between a visible body and evidence.
		_end("perceivable")
		return
	var tracked := _in_cone(subject, aim, active.position + Vector3(0, Arena.CHEST_HEIGHT, 0))
	accumulator["ticks"] = int(accumulator.get("ticks", 0)) + 1
	accumulator["tracked"] = int(accumulator.get("tracked", 0)) + (1 if tracked else 0)
	perf.tracked_ticks += 1 if tracked else 0
	accumulator["vision"] = _worst(accumulator.get("vision", "absent"), vision)
	accumulator["audio"] = _worst(accumulator.get("audio", "absent"), audio)


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


## The real enemy this player's aim cone holds, nearest the crosshair, with the server's verdict on it.
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


# Fire, damage and sound. The probe has no collider, so no trace can ever touch it.


func _fire(name: String) -> void:
	var state: Dictionary = players[name]
	if not state.cmd.fire or tick - int(state.last_fire) < FIRE_COOLDOWN_TICKS:
		return
	state.last_fire = tick
	state.shots += 1
	var eye := _eye(name)
	var aim := Arena.aim_direction(state.yaw, state.pitch)
	var query := PhysicsRayQueryParameters3D.create(eye, eye + aim * 100.0, Arena.WORLD_LAYER | Arena.PLAYER_LAYER, [state.body.get_rid()])
	var hit := get_world_3d().direct_space_state.intersect_ray(query)
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
	if deterministic:
		return
	var heard_by: String = entity_ids.get(source, "")
	for peer in peers:
		var listener: String = peers[peer]
		if listener != source and _position(listener).distance_to(where) <= HEARING_RADIUS:
			sound.rpc_id(peer, kind, where, heard_by)


# Telemetry: the NDJSON fpsdet reads, written when the server accepts each sample, on the server clock.


func _movement_event(name: String) -> void:
	var body: CharacterBody3D = players[name].body
	_emit(name, {"event_type": "movement", "speed_mps": snappedf(Vector2(body.velocity.x, body.velocity.z).length(), 0.001),
		"on_ground": body.is_on_floor(), "expected_max_ground_speed_mps": Arena.RUN_SPEED + 0.5})


func _emit(name: String, fields: Dictionary) -> void:
	var line := {"game_id": GAME_ID, "match_id": match_id, "player_id": name, "t_ms": Arena.t_ms(tick), "map_id": "pilot-arena"}
	line.merge(fields)
	line.merge(_aimed_enemy(name))
	if name == SUBJECT and not active.is_empty():
		line.merge(_challenge_fields())
	_write(line)
	if scenario == "contradictory_channels" and name == SUBJECT and line.has("challenge_id") and fields.event_type == "movement":
		emissions_in_window += 1
		if emissions_in_window == 5:
			# The deliberately broken emitter: a second line at the same moment, contradicting the first.
			var torn := line.duplicate()
			torn["challenge_vision_state"] = "known" if line["challenge_vision_state"] != "known" else "absent"
			_write(torn)


## The challenge's fields for this tick: once per tick, however many events this player has at it.
func _challenge_fields() -> Dictionary:
	if tick_fields.is_empty():
		var ticks := int(accumulator.get("ticks", 0))
		tick_fields = {"challenge_id": active.plan.challenge_id,
			"challenge_track_ms": snappedf(int(accumulator.get("tracked", 0)) * Arena.TICK_MS, 0.001),
			"challenge_vision_state": accumulator.get("vision", "unchecked") if ticks > 0 else "unchecked",
			"challenge_audio_state": accumulator.get("audio", "unchecked") if ticks > 0 else "unchecked"}
		accumulator = {}
	return tick_fields


func _write(line: Dictionary) -> void:
	var text := JSON.stringify(line, "", true) + "\n"
	events.store_string(text)
	perf.event_bytes += text.to_utf8_buffer().size()
	perf.event_lines += 1


# Replication: every client gets every player, the subject's client also gets the probe, all in one shape.


func _snapshots() -> void:
	for peer in peers:
		var viewer: String = peers[peer]
		var entities := []
		for name in order:
			if name != viewer:
				var position := _position(name)
				entities.append([entity_ids[name], position.x, position.y, position.z, players[name].yaw, players[name].pitch])
		if viewer == SUBJECT and not active.is_empty():
			entities.append([entity_ids["probe"], active.position.x, active.position.y, active.position.z, active.yaw, 0.0])
		entities.sort_custom(func(a, b): return a[0] < b[0])
		var me := _position(viewer)
		var you := [me.x, me.y, me.z, players[viewer].yaw, players[viewer].pitch, players[viewer].health]
		perf.snapshot_bytes[viewer] = int(perf.snapshot_bytes.get(viewer, 0)) + var_to_bytes([tick, you, entities]).size()
		snapshot.rpc_id(peer, tick, you, entities)


func _scoreboard() -> void:
	var rows := []
	for name in order:
		rows.append([name, players[name].kills, players[name].deaths, players[name].hits])
	for peer in peers:
		scoreboard.rpc_id(peer, rows)


# The non-interference runs: a digest of everything gameplay decides, every tick.


func _ab_record() -> void:
	if scenario not in ["ab_with", "ab_without"]:
		return
	var row := [tick]
	for name in order:
		var position := _position(name)
		row.append_array([snappedf(position.x, 0.0001), snappedf(position.y, 0.0001), snappedf(position.z, 0.0001), players[name].health,
			players[name].shots, players[name].hits, players[name].damage, players[name].kills])
	ab_hash.update(var_to_bytes(row))
	if tick % 600 == 0:
		ab_log.append(row)


func _log(entry: Dictionary) -> void:
	entry["server_ms"] = Time.get_ticks_msec()
	log_file.store_string(JSON.stringify(entry, "", true) + "\n")
	log_file.flush()


func _finish() -> void:
	finished = true
	if not active.is_empty():
		_end("match over")
	var scores := {}
	for name in order:
		scores[name] = {"kills": players[name].kills, "deaths": players[name].deaths, "shots": players[name].shots, "hits": players[name].hits, "damage": players[name].damage}
	_log({"kind": "match_end", "ticks": tick, "t_ms": Arena.t_ms(tick), "scoreboard": scores,
		"entities": {"subject": entity_ids[SUBJECT], "enemy": entity_ids[ENEMY], "probe": entity_ids["probe"]}})
	perf["memory_static_bytes"] = OS.get_static_memory_usage()
	perf["memory_static_peak_bytes"] = OS.get_static_memory_peak_usage()
	perf["match_ms"] = Arena.t_ms(tick)
	var perf_path: String = options.get("perf", "")
	if perf_path != "":
		var out := FileAccess.open(perf_path, FileAccess.WRITE)
		out.store_string(JSON.stringify(perf, "", true) + "\n")
		out.close()
	var ab_path: String = options.get("ab-out", "")
	if ab_path != "":
		var out := FileAccess.open(ab_path, FileAccess.WRITE)
		out.store_string(JSON.stringify({"scenario": scenario, "ticks": tick, "digest": ab_hash.finish().hex_encode(), "samples": ab_log, "scoreboard": scores}, "", true) + "\n")
		out.close()
	events.close()
	if not deterministic:
		for peer in peers:
			match_over.rpc_id(peer)
	await get_tree().create_timer(0.5).timeout
	log_file.close()
	get_tree().quit(0)
