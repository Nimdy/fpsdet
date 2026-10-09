extends Node3D
## The Arena's authoritative dedicated server.
##
## It simulates every player from the commands clients send, decides every hit with its own trace, applies
## every kick, enforces the fire cycle, drives the bots, runs each scenario's fault or stand-in, runs the
## challenge from the plan and the secret, answers every knowledge question with its own queries, and
## writes the NDJSON fpsdet reads. A client never reports what it saw, heard or tracked.
##
## It also feeds one operator (loopback, token-gated) with what it knows: true positions, the probe, the
## per-tick verdicts, the player's command beside what was accepted. That feed is for the security view and
## never reaches a player's client.
##
## The knowledge queries (_vision, _body_points, _audio, _in_cone, _worst) and the challenge fields are the
## P12 pilot's, byte for byte (tests/test_fps_arena.py compares them with examples/pilot/godot/scripts/server.gd).
##
## Options (after "--"): --run --scenarios --port --bind --scenario --match --once --deterministic --wait-ms
## --secret-file --operator-token-file --log

const Arena = preload("res://scripts/arena_map.gd")
const Recipe = preload("res://scripts/recipe.gd")
const Weapon = preload("res://scripts/weapon.gd")
const Scenario = preload("res://scripts/scenario.gd")
const Bots = preload("res://scripts/bots.gd")
const Autopilot = preload("res://scripts/autopilot.gd")

const GAME_ID := "fpsdet-arena"
const SUBJECT := "arena-player"
const ARENA_VERSION := 1
const CONE_DEG := 2.0  # the aim cone's half-angle, before the body's own angular radius is added
const HEARING_RADIUS := 30.0
const SOUND_MEMORY_TICKS := 30  # a sound counts as heard for 500 ms
const STEP_EVERY_TICKS := 24  # a footstep every 400 ms while moving faster than 2 m/s
const RESPONDER_LAG_TICKS := 9  # a stand-in reader aims where the body was 150 ms ago
const VISION_MARGIN := 0.25
const LOADOUT_KG := 12.0
const FEED_EVERY_TICKS := 3  # the operator feed and the timeline row, at 20 Hz
const SNAPSHOT_HISTORY := 40

var options := {}
var run_dir := ""
var public_dir := ""
var private_dir := ""
var operator_dir := ""
var scenarios := {}
var scenario := {}
var match_id := ""
var match_ms := 0
var in_match := false
var once := false
var deterministic := false
var tick := 0
var started := false
var finished := false
var wait_until_ms := 0
var match_counter := {}

var players := {}  # name -> state (the subject and the bots)
var order := []
var bots := {}  # bot name -> Bots.make()
var peers := {}  # peer id -> player name
var operators := {}  # peer id -> true
var operator_token := ""
var entity_ids := {}  # SUBJECT, bot names, "probe" -> the public entity id clients see
var events: FileAccess
var log_file: FileAccess
var timeline: FileAccess
var rng := RandomNumberGenerator.new()
var noise := RandomNumberGenerator.new()
var kick_rng := RandomNumberGenerator.new()
var autopilot_memory := {}

var plans := {}  # match id -> [{plan, parameters, ran}], derived at startup; the secret is then dropped
var plan_files := {}  # match id -> the plan file path
var plan_status := {}
var challenges := []  # this match's challenges
var active := {}  # the running challenge, or empty
var probe_history := []  # [tick, position] while the probe runs
var sounds := []  # [tick, source, position, kind]
var accumulator := {}  # challenge fields since the last event that named the challenge
var tick_fields := {}  # the challenge fields of this tick, once computed
var sent := {}  # entity -> [[server_ms, position]]: the snapshots sent to the subject's client (the wire)
var knowledge := {}  # enemy -> {vision, audio, since_ms, in_cone, chest} for the subject, this tick
var probe_verdict := {}  # this tick's verdict on the probe for the subject
var last_event := ""
var feed_bytes := 0

var perf := {}


func _ready() -> void:
	run_dir = options.get("run", "")
	once = options.get("once", "false") == "true"
	deterministic = options.get("deterministic", "false") == "true"
	public_dir = run_dir.path_join("public")
	private_dir = run_dir.path_join("private")
	operator_dir = run_dir.path_join("operator")
	for folder in [public_dir, private_dir, operator_dir, public_dir.path_join("matches")]:
		DirAccess.make_dir_recursive_absolute(folder)
	log_file = FileAccess.open(options.get("log", public_dir.path_join("server.log")), FileAccess.WRITE)
	scenarios = Scenario.load_all(options.get("scenarios", ""))
	if scenarios.is_empty():
		_log({"kind": "error", "detail": "no scenarios loaded; pass --scenarios=<folder>"})
		get_tree().quit(2)
		return
	rng.randomize()
	noise.seed = 20261009  # the stand-ins' aim noise: fixed, so a scenario's behaviour is the same each run
	Arena.build(self, false)
	_reset_perf()
	_log({"kind": "start", "engine": Engine.get_version_info().string, "arena_version": ARENA_VERSION, "deterministic": deterministic,
		"once": once, "tick_hz": Arena.TICK_HZ, "scenarios": scenarios.keys()})
	_load_plans()
	var token_path: String = options.get("operator-token-file", "")
	if token_path != "" and FileAccess.file_exists(token_path):
		operator_token = FileAccess.get_file_as_string(token_path).strip_edges()
	if deterministic:
		started = true
	else:
		var bind: String = options.get("bind", "127.0.0.1")
		if not _private(bind):
			_log({"kind": "error", "detail": "the server binds only to loopback or a private LAN address; refusing a public one"})
			get_tree().quit(3)
			return
		var peer := ENetMultiplayerPeer.new()
		peer.set_bind_ip(bind)  # local only by default: the arena never listens beyond this machine unless told a LAN address
		if peer.create_server(int(options.get("port", "24760")), 4) != OK:
			_log({"kind": "error", "detail": "cannot listen"})
			get_tree().quit(3)
			return
		multiplayer.multiplayer_peer = peer
		multiplayer.peer_disconnected.connect(_on_peer_left)
		wait_until_ms = Time.get_ticks_msec() + int(options.get("wait-ms", "30000"))
	var first: String = options.get("scenario", Scenario.LOBBY)
	_start_match(first, options.get("match", ""))


