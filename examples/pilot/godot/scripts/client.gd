extends Node3D
## The pilot's stock client.
##
## It sends the player's commands (move, look, fire) and draws what the server sends: every remote body,
## the same way, behind the same walls. It never reports what it saw, heard or tracked; the server
## decides all of that. An autopilot stands in for the person at the keyboard, using only what the
## client legitimately shows: bodies it can see.
##
## Test instrumentation, kept apart from the game: it counts what arrived per entity, logs every sound
## it was sent, and, when drawing, checks how many pixels each body contributes to the frame by drawing
## the same frozen frame with that body hidden.
##
## Options (after "--"): --name --port --behaviour --render --log --shots --timeout-ms --check-every-ms

const Arena = preload("res://scripts/arena.gd")

var options := {}
var player_name := "pilot-subject"
var behaviour := "honest"
var render := false
var log_file: FileAccess
var shots_dir := ""

var connected := false
var seq := 0
var latest_ms := 0.0
var me := {"position": Vector3.ZERO, "yaw": 180.0, "pitch": 0.0, "health": 100}
var look := Vector2(180.0, 0.0)  # the yaw and pitch this client commands
var strafe := 1.0
var started_ms := 0
var entities := {}  # entity id -> {"buffer": [[server_ms, position, yaw]], "node": MeshInstance3D or null}
var received := {}  # entity id -> {"updates", "bytes", "first_ms", "last_ms"}
var sounds_heard := {}  # source entity id -> count
var last_scoreboard := []
var fps_samples := []
var frozen := false
var checking := false
var next_check_ms := 0
var checks := 0
var camera: Camera3D
var hud: Label


func _ready() -> void:
	player_name = options.get("name", player_name)
	behaviour = options.get("behaviour", behaviour)
	render = options.get("render", "false") == "true"
	shots_dir = options.get("shots", "")
	log_file = FileAccess.open(options.get("log", "user://client.jsonl"), FileAccess.WRITE)
	Arena.build(self, render)
	if render:
		camera = Camera3D.new()
		camera.fov = 75
		camera.far = 200
		add_child(camera)
		camera.make_current()
		var light := DirectionalLight3D.new()
		light.rotation_degrees = Vector3(-55, 35, 0)
		light.shadow_enabled = false  # the pilot draws no shadows; a game that does must query them as vision
		add_child(light)
		var environment := WorldEnvironment.new()
		environment.environment = Environment.new()
		environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
		environment.environment.ambient_light_color = Color(0.6, 0.6, 0.6)
		add_child(environment)
		hud = Label.new()
		hud.position = Vector2(8, 8)
		add_child(hud)
	var peer := ENetMultiplayerPeer.new()
	peer.create_client("127.0.0.1", int(options.get("port", "24680")))
	multiplayer.multiplayer_peer = peer
	multiplayer.connected_to_server.connect(_on_connected)
	multiplayer.server_disconnected.connect(func(): _quit("server_disconnected"))
	started_ms = Time.get_ticks_msec()
	get_tree().create_timer(float(options.get("timeout-ms", "120000")) / 1000.0).timeout.connect(func(): _quit("timeout"))


func _on_connected() -> void:
	connected = true
	_log({"kind": "connected", "name": player_name, "render": render, "adapter": RenderingServer.get_video_adapter_name() if render else "none"})
	hello.rpc_id(1, player_name)


# RPCs. The same names and annotations are on the server (server.gd).


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
			entities[id] = {"buffer": [], "node": _body_mesh() if render else null}
		var buffer: Array = entities[id].buffer
		buffer.append([server_ms, Vector3(row[1], row[2], row[3]), float(row[4])])
		if buffer.size() > 40:
			buffer.pop_front()
		var seen: Dictionary = received.get(id, {"updates": 0, "bytes": 0, "first_ms": server_ms, "last_ms": server_ms})
		seen.updates += 1
		seen.bytes += var_to_bytes(row).size()
		seen.last_ms = server_ms
		received[id] = seen


@rpc("authority", "reliable")
func sound(kind: String, _position: Vector3, source: String) -> void:
	# Every sound the server sent this client. Remote players make sound only this way: the client never
	# makes up a footstep from movement, so a body the server keeps silent is silent here.
	var key := source + ":" + kind
	sounds_heard[key] = int(sounds_heard.get(key, 0)) + 1


@rpc("authority", "reliable")
func scoreboard(rows: Array) -> void:
	last_scoreboard = rows


@rpc("authority", "reliable")
func match_over() -> void:
	_quit("match_over")


func _body_mesh() -> MeshInstance3D:
	# Every remote body is drawn the same way. Nothing a client receives says which one is a probe.
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


## A remote body's drawn position: interpolated one delay behind the newest snapshot, as the stock client draws it.
func _drawn(id: String) -> Variant:
	var buffer: Array = entities[id].buffer
	if buffer.is_empty() or latest_ms - float(buffer[-1][0]) > 250.0:
		return null  # no recent update: the body is gone
	var at := latest_ms - Arena.INTERP_DELAY_MS
	for index in range(buffer.size() - 1, 0, -1):
		var after: Array = buffer[index]
		var before: Array = buffer[index - 1]
		if float(before[0]) <= at and at <= float(after[0]):
			var weight := (at - float(before[0])) / maxf(float(after[0]) - float(before[0]), 0.001)
			return before[1].lerp(after[1], weight)
	return buffer[0][1] if at < float(buffer[0][0]) else buffer[-1][1]


