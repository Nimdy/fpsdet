extends Node
## The operator's end of the privileged link (Main/Operator), and the security view it draws from it.
##
## It receives the server's feed (true positions, the probe, the per-tick verdicts, the player's command
## beside what was accepted) and reads what the Python scorer beside the server writes from fpsdet: the
## subject's case, its findings, its detector eligibility, its packet and graph, and fpsdet's own answer to
## "could this client know" for every channel combination (live/knowledge-table.json). Every chip on
## every panel is one of those two sources. Nothing here computes a finding, a decision or a knowledge
## state; where the panel shows a word, the word came from fpsdet or from the server's own query.
##
## It never sends a player command. It asks the server for a scenario or an option, with the run's token.
##
## Options (after "--"): --run --scenarios --port --operator-token-file --mode

const Arena = preload("res://scripts/arena_map.gd")
const Scenario = preload("res://scripts/scenario.gd")

const FONT_NAMES := ["DejaVu Sans Mono", "Liberation Mono", "Menlo", "Consolas", "Courier New", "monospace"]
const COLORS := {
	"known": "39d353", "unknowable": "c084fc", "unknown": "9aa4b2", "absent": "6b7280", "unchecked": "f5c542",
	"eligible": "39d353", "baseline_too_thin": "f5c542", "insufficient_samples": "f5c542", "conflict": "ff5c5c",
	"telemetry_unavailable": "6b7280", "disabled": "6b7280", "not_applicable": "6b7280",
	"review": "ff5c5c", "watch": "f5c542", "clean": "39d353", "insufficient_data": "9aa4b2",
	"followed": "ff5c5c", "not_followed": "39d353", "abstained": "f5c542", "no_samples": "6b7280", "unplanned": "6b7280",
	"identical": "39d353", "mismatch": "ff5c5c", "experimental": "ff9f1c", "none": "39d353", "off": "6b7280", "on": "39d353",
}
const INK := "0b0c10"

var options := {}
var run_dir := ""
var mode := "developer"
var scenarios := {}
var token := ""
var accepted := false
var latest := {}  # the server's last feed
var live := {}  # the scorer's current.json
var status := {}  # the scorer's status.json
var table := {}  # fpsdet's knowledge table
var live_mtime := 0
var status_mtime := 0
var next_poll_ms := 0
var tail_lines := []  # the telemetry inspector's last lines
var tail_offset := 0
var tail_match := ""
var filter := "all"
var tab := 0
var camera_mode := 0
var client_fps := 0.0
var replaying := false
var replay_rows := []
var replay_case := {}
var replay_index := 0
var replay_playing := false
var replay_match := ""
var last_frame_ms := 0
var last_scenario := ""
var card_until := 0

var world: Node3D
var camera: Camera3D
var rays: MeshInstance3D
var markers := {}  # id -> {"body": MeshInstance3D, "label": Label, "anchor": Vector3, "wire": MeshInstance3D, "picture": MeshInstance3D}
var probe_node: MeshInstance3D
var probe_label: Label
var probe_anchor := Vector3.ZERO
var tag_layer: CanvasLayer
var subject_node: MeshInstance3D
var sound_rings := []
var panels := {}  # key -> RichTextLabel
var panel_titles := {}  # key -> Label
var strip: RichTextLabel
var badges: RichTextLabel
var card: RichTextLabel
var card_box: PanelContainer
var graph_view: Control
var timeline_view: Control
var panel_root: Control
var theme: Theme
var draws := false
var overlay: Label


func _ready() -> void:
	run_dir = options.get("run", "")
	mode = options.get("mode", mode)
	scenarios = Scenario.load_all(options.get("scenarios", ""))
	var path: String = options.get("operator-token-file", "")
	if path != "" and FileAccess.file_exists(path):
		token = FileAccess.get_file_as_string(path).strip_edges()


## Build the security view under ``viewport`` and the panels under ``panel_parent`` (null: this node owns the window
## and lays out both itself). Then join the server as the operator.
func attach(viewport: Viewport, panel_parent: Control) -> void:
	draws = DisplayServer.get_name() != "headless"
	theme = _theme()
	var standalone := viewport == get_tree().root
	var holder: Node = self if standalone else viewport
	world = Node3D.new()
	world.name = "SecurityWorld"
	holder.add_child(world)
	Arena.build(world, draws)
	if draws:
		Arena.light(world)
		camera = Camera3D.new()
		camera.fov = 80
		camera.far = 200
		world.add_child(camera)
		camera.make_current()
		rays = MeshInstance3D.new()
		rays.mesh = ImmediateMesh.new()
		var material := StandardMaterial3D.new()
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.vertex_color_use_as_albedo = true
		material.no_depth_test = true
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		rays.material_override = material
		world.add_child(rays)
		subject_node = _ghost(Color(0.35, 0.65, 1.0, 0.55), false)
		probe_node = _ghost(Arena.COLOR_PROBE, true)
		probe_node.visible = false
		_build_overlay(viewport if not standalone else null)
		probe_label = _tag()
		probe_label.visible = false
	if standalone:
		panel_root = Control.new()
		panel_root.set_anchors_preset(Control.PRESET_FULL_RECT)
		var layer := CanvasLayer.new()
		add_child(layer)
		layer.add_child(panel_root)
		var box := VBoxContainer.new()
		box.set_anchors_preset(Control.PRESET_FULL_RECT)
		panel_root.add_child(box)
		var spacer := Control.new()
		spacer.custom_minimum_size = Vector2(0, 560)
		box.add_child(spacer)
		build_panels(box)
	elif panel_parent != null:
		build_panels(panel_parent)
	if multiplayer.multiplayer_peer == null or multiplayer.multiplayer_peer is OfflineMultiplayerPeer:
		var peer := ENetMultiplayerPeer.new()
		peer.create_client("127.0.0.1", int(options.get("port", "24760")))
		multiplayer.multiplayer_peer = peer
	if multiplayer.multiplayer_peer.get_connection_status() == MultiplayerPeer.CONNECTION_CONNECTED:
		operator_hello.rpc_id(1, token)
	else:
		multiplayer.connected_to_server.connect(func(): operator_hello.rpc_id(1, token))


# RPCs. The same names and annotations are on the server (server_operator.gd).


@rpc("any_peer", "reliable")
func operator_hello(_token: String) -> void:
	pass


@rpc("any_peer", "reliable")
func operator_request(_what: String, _value: String) -> void:
	pass


@rpc("authority", "reliable")
func operator_welcome(ok: bool) -> void:
	accepted = ok


@rpc("authority", "unreliable_ordered")
func operator_state(state: Dictionary) -> void:
	latest = state
	if String(state.get("scenario", "")) != last_scenario:
		last_scenario = String(state.get("scenario", ""))
		card_until = Time.get_ticks_msec() + 8000


## Ask the server for a scenario, to end one, or to flip an option. Only the server decides whether it happens.
func request(what: String, value: String) -> void:
	if accepted:
		operator_request.rpc_id(1, what, value)


func set_ai(enabled: bool) -> void:
	var out := FileAccess.open(run_dir.path_join("public").path_join("control").path_join("ai.json"), FileAccess.WRITE)
	if out != null:
		out.store_string(JSON.stringify({"enabled": enabled}) + "\n")
		out.close()


func cycle_camera() -> void:
	camera_mode = (camera_mode + 1) % 3


func cycle_tab() -> void:
	tab = (tab + 1) % 6


func cycle_filter() -> void:
	var order := ["all", "movement", "shot", "challenge", "knowledge"]
	filter = order[(order.find(filter) + 1) % order.size()]


func copy_last_event() -> void:
	if not tail_lines.is_empty():
		DisplayServer.clipboard_set(String(tail_lines[-1]))


# Replay: the recorded timeline of a finished match, and fpsdet's case as it stood second by second.


func enter_replay(match_id: String) -> bool:
	var path := run_dir.path_join("operator").path_join(match_id).path_join("timeline.ndjson")
	if not FileAccess.file_exists(path):
		return false
	replay_rows = []
	for line in FileAccess.get_file_as_string(path).split("\n"):
		if line.strip_edges() != "":
			replay_rows.append(JSON.parse_string(line))
	var case_path := run_dir.path_join("public").path_join("live").path_join(match_id + ".json")
	replay_case = JSON.parse_string(FileAccess.get_file_as_string(case_path)) if FileAccess.file_exists(case_path) else {}
	if replay_rows.is_empty():
		return false
	replay_match = match_id
	replay_index = 0
	replay_playing = false
	replaying = true
	return true


