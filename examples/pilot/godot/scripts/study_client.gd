extends Node3D
## The consented human pilot's client: the stock client, played by a person with keyboard and mouse.
##
## It shows a consent gate before it connects, then the mode's instructions. It sends commands only (move,
## look, fire) and draws whatever the server sends, every remote body the same way. It records nothing but
## its own frame rate and what it received from the game server: no screen, no other window, no file, no
## process. It runs no pixel checks, so a participant never sees a frozen frame.
##
## For the machine dry run only, --input=standin --behaviour=... replaces the person with a script. Those
## sessions are labelled machine stand-ins everywhere, and never counted as human.
##
## Controls: mouse to look, W A S D to move, left button to fire, Esc to free the mouse, click to take it back,
## arrow keys to look if the mouse cannot be captured.
##
## Options (after "--"): --participant --port --server --input --behaviour --sens --instructions --log --timeout-ms

const Arena = preload("res://scripts/arena.gd")
const Study = preload("res://scripts/study_arena.gd")

var options := {}
var participant := ""
var human := true
var behaviour := "tracker"
var sens := 0.15
var log_file: FileAccess
var agreed := false
var connected := false
var seq := 0
var latest_ms := 0.0
var me := {"position": Study.SPAWN, "health": 100}
var look := Vector2(0.0, 0.0)
var entities := {}
var received := {}
var sounds_heard := {}
var kills := 0
var fps_samples := []
var camera: Camera3D
var hud: Label
var gate: Label
var started_ms := 0
var script_state := {"index": 0, "until": 0, "strafe": 1.0, "rng": RandomNumberGenerator.new()}


func _ready() -> void:
	participant = options.get("participant", "")
	human = options.get("input", "human") == "human"
	behaviour = options.get("behaviour", behaviour)
	sens = float(options.get("sens", str(sens)))
	log_file = FileAccess.open(options.get("log", "user://client.jsonl"), FileAccess.WRITE)
	script_state.rng.seed = 7
	var draws := DisplayServer.get_name() != "headless"
	Study.build(self, draws)
	if draws:
		camera = Camera3D.new()
		camera.fov = 80
		camera.far = 200
		add_child(camera)
		camera.make_current()
		var light := DirectionalLight3D.new()
		light.rotation_degrees = Vector3(-55, 35, 0)
		add_child(light)
		var environment := WorldEnvironment.new()
		environment.environment = Environment.new()
		environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
		environment.environment.ambient_light_color = Color(0.6, 0.6, 0.6)
		add_child(environment)
		var layer := CanvasLayer.new()
		add_child(layer)
		hud = Label.new()
		hud.position = Vector2(12, 10)
		layer.add_child(hud)
		var crosshair := Label.new()
		crosshair.text = "+"
		crosshair.set_anchors_preset(Control.PRESET_CENTER)
		layer.add_child(crosshair)
		gate = Label.new()
		gate.position = Vector2(40, 60)
		gate.autowrap_mode = TextServer.AUTOWRAP_WORD
		gate.size = Vector2(560, 300)
		gate.text = ("Playtest: how honest players aim.\n\nThe game server records your movement, aim and shots in this game, and nothing else.\n"
			+ "Hidden test objects exist behind walls; you will not see or hear them. Please don't try to find them: just play the round as asked.\n"
			+ "You are " + participant + ". You can stop at any time.\n\nThis round: " + options.get("instructions", "") + "\n\nPress Y to agree and start, or N to leave.")
		layer.add_child(gate)
	if not human:
		agreed = true
		_connect()
	started_ms = Time.get_ticks_msec()
	get_tree().create_timer(float(options.get("timeout-ms", "900000")) / 1000.0).timeout.connect(func(): _quit("timeout"))


func _connect() -> void:
	if gate != null:
		gate.visible = false
	var peer := ENetMultiplayerPeer.new()
	peer.create_client(options.get("server", "127.0.0.1"), int(options.get("port", "24800")))
	multiplayer.multiplayer_peer = peer
	multiplayer.connected_to_server.connect(func():
		connected = true
		_log({"kind": "connected", "input": "human" if human else "machine stand-in: " + behaviour})
		hello.rpc_id(1, participant))
	multiplayer.server_disconnected.connect(func(): _quit("server_disconnected"))
	if human:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _unhandled_input(event: InputEvent) -> void:
	if not human:
		return
	if not agreed and event is InputEventKey and event.pressed:
		if event.keycode == KEY_Y:
			agreed = true
			_log({"kind": "consent", "agreed": true})
			_connect()
		elif event.keycode == KEY_N:
			_log({"kind": "consent", "agreed": false})
			_quit("declined")
		return
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		look = Vector2(wrapf(look.x - event.relative.x * sens, -180.0, 180.0), clampf(look.y - event.relative.y * sens, -89.0, 89.0))
	elif event is InputEventMouseButton and event.pressed and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	elif event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