## Loopback or RFC 1918 only. The address itself is never written anywhere.
func _private(address: String) -> bool:
	var parts := address.split(".")
	if parts.size() != 4:
		return false
	var a := int(parts[0])
	var b := int(parts[1])
	return a == 127 or a == 10 or (a == 172 and b >= 16 and b <= 31) or (a == 192 and b == 168)


func _reset_perf() -> void:
	perf = {"ticks": 0, "tick_us": 0, "tick_us_max": 0, "challenge_us": 0, "knowledge_us": 0, "telemetry_us": 0, "feed_us": 0, "rays": 0,
		"event_bytes": 0, "event_lines": 0, "snapshot_bytes": 0, "feed_bytes": 0, "probe_ticks": 0, "tracked_ticks": 0,
		"verdicts": {"vision": {}, "audio": {}}}


# Plans and the secret. Every plan in public/plans is derived at startup, keyed by its match id. Only challenges
# this secret made, for this game and the subject, ever run; the key is dropped as soon as they are derived.


func _load_plans() -> void:
	var folder := public_dir.path_join("plans")
	var dir := DirAccess.open(folder)
	if dir == null:
		_log({"kind": "plans", "status": "none", "detail": "no plans folder: no challenge runs in this session"})
		return
	var loaded := Recipe.load_secret(options.get("secret-file", ""))
	var secret: PackedByteArray = loaded[0]
	var files := dir.get_files()
	files.sort()
	for name in files:
		if not name.ends_with(".json"):
			continue
		var path := folder.path_join(name)
		var plan = JSON.parse_string(FileAccess.get_file_as_string(path))
		if typeof(plan) != TYPE_DICTIONARY or plan.get("format") != Recipe.PLAN_FORMAT or plan.get("game_id") != GAME_ID:
			plan_status[name] = {"status": "refused", "detail": "not a challenge plan for this game"}
			continue
		if secret.is_empty():
			plan_status[name] = {"status": "refused", "detail": loaded[1] + ": no challenge runs"}
			continue
		var accepted := []
		var refused := []
		var derived := []
		for raw in plan.get("challenges", []):
			var record := Recipe.record(raw)
			if record.subject_id != SUBJECT:
				refused.append({"challenge_id": record.challenge_id, "detail": "planned for a player not in this arena"})
				continue
			if record.challenge_type != "occluded_motion_replay" or int(record.version) != 2:
				refused.append({"challenge_id": record.challenge_id, "detail": "a challenge type or version this server cannot run"})
				continue
			var realized := Recipe.realize(secret, record)
			if realized[1] != "":
				refused.append({"challenge_id": record.challenge_id, "detail": realized[1]})
				continue
			derived.append({"plan": record, "parameters": realized[0], "ran": false})
			accepted.append({"challenge_id": record.challenge_id, "version": record.version, "start_ms": record.start_ms, "end_ms": record.end_ms})
		plans[String(plan.match_id)] = derived
		plan_files[String(plan.match_id)] = path
		plan_status[name] = {"status": "loaded", "match_id": plan.match_id, "accepted": accepted, "refused": refused}
	secret = PackedByteArray()  # the realizations are derived; the key is not kept
	_log({"kind": "plans", "status": "loaded" if not plans.is_empty() else "none", "files": plan_status})


## The next unused planned match id for a scenario, or "" when the scenario needs a plan and none is left.
func _next_match_id(scenario_id: String) -> String:
	var spec: Dictionary = scenarios.get(scenario_id, {})
	var counter := int(match_counter.get(scenario_id, 0)) + 1
	if spec.has("challenge"):
		var ids := plans.keys()
		ids.sort()
		for id in ids:
			if id.begins_with("arena-" + scenario_id + "-") and not match_counter.has(id):
				match_counter[id] = 1
				return id
		return ""
	match_counter[scenario_id] = counter
	return "arena-%s-%d" % [scenario_id, counter]


# Matches. A match is one scenario run: its own events file, its own match id, its own clock from tick 0.


func _start_match(scenario_id: String, wanted_match: String) -> void:
	if not scenarios.has(scenario_id) and scenario_id != Scenario.LOBBY:
		_log({"kind": "error", "detail": "unknown scenario " + scenario_id})
		scenario_id = Scenario.LOBBY
	scenario = Scenario.lobby() if scenario_id == Scenario.LOBBY else scenarios[scenario_id]
	in_match = scenario_id != Scenario.LOBBY
	if in_match:
		match_id = wanted_match if wanted_match != "" else _next_match_id(scenario_id)
		if match_id == "":
			_log({"kind": "match_refused", "scenario": scenario_id, "detail": "no unused challenge plan is left for this scenario; restart the lab to plan more"})
			scenario = Scenario.lobby()
			in_match = false
	if not in_match:
		match_id = "arena-lobby"
	tick = 0
	finished = false
	match_ms = int(scenario.get("duration_ms", 0))
	_reset_perf()
	kick_rng.seed = hash(match_id)  # the fresh part of every kick: fixed per match, so a capture reproduces
	autopilot_memory = {}
	# Players: the subject, then the scenario's bots, each with a public entity id that says nothing about what it is.
	for name in players.keys():
		players[name].body.queue_free()
	players = {}
	order = [SUBJECT]
	bots = {}
	var subject_spec: Dictionary = scenario.get("subject", {})
	var spawn := Arena.from_v3(subject_spec.get("spawn", [0, 0, 11]))
	players[SUBJECT] = _spawn(SUBJECT, spawn, float(subject_spec.get("yaw", 0.0)))
	for program in scenario.get("bots", []):
		var bot := Bots.make(program)
		order.append(bot.id)
		bots[bot.id] = bot
		players[bot.id] = _spawn(bot.id, bot.path[0] if not bot.path.is_empty() else Vector3(0, 0, -5), 180.0)
	var used := {}
	entity_ids = {}
	for key in order + ["probe"]:
		var id := ""
		while id == "" or used.has(id):
			id = "e%d" % rng.randi_range(100, 999)
		used[id] = true
		entity_ids[key] = id
	sounds = []
	sent = {}
	knowledge = {}
	probe_verdict = {}
	active = {}
	probe_history = []
	accumulator = {}
	tick_fields = {}
	challenges = plans.get(match_id, []) if in_match else []
	for entry in challenges:
		entry.ran = false
	if events != null:
		events.close()
		events = null
	if timeline != null:
		timeline.close()
		timeline = null
	if in_match:
		var folder := public_dir.path_join("matches").path_join(match_id)
		DirAccess.make_dir_recursive_absolute(folder)
		DirAccess.make_dir_recursive_absolute(operator_dir.path_join(match_id))
		events = FileAccess.open(folder.path_join("events.ndjson"), FileAccess.WRITE)
		timeline = FileAccess.open(operator_dir.path_join(match_id).path_join("timeline.ndjson"), FileAccess.WRITE)
		if plan_files.has(match_id):
			var copy := FileAccess.open(folder.path_join("plan.json"), FileAccess.WRITE)
			copy.store_string(FileAccess.get_file_as_string(plan_files[match_id]))
			copy.close()
		_write_json(public_dir.path_join("current.json"), {"match_id": match_id, "scenario": scenario.id, "started": true, "ended": false,
			"duration_ms": match_ms, "challenge": not challenges.is_empty(), "external": scenario.has("external")})
	else:
		_write_json(public_dir.path_join("current.json"), {"match_id": "", "scenario": Scenario.LOBBY, "started": false, "ended": false})
	_log({"kind": "match_start", "match_id": match_id, "scenario": scenario.id, "match_ms": match_ms, "in_match": in_match,
		"challenges": challenges.map(func(entry): return entry.plan.challenge_id), "entities": entity_ids.size()})
	for peer in peers:
		banner.rpc_id(peer, String(scenario.get("title", "Lobby")), String(scenario.get("instructions", "")), match_ms)