func exit_replay() -> void:
	replaying = false
	replay_playing = false


func replay_step(ms: int) -> void:
	if replay_rows.is_empty():
		return
	var target := int(replay_rows[replay_index].t_ms) + ms
	var index := replay_index
	if ms > 0:
		while index < replay_rows.size() - 1 and int(replay_rows[index].t_ms) < target:
			index += 1
	else:
		while index > 0 and int(replay_rows[index].t_ms) > target:
			index -= 1
	replay_index = index


func replay_home() -> void:
	replay_index = 0


func replay_end() -> void:
	replay_index = max(0, replay_rows.size() - 1)


func toggle_replay_play() -> void:
	replay_playing = not replay_playing


## The newest finished match, for the replay key.
func last_finished_match() -> String:
	var folder := run_dir.path_join("public").path_join("matches")
	var dir := DirAccess.open(folder)
	if dir == null:
		return ""
	var names := []
	for name in dir.get_directories():
		if FileAccess.file_exists(folder.path_join(name).path_join("match.json")):
			names.append(name)
	if names.is_empty():
		return ""
	names.sort_custom(func(a, b): return FileAccess.get_modified_time(folder.path_join(a).path_join("match.json")) < FileAccess.get_modified_time(folder.path_join(b).path_join("match.json")))
	return names[-1]


# Files from the scorer.


func _poll_files() -> void:
	var now := Time.get_ticks_msec()
	if now < next_poll_ms:
		return
	next_poll_ms = now + 250
	var live_path := run_dir.path_join("public").path_join("live").path_join("current.json")
	if FileAccess.file_exists(live_path):
		var stamp := FileAccess.get_modified_time(live_path)
		if stamp != live_mtime:
			live_mtime = stamp
			var parsed = JSON.parse_string(FileAccess.get_file_as_string(live_path))
			if typeof(parsed) == TYPE_DICTIONARY:
				live = parsed
	var status_path := run_dir.path_join("public").path_join("live").path_join("status.json")
	if FileAccess.file_exists(status_path):
		var stamp := FileAccess.get_modified_time(status_path)
		if stamp != status_mtime:
			status_mtime = stamp
			var parsed = JSON.parse_string(FileAccess.get_file_as_string(status_path))
			if typeof(parsed) == TYPE_DICTIONARY:
				status = parsed
	if table.is_empty():
		var table_path := run_dir.path_join("public").path_join("live").path_join("knowledge-table.json")
		if FileAccess.file_exists(table_path):
			var parsed = JSON.parse_string(FileAccess.get_file_as_string(table_path))
			if typeof(parsed) == TYPE_DICTIONARY:
				table = parsed
	_tail_events()


## The telemetry inspector: the last lines the server wrote for the current match, as written.
func _tail_events() -> void:
	var match_id := String(latest.get("match_id", ""))
	if match_id == "" or not bool(latest.get("in_match", false)):
		return
	var path := run_dir.path_join("public").path_join("matches").path_join(match_id).path_join("events.ndjson")
	if not FileAccess.file_exists(path):
		return
	if match_id != tail_match:
		tail_match = match_id
		tail_offset = 0
		tail_lines = []
	var file := FileAccess.open(path, FileAccess.READ)
	if file == null:
		return
	var length := file.get_length()
	if length <= tail_offset:
		file.close()
		return
	file.seek(tail_offset)
	var chunk := file.get_buffer(length - tail_offset).get_string_from_utf8()
	file.close()
	var last_newline := chunk.rfind("\n")
	if last_newline < 0:
		return
	tail_offset += chunk.substr(0, last_newline + 1).to_utf8_buffer().size()
	for line in chunk.substr(0, last_newline).split("\n"):
		if line.strip_edges() != "":
			tail_lines.append(line)
	while tail_lines.size() > 60:
		tail_lines.pop_front()


# The knowledge chips: fpsdet's table, keyed by the server's channel words. Never computed here.


func _enemy_knowledge(vision: String, audio: String, since_ms) -> Dictionary:
	if table.is_empty():
		return {"status": "…", "cause": "waiting for fpsdet"}
	var recent := "unchecked"
	if since_ms != null:
		recent = "known" if float(since_ms) < float(table.get("hidden_grace_ms", 1000)) else "absent"
	var key := "%s|%s|%s" % [vision, audio, recent]
	var found: Dictionary = table.get("enemy", {}).get(key, {})
	return found if not found.is_empty() else {"status": "?", "cause": key}


func _body_knowledge(vision: String, audio: String) -> Dictionary:
	if table.is_empty():
		return {"status": "…", "cause": "waiting for fpsdet"}
	var found: Dictionary = table.get("challenge_body", {}).get("%s|%s" % [vision, audio], {})
	return found if not found.is_empty() else {"status": "?", "cause": ""}


# Each frame: poll, draw the 3D view, refresh the panels.


func _process(delta: float) -> void:
	_poll_files()
	if replaying and replay_playing and not replay_rows.is_empty():
		var now := Time.get_ticks_msec()
		if last_frame_ms > 0:
			replay_step(now - last_frame_ms)
		last_frame_ms = now
		if replay_index >= replay_rows.size() - 1:
			replay_playing = false
	else:
		last_frame_ms = 0
	if draws:
		_draw_world(delta)
		if overlay != null:
			overlay.text = _actor_line(_frame())
	if panel_root != null or not panels.is_empty():
		_refresh_panels()


## The frame the view shows: the live feed, or the replay row under the scrubber.
func _frame() -> Dictionary:
	if replaying and not replay_rows.is_empty():
		var row: Dictionary = replay_rows[replay_index]
		return {"subject": {"pos": row.subject.pos, "yaw": row.subject.yaw, "pitch": row.subject.pitch, "aim": row.subject.aim, "recoil_offset": row.subject.recoil_offset,
				"override": row.subject.get("override", false), "speed": row.subject.speed}, "enemies": row.enemies, "probe": row.probe, "t_ms": row.t_ms,
			"audio_query": row.get("audio_query", true), "phase": row.get("phase", ""), "sounds": [], "replay": true,
			"standin": row.get("standin", ""), "subject_input": row.get("subject_input", "human"), "actor": row.get("actor", "")}
	return latest