# RPCs, the same on the server (study_server.gd).


@rpc("any_peer", "reliable")
func hello(_player_name: String) -> void:
	pass


@rpc("any_peer", "unreliable_ordered")
func input_cmd(_seq: int, _move: Vector2, _yaw: float, _pitch: float, _fire: bool) -> void:
	pass


@rpc("authority", "unreliable_ordered")
func snapshot(server_tick: int, you: Array, rows: Array) -> void:
	var server_ms := server_tick * Arena.TICK_MS
	latest_ms = maxf(latest_ms, server_ms)
	me.position = Vector3(you[0], you[1], you[2])
	me.health = you[5]
	for row in rows:
		var id: String = row[0]
		if not entities.has(id):
			entities[id] = {"buffer": [], "node": _body_mesh() if camera != null else null}
		var buffer: Array = entities[id].buffer
		buffer.append([server_ms, Vector3(row[1], row[2], row[3])])
		if buffer.size() > 40:
			buffer.pop_front()
		var seen: Dictionary = received.get(id, {"updates": 0, "bytes": 0})
		seen.updates += 1
		seen.bytes += var_to_bytes(row).size()
		received[id] = seen


@rpc("authority", "reliable")
func sound(kind: String, _position: Vector3, source: String) -> void:
	var key := source + ":" + kind
	sounds_heard[key] = int(sounds_heard.get(key, 0)) + 1


@rpc("authority", "reliable")
func scoreboard(rows: Array) -> void:
	kills = int(rows[0][1]) if rows.size() > 0 else kills


@rpc("authority", "reliable")
func match_over() -> void:
	if hud != null:
		hud.text = "Round over. Thank you. The playtest host will ask you four quick questions."
	await get_tree().create_timer(2.0).timeout
	_quit("session_over")


func _body_mesh() -> MeshInstance3D:
	var node := MeshInstance3D.new()
	var capsule := CapsuleMesh.new()
	capsule.radius = Arena.BODY_RADIUS
	capsule.height = Arena.BODY_HEIGHT
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.75, 0.2, 0.15)
	capsule.material = material
	node.mesh = capsule
	add_child(node)
	return node


func _drawn(id: String) -> Variant:
	var buffer: Array = entities[id].buffer
	if buffer.is_empty() or latest_ms - float(buffer[-1][0]) > 250.0:
		return null
	var at := latest_ms - Arena.INTERP_DELAY_MS
	for index in range(buffer.size() - 1, 0, -1):
		var after: Array = buffer[index]
		var before: Array = buffer[index - 1]
		if float(before[0]) <= at and at <= float(after[0]):
			return before[1].lerp(after[1], (at - float(before[0])) / maxf(float(after[0]) - float(before[0]), 0.001))
	return buffer[0][1] if at < float(buffer[0][0]) else buffer[-1][1]


func _physics_process(delta: float) -> void:
	if not connected:
		return
	var command := _keyboard(delta) if human else _stand_in(delta)
	seq += 1
	input_cmd.rpc_id(1, seq, command[0], look.x, look.y, command[1])


## A person: W A S D relative to where they look, the arrow keys as a fallback for looking, the left button to fire.
func _keyboard(delta: float) -> Array:
	var turn := 90.0 * delta
	look = Vector2(wrapf(look.x + (turn if Input.is_key_pressed(KEY_LEFT) else 0.0) - (turn if Input.is_key_pressed(KEY_RIGHT) else 0.0), -180.0, 180.0),
		clampf(look.y + (turn if Input.is_key_pressed(KEY_UP) else 0.0) - (turn if Input.is_key_pressed(KEY_DOWN) else 0.0), -89.0, 89.0))
	var forward := Arena.aim_direction(look.x, 0.0)
	var right := Vector3(-forward.z, 0, forward.x)
	var step := forward * ((1.0 if Input.is_key_pressed(KEY_W) else 0.0) - (1.0 if Input.is_key_pressed(KEY_S) else 0.0))
	step += right * ((1.0 if Input.is_key_pressed(KEY_D) else 0.0) - (1.0 if Input.is_key_pressed(KEY_A) else 0.0))
	var fire := Input.mouse_mode == Input.MOUSE_MODE_CAPTURED and Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)
	return [Vector2(step.x, step.z).limit_length(1.0), fire]