func _finish_match(why: String) -> void:
	if not in_match:
		return
	finished = true
	if not active.is_empty():
		_end("match over")
	var scores := {}
	for name in order:
		scores[name] = {"kills": players[name].kills, "deaths": players[name].deaths, "shots": players[name].shots, "hits": players[name].hits}
	perf["memory_static_peak_bytes"] = OS.get_static_memory_peak_usage()
	perf["match_ms"] = Arena.t_ms(tick)
	var record := {"match_id": match_id, "scenario": scenario.id, "ended": true, "why": why, "ticks": tick, "t_ms": Arena.t_ms(tick),
		"scoreboard": scores, "entities": entity_ids, "perf": perf, "challenge": not challenges.is_empty(), "external": scenario.has("external"),
		"audio_query": bool(scenario.get("audio_query", true)), "experimental": bool(scenario.get("experimental", false))}
	_log({"kind": "match_end", "match_id": match_id, "scenario": scenario.id, "why": why, "ticks": tick, "t_ms": Arena.t_ms(tick), "scoreboard": scores})
	if events != null:
		events.close()
		events = null
	if timeline != null:
		timeline.close()
		timeline = null
	_write_json(public_dir.path_join("matches").path_join(match_id).path_join("match.json"), record)
	_write_json(public_dir.path_join("current.json"), {"match_id": match_id, "scenario": scenario.id, "started": true, "ended": true})
	in_match = false
	if once:
		for peer in peers:
			match_over.rpc_id(peer)
		await get_tree().create_timer(0.5).timeout
		log_file.close()
		get_tree().quit(0)
		return
	_start_match(Scenario.LOBBY, "")


# Players.


func _spawn(name: String, at: Vector3, yaw: float) -> Dictionary:
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
	body.position = at
	add_child(body)
	return {"name": name, "body": body, "spawn": at, "spawn_yaw": yaw, "yaw": yaw, "pitch": 0.0, "recoil_offset": 0.0,
		"cmd": {"move": Vector2.ZERO, "yaw": yaw, "pitch": 0.0, "fire": false}, "prev_cmd_pitch": 0.0, "cmd_seq": 0,
		"health": 100, "kills": 0, "deaths": 0, "shots": 0, "accepted": 0, "hits": 0, "damage": 0, "ammo": Weapon.MAGAZINE, "reload_until": -1,
		"last_fire": -1000, "spray_index": -1, "last_step": -1000, "track": [], "hidden_ticks": 0, "last_perceived": {},
		"override": false, "last_kick": 0.0, "last_compensation": 0.0}


func _position(name: String) -> Vector3:
	return players[name].body.global_position


func _eye(name: String) -> Vector3:
	return _position(name) + Vector3(0, Arena.EYE_HEIGHT, 0)


## The server-side view: the client's command plus the kick the server applied and has not yet let settle.
func _aim(name: String) -> Vector3:
	return Arena.aim_direction(players[name].yaw, players[name].pitch + players[name].recoil_offset)


# RPCs. The same names and annotations are on the client (arena_client.gd).


@rpc("any_peer", "reliable")
func hello(player_name: String) -> void:
	var peer := multiplayer.get_remote_sender_id()
	if player_name == SUBJECT and not peers.values().has(player_name):
		peers[peer] = player_name
		_log({"kind": "joined", "player": player_name})
		banner.rpc_id(peer, String(scenario.get("title", "Lobby")), String(scenario.get("instructions", "")), match_ms)


@rpc("any_peer", "unreliable_ordered")
func input_cmd(seq: int, move: Vector2, yaw: float, pitch: float, fire: bool) -> void:
	var name = peers.get(multiplayer.get_remote_sender_id())
	if name == null:
		return
	# A command, and nothing else: where to move, where to look, whether to fire. Never what was seen.
	players[name].cmd = {"move": move.limit_length(1.0), "yaw": wrapf(yaw, -180.0, 180.0), "pitch": clampf(pitch, -89.0, 89.0), "fire": fire}
	players[name].cmd_seq = seq


@rpc("authority", "unreliable_ordered")
func snapshot(_tick: int, _you: Array, _entities: Array) -> void:
	pass


@rpc("authority", "reliable")
func sound(_kind: String, _position: Vector3, _source: String) -> void:
	pass


@rpc("authority", "reliable")
func fired(_ammo: int, _hit: bool) -> void:
	pass


@rpc("authority", "reliable")
func banner(_title: String, _instructions: String, _duration_ms: int) -> void:
	pass