func _draw_world(_delta: float) -> void:
	var frame := _frame()
	if frame.is_empty():
		return
	var subject: Dictionary = frame.get("subject", {})
	if subject.is_empty():
		return
	var pos := Arena.from_v3(subject.pos)
	var aim := Arena.from_v3(subject.aim)
	var eye := pos + Vector3(0, Arena.EYE_HEIGHT, 0)
	subject_node.position = pos + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
	match camera_mode:
		0:  # the player's own eye, with everything the server knows drawn on top
			camera.position = eye
			camera.rotation_degrees = Vector3(float(subject.pitch) + float(subject.recoil_offset), float(subject.yaw), 0)
			subject_node.visible = false
		1:  # over the shoulder
			var back := Arena.aim_direction(float(subject.yaw), 0.0)
			camera.position = eye - back * 4.0 + Vector3(0, 2.0, 0)
			camera.look_at(eye + back * 6.0, Vector3.UP)
			subject_node.visible = true
		_:  # overhead
			camera.position = Vector3(0, 30, 2)
			camera.look_at(Vector3(0, 0, 0), Vector3(0, 0, -1))
			subject_node.visible = true
	var mesh: ImmediateMesh = rays.mesh
	mesh.clear_surfaces()
	mesh.surface_begin(Mesh.PRIMITIVE_LINES)
	# The aim: a thin line from the eye along the server's authoritative view.
	mesh.surface_set_color(Color(0.5, 0.8, 1.0, 0.6))
	mesh.surface_add_vertex(eye)
	mesh.surface_add_vertex(eye + aim * 40.0)
	var seen := {}
	for enemy in frame.get("enemies", []):
		var id := String(enemy.id)
		seen[id] = true
		var marker: Dictionary = _marker_for(id)
		var true_pos := Arena.from_v3(enemy.pos)
		marker.body.position = true_pos + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
		var chest := true_pos + Vector3(0, Arena.CHEST_HEIGHT, 0)
		var vision := String(enemy.get("vision", "unchecked"))
		var audio := String(enemy.get("audio", "unchecked"))
		var known := _enemy_knowledge(vision, audio, enemy.get("since_ms"))
		var color := _color(String(known.status))
		marker.anchor = true_pos + Vector3(0, Arena.BODY_HEIGHT + 0.25, 0)
		marker.label.text = "%s  ·  vision %s · audio %s\n%s (%s)%s" % [id, vision, audio, String(known.status).to_upper(), known.cause, "  ◎ in cone" if bool(enemy.get("in_cone", false)) else ""]
		marker.label.modulate = color
		(marker.body.material_override as StandardMaterial3D).albedo_color = Color(color, 0.55)
		# The vision ray: eye to chest, green when the server's query says seen, red when it says not.
		mesh.surface_set_color(Color(0.3, 0.9, 0.4, 0.8) if vision == "known" else Color(1.0, 0.3, 0.3, 0.8))
		mesh.surface_add_vertex(eye)
		mesh.surface_add_vertex(chest)
		var show_ghosts: bool = enemy.has("wire") and enemy.has("picture") and (mode != "demo" or String(latest.get("scenario", "")) == "wire_vs_picture")
		if show_ghosts:
			marker.wire.visible = true
			marker.picture.visible = true
			marker.wire.position = Arena.from_v3(enemy.wire) + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
			marker.picture.position = Arena.from_v3(enemy.picture) + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
		else:
			marker.wire.visible = false
			marker.picture.visible = false
	for id in markers:
		if not seen.has(id):
			markers[id].body.visible = false
			markers[id].label.visible = false
			markers[id].wire.visible = false
			markers[id].picture.visible = false
		else:
			markers[id].body.visible = true
			markers[id].label.visible = true
	var probe = frame.get("probe")
	if probe != null and typeof(probe) == TYPE_DICTIONARY:
		var probe_pos := Arena.from_v3(probe.pos)
		probe_node.visible = true
		probe_label.visible = true
		probe_node.position = probe_pos + Vector3(0, Arena.BODY_HEIGHT / 2, 0)
		probe_anchor = probe_pos + Vector3(0, Arena.BODY_HEIGHT + 0.25, 0)
		var body_known := _body_knowledge(String(probe.vision), String(probe.audio))
		probe_label.text = "CHALLENGE PROBE %s  ·  vision %s · audio %s\nbody %s%s" % [String(probe.challenge_id).substr(0, 12), probe.vision, probe.audio,
			String(body_known.status).to_upper(), "  ◎ in cone" if bool(probe.get("in_cone", false)) else ""]
		probe_label.modulate = Arena.COLOR_PROBE
		mesh.surface_set_color(Color(1.0, 0.6, 0.1, 0.8))
		mesh.surface_add_vertex(eye)
		mesh.surface_add_vertex(probe_pos + Vector3(0, Arena.CHEST_HEIGHT, 0))
	else:
		probe_node.visible = false
		probe_label.visible = false
	# Sound rings: a flat circle around each recent server sound, fading with age.
	for sound in frame.get("sounds", []):
		var at := Arena.from_v3(sound.pos)
		var age := float(sound.get("age_ms", 0)) / 500.0
		var radius := 0.6 + 2.0 * age
		mesh.surface_set_color(Color(1.0, 0.9, 0.3, 0.9 * (1.0 - age)))
		for step in 24:
			var a := TAU * step / 24.0
			var b := TAU * (step + 1) / 24.0
			mesh.surface_add_vertex(at + Vector3(cos(a) * radius, 0.05, sin(a) * radius))
			mesh.surface_add_vertex(at + Vector3(cos(b) * radius, 0.05, sin(b) * radius))
	mesh.surface_end()
	var tags := []
	for id in markers:
		if markers[id].label.visible:
			tags.append({"label": markers[id].label, "anchor": markers[id].anchor})
	if probe_label.visible:
		tags.append({"label": probe_label, "anchor": probe_anchor})
	_place_tags(tags)


## Project each tag to the screen just above its body, then push any tag that would cover another one upward,
## so the tags stack when bodies line up behind each other instead of writing over each other.
func _place_tags(tags: Array) -> void:
	var view := camera.get_viewport().get_visible_rect().size
	var top_limit := 28.0 + 27.0 * overlay.get_line_count() + 6.0  # under the actor label, which nothing may cover
	var placed: Array[Rect2] = []
	var rows := []
	for tag in tags:
		var label: Label = tag.label
		if camera.is_position_behind(tag.anchor):
			label.visible = false
			continue
		label.reset_size()
		var foot: Vector3 = tag.anchor - Vector3(0, Arena.BODY_HEIGHT + 0.25, 0)
		rows.append({"label": label, "at": camera.unproject_position(tag.anchor), "foot": camera.unproject_position(foot)})
	rows.sort_custom(func(a, b): return a.at.y > b.at.y)  # the nearest to the bottom of the pane first
	for row in rows:
		var label: Label = row.label
		var size: Vector2 = label.size
		var x := clampf(row.at.x - size.x / 2.0, 4.0, maxf(4.0, view.x - size.x - 4.0))
		var rect := Rect2(Vector2(x, row.at.y - size.y), size)
		var down := false  # a stack that would reach the actor label continues under the body instead
		var moved := true
		var guard := 0
		while moved and guard < 24:
			guard += 1
			moved = false
			if not down and rect.position.y < top_limit:
				down = true
				rect.position.y = row.foot.y + 4.0
				moved = true
				continue
			for other in placed:
				if rect.intersects(other.grow(3.0)):
					rect.position.y = (other.end.y + 4.0) if down else (other.position.y - size.y - 4.0)
					moved = true
		rect.position.y = clampf(rect.position.y, 0.0, maxf(0.0, view.y - size.y))
		placed.append(rect)
		label.position = rect.position


func _marker_for(id: String) -> Dictionary:
	if not markers.has(id):
		markers[id] = {"body": _ghost(Arena.COLOR_ENEMY, true), "label": _tag(), "anchor": Vector3.ZERO,
			"wire": _ghost(Color(1.0, 0.95, 0.4, 0.35), true), "picture": _ghost(Color(0.4, 0.9, 1.0, 0.35), true)}
	return markers[id]


## A body drawn through walls (no depth test), so the server view shows what the player view hides.
func _ghost(color: Color, through_walls: bool) -> MeshInstance3D:
	var node := Arena.body_mesh(color)
	var material := StandardMaterial3D.new()
	material.albedo_color = color if color.a < 1.0 else Color(color, 0.55)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.no_depth_test = through_walls
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	node.material_override = material
	world.add_child(node)
	return node


## A body's tag: two lines of screen text over the security pane, placed by _place_tags each frame.
func _tag() -> Label:
	var label := Label.new()
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.9))
	label.add_theme_constant_override("outline_size", 5)
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	tag_layer.add_child(label)
	return label


## A persistent label over the security view: who is at the keyboard and which scripted stand-in, if any, holds
## the aim. Large, so a clip of the pane cannot be mistaken for a person or for real cheat software.
func _build_overlay(viewport: Viewport) -> void:
	var layer := CanvasLayer.new()
	layer.name = "ActorOverlay"
	if viewport != null:
		viewport.add_child(layer)
	else:
		add_child(layer)
	tag_layer = layer
	overlay = Label.new()
	overlay.set_anchors_and_offsets_preset(Control.PRESET_TOP_WIDE, Control.PRESET_MODE_MINSIZE, 14)
	overlay.offset_top = 28
	overlay.offset_bottom = 118
	overlay.autowrap_mode = TextServer.AUTOWRAP_WORD
	overlay.add_theme_font_size_override("font_size", 20)
	overlay.add_theme_color_override("font_color", Color(1.0, 0.72, 0.25))
	overlay.add_theme_color_override("font_outline_color", Color(0, 0, 0, 0.9))
	overlay.add_theme_constant_override("outline_size", 6)
	layer.add_child(overlay)


## The actor line for a frame: the scenario's own label while a stand-in holds the aim, or who is at the keyboard.
func _actor_line(frame: Dictionary) -> String:
	var kind := String(frame.get("standin", ""))
	var open := bool(frame.get("standin_open", kind != "")) if not bool(frame.get("replay", false)) else kind != ""
	var actor := String(frame.get("actor", latest.get("actor", "")))
	if kind != "" and open and actor != "":
		return actor
	if String(frame.get("subject_input", latest.get("subject_input", "human"))) == "autopilot":
		return "HONEST AUTOPILOT AT THE KEYBOARD · SCRIPTED · NOT A HUMAN"
	return "A PERSON AT THE KEYBOARD · no stand-in holds the aim"


