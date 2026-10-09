extends Node
## The Arena's stock client: what a normal player runs.
##
## It sends the player's commands (move, look, fire) and draws what the server sends: every remote body,
## the same way, behind the same walls. It never reports what it saw, heard or tracked; the server decides
## all of that. It has no node at the operator path and no method that could receive the operator feed.
## Nothing it receives says whether a body is a bot, another player or a challenge probe.
##
## A person plays it with the mouse and W A S D (Esc frees the mouse, click takes it back, arrow keys look
## if the mouse cannot be captured). For the harness, --input=standin --behaviour=... puts the honest
## autopilot (autopilot.gd) at the keyboard instead.
##
## Options (after "--"): --name --port --server --input --behaviour --sens --log --timeout-ms

const Arena = preload("res://scripts/arena_map.gd")
const Autopilot = preload("res://scripts/autopilot.gd")
const Sounds = preload("res://scripts/sounds.gd")

var options := {}
var player_name := "arena-player"
var human := true
var behaviour := "tracker"
var sens := 0.12
var log_file: FileAccess
var connected := false
var seq := 0
var latest_ms := 0.0
var started_ms := 0
var me := {"position": Arena.SPAWN, "health": 100, "recoil_offset": 0.0, "ammo": 30, "override": false, "yaw": 0.0, "pitch": 0.0, "reloading": false}
var look := Vector2(Arena.SPAWN_YAW, 0.0)  # the yaw and pitch this client commands
var smooth_position := Arena.SPAWN
var entities := {}  # entity id -> {"buffer": [[server_ms, position, yaw]], "node": MeshInstance3D or null}
var received := {}  # entity id -> {"updates", "bytes"}
var sounds_heard := {}  # source entity id : kind -> count
var last_scoreboard := []
var fps_samples := []
var memory := {}
var fire_held := false
var hit_flash_until := 0
var hurt_until := 0
var last_health := 100
var title_until := 0
var instructions := ""
var title := ""
var duration_ms := 0
var banner_at_ms := 0.0

var world: Node3D
var camera: Camera3D
var hud: CanvasLayer
var crosshair: Label
var status: Label
var banner_label: Label
var vignette: ColorRect
var draws := false
var footstep_stream: AudioStreamWAV
var gunshot_stream: AudioStreamWAV
var standalone := false


func _ready() -> void:
	player_name = options.get("name", player_name)
	human = options.get("input", "human") == "human"
	behaviour = options.get("behaviour", behaviour)
	sens = float(options.get("sens", str(sens)))
	log_file = FileAccess.open(options.get("log", "user://client.jsonl"), FileAccess.WRITE)
	started_ms = Time.get_ticks_msec()
	get_tree().create_timer(float(options.get("timeout-ms", "3600000")) / 1000.0).timeout.connect(func(): _quit("timeout"))


## Build the world this client draws: under this node when it owns the window (the root is still setting up
## while we start, so nothing is added to it directly), or under the lab's left pane. ``standalone`` is true
## when this client owns the window and reads input itself.
func attach(viewport: Viewport, _parent) -> void:
	standalone = viewport == get_tree().root
	draws = DisplayServer.get_name() != "headless"
	world = Node3D.new()
	world.name = "ClientWorld"
	var holder: Node = self if standalone else viewport
	holder.add_child(world)
	Arena.build(world, draws)
	if draws:
		Arena.light(world)
		camera = Camera3D.new()
		camera.fov = 80
		camera.far = 200
		world.add_child(camera)
		camera.make_current()
		footstep_stream = Sounds.footstep()
		gunshot_stream = Sounds.gunshot()
		_build_hud(viewport)
	_connect()


func _build_hud(viewport: Viewport) -> void:
	hud = CanvasLayer.new()
	hud.name = "HUD"
	var holder: Node = self if standalone else viewport
	holder.add_child(hud)
	vignette = ColorRect.new()
	vignette.color = Color(0.8, 0.1, 0.1, 0.0)
	vignette.set_anchors_preset(Control.PRESET_FULL_RECT)
	vignette.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hud.add_child(vignette)
	crosshair = Label.new()
	crosshair.text = "+"
	crosshair.add_theme_font_size_override("font_size", 28)
	crosshair.add_theme_color_override("font_color", Color(0.9, 0.95, 1.0))
	crosshair.set_anchors_preset(Control.PRESET_CENTER)
	crosshair.grow_horizontal = Control.GROW_DIRECTION_BOTH
	crosshair.grow_vertical = Control.GROW_DIRECTION_BOTH
	crosshair.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	crosshair.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	hud.add_child(crosshair)
	status = Label.new()
	status.position = Vector2(18, 14)
	status.autowrap_mode = TextServer.AUTOWRAP_WORD
	status.size = Vector2(900, 120)
	status.add_theme_font_size_override("font_size", 18)
	status.add_theme_color_override("font_color", Color(0.85, 0.9, 0.95))
	hud.add_child(status)
	banner_label = Label.new()
	banner_label.set_anchors_preset(Control.PRESET_CENTER_TOP)
	banner_label.grow_horizontal = Control.GROW_DIRECTION_BOTH
	banner_label.position.y = 60
	banner_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	banner_label.add_theme_font_size_override("font_size", 34)
	banner_label.add_theme_color_override("font_color", Color(1.0, 0.95, 0.8))
	hud.add_child(banner_label)