@rpc("authority", "reliable")
func scoreboard(_rows: Array) -> void:
	pass


@rpc("authority", "reliable")
func match_over() -> void:
	pass


func _on_peer_left(id: int) -> void:
	peers.erase(id)
	operators.erase(id)


# The operator link: a second node at a fixed path (Main/Operator) carries the privileged RPCs, so a stock
# client has no method that could receive them. It calls back into the server here.


func operator_join(peer: int, token: String) -> bool:
	if operator_token == "" or token != operator_token:
		_log({"kind": "operator_refused", "detail": "wrong or missing token"})
		return false
	operators[peer] = true
	_log({"kind": "operator_joined"})
	return true


func operator_request(peer: int, what: String, value: String) -> void:
	if not operators.has(peer):
		return
	match what:
		"scenario":
			if value == Scenario.LOBBY or scenarios.has(value):
				_log({"kind": "operator_request", "scenario": value})
				if in_match:
					_finish_match("operator switched scenario")
				if value != Scenario.LOBBY:
					_start_match(value, "")
		"end":
			if in_match:
				_finish_match("operator ended the scenario")
		"audio_query":
			scenario = scenario.duplicate(true)
			scenario["audio_query"] = value == "true"
			_log({"kind": "operator_option", "audio_query": scenario.audio_query})
		"standin":
			scenario = scenario.duplicate(true)
			if value == "true" and scenario.get("standin", {}).is_empty():
				var target := ""
				for name in bots:
					target = name  # the last bot: in the scenarios with a hidden bot, that is the hidden one
				scenario["standin"] = {"kind": "wallhack", "target": target, "from_ms": 0, "to_ms": 1 << 30, "fire_every_ms": 300}
			elif value == "false":
				scenario["standin"] = {}
			_log({"kind": "operator_option", "standin": scenario.get("standin", {})})


# The tick.


func _physics_process(_delta: float) -> void:
	if finished:
		return
	if not started:
		if not peers.is_empty() or Time.get_ticks_msec() > wait_until_ms:
			started = true
			_log({"kind": "server_ready", "client": not peers.is_empty()})
			if peers.is_empty() and once:
				_log({"kind": "error", "detail": "no client joined"})
				_finish_match("no client joined")
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
	if in_match and tick % Arena.EVENT_EVERY_TICKS == 0:
		for name in order:
			_movement_event(name)
	perf.telemetry_us += Time.get_ticks_usec() - mark
	if tick % Arena.SNAPSHOT_EVERY_TICKS == 0:
		_snapshots()
	if tick % FEED_EVERY_TICKS == 0:
		mark = Time.get_ticks_usec()
		_feed()
		perf.feed_us += Time.get_ticks_usec() - mark
	if tick % Arena.TICK_HZ == 0:
		_scoreboard()
	var spent := Time.get_ticks_usec() - begin
	perf.ticks += 1
	perf.tick_us += spent
	perf.tick_us_max = max(perf.tick_us_max, spent)
	if in_match and Arena.t_ms(tick) >= match_ms:
		_finish_match("scenario over")


## Where each player looks and moves this tick: a client's last command, a bot's program, the deterministic
## autopilot, or a scenario's stand-in over the subject's aim.
func _steer() -> void:
	var now := Arena.t_ms(tick)
	var subject: Dictionary = players[SUBJECT]
	for name in bots:
		var bot: Dictionary = bots[name]
		var sees := _vision(name, _position(SUBJECT), _position(SUBJECT)) == "known"
		if sees:
			players[name].last_perceived[SUBJECT] = tick
		players[name].cmd = Bots.steer(bot, _position(name), _eye(name), _eye(SUBJECT), sees, now, noise)
	if deterministic:
		# No client: the honest autopilot the stock client would run, shown only bodies with a clear line from the eye.
		var bodies := []
		for name in bots:
			# What the stock client would draw: the picture, one interpolation delay behind the newest snapshot.
			var shown := _wire_and_picture(name)
			var drawn: Vector3 = shown.picture if not shown.is_empty() else _position(name)
			bodies.append({"id": name, "chest": drawn + Vector3(0, Arena.CHEST_HEIGHT, 0), "visible": _vision(SUBJECT, drawn, drawn) == "known"})
		var view := {"position": _position(SUBJECT), "look": Vector2(float(subject.cmd.yaw), float(subject.cmd.pitch)), "recoil_offset": subject.recoil_offset,
			"bodies": bodies, "elapsed_ms": now}
		var decided := Autopilot.decide(String(scenario.get("subject", {}).get("autopilot", "tracker")), view, autopilot_memory, 1.0 / Arena.TICK_HZ)
		subject.cmd = {"move": decided.move, "yaw": decided.look.x, "pitch": decided.look.y, "fire": decided.fire}
	subject.override = false
	var standin: Dictionary = scenario.get("standin", {})
	if not Scenario.window_open(standin, now):
		return
	var kind := String(standin.get("kind", ""))
	var fire_every := int(standin.get("fire_every_ms", 0))
	var wants_fire := fire_every > 0 and now - Arena.t_ms(int(subject.last_fire)) >= fire_every
	match kind:
		"follower":
			# The stand-in for a reader of the body it was sent: aim where the probe was 150 ms ago, with a little noise.
			if not active.is_empty() and probe_history.size() > 0:
				var seen: Vector3 = probe_history[max(0, probe_history.size() - 1 - RESPONDER_LAG_TICKS)][1]
				_override_aim(subject, seen + Vector3(0, Arena.CHEST_HEIGHT, 0), 0.3, false)
		"wallhack":
			# A reader of hidden positions: aim where the target's body was 150 ms ago, fire on its own clock.
			var target := String(standin.get("target", ""))
			if players.has(target):
				var trail: Array = players[target].track
				var seen: Vector3 = trail[max(0, trail.size() - 1 - RESPONDER_LAG_TICKS)][1] if not trail.is_empty() else _position(target)
				_override_aim(subject, seen + Vector3(0, Arena.CHEST_HEIGHT, 0), 0.3, wants_fire)
		"packet_reader":
			# A reader of the newest snapshot: aim at the wire, the position the server sent last, never the drawn one.
			var target := String(standin.get("target", ""))
			if players.has(target) and sent.has(entity_ids[target]) and not sent[entity_ids[target]].is_empty():
				var wire: Vector3 = sent[entity_ids[target]][-1][1]
				_override_aim(subject, wire + Vector3(0, Arena.CHEST_HEIGHT, 0), 0.05, wants_fire)
		"holder":
			# Ordinary angle holding: the doorway's left frame, still, with a short burst at it now and then.
			_override_aim(subject, Arena.FRAME_EDGE, 0.0, now % 6000 < 300)
		"mirror":
			pass  # applied in _fire, on the shot's own tick, where the kick is drawn
		_:
			pass