# Panels.


func _theme() -> Theme:
	var found := Theme.new()
	var font := SystemFont.new()
	font.font_names = PackedStringArray(FONT_NAMES)
	found.default_font = font
	found.default_font_size = 15
	var box := StyleBoxFlat.new()
	box.bg_color = Color(0.07, 0.08, 0.10, 0.96)
	box.border_color = Color(0.2, 0.26, 0.32)
	box.set_border_width_all(1)
	box.set_corner_radius_all(6)
	box.set_content_margin_all(10)
	found.set_stylebox("panel", "PanelContainer", box)
	return found


func build_panels(parent: Control) -> void:
	panel_root = parent
	parent.theme = theme
	var box := VBoxContainer.new()
	box.set_anchors_preset(Control.PRESET_FULL_RECT)
	box.add_theme_constant_override("separation", 4)
	parent.add_child(box)
	strip = _line_label(box, 17)
	badges = _line_label(box, 15)
	var columns := HBoxContainer.new()
	columns.size_flags_vertical = Control.SIZE_EXPAND_FILL
	columns.add_theme_constant_override("separation", 6)
	box.add_child(columns)
	if mode == "demo":
		_column(columns, [["knowledge", "SERVER KNOWLEDGE"]], 0.85)
		_column(columns, [["detectors", "DETECTORS"]], 1.05)
		_column(columns, [["case", "CASE  ·  fpsdet's decision and findings"]], 1.2)
		_column(columns, [["scenario", "SCENARIO"]], 0.95)
	else:
		_column(columns, [["player", "PLAYER STATE"], ["knowledge", "SERVER KNOWLEDGE"]], 0.95)
		_column(columns, [["detectors", "DETECTOR ELIGIBILITY"]], 1.05)
		_column(columns, [["case", "CASE"], ["findings", "FINDINGS"]], 1.15)
		_column(columns, [["tab", "PACKET / GRAPH"]], 1.15)
	if card_box == null:
		_build_card()


## One line of text that never wraps into the panels below it.
func _line_label(parent: Control, size: int) -> RichTextLabel:
	var label := RichTextLabel.new()
	label.bbcode_enabled = true
	label.scroll_active = false
	label.clip_contents = true
	label.autowrap_mode = TextServer.AUTOWRAP_OFF
	label.custom_minimum_size = Vector2(0, size + 12)
	label.add_theme_font_size_override("normal_font_size", size)
	parent.add_child(label)
	return label


## The scenario card: a box over the 3D views for eight seconds when a scenario starts, in its own layer, so it
## never sits on top of the panels.
func _build_card() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 10
	add_child(layer)
	card_box = PanelContainer.new()
	card_box.theme = theme
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.05, 0.06, 0.08, 0.97)
	style.border_color = Color(1.0, 0.62, 0.11)
	style.set_border_width_all(2)
	style.set_corner_radius_all(8)
	style.set_content_margin_all(16)
	card_box.add_theme_stylebox_override("panel", style)
	card_box.custom_minimum_size = Vector2(900, 0)
	card_box.visible = false
	layer.add_child(card_box)
	card = RichTextLabel.new()
	card.bbcode_enabled = true
	card.fit_content = true
	card.scroll_active = false
	card.custom_minimum_size = Vector2(860, 0)
	card.add_theme_font_size_override("normal_font_size", 17)
	card_box.add_child(card)


func _column(parent: HBoxContainer, items: Array, weight: float) -> void:
	var column := VBoxContainer.new()
	column.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	column.size_flags_stretch_ratio = weight
	column.add_theme_constant_override("separation", 6)
	parent.add_child(column)
	for item in items:
		var holder := PanelContainer.new()
		holder.size_flags_vertical = Control.SIZE_EXPAND_FILL
		column.add_child(holder)
		var inner := VBoxContainer.new()
		holder.add_child(inner)
		var title := Label.new()
		title.text = item[1]
		title.add_theme_font_size_override("font_size", 13)
		title.add_theme_color_override("font_color", Color(0.55, 0.68, 0.78))
		inner.add_child(title)
		panel_titles[item[0]] = title
		if item[0] == "tab":
			graph_view = GraphView.new()
			graph_view.custom_minimum_size = Vector2(0, 170)
			graph_view.visible = false
			inner.add_child(graph_view)
			timeline_view = TimelineView.new()
			timeline_view.custom_minimum_size = Vector2(0, 64)
			timeline_view.visible = false
			inner.add_child(timeline_view)
		var text := RichTextLabel.new()
		text.bbcode_enabled = true
		text.scroll_active = true
		text.size_flags_vertical = Control.SIZE_EXPAND_FILL
		text.add_theme_font_size_override("normal_font_size", 16 if mode == "demo" else 14)
		inner.add_child(text)
		panels[item[0]] = text


func _color(word: String) -> Color:
	return Color.html("#" + String(COLORS.get(word, "9aa4b2")))


func _chip(word: String, label: String = "") -> String:
	var shown := label if label != "" else word
	return "[bgcolor=#%s][color=#%s] %s [/color][/bgcolor]" % [COLORS.get(word, "9aa4b2"), INK, shown.to_upper() if mode == "demo" else shown]


func _dim(text: String) -> String:
	return "[color=#8b95a3]%s[/color]" % text


## The actor, as a line of bold orange text that wraps cleanly (a chip cannot).
func _actor_text(text: String) -> String:
	return "[color=#ff9f1c][b]%s[/b][/color]" % text


## The actor in a few words, for the badge row; the full label is over the security pane.
func _actor_short(frame: Dictionary) -> String:
	var kind := String(frame.get("standin", ""))
	var open := bool(frame.get("standin_open", kind != "")) if not bool(frame.get("replay", false)) else kind != ""
	if kind != "" and open:
		return ("CONTROLLED FOLLOWER" if kind == "follower" else ("HONEST-STYLE STAND-IN" if kind == "holder" else "SCRIPTED STAND-IN: " + kind)) + " · NOT A HUMAN"
	if String(frame.get("subject_input", latest.get("subject_input", "human"))) == "autopilot":
		return "HONEST AUTOPILOT · SCRIPTED · NOT A HUMAN"
	return "A PERSON AT THE KEYBOARD"


func _subject_case() -> Dictionary:
	if replaying:
		var at := int(_frame().get("t_ms", 0))
		var rows: Array = replay_case.get("case_timeline", [])
		var chosen := {}
		for row in rows:
			if int(row.t_ms) <= at:
				chosen = row
		var full: Dictionary = replay_case.get("subject", {}) if replay_case.has("subject") and replay_case.subject != null else {}
		if chosen.is_empty():
			return {}
		# The case as it stood then: the timeline's decision and findings, the full case's detail for those findings.
		var findings := []
		for finding in full.get("findings", []):
			if chosen.observations.has(finding.observation_id):
				findings.append(finding)
		return {"decision": chosen.decision, "automated_action": "none", "recommended_action": full.get("recommended_action", ""), "findings": findings,
			"eligibility_rollup": chosen.eligibility, "challenges": chosen.get("challenges", []), "packet": full.get("packet", {}), "graph": full.get("graph", {}),
			"provenance": full.get("provenance", {}), "knowledge": full.get("knowledge", {}), "fusion": full.get("fusion"), "reasons": full.get("reasons", []),
			"notes": full.get("notes", []), "checks": chosen.kinds, "at_ms": chosen.t_ms, "events": chosen.get("events", 0), "replay": true,
			"speed_cadence": full.get("speed_cadence", {})}
	var subject = live.get("subject")
	return subject if typeof(subject) == TYPE_DICTIONARY else {}