func _connect() -> void:
	var peer := ENetMultiplayerPeer.new()
	peer.create_client(options.get("server", "127.0.0.1"), int(options.get("port", "24760")))
	multiplayer.multiplayer_peer = peer
	multiplayer.connected_to_server.connect(_on_connected)
	multiplayer.server_disconnected.connect(func(): _quit("server_disconnected"))
	multiplayer.connection_failed.connect(func(): _quit("connection_failed"))
	if human and draws and standalone:
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _on_connected() -> void:
	connected = true
	_log({"kind": "connected", "name": player_name, "input": "human" if human else "machine stand-in: " + behaviour, "draws": draws})
	hello.rpc_id(1, player_name)


## Mouse look, Esc to free the mouse, click to take it back. Fed by the window when standalone, by the lab otherwise.
func feed_input(event: InputEvent) -> void:
	if not human:
		return
	if event is InputEventMouseMotion and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		look = Vector2(wrapf(look.x - event.relative.x * sens, -180.0, 180.0), clampf(look.y - event.relative.y * sens, -89.0, 89.0))
	elif event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
			Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
		fire_held = event.pressed and Input.mouse_mode == Input.MOUSE_MODE_CAPTURED
	elif event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
		fire_held = false


func _unhandled_input(event: InputEvent) -> void:
	if standalone:
		feed_input(event)


# RPCs. The same names and annotations are on the server (arena_server.gd).


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
	me.yaw = float(you[3])
	me.pitch = float(you[4])
	me.health = int(you[5])
	me.recoil_offset = float(you[6])
	me.ammo = int(you[7])
	me.override = bool(you[8])
	me.reloading = bool(you[9])
	if me.override:
		look = Vector2(me.yaw, me.pitch)  # a stand-in holds the view; the local look follows it, so nothing jumps when it lets go
	if me.health < last_health:
		hurt_until = Time.get_ticks_msec() + 300
	last_health = me.health
	for row in rows:
		var id: String = row[0]
		if not entities.has(id):
			entities[id] = {"buffer": [], "node": _body_node() if draws else null}
		var buffer: Array = entities[id].buffer
		buffer.append([server_ms, Vector3(row[1], row[2], row[3]), float(row[4])])
		if buffer.size() > 40:
			buffer.pop_front()
		var seen: Dictionary = received.get(id, {"updates": 0, "bytes": 0})
		seen.updates += 1
		seen.bytes += var_to_bytes(row).size()
		received[id] = seen


@rpc("authority", "reliable")
func sound(kind: String, position: Vector3, source: String) -> void:
	# Every sound the server sent this client. Remote players make sound only this way: the client never
	# makes up a footstep from movement, so a body the server keeps silent is silent here.
	var key := source + ":" + kind
	sounds_heard[key] = int(sounds_heard.get(key, 0)) + 1
	if draws:
		_play(gunshot_stream if kind == "gunshot" else footstep_stream, position, -14.0 if kind == "gunshot" else -10.0)


@rpc("authority", "reliable")
func fired(ammo: int, hit: bool) -> void:
	me.ammo = ammo
	if hit:
		hit_flash_until = Time.get_ticks_msec() + 120
	if draws:
		_play(gunshot_stream, me.position, -8.0)


@rpc("authority", "reliable")
func banner(new_title: String, new_instructions: String, new_duration_ms: int) -> void:
	title = new_title
	instructions = new_instructions
	duration_ms = new_duration_ms
	banner_at_ms = latest_ms
	title_until = Time.get_ticks_msec() + 4000
	_log({"kind": "banner", "title": new_title})


@rpc("authority", "reliable")
func scoreboard(rows: Array) -> void:
	last_scoreboard = rows


@rpc("authority", "reliable")
func match_over() -> void:
	_quit("match_over")


func _body_node() -> MeshInstance3D:
	# Every remote body is drawn the same way. Nothing a client receives says which one is a probe.
	var node := Arena.body_mesh(Arena.COLOR_ENEMY)
	world.add_child(node)
	return node