func _override_aim(subject: Dictionary, target: Vector3, jitter: float, fire: bool) -> void:
	var angles := Arena.look_angles(_eye(SUBJECT), target)
	# The stand-in commands the view it wants; the kick the server holds is part of what it sees, so it aims under it.
	subject.cmd = {"move": subject.cmd.move, "yaw": angles.x + (noise.randf_range(-jitter, jitter) if jitter > 0 else 0.0),
		"pitch": angles.y - subject.recoil_offset + (noise.randf_range(-jitter, jitter) if jitter > 0 else 0.0), "fire": fire}
	subject.override = true


func _move(name: String) -> void:
	var state: Dictionary = players[name]
	var body: CharacterBody3D = state.body
	state.prev_cmd_pitch = state.pitch
	state.yaw = float(state.cmd.yaw)
	state.pitch = float(state.cmd.pitch)
	var faults: Dictionary = scenario.get("faults", {})
	var multiplier := 1.0
	if name == SUBJECT and Scenario.window_open(faults, Arena.t_ms(tick)):
		multiplier = float(faults.get("speed_multiplier", 1.0))
	# The move command is in the player's own frame: x right, y forward of where they look.
	var move: Vector2 = state.cmd.move
	var forward := Arena.aim_direction(state.yaw, 0.0)
	var right := Vector3(-forward.z, 0, forward.x)
	var velocity := (right * move.x + forward * move.y) * Arena.RUN_SPEED * multiplier
	body.velocity = Vector3(velocity.x, body.velocity.y - 9.8 / Arena.TICK_HZ, velocity.z)
	if body.is_on_floor() and body.velocity.y < 0:
		body.velocity.y = 0
	body.move_and_slide()
	# The kicked view settles back between shots.
	if state.recoil_offset > 0.0:
		state.recoil_offset = maxf(0.0, state.recoil_offset - Weapon.RECOVERY_DEG_PER_S / Arena.TICK_HZ)
	state.track.append([tick, body.global_position, state.yaw])
	if state.track.size() > 25 * Arena.TICK_HZ:
		state.track.pop_front()


# The challenge: the probe follows the realization the secret derived, and nothing else. It lives in the
# sealed chamber; its route is a bot's track, turned by the secret heading, taken from the secret delay back.


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
	position = Vector3(clampf(position.x, Arena.CHAMBER_MIN.x, Arena.CHAMBER_MAX.x), 0, clampf(position.z, Arena.CHAMBER_MIN.y, Arena.CHAMBER_MAX.y))
	active.position = position
	active.yaw = wrapf(float(_yaw_at(source, then)) + active.heading, -180.0, 180.0)
	probe_history.append([tick, position])
	if probe_history.size() > 25 * Arena.TICK_HZ:
		probe_history.pop_front()
	active.ticks += 1
	perf.probe_ticks += 1


func _begin(entry: Dictionary) -> void:
	var parameters: Dictionary = entry.parameters
	var sources := order.filter(func(name): return name != SUBJECT)
	sources.sort()
	var source: String = sources[int(parameters.route_pick) % sources.size()] if not sources.is_empty() else SUBJECT
	var delay_ticks := int(round(int(parameters.replay_delay_ms) / Arena.TICK_MS))
	entry.ran = true
	active = {"entry": entry, "plan": entry.plan, "source": source, "placement": 0, "anchor": Arena.CHAMBER_CENTER,
		"heading": float(parameters.heading_offset_deg), "delay_ticks": delay_ticks,
		"base": _track_at(players[source], tick - delay_ticks), "position": Arena.CHAMBER_CENTER, "yaw": 0.0,
		"ticks": 0, "first_ms": Arena.t_ms(tick), "ended": ""}
	accumulator = {}
	_log({"kind": "challenge_start", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick)})


func _end(why: String) -> void:
	var entry: Dictionary = active.entry
	_log({"kind": "challenge_end", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick), "why": why, "ticks": active.ticks})
	if private_dir != "":
		# What ran, for the operator's own check after the match (arena.py reproduce). Never a public file.
		var record := {"challenge_id": entry.plan.challenge_id, "start_ms": entry.plan.start_ms, "end_ms": entry.plan.end_ms,
			"parameters": entry.parameters, "placement_index": active.placement, "source": active.source,
			"first_ms": active.first_ms, "last_ms": Arena.t_ms(tick - 1), "ticks": active.ticks, "ended": why}
		var out := FileAccess.open(private_dir.path_join("realization-%s.json" % entry.plan.challenge_id), FileAccess.WRITE)
		out.store_string(JSON.stringify(record, "", true) + "\n")
		out.close()
	active = {}
	probe_history = []
	probe_verdict = {}


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


# Knowledge: the server's own queries, every tick, for the subject against every body it could aim at. Vision
# is line of sight from this client's eye to the body, against the world's geometry; audio is a sound from
# that body this client was in range of. Audio can be switched off by a scenario, and then it is unchecked:
# the server says so, and measures no hidden time, instead of claiming absent.