func _refresh_panels() -> void:
	var frame := _frame()
	var scenario_id := String(latest.get("scenario", Scenario.LOBBY))
	var spec: Dictionary = scenarios.get(scenario_id, Scenario.lobby())
	var subject := _subject_case()
	_refresh_strip(frame, spec, subject)
	if panels.has("player"):
		panels["player"].text = _player_text(frame)
	panels["knowledge"].text = _knowledge_text(frame, subject)
	panels["detectors"].text = _detectors_text(subject)
	panels["case"].text = _case_text(subject, spec)
	if panels.has("findings"):
		panels["findings"].text = _findings_text(subject)
	if panels.has("tab"):
		_refresh_tab(subject, frame)
	if panels.has("scenario"):
		panels["scenario"].text = _scenario_text(spec, frame, subject)
	var show_card := card_box != null and Time.get_ticks_msec() < card_until and scenario_id != Scenario.LOBBY and not replaying
	if card_box != null:
		card_box.visible = show_card
	if show_card:
		var text := _card_text(spec, "overlay")
		if card.text != text:
			card.text = text
		card_box.reset_size()
		# Low in the views, where there is only floor, clear of the player's HUD and of the actor label.
		var width := get_viewport().get_visible_rect().size.x
		var bottom := panel_root.global_position.y if panel_root != null and panel_root.global_position.y > 100 else 560.0
		card_box.position = Vector2((width - card_box.size.x) / 2.0, bottom - card_box.size.y - 16)


func _refresh_strip(frame: Dictionary, spec: Dictionary, subject: Dictionary) -> void:
	var parts := ["[b]fpsdet ARENA[/b]", "[b]%s[/b]" % String(spec.get("title", "Lobby"))]
	if replaying:
		parts.append(_chip("experimental", "REPLAY  %s  %d ms  %s" % [replay_match, int(frame.get("t_ms", 0)), "▶" if replay_playing else "❚❚"]))
	else:
		parts.append("t %5.1f s" % (float(latest.get("t_ms", 0)) / 1000.0))
		parts.append(_dim(String(latest.get("match_id", ""))))
		parts.append(String(latest.get("phase", "")))
	strip.text = "   ".join(parts)
	var chips := [_chip("experimental", _actor_short(frame))]
	var badge = live.get("live_vs_offline") if not replaying else replay_case.get("live_vs_offline")
	if badge != null:
		chips.append(_chip(String(badge), "OFFLINE REPLAY: " + String(badge).to_upper()))
	var ai: Dictionary = status.get("ai", {})
	if not ai.is_empty():
		chips.append(_chip("on" if bool(ai.get("enabled", false)) else "off", "AI brief: " + ("on" if bool(ai.get("enabled", false)) else "off")))
	if bool(spec.get("experimental", false)) and subject.get("decision") == "review":
		chips.append(_chip("experimental", "EXPERIMENTAL: NOT PRODUCTION QUALIFIED"))
	if not accepted:
		chips.append(_chip("conflict", "operator link not accepted"))
	badges.text = "  ".join(chips)


func _player_text(frame: Dictionary) -> String:
	var s: Dictionary = frame.get("subject", {})
	if s.is_empty():
		return _dim("waiting for the server")
	var cmd: Dictionary = s.get("cmd", {})
	var lines := []
	lines.append("%s  %s  %s" % [s.get("id", ""), _dim("entity " + String(s.get("entity", ""))), _dim(String(s.get("weapon", "")))])
	lines.append("pos %s" % _fmt3(s.get("pos", [])))
	lines.append("speed %5.2f m/s  cap %.2f%s" % [float(s.get("speed", 0)), float(s.get("cap", 0)), "  " + _chip("review", "x%.1f FAULT" % float(s.get("speed_multiplier", 1))) if float(s.get("speed_multiplier", 1)) != 1.0 else ""])
	lines.append("yaw %7.2f  pitch %6.2f  kick offset %5.2f" % [float(s.get("yaw", 0)), float(s.get("pitch", 0)), float(s.get("recoil_offset", 0))])
	lines.append("aim %s" % _fmt3(s.get("aim", [])))
	lines.append("ammo %s/30%s  health %s  shots %s  accepted %s  hits %s" % [s.get("ammo", ""), " reloading" if bool(s.get("reloading", false)) else "", s.get("health", ""), s.get("shots", 0), s.get("accepted", 0), s.get("hits", 0)])
	lines.append("cycle %s ticks%s  kick %.2f  command %.2f  floor %.2f%s" % [s.get("cycle_ticks", 6), "  " + _chip("review", "FAULT") if int(s.get("cycle_ticks", 6)) != 6 else "",
		float(s.get("last_kick", 0)), float(s.get("last_compensation", 0)), float(s.get("floor_deg", 0)),
		"  " + _chip("review", "x%.1f FAULT" % float(s.get("recoil_multiplier", 1))) if float(s.get("recoil_multiplier", 1)) != 1.0 else ""])
	if not cmd.is_empty():
		lines.append(_dim("client command  move %s  yaw %.1f  pitch %.1f  fire %s  #%s" % [cmd.get("move", []), float(cmd.get("yaw", 0)), float(cmd.get("pitch", 0)), cmd.get("fire", false), cmd.get("seq", 0)]))
	if bool(s.get("override", false)):
		lines.append(_actor_text("STAND-IN HOLDS THE AIM: " + String(frame.get("standin", "")) + " · scripted · not a human · not real cheat software"))
	return "\n".join(lines)


func _knowledge_text(frame: Dictionary, subject: Dictionary) -> String:
	var lines := []
	var declared: Array = table.get("required", ["vision", "audio"])
	var audio_on := bool(frame.get("audio_query", true))
	if mode != "demo":
		lines.append(_dim("declared channels: %s   grace %s ms   audio query %s" % [", ".join(declared), table.get("hidden_grace_ms", "?"), _chip("on" if audio_on else "unchecked", "on" if audio_on else "OFF")]))
	elif not audio_on:
		lines.append(_chip("unchecked", "AUDIO QUERY OFF") + _dim("  the server does not listen, and says so"))
	for enemy in frame.get("enemies", []):
		var vision := String(enemy.get("vision", "unchecked"))
		var audio := String(enemy.get("audio", "unchecked"))
		var since = enemy.get("since_ms")
		var known := _enemy_knowledge(vision, audio, since)
		var cone := "  ◎ in cone" if bool(enemy.get("in_cone", false)) else ""
		lines.append("[b]%s[/b]%s" % [enemy.id, cone])
		lines.append("  vision %s  audio %s" % [_chip(vision), _chip(audio)])
		lines.append("  →  %s %s" % [_chip(String(known.status)), _dim("(%s)" % known.cause)])
		if mode != "demo":
			lines.append(_dim("  perceived %s" % ("never" if since == null else ("%d ms ago" % int(since)))))
	var probe = frame.get("probe")
	if probe != null and typeof(probe) == TYPE_DICTIONARY:
		var body := _body_knowledge(String(probe.vision), String(probe.audio))
		lines.append("[b]challenge body[/b]%s" % ("  ◎ in cone" if bool(probe.get("in_cone", false)) else ""))
		lines.append("  vision %s  audio %s" % [_chip(String(probe.vision)), _chip(String(probe.audio))])
		lines.append("  →  %s %s" % [_chip(String(body.status)), _dim("(%s)" % body.cause)])
	if frame.get("enemies", []).is_empty() and probe == null:
		lines.append(_dim("no enemy in play"))
	lines.append(_dim("unchecked ≠ absent · missing telemetry → unknown → abstain"))
	var fpsdet: Dictionary = subject.get("knowledge", {}) if not subject.is_empty() else {}
	if mode != "demo" and not fpsdet.is_empty():
		lines.append(_dim("fpsdet, over the subject's events so far:"))
		for enemy in fpsdet:
			var row: Dictionary = fpsdet[enemy]
			var counts := []
			for key in row.get("statuses", {}):
				counts.append("%s %s" % [key, row.statuses[key]])
			lines.append("  %s  %s  %s" % [enemy, _chip(String(row.get("majority", "?"))), _dim(", ".join(counts))])
	return "\n".join(lines)