func _play(stream: AudioStreamWAV, at: Vector3, volume_db: float) -> void:
	if stream == null or world == null:
		return
	var player := AudioStreamPlayer3D.new()
	player.stream = stream
	player.volume_db = volume_db
	player.position = at + Vector3(0, 1, 0)
	player.max_distance = Arena.HEARING_RADIUS
	world.add_child(player)
	player.finished.connect(player.queue_free)
	player.play()


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
	var command := _keyboard() if human else _stand_in(delta)
	seq += 1
	input_cmd.rpc_id(1, seq, command[0], look.x, look.y, command[1])


## A person: W A S D relative to where they look, the arrow keys as a fallback for looking, the left button to fire.
func _keyboard() -> Array:
	var turn := 90.0 / Arena.TICK_HZ
	look = Vector2(wrapf(look.x + (turn if Input.is_key_pressed(KEY_LEFT) else 0.0) - (turn if Input.is_key_pressed(KEY_RIGHT) else 0.0), -180.0, 180.0),
		clampf(look.y + (turn if Input.is_key_pressed(KEY_UP) else 0.0) - (turn if Input.is_key_pressed(KEY_DOWN) else 0.0), -89.0, 89.0))
	var step := Vector2(
		(1.0 if Input.is_key_pressed(KEY_D) else 0.0) - (1.0 if Input.is_key_pressed(KEY_A) else 0.0),
		(1.0 if Input.is_key_pressed(KEY_W) else 0.0) - (1.0 if Input.is_key_pressed(KEY_S) else 0.0),
	)
	var fire := fire_held or (Input.mouse_mode == Input.MOUSE_MODE_CAPTURED and Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT))
	return [step.limit_length(1.0), fire]


## The honest autopilot, shown only what a person would see: drawn bodies with a clear line from the eye.
func _stand_in(delta: float) -> Array:
	var eye: Vector3 = me.position + Vector3(0, Arena.EYE_HEIGHT, 0)
	var space := world.get_world_3d().direct_space_state
	var bodies := []
	for id in entities:
		var position = _drawn(id)
		if position == null:
			continue
		var chest: Vector3 = position + Vector3(0, Arena.CHEST_HEIGHT, 0)
		var blocked := not space.intersect_ray(PhysicsRayQueryParameters3D.create(eye, chest, Arena.WORLD_LAYER)).is_empty()
		bodies.append({"id": id, "chest": chest, "visible": not blocked})
	var view := {"position": me.position, "look": look, "recoil_offset": me.recoil_offset, "bodies": bodies, "elapsed_ms": int(latest_ms)}
	var decided := Autopilot.decide(behaviour, view, memory, delta)
	look = decided.look
	return [decided.move, decided.fire]


func _process(_delta: float) -> void:
	if Engine.get_process_frames() % 60 == 0 and connected:
		fps_samples.append(Engine.get_frames_per_second())
	if not draws:
		return
	smooth_position = smooth_position.lerp(me.position, 0.35)
	camera.position = smooth_position + Vector3(0, Arena.EYE_HEIGHT, 0)
	var yaw: float = me.yaw if me.override else look.x
	var pitch: float = me.pitch if me.override else look.y
	camera.rotation_degrees = Vector3(pitch + me.recoil_offset, yaw, 0)
	for id in entities:
		var position = _drawn(id)
		var node: MeshInstance3D = entities[id].node
		node.visible = position != null
		if position != null:
			node.position = position + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
	var now := Time.get_ticks_msec()
	crosshair.add_theme_color_override("font_color", Color(1.0, 0.4, 0.3) if now < hit_flash_until else Color(0.9, 0.95, 1.0))
	vignette.color.a = 0.35 if now < hurt_until else 0.0
	var left := maxi(0, int((duration_ms - (latest_ms - banner_at_ms)) / 1000.0)) if duration_ms > 0 else 0
	var clock := ("   %d:%02d left" % [left / 60, left % 60]) if duration_ms > 0 else ""
	var ammo_text := "reloading" if me.reloading else "%d / 30" % me.ammo
	var held := "\nSCRIPTED TEST STAND-IN HOLDS YOUR AIM: a server-side script, not a human, not real cheat software" if me.override else ""
	status.text = "health %d   ammo %s%s\n%s%s" % [me.health, ammo_text, clock, instructions, held]
	banner_label.text = title if now < title_until else ""


func _log(entry: Dictionary) -> void:
	if log_file == null:
		return
	log_file.store_string(JSON.stringify(entry, "", true) + "\n")
	log_file.flush()


func _quit(why: String) -> void:
	if log_file == null or not log_file.is_open():
		return
	if Input.mouse_mode == Input.MOUSE_MODE_CAPTURED:
		Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	_log({"kind": "summary", "why": why, "name": player_name, "entities": received, "sounds": sounds_heard, "scoreboard": last_scoreboard, "fps": fps_samples})
	log_file.close()
	if standalone:
		get_tree().quit(0)