func _observe() -> void:
	var subject := _eye(SUBJECT)
	var aim := _aim(SUBJECT)
	var audio_on := bool(scenario.get("audio_query", true))
	var hidden_this_tick := false
	knowledge = {}
	for name in bots:
		var trail: Array = players[name].track
		var before: Vector3 = trail[max(0, trail.size() - 1 - int(Arena.INTERP_DELAY_MS / Arena.TICK_MS))][1]
		var vision := _vision(SUBJECT, _position(name), before)
		var audio := _audio(SUBJECT, name) if audio_on else "unchecked"
		if vision == "known" or audio == "known":
			players[SUBJECT].last_perceived[name] = tick
		var chest := _position(name) + Vector3(0, Arena.CHEST_HEIGHT, 0)
		var in_cone := _in_cone(subject, aim, chest)
		var perceived = players[SUBJECT].last_perceived.get(name)
		var since_ms = null if perceived == null else Arena.t_ms(tick) - Arena.t_ms(int(perceived))
		knowledge[name] = {"vision": vision, "audio": audio, "since_ms": since_ms, "in_cone": in_cone, "chest": chest}
		if in_cone and vision == "absent" and audio == "absent":
			hidden_this_tick = true
	if audio_on and hidden_this_tick:
		players[SUBJECT].hidden_ticks += 1
	if active.is_empty():
		return
	var before: Vector3 = probe_history[max(0, probe_history.size() - 1 - int(Arena.INTERP_DELAY_MS / Arena.TICK_MS))][1]
	var vision := _vision(SUBJECT, active.position, before)
	var audio := "unchecked" if not audio_on else _audio(SUBJECT, "probe")
	perf.verdicts.vision[vision] = perf.verdicts.vision.get(vision, 0) + 1
	perf.verdicts.audio[audio] = perf.verdicts.audio.get(audio, 0) + 1
	var tracked := _in_cone(subject, aim, active.position + Vector3(0, Arena.CHEST_HEIGHT, 0))
	probe_verdict = {"vision": vision, "audio": audio, "in_cone": tracked}
	if vision == "known" or audio == "known":
		_end("perceivable")  # the type's rule: a body the client could perceive is no longer a challenge
		return
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


## The real enemy this player's aim cone holds, nearest the crosshair, with the server's verdict on it, and
## for the subject the wire and the picture of that enemy as its client was sent and draws them.
func _aimed_enemy(name: String) -> Dictionary:
	var eye := _eye(name)
	var aim := _aim(name)
	var audio_on := bool(scenario.get("audio_query", true))
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
				var vision := _vision(name, _position(other), before)
				var audio := _audio(name, other) if audio_on else "unchecked"
				if vision == "known" or audio == "known":
					players[name].last_perceived[other] = tick
				best = {"enemy_id": other, "vision_state": vision, "audio_state": audio}
				var perceived = players[name].last_perceived.get(other)
				if perceived != null:
					best["since_perceived_ms"] = Arena.t_ms(tick) - Arena.t_ms(int(perceived))
				if name == SUBJECT:
					var shown := _wire_and_picture(other)
					if not shown.is_empty():
						best["wire_error_deg"] = snappedf(rad_to_deg(aim.angle_to(shown.wire + Vector3(0, Arena.CHEST_HEIGHT, 0) - eye)), 0.001)
						best["picture_error_deg"] = snappedf(rad_to_deg(aim.angle_to(shown.picture + Vector3(0, Arena.CHEST_HEIGHT, 0) - eye)), 0.001)
						best["interp_delay_ms"] = Arena.INTERP_DELAY_MS
	return best


## The wire is the newest snapshot the server sent the subject's client for this body; the picture is where
## the stock client draws it, interpolated one delay behind, from the server's own record of what it sent.
func _wire_and_picture(name: String) -> Dictionary:
	var rows: Array = sent.get(entity_ids.get(name, ""), [])
	if rows.size() < 2:
		return {}
	var latest: float = rows[-1][0]
	var at := latest - Arena.INTERP_DELAY_MS
	var picture: Vector3 = rows[0][1]
	for index in range(rows.size() - 1, 0, -1):
		var after: Array = rows[index]
		var before: Array = rows[index - 1]
		if float(before[0]) <= at and at <= float(after[0]):
			var weight := (at - float(before[0])) / maxf(float(after[0]) - float(before[0]), 0.001)
			picture = before[1].lerp(after[1], weight)
			break
		if index == 1 and at > float(after[0]):
			picture = after[1]
	return {"wire": rows[-1][1], "picture": picture}


# Fire, damage and sound. The server enforces the cycle and the magazine, applies the kick, and traces the
# shot itself. The probe has no collider, so no trace can ever touch it.