func _detectors_text(subject: Dictionary) -> String:
	if subject.is_empty():
		return _dim("waiting for fpsdet (the scorer runs once a second)")
	var eligibility: Dictionary = subject.get("eligibility", {})
	var rollup: Dictionary = subject.get("eligibility_rollup", {})
	var kinds: Array = eligibility.keys() if not eligibility.is_empty() else rollup.keys()
	var counts := {}
	for finding in subject.get("findings", []):
		counts[finding.kind] = int(counts.get(finding.kind, 0)) + 1
	# Grouped by status: the chip, then the detectors under it, the ones that fired in bold.
	var groups := {}
	for kind in kinds:
		var entry: Dictionary = eligibility.get(kind, {})
		var statusword := String(entry.get("rollup", rollup.get(kind, "?")))
		var shown: String = ("[b]%s ×%d[/b]" % [kind, int(counts[kind])]) if counts.has(kind) else String(kind)
		if mode != "demo" and entry.get("role", "") != "":
			shown += _dim(" " + String(entry.role))
		groups[statusword] = groups.get(statusword, []) + [shown]
	var lines := []
	var quiet := []
	for statusword in ["eligible", "baseline_too_thin", "insufficient_samples", "conflict", "telemetry_unavailable", "disabled", "not_applicable"]:
		if not groups.has(statusword):
			continue
		if mode == "demo" and statusword in ["telemetry_unavailable", "disabled", "not_applicable"]:
			quiet.append(_dim("    %s: %s" % [statusword.replace("_", " "), " · ".join(groups[statusword])]))
			continue
		lines.append(_chip(statusword))
		lines.append("    " + " · ".join(groups[statusword]))
	if not quiet.is_empty():
		lines.append(_dim("could not run here"))
		lines.append_array(quiet)
	lines.append(_dim("a check that cannot run is never a zero"))
	return "\n".join(lines)


func _case_text(subject: Dictionary, spec: Dictionary) -> String:
	if subject.is_empty():
		return _dim("no case yet")
	var lines := []
	var decision := String(subject.get("decision", ""))
	var head := "decision %s   [b]automated_action: %s[/b]" % [_chip(decision), _chip("none", String(subject.get("automated_action", "none")))]
	if mode != "demo":
		head += "   recommended " + String(subject.get("recommended_action", ""))
	lines.append(head)
	if decision == "insufficient_data":
		lines.append("[b]INSUFFICIENT DATA: This is not a clean verdict.[/b] Nothing was comparable, and the telemetry is not enough for a stronger conclusion. fpsdet writes clean only after a number was compared against a thick enough cohort.")
	if bool(spec.get("experimental", false)) and decision == "review":
		lines.append(_chip("experimental", "EXPERIMENTAL CHALLENGE RESULT · NOT PRODUCTION QUALIFIED"))
		if String(spec.get("id", "")) == "angle_hold_false_positive":
			lines.append("geometric alignment is not the same thing as responding to hidden information")
	var fusion = subject.get("fusion")
	if fusion != null and typeof(fusion) == TYPE_DICTIONARY:
		lines.append("native fpsdet result %s  →  external evidence: %s  →  final %s" % [_chip(String(fusion.native_decision)),
			("%d adverse fictional provider record%s" % [int(fusion.signals), "" if int(fusion.signals) == 1 else "s"]) if int(fusion.signals) else "context only", _chip(String(fusion.decision))])
		if String(fusion.decision) != String(fusion.native_decision):
			lines.append("[b]EXTERNAL EVIDENCE CAUSED THIS WATCH.[/b] Native fpsdet evidence did not create it. External evidence cannot create a review.")
		for finding in subject.get("findings", []):
			if String(finding.get("source", "")) == "external":
				var auth: Dictionary = finding.get("evidence", {}).get("authenticity", {})
				lines.append(_dim("record %s: signature %s. A signature verifies origin, not truth." % [finding.get("key", ""), String(auth.get("status", "unsigned"))]))
	var reasons: Array = subject.get("reasons", [])
	for reason in reasons.slice(0, 2 if mode == "demo" else reasons.size()):
		lines.append("• " + String(reason))
	if mode == "demo" and reasons.size() > 2:
		lines.append(_dim("• and %d more" % (reasons.size() - 2)))
	if mode == "demo":
		var findings: Array = subject.get("findings", [])
		for finding in findings:
			var source := " · external record, not fpsdet's own" if String(finding.get("source", "")) == "external" else ""
			lines.append("%s [b]%s[/b]%s  %s" % [_chip(String(finding.role)), finding.kind, _dim(source), _dim(String(finding.get("summary", "")))])
		if findings.is_empty():
			lines.append(_dim("no observation: a detector that could run and found nothing writes none"))
	var problems: Array = live.get("problems", []) if not replaying else replay_case.get("problems", [])
	if bool(live.get("final", false)) or replaying:
		lines.append(_chip("identical" if problems.is_empty() else "mismatch", "EXPECTED == ACTUAL" if problems.is_empty() else "EXPECTED != ACTUAL"))
		for problem in problems:
			lines.append(_dim("  " + String(problem)))
	if subject.has("at_ms"):
		lines.append(_dim("as it stood at %d ms, on %d events" % [int(subject.at_ms), int(subject.get("events", 0))]))
	elif not live.is_empty():
		lines.append(_dim("scored %d events in %.1f ms, up to t %d ms" % [int(live.get("events", 0)), float(live.get("scoring_ms", 0)), int(live.get("scored_at_t_ms", 0))]))
	var brief := String(live.get("ai_brief", "")) if not replaying else String(replay_case.get("ai_brief", ""))
	if brief != "":
		var ai: Dictionary = live.get("ai", {})
		lines.append("[b]AI reviewer brief[/b] " + _dim("summarizes evidence; creates none; decision unchanged: %s" % ai.get("decision_unchanged", "")))
		lines.append(brief)
	return "\n".join(lines)


func _findings_text(subject: Dictionary) -> String:
	if subject.is_empty():
		return ""
	var findings: Array = subject.get("findings", [])
	if findings.is_empty():
		return _dim("no observation. A detector that could run and found nothing writes none; one that could not run says so above.")
	var lines := []
	for finding in findings:
		var source := "external record, not fpsdet's own evidence" if String(finding.get("source", "")) == "external" else String(finding.family)
		lines.append("[b]%s[/b] %s %s  %s" % [finding.kind, _chip(String(finding.role)), _dim(source), finding.get("key", "")])
		lines.append("    %s" % finding.get("summary", ""))
		if finding.kind == "speed":
			var cadence: Dictionary = subject.get("speed_cadence", {})
			if not cadence.is_empty():
				lines.append(_dim("    over-cap samples: %d   movement sample interval: %d ms (as emitted)   approx observed run duration: %d ms   the bar counts samples, not milliseconds" % [
					int(finding.get("evidence", {}).get("longest_run", 0)), int(cadence.get("interval_ms", 0)), int(cadence.get("run_ms", 0))]))
		lines.append(_dim("    %s  matches %s" % [finding.observation_id, ", ".join(finding.get("match_ids", []))]))
	return "\n".join(lines)


func _challenge_text(frame: Dictionary, subject: Dictionary) -> String:
	var lines := []
	var probe = frame.get("probe")
	var planned: Array = latest.get("planned", [])
	if probe == null and planned.is_empty():
		return _dim("no challenge planned in this match")
	if probe != null and typeof(probe) == TYPE_DICTIONARY:
		var body := _body_knowledge(String(probe.vision), String(probe.audio))
		lines.append("id %s   version %s" % [probe.challenge_id, probe.version])
		lines.append("window %s → %s ms   now %s ms" % [probe.window[0], probe.window[1], frame.get("t_ms", 0)])
		lines.append("commitment %s" % String(probe.commitment).substr(0, 30) + "…")
		lines.append("runtime vision %s   runtime audio %s   body %s" % [_chip(String(probe.vision)), _chip(String(probe.audio)), _chip(String(body.status))])
		lines.append("in cone now %s   tracked ticks %s (%.0f ms)" % [probe.get("in_cone", false), probe.get("tracked_ticks", 0), float(probe.get("tracked_ticks", 0)) * Arena.TICK_MS])
	for row in planned:
		if probe == null or row.challenge_id != probe.challenge_id:
			lines.append(_dim("%s window %s → %s ms  %s" % [row.challenge_id, row.window[0], row.window[1], "ran" if row.ran else "pending"]))
	for result in subject.get("challenges", []):
		lines.append("fpsdet: %s  verification %s  eligible %s  tracked %s  verified %s  %.0f ms%s" % [_chip(String(result.get("status", ""))), result.get("verification", "plan"),
			result.get("eligible_samples", 0), result.get("tracked_samples", 0), result.get("verified_samples", 0), float(result.get("total_ms", 0)),
			"  cause " + String(result.cause) if result.get("cause") else ""])
	lines.append(_chip("experimental", "challenge reviews are experimental and not production qualified"))
	return "\n".join(lines)