## Machine stand-ins for the dry run: scripted honest-style aiming, never counted as people.
func _stand_in(delta: float) -> Array:
	var now := Time.get_ticks_msec() - started_ms
	var eye: Vector3 = me.position + Vector3(0, Arena.EYE_HEIGHT, 0)
	var target := look
	var fire := false
	var move := Vector2.ZERO
	var visible = _nearest_visible(eye)
	match behaviour:
		"prefire":  # pre-aim common angles in turn, a second each, and fire at what shows up
			if now >= int(script_state.until):
				script_state.index = (int(script_state.index) + 1) % Study.ANGLES.size()
				script_state.until = now + 1200
			target = Arena.look_angles(eye, Study.ANGLES[script_state.index])
		"holder":  # hold the doorway's left frame from the spawn for the whole round
			target = Arena.look_angles(eye, Study.ANGLES[0])
		"sweeper":  # sweep the room ahead, side to side, at head height
			target = Vector2(80.0 * sin(now / 1500.0), -2.0)
		"flicker":  # flick between common angles and random directions
			if now >= int(script_state.until):
				script_state.until = now + 350
				script_state.index = script_state.rng.randi_range(0, Study.ANGLES.size() + 3)
			target = Arena.look_angles(eye, Study.ANGLES[script_state.index]) if int(script_state.index) < Study.ANGLES.size() else Vector2(script_state.rng.randf_range(-180, 180), script_state.rng.randf_range(-10, 10))
		_:  # tracker: aim at the nearest bot it can see, as P12's honest client
			target = visible if visible != null else Vector2(180.0, 0.0)
	if behaviour != "tracker" and visible != null and now % 3000 < 600:
		target = visible  # every stand-in also shoots a bot it can see now and then
	var rate := (1080.0 if behaviour == "flicker" else 300.0) * delta
	look = Vector2(look.x + clampf(wrapf(target.x - look.x, -180.0, 180.0), -rate, rate), clampf(look.y + clampf(target.y - look.y, -rate, rate), -89.0, 89.0))
	if visible != null and absf(wrapf(visible.x - look.x, -180.0, 180.0)) < 1.5 and absf(visible.y - look.y) < 1.5:
		fire = now % 1500 < 300
	if behaviour in ["prefire", "flicker", "tracker"]:
		if me.position.x > 2.5:
			script_state.strafe = -1.0
		elif me.position.x < -2.5:
			script_state.strafe = 1.0
		move = Vector2(script_state.strafe * 0.4, 0)
	return [move, fire]


func _nearest_visible(eye: Vector3) -> Variant:
	var best := 1e9
	var found: Variant = null
	var space := get_world_3d().direct_space_state
	for id in entities:
		var position = _drawn(id)
		if position == null:
			continue
		var chest: Vector3 = position + Vector3(0, Arena.CHEST_HEIGHT, 0)
		if not space.intersect_ray(PhysicsRayQueryParameters3D.create(eye, chest, Arena.WORLD_LAYER)).is_empty():
			continue  # behind a wall: nobody could see it, so no stand-in aims at it
		if eye.distance_to(chest) < best:
			best = eye.distance_to(chest)
			found = Arena.look_angles(eye, chest)
	return found


func _process(_delta: float) -> void:
	if Engine.get_process_frames() % 60 == 0 and connected:
		fps_samples.append(Engine.get_frames_per_second())
	if camera == null:
		return
	camera.position = me.position + Vector3(0, Arena.EYE_HEIGHT, 0)
	camera.rotation_degrees = Vector3(look.y, look.x, 0)
	for id in entities:
		var position = _drawn(id)
		var node: MeshInstance3D = entities[id].node
		node.visible = position != null
		if position != null:
			node.position = position + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
	if connected and hud != null:
		var left := maxi(0, int((float(options.get("match-ms", "240000")) - latest_ms) / 1000.0))
		hud.text = "%s   |   %d:%02d left   |   bots hit down: %d   |   Esc frees the mouse" % [options.get("instructions", ""), left / 60, left % 60, kills]


func _log(entry: Dictionary) -> void:
	log_file.store_string(JSON.stringify(entry, "", true) + "\n")
	log_file.flush()


func _quit(why: String) -> void:
	if log_file == null or not log_file.is_open():
		return
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	_log({"kind": "summary", "why": why, "entities": received, "sounds": sounds_heard, "fps": fps_samples})
	log_file.close()
	get_tree().quit(0)