func _fire(name: String) -> void:
	var state: Dictionary = players[name]
	var now := Arena.t_ms(tick)
	if state.reload_until >= 0:
		if tick < state.reload_until:
			return
		state.reload_until = -1
		state.ammo = Weapon.MAGAZINE
	if not state.cmd.fire:
		return
	var cycle := Weapon.CYCLE_TICKS
	var kick_multiplier := 1.0
	var faults: Dictionary = scenario.get("faults", {})
	if name == SUBJECT and Scenario.window_open(faults, now):
		cycle = int(faults.get("fire_cycle_ticks", cycle))
		kick_multiplier = float(faults.get("recoil_multiplier", 1.0))
	if tick - int(state.last_fire) < cycle:
		return
	if state.ammo <= 0:
		state.reload_until = tick + Weapon.RELOAD_TICKS
		return
	state.spray_index = 0 if tick - int(state.last_fire) > Weapon.SPRAY_RESET_TICKS else int(state.spray_index) + 1
	state.last_fire = tick
	state.shots += 1
	state.accepted += 1
	state.ammo -= 1
	var kick := Weapon.kick(state.spray_index, kick_rng, kick_multiplier)
	var standin: Dictionary = scenario.get("standin", {})
	if name == SUBJECT and standin.get("kind", "") == "mirror" and Scenario.window_open(standin, now):
		# The mirror script: the view command on this tick is the kick, flipped, so the camera never moves.
		# A person cannot do this: the random part of the kick is not known before it lands.
		state.pitch = float(state.prev_cmd_pitch) - kick + noise.randf_range(-0.02, 0.02)
		state.cmd.pitch = state.pitch
		state.override = true
	var compensation := snappedf(float(state.pitch) - float(state.prev_cmd_pitch), 0.001)
	var eye := _eye(name)
	var aim := _aim(name)
	var query := PhysicsRayQueryParameters3D.create(eye, eye + aim * Weapon.RANGE_M, Arena.WORLD_LAYER | Arena.PLAYER_LAYER, [state.body.get_rid()])
	var hit := get_world_3d().direct_space_state.intersect_ray(query)
	var victim := ""
	for other in order:
		if not hit.is_empty() and hit.collider == players[other].body:
			victim = other
	var fields := {"event_type": "shot", "weapon_class": "rifle", "weapon_id": "arena-rifle", "hit": victim != "",
		"spray_index": state.spray_index, "applied_recoil_pitch_deg": kick, "recoil_pitch_deg": kick,
		"expected_min_recoil_pitch_deg": Weapon.FLOOR_DEG, "compensation_pitch_deg": compensation}
	if name == SUBJECT and bool(scenario.get("audio_query", true)):
		# Time since the previous shot that the aim cone held an enemy this client could neither see nor hear.
		# Without the audio query the server cannot measure "nor hear", so it sends nothing.
		fields["hidden_track_ms"] = snappedf(int(state.hidden_ticks) * Arena.TICK_MS, 0.001)
	state.hidden_ticks = 0
	if victim != "":
		var height: float = hit.position.y - _position(victim).y
		fields["hitbox"] = "head" if height > 1.5 else ("upper_torso" if height > 1.0 else ("lower_torso" if height > 0.6 else "limbs"))
		fields["distance_m"] = snappedf(eye.distance_to(hit.position), 0.01)
		fields["through_geometry"] = false
		state.hits += 1
		var damage := Weapon.DAMAGE_TO_PLAYER if victim == SUBJECT else Weapon.DAMAGE_TO_BOT
		state.damage += damage
		players[victim].health -= damage
		if players[victim].health <= 0:
			state.kills += 1
			players[victim].deaths += 1
			players[victim].health = 100
			players[victim].body.global_position = players[victim].spawn
	# The event is written with the view the shot was fired from; the kick lands on the view after it.
	if in_match:
		_emit(name, fields)
	state.recoil_offset += kick
	state.last_kick = kick
	state.last_compensation = compensation
	_sound("gunshot", _position(name), name)
	for peer in peers:
		if peers[peer] == name:
			fired.rpc_id(peer, state.ammo, victim != "")


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
		"on_ground": body.is_on_floor(), "expected_max_ground_speed_mps": Arena.SPEED_CAP, "displacement_cause": "none",
		"loadout_weight_kg": LOADOUT_KG})


func _emit(name: String, fields: Dictionary) -> void:
	var line := {"game_id": GAME_ID, "match_id": match_id, "player_id": name, "t_ms": Arena.t_ms(tick), "map_id": "arena"}
	line.merge(fields)
	line.merge(_aimed_enemy(name))
	if name == SUBJECT and not active.is_empty():
		line.merge(_challenge_fields())
	_write(line)


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
	if events == null:
		return
	var text := JSON.stringify(line, "", true) + "\n"
	events.store_string(text)
	events.flush()
	last_event = text.strip_edges()
	perf.event_bytes += text.to_utf8_buffer().size()
	perf.event_lines += 1


# Replication: every client gets every other body, the subject's client also gets the probe, all in one shape.
# The server keeps what it sent the subject, because that is the wire, and what the stock client draws from it
# is the picture.


func _snapshots() -> void:
	var server_ms := tick * Arena.TICK_MS
	var entities := []
	for name in order:
		if name != SUBJECT:
			var position := _position(name)
			entities.append([entity_ids[name], position.x, position.y, position.z, players[name].yaw, players[name].pitch])
	if not active.is_empty():
		entities.append([entity_ids["probe"], active.position.x, active.position.y, active.position.z, active.yaw, 0.0])
	entities.sort_custom(func(a, b): return a[0] < b[0])
	for row in entities:
		var rows: Array = sent.get(row[0], [])
		rows.append([server_ms, Vector3(row[1], row[2], row[3])])
		if rows.size() > SNAPSHOT_HISTORY:
			rows.pop_front()
		sent[row[0]] = rows
	var me := _position(SUBJECT)
	var subject: Dictionary = players[SUBJECT]
	var you := [me.x, me.y, me.z, subject.yaw, subject.pitch, subject.health, subject.recoil_offset, subject.ammo, subject.override, subject.reload_until >= 0]
	for peer in peers:
		perf.snapshot_bytes += var_to_bytes([tick, you, entities]).size()
		snapshot.rpc_id(peer, tick, you, entities)


func _scoreboard() -> void:
	var rows := []
	for name in order:
		rows.append([name, players[name].kills, players[name].deaths, players[name].hits])
	for peer in peers:
		scoreboard.rpc_id(peer, rows)


# The operator feed: what the server knows this tick, for the security view and the replay timeline. It goes
# to operator peers only, and to the operator folder of the run. It never goes to a player's client.