## The demo's fourth panel: the card's essentials, and the challenge when one is planned.
func _scenario_text(spec: Dictionary, frame: Dictionary, subject: Dictionary) -> String:
	var lines := [_card_text(spec, "panel")]
	var probe = frame.get("probe")
	if (probe != null and typeof(probe) == TYPE_DICTIONARY) or not subject.get("challenges", []).is_empty():
		lines.append("")
		lines.append("[b]ACTIVE CHALLENGE[/b]")
		lines.append(_challenge_text(frame, subject))
	return "\n".join(lines)


## The scenario card. ``form`` is "overlay" (the box over the views at scenario start), "panel" (the demo's
## scenario panel: the essentials) or "full" (the developer tab: everything, with the caveats).
func _card_text(spec: Dictionary, form: String) -> String:
	var card_spec: Dictionary = spec.get("card", {})
	if card_spec.is_empty():
		return _dim(String(spec.get("instructions", "")))
	var lines := []
	if form != "panel":
		lines.append("[font_size=22][b]%s[/b][/font_size]" % spec.get("title", ""))
	lines.append(_actor_text(String(spec.get("actor", ""))))
	if form == "overlay":
		lines.append(String(spec.get("instructions", "")))
	lines.append("[b]WHAT THIS TESTS[/b]  %s" % card_spec.get("what_this_tests", ""))
	if form == "full":
		lines.append("[b]WHAT THE PLAYER CAN KNOW[/b]  %s" % card_spec.get("player_can_know", ""))
		lines.append("[b]WHAT THE SERVER KNOWS[/b]  %s" % card_spec.get("server_knows", ""))
	lines.append("[b]EXPECTED FPSDET BEHAVIOR[/b]  %s" % card_spec.get("expected_fpsdet_behavior", ""))
	if form == "full":
		lines.append("[b]WHAT WOULD MAKE THIS INVALID[/b]  %s" % card_spec.get("invalid_if", ""))
		for caveat in spec.get("caveats", []):
			lines.append(_dim("caveat: " + String(caveat)))
	return "\n".join(lines)


func _refresh_tab(subject: Dictionary, frame: Dictionary) -> void:
	var names := ["PACKET / GRAPH", "TELEMETRY (NDJSON)", "INTEGRATION: HOW THIS MAPS TO YOUR GAME", "PERFORMANCE", "ACTIVE CHALLENGE", "SCENARIO CARD / REPLAY"]
	panel_titles["tab"].text = names[tab] + _dim_plain("   (tab cycles)")
	graph_view.visible = tab == 0 and not subject.is_empty()
	timeline_view.visible = tab == 5 and replaying
	var text: RichTextLabel = panels["tab"]
	match tab:
		0:
			graph_view.graph = subject.get("graph", {}) if not subject.is_empty() else {}
			graph_view.queue_redraw()
			text.text = _packet_text(subject)
		1:
			text.text = _telemetry_text()
		2:
			text.text = _integration_text()
		3:
			text.text = _performance_text()
		4:
			text.text = _challenge_text(frame, subject)
		_:
			var spec: Dictionary = scenarios.get(String(latest.get("scenario", Scenario.LOBBY)), Scenario.lobby())
			if replaying:
				timeline_view.rows = replay_rows
				timeline_view.case_timeline = replay_case.get("case_timeline", [])
				timeline_view.index = replay_index
				timeline_view.queue_redraw()
				text.text = _replay_text()
			else:
				text.text = _card_text(spec, "full") + "\n" + _dim("r replays the last finished match: ← → scrub, shift for 1 s, space plays, home/end, r again to leave")


func _dim_plain(text: String) -> String:
	return text


func _packet_text(subject: Dictionary) -> String:
	if subject.is_empty():
		return _dim("no case yet")
	var packet: Dictionary = subject.get("packet", {})
	var graph: Dictionary = subject.get("graph", {})
	var provenance: Dictionary = subject.get("provenance", {})
	var inputs: Dictionary = provenance.get("inputs", {}) if provenance.get("inputs") != null else {}
	var lines := []
	lines.append("packet %s %s" % [packet.get("recipe", ""), _chip("identical" if packet.get("status") == "complete" else "mismatch", String(packet.get("status", "")))])
	lines.append("  digest %s" % packet.get("digest", "incomplete"))
	lines.append("graph %s  nodes %d  edges %d" % [graph.get("recipe", ""), int(graph.get("node_count", 0)), int(graph.get("edge_count", 0))])
	lines.append("  digest %s" % graph.get("digest", ""))
	lines.append("detector %s" % provenance.get("detector", ""))
	lines.append("profile  %s" % provenance.get("profile", ""))
	lines.append("inputs   %s  %d events  %s" % [inputs.get("recipe", ""), int(inputs.get("events", 0)), inputs.get("digest", "")])
	var independence: Dictionary = graph.get("independence", {})
	if not independence.is_empty():
		lines.append(_dim("supporting %s  native families %s  external sources %s" % [independence.get("supporting", 0), independence.get("native_families", []), independence.get("external_sources", [])]))
	var problems = live.get("case_problems") if not replaying else replay_case.get("case_problems")
	if problems != null and typeof(problems) == TYPE_DICTIONARY:
		var own: Array = problems.get(String(subject.get("player_id", "arena-player")), [])
		lines.append("verification %s" % _chip("identical" if own.is_empty() else "mismatch", "packet and graph verify" if own.is_empty() else "; ".join(own)))
	lines.append(_dim("a digest proves content, not origin; the same inputs give the same packet"))
	return "\n".join(lines)


func _telemetry_text() -> String:
	var lines := []
	lines.append(_dim("filter: %s (f cycles)   c copies the last line   fields as the server wrote them" % filter))
	var shown := 0
	for index in range(tail_lines.size() - 1, -1, -1):
		var line := String(tail_lines[index])
		if filter == "movement" and '"event_type":"movement"' not in line:
			continue
		if filter == "shot" and '"event_type":"shot"' not in line:
			continue
		if filter == "challenge" and '"challenge_id"' not in line:
			continue
		if filter == "knowledge" and '"vision_state"' not in line and '"challenge_vision_state"' not in line:
			continue
		lines.append(line.replace("[", "［").replace("]", "］"))
		shown += 1
		if shown >= 9:
			break
	if shown == 0:
		lines.append(_dim("nothing emitted yet for this match"))
	return "\n".join(lines)


func _integration_text() -> String:
	return "\n".join([
		"your dedicated server → movement + shot telemetry → visibility / audio queries → fpsdet → case JSON → human reviewer",
		"",
		"[b]Level 1  basic rules[/b]  needs movement, weapon, timing and recoil fields  →  speed, fire rate, recoil floor, mirror",
		"[b]Level 2  human baselines[/b]  needs enough players, a frozen trusted baseline, a rank band if you have one  →  accuracy, headshots, distance, geometry",
		"[b]Level 3  knowledge checks[/b]  needs authoritative visibility and audio queries, recent perception where you have it  →  hidden mover, quiet aim, wire",
		"[b]Level 4  active challenges[/b]  needs a custom server/client integration, secret planning, runtime channel proof  →  occluded motion replay (experimental)",
		"",
		_dim("Start with level 1: an afternoon. Omit a field and its check turns off. A wrong field frames players. This arena emits levels 1, 3 and 4; it has no population for level 2."),
		_dim("Server code: godot/scripts/arena_server.gd (_emit, _observe, _vision, _audio, _fire). Fields: docs/integration.md."),
	])