func _physics_process(delta: float) -> void:
	if not connected:
		return
	var command := _autopilot(delta)
	seq += 1
	input_cmd.rpc_id(1, seq, command[0], look.x, look.y, command[1])


## The person at the keyboard, scripted. It only aims at bodies it can see: a clear line from its eye to
## the drawn body, through the same walls the server has.
func _autopilot(delta: float) -> Array:
	if me.position.x > 1.5:
		strafe = -1.0
	elif me.position.x < -1.5:
		strafe = 1.0
	var move := Vector2(strafe * 0.4, 0)
	var elapsed := Time.get_ticks_msec() - started_ms
	if behaviour == "spinner" and elapsed % 5000 < 1200:
		look = Vector2(wrapf(look.x + 300.0 * delta, -180.0, 180.0), 0.0)
		return [move, false]
	var eye: Vector3 = me.position + Vector3(0, Arena.EYE_HEIGHT, 0)
	var target := Vector2(180.0, 0.0)  # nothing in sight: face away from the wall
	var best := 1e9
	var space := get_world_3d().direct_space_state
	for id in entities:
		var position = _drawn(id)
		if position == null:
			continue
		var chest: Vector3 = position + Vector3(0, Arena.CHEST_HEIGHT, 0)
		if not space.intersect_ray(PhysicsRayQueryParameters3D.create(eye, chest, Arena.WORLD_LAYER)).is_empty():
			continue  # behind the wall: a person cannot see it, so the autopilot does not aim at it
		var distance := eye.distance_to(chest)
		if distance < best:
			best = distance
			target = Arena.look_angles(eye, chest)
	var turn := 360.0 * delta
	look = Vector2(look.x + clampf(wrapf(target.x - look.x, -180.0, 180.0), -turn, turn), look.y + clampf(target.y - look.y, -turn, turn))
	var on_target := best < 1e9 and absf(wrapf(target.x - look.x, -180.0, 180.0)) < 1.5 and absf(target.y - look.y) < 1.5
	return [move, on_target and elapsed % 1500 < 300]


func _process(_delta: float) -> void:
	if not render or frozen:
		return
	camera.position = me.position + Vector3(0, Arena.EYE_HEIGHT, 0)
	camera.rotation_degrees = Vector3(look.y, look.x, 0)
	for id in entities:
		var position = _drawn(id)
		var node: MeshInstance3D = entities[id].node
		node.visible = position != null
		if position != null:
			node.position = position + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
	hud.text = "health %d   %s" % [me.health, "   ".join(last_scoreboard.map(func(row): return "%s %d" % [row[0], row[1]]))]
	if Engine.get_process_frames() % 60 == 0:
		fps_samples.append(Engine.get_frames_per_second())
	var now := Time.get_ticks_msec()
	if connected and not checking and now >= next_check_ms and latest_ms > 0:
		next_check_ms = now + int(options.get("check-every-ms", "2000"))
		_pixel_check()


## Instrumentation: freeze the frame, draw it, then draw it once more with each remote body hidden in turn.
## A body that changes no pixel was not visible on this client's screen.
func _pixel_check() -> void:
	checking = true
	frozen = true
	await RenderingServer.frame_post_draw
	await RenderingServer.frame_post_draw
	var base := get_viewport().get_texture().get_image()
	var rows := []
	for id in entities:
		var node: MeshInstance3D = entities[id].node
		if not node.visible:
			rows.append({"entity": id, "drawn": false, "pixels": 0})
			continue
		node.visible = false
		await RenderingServer.frame_post_draw
		rows.append({"entity": id, "drawn": true, "pixels": _changed(base, get_viewport().get_texture().get_image())})
		node.visible = true
	await RenderingServer.frame_post_draw
	var stable := _changed(base, get_viewport().get_texture().get_image())
	checks += 1
	var shot := ""
	if shots_dir != "":
		shot = "view-%06d.png" % int(latest_ms)
		base.save_png(shots_dir.path_join(shot))
	_log({"kind": "pixels", "server_ms": snappedf(latest_ms, 0.001), "entities": rows, "unchanged_frame_pixels": stable, "screenshot": shot})
	frozen = false
	checking = false


func _changed(a: Image, b: Image) -> int:
	var left := a.get_data()
	var right := b.get_data()
	if left == right:
		return 0
	var step := 4 if a.get_format() == Image.FORMAT_RGBA8 else 3
	var count := 0
	for index in range(0, mini(left.size(), right.size()), step):
		if left[index] != right[index] or left[index + 1] != right[index + 1] or left[index + 2] != right[index + 2]:
			count += 1
	return count


func _log(entry: Dictionary) -> void:
	log_file.store_string(JSON.stringify(entry, "", true) + "\n")
	log_file.flush()


func _quit(why: String) -> void:
	if log_file == null or not log_file.is_open():
		return
	_log({"kind": "summary", "why": why, "name": player_name, "entities": received, "sounds": sounds_heard,
		"scoreboard": last_scoreboard, "fps": fps_samples, "pixel_checks": checks})
	log_file.close()
	get_tree().quit(0)