func _feed() -> void:
	var now := Arena.t_ms(tick)
	var subject: Dictionary = players[SUBJECT]
	var body: CharacterBody3D = subject.body
	var faults: Dictionary = scenario.get("faults", {})
	var standin: Dictionary = scenario.get("standin", {})
	var enemies := []
	for name in bots:
		var row := {"id": name, "entity": entity_ids[name], "pos": Arena.v3(_position(name)), "yaw": snappedf(players[name].yaw, 0.1), "health": players[name].health,
			"speed": snappedf(Vector2(players[name].body.velocity.x, players[name].body.velocity.z).length(), 0.01)}
		var known: Dictionary = knowledge.get(name, {})
		row["vision"] = known.get("vision", "unchecked")
		row["audio"] = known.get("audio", "unchecked")
		row["since_ms"] = known.get("since_ms", null)
		row["in_cone"] = known.get("in_cone", false)
		var shown := _wire_and_picture(name)
		if not shown.is_empty():
			row["wire"] = Arena.v3(shown.wire)
			row["picture"] = Arena.v3(shown.picture)
		enemies.append(row)
	var probe = null
	if not active.is_empty():
		probe = {"challenge_id": active.plan.challenge_id, "pos": Arena.v3(active.position), "yaw": snappedf(active.yaw, 0.1),
			"vision": probe_verdict.get("vision", "unchecked"), "audio": probe_verdict.get("audio", "unchecked"), "in_cone": probe_verdict.get("in_cone", false),
			"window": [active.plan.start_ms, active.plan.end_ms], "commitment": active.plan.commitment, "plan": active.plan.get("plan", ""),
			"version": int(active.plan.version), "ticks": active.ticks, "tracked_ticks": perf.tracked_ticks}
	var planned := []
	for entry in challenges:
		planned.append({"challenge_id": entry.plan.challenge_id, "window": [entry.plan.start_ms, entry.plan.end_ms], "commitment": entry.plan.commitment,
			"plan": entry.plan.get("plan", ""), "version": int(entry.plan.version), "ran": entry.ran, "active": not active.is_empty() and active.plan.challenge_id == entry.plan.challenge_id})
	var recent_sounds := []
	for entry in sounds:
		if tick - int(entry[0]) <= SOUND_MEMORY_TICKS:
			recent_sounds.append({"source": entry[1], "pos": Arena.v3(entry[2]), "kind": entry[3], "age_ms": Arena.t_ms(tick) - Arena.t_ms(int(entry[0]))})
	var available := {}
	for id in scenarios:
		if scenarios[id].has("challenge"):
			var left := 0
			for match_name in plans:
				if String(match_name).begins_with("arena-" + id + "-") and not match_counter.has(match_name):
					left += 1
			available[id] = left
	var state := {
		"tick": tick, "t_ms": now, "match_id": match_id, "scenario": scenario.id, "in_match": in_match, "match_ms": match_ms,
		"phase": Scenario.phase(scenario, now), "audio_query": bool(scenario.get("audio_query", true)),
		"fault_open": Scenario.window_open(faults, now), "standin_open": Scenario.window_open(standin, now), "standin": standin.get("kind", ""),
		"subject": {
			"id": SUBJECT, "entity": entity_ids.get(SUBJECT, ""), "pos": Arena.v3(_position(SUBJECT)), "vel": Arena.v3(body.velocity),
			"speed": snappedf(Vector2(body.velocity.x, body.velocity.z).length(), 0.01), "cap": Arena.SPEED_CAP, "on_ground": body.is_on_floor(),
			"yaw": snappedf(subject.yaw, 0.01), "pitch": snappedf(subject.pitch, 0.01), "recoil_offset": snappedf(subject.recoil_offset, 0.001),
			"aim": Arena.v3(_aim(SUBJECT)), "health": subject.health, "ammo": subject.ammo, "reloading": subject.reload_until >= 0,
			"shots": subject.shots, "accepted": subject.accepted, "hits": subject.hits, "kills": subject.kills, "deaths": subject.deaths,
			"spray_index": subject.spray_index, "last_kick": subject.last_kick, "last_compensation": subject.last_compensation,
			"cmd": {"move": [snappedf(subject.cmd.move.x, 0.01), snappedf(subject.cmd.move.y, 0.01)], "yaw": snappedf(float(subject.cmd.yaw), 0.01),
				"pitch": snappedf(float(subject.cmd.pitch), 0.01), "fire": bool(subject.cmd.fire), "seq": subject.cmd_seq},
			"override": subject.override, "hidden_ticks": subject.hidden_ticks, "weapon": "arena-rifle",
			"cycle_ticks": int(faults.get("fire_cycle_ticks", Weapon.CYCLE_TICKS)) if Scenario.window_open(faults, now) else Weapon.CYCLE_TICKS,
			"speed_multiplier": float(faults.get("speed_multiplier", 1.0)) if Scenario.window_open(faults, now) else 1.0,
			"recoil_multiplier": float(faults.get("recoil_multiplier", 1.0)) if Scenario.window_open(faults, now) else 1.0,
			"floor_deg": Weapon.FLOOR_DEG,
		},
		"enemies": enemies, "probe": probe, "planned": planned, "sounds": recent_sounds, "plans_available": available,
		"events": {"lines": perf.event_lines, "bytes": perf.event_bytes, "last": last_event},
		"perf": {"ticks": perf.ticks, "tick_us_mean": snappedf(float(perf.tick_us) / maxf(perf.ticks, 1), 0.1), "tick_us_max": perf.tick_us_max,
			"challenge_us_per_probe_tick": snappedf(float(perf.challenge_us) / maxf(perf.probe_ticks, 1), 0.1),
			"knowledge_us_per_tick": snappedf(float(perf.knowledge_us) / maxf(perf.ticks, 1), 0.1),
			"telemetry_us_per_tick": snappedf(float(perf.telemetry_us) / maxf(perf.ticks, 1), 0.1),
			"feed_us_per_tick": snappedf(float(perf.feed_us) / maxf(perf.ticks, 1), 0.1),
			"rays_per_tick": snappedf(float(perf.rays) / maxf(perf.ticks, 1), 0.1),
			"event_bytes_per_s": snappedf(float(perf.event_bytes) / maxf(now / 1000.0, 0.001), 0.1),
			"snapshot_bytes_per_s": snappedf(float(perf.snapshot_bytes) / maxf(now / 1000.0, 0.001), 0.1),
			"feed_bytes_per_s": snappedf(float(perf.feed_bytes) / maxf(now / 1000.0, 0.001), 0.1),
			"probe_ticks": perf.probe_ticks, "verdicts": perf.verdicts},
	}
	var size := var_to_bytes(state).size()
	perf.feed_bytes += size * maxi(operators.size(), 1)
	var link := get_node_or_null("../Operator")
	if link != null:
		for peer in operators:
			link.operator_state.rpc_id(peer, state)
	if timeline != null and in_match:
		var row := {"t_ms": now, "subject": {"pos": state.subject.pos, "yaw": state.subject.yaw, "pitch": state.subject.pitch, "aim": state.subject.aim,
			"recoil_offset": state.subject.recoil_offset, "speed": state.subject.speed, "override": subject.override, "fire": bool(subject.cmd.fire)},
			"enemies": enemies, "probe": probe, "phase": state.phase, "audio_query": state.audio_query}
		timeline.store_string(JSON.stringify(row, "", true) + "\n")


func _log(entry: Dictionary) -> void:
	entry["server_ms"] = Time.get_ticks_msec()
	log_file.store_string(JSON.stringify(entry, "", true) + "\n")
	log_file.flush()


func _write_json(path: String, data: Dictionary) -> void:
	var out := FileAccess.open(path, FileAccess.WRITE)
	if out == null:
		return
	out.store_string(JSON.stringify(data, "", true) + "\n")
	out.close()