func _performance_text() -> String:
	var perf: Dictionary = latest.get("perf", {})
	var events: Dictionary = latest.get("events", {})
	var lines := []
	lines.append("server tick %s µs mean  %s µs max  (budget 16667 µs at 60 Hz)" % [perf.get("tick_us_mean", 0), perf.get("tick_us_max", 0)])
	lines.append("knowledge queries %s µs/tick  %s rays/tick   challenge %s µs/probe tick" % [perf.get("knowledge_us_per_tick", 0), perf.get("rays_per_tick", 0), perf.get("challenge_us_per_probe_tick", 0)])
	lines.append("telemetry %s µs/tick   operator feed %s µs/tick" % [perf.get("telemetry_us_per_tick", 0), perf.get("feed_us_per_tick", 0)])
	lines.append("events %s lines  %s bytes  %s B/s   snapshots %s B/s   feed %s B/s" % [events.get("lines", 0), events.get("bytes", 0), perf.get("event_bytes_per_s", 0), perf.get("snapshot_bytes_per_s", 0), perf.get("feed_bytes_per_s", 0)])
	lines.append("fpsdet scoring %s ms per pass (%s events), every %s s   client fps %.0f   this window fps %d" % [status.get("scoring_ms", live.get("scoring_ms", 0)), live.get("events", 0), status.get("live_every_s", 1), client_fps, Engine.get_frames_per_second()])
	var verdicts: Dictionary = perf.get("verdicts", {})
	if not verdicts.is_empty():
		lines.append(_dim("probe verdicts so far: vision %s  audio %s" % [verdicts.get("vision", {}), verdicts.get("audio", {})]))
	var proof: Dictionary = status.get("pixel_proof", {})
	if not proof.is_empty():
		lines.append("[b]PLAYER PIXEL PROOF[/b] (qualification, this code)  hidden: %s challenge pixels in %s checks   visible control: %s changed pixels   %s" % [
			proof.get("hidden_pixels", "?"), proof.get("hidden_checks", "?"), proof.get("visible_pixels", "?"), _chip("identical" if bool(proof.get("pass", false)) else "mismatch", "PASS" if bool(proof.get("pass", false)) else "FAIL")])
	else:
		lines.append(_dim("player pixel proof: this code has not been qualified (run arena.py qualify)"))
	lines.append(_dim("state %s" % status.get("state", "")) + (_dim("  " + String(status.get("error", ""))) if status.has("error") else ""))
	return "\n".join(lines)


func _replay_text() -> String:
	var frame := _frame()
	var subject := _subject_case()
	var lines := []
	lines.append("[b]%s[/b]  t %d ms  %s   %s" % [replay_match, int(frame.get("t_ms", 0)), "playing" if replay_playing else "paused", _chip("experimental", _actor_line(frame))])
	lines.append("← → 100 ms   shift ← → 1 s   space play   home/end   r leaves replay")
	if not subject.is_empty():
		lines.append("case then: %s   findings %s" % [_chip(String(subject.get("decision", ""))), subject.get("checks", [])])
	var s: Dictionary = frame.get("subject", {})
	lines.append(_dim("player %s  aim %s  speed %s  stand-in %s" % [_fmt3(s.get("pos", [])), _fmt3(s.get("aim", [])), s.get("speed", 0), s.get("override", false)]))
	for enemy in frame.get("enemies", []):
		lines.append(_dim("%s pos %s  wire %s  picture %s  vision %s audio %s" % [enemy.id, _fmt3(enemy.pos), _fmt3(enemy.get("wire", [])), _fmt3(enemy.get("picture", [])), enemy.get("vision"), enemy.get("audio")]))
	var probe = frame.get("probe")
	if probe != null and typeof(probe) == TYPE_DICTIONARY:
		lines.append(_dim("probe %s  pos %s  vision %s audio %s  in cone %s" % [probe.challenge_id, _fmt3(probe.pos), probe.vision, probe.audio, probe.in_cone]))
	lines.append(_dim("every point of the case timeline is a real fpsdet scoring of the events up to that second"))
	return "\n".join(lines)


func _fmt3(v) -> String:
	if typeof(v) != TYPE_ARRAY or v.size() < 3:
		return "-"
	return "(%6.2f %5.2f %6.2f)" % [float(v[0]), float(v[1]), float(v[2])]


# The evidence graph, drawn from fpsdet's own nodes and edges: the case in the middle, this case's observations
# around it, everything else on the outer ring. Nothing is inferred; a node here is a node in the case.


class GraphView extends Control:
	var graph := {}
	const RELATION_COLORS := {"supports": Color(0.4, 0.9, 0.5), "about": Color(0.5, 0.6, 0.7), "derived_from": Color(1.0, 0.6, 0.2), "depends_on": Color(1.0, 0.4, 0.4),
		"occurred_in": Color(0.6, 0.6, 0.9), "provided_by": Color(0.9, 0.8, 0.3), "uses_telemetry_domain": Color(0.7, 0.5, 0.9), "compared_against": Color(0.3, 0.8, 0.9),
		"uses_history": Color(0.3, 0.8, 0.9), "names_partner": Color(0.9, 0.5, 0.7), "authenticated_by": Color(0.9, 0.9, 0.5), "belongs_to": Color(0.9, 0.9, 0.5)}

	func _draw() -> void:
		var nodes: Array = graph.get("nodes", [])
		var edges: Array = graph.get("edges", [])
		if nodes.is_empty():
			return
		var center := size / 2.0
		var places := {}
		var observations := []
		var others := []
		var case_id := ""
		for node in nodes:
			var kind := String(node.type)
			if kind == "case":
				case_id = node.id
			elif kind == "observation":
				observations.append(node)
			else:
				others.append(node)
		if case_id != "":
			places[case_id] = center
		var ring := minf(size.x, size.y) * 0.22
		for index in observations.size():
			var angle := TAU * index / maxf(observations.size(), 1) - PI / 2
			places[observations[index].id] = center + Vector2(cos(angle), sin(angle)) * ring
		var outer := minf(size.x, size.y) * 0.44
		for index in others.size():
			var angle := TAU * index / maxf(others.size(), 1) - PI / 2 + 0.3
			places[others[index].id] = center + Vector2(cos(angle) * 1.9, sin(angle)) * outer
		for edge in edges:
			if places.has(edge.source) and places.has(edge.target):
				draw_line(places[edge.source], places[edge.target], RELATION_COLORS.get(edge.relation, Color(0.5, 0.5, 0.5)), 1.5, true)
		var font := get_theme_default_font()
		for node in nodes:
			var at: Vector2 = places[node.id]
			var kind := String(node.type)
			var color := Color(0.85, 0.3, 0.3) if kind == "case" else (Color(0.95, 0.85, 0.4) if kind == "observation" else (Color(1.0, 0.6, 0.2) if kind == "challenge" else (Color(0.4, 0.7, 1.0) if kind == "player" else Color(0.6, 0.65, 0.7))))
			draw_circle(at, 7 if kind == "case" else 5, color)
			var label := String(node.id)
			if label.length() > 26:
				label = label.substr(0, 26) + "…"
			draw_string(font, at + Vector2(8, 4), label, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.8, 0.85, 0.9))
		var legend_y := 14
		for relation in ["supports", "about", "derived_from", "occurred_in", "depends_on"]:
			draw_rect(Rect2(6, legend_y - 8, 10, 10), RELATION_COLORS[relation])
			draw_string(font, Vector2(20, legend_y), relation, HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.6, 0.65, 0.7))
			legend_y += 14


# The replay scrubber: the match's time line, where each observation first appeared, and where the scrubber is.


class TimelineView extends Control:
	var rows := []
	var case_timeline := []
	var index := 0

	func _draw() -> void:
		if rows.is_empty():
			return
		var end := maxf(float(rows[-1].t_ms), 1.0)
		var left := 8.0
		var width := size.x - 16.0
		var y := size.y / 2.0
		draw_line(Vector2(left, y), Vector2(left + width, y), Color(0.35, 0.4, 0.45), 2.0)
		var seen := {}
		var font := get_theme_default_font()
		for row in case_timeline:
			for name in row.get("observations", []):
				if not seen.has(name):
					seen[name] = row.t_ms
					var x := left + width * float(row.t_ms) / end
					draw_line(Vector2(x, y - 14), Vector2(x, y + 14), Color(1.0, 0.45, 0.4), 2.0)
			var colour := Color(0.3, 0.8, 0.4) if row.decision == "clean" else (Color(1.0, 0.4, 0.4) if row.decision == "review" else (Color(0.95, 0.8, 0.3) if row.decision == "watch" else Color(0.5, 0.55, 0.6)))
			draw_rect(Rect2(left + width * float(row.t_ms) / end - 2, y + 16, 4, 6), colour)
		var at := left + width * float(rows[index].t_ms) / end
		draw_line(Vector2(at, y - 22), Vector2(at, y + 22), Color(0.6, 0.85, 1.0), 2.0)
		draw_string(font, Vector2(left, 11), "0 ms", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.6, 0.65, 0.7))
		draw_string(font, Vector2(left + width - 60, 11), "%d ms" % int(end), HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.6, 0.65, 0.7))
		draw_string(font, Vector2(left + 50, 11), "red: an observation first appeared · squares: the decision each second", HORIZONTAL_ALIGNMENT_LEFT, -1, 11, Color(0.5, 0.55, 0.6))
