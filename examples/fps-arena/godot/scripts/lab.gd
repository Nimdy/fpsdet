extends Node
## The lab window: the stock client on the left (exactly what the player sees), the security view on the
## right (what the server knows), and the operator panels underneath. One recordable 16:9 window.
##
## The two views are two worlds in one process. The left world is drawn by arena_client.gd from the
## snapshots the server sends every client; the right world is drawn by operator_view.gd from the operator
## feed, which the server sends only to a peer that proved itself with the run's token. The two scripts share
## no state: the client has no node at the operator path and no method that could receive the feed. For a
## process boundary as well, run arena.py run --separate, which opens the same two scripts as two windows.
##
## Keys: scenario keys (1-9 and the letters each scenario file declares), x end, v camera, r replay, i AI brief,
## m demo/developer, tab panels, f telemetry filter, c copy last event, p screenshot, esc frees the mouse.
##
## Options (after "--"): --run --scenarios --port --operator-token-file --mode --name --input --behaviour --log
## --screenshot-every-ms --quit-after-ms

const Client = preload("res://scripts/arena_client.gd")
const OperatorView = preload("res://scripts/operator_view.gd")
const Scenario = preload("res://scripts/scenario.gd")

var options := {}
var client: Node
var operator: Node
var left: SubViewport
var right: SubViewport
var panel_host: Control
var hint: Label
var keys := {}
var mode := "developer"
var next_shot_ms := 0
var shots := 0
var quit_at_ms := 0
var start_scenario := ""
var start_at_ms := 0
var replay_at_ms := 0


func _ready() -> void:
	mode = options.get("mode", mode)
	get_window().title = "fpsdet Arena"
	keys = Scenario.key_map(Scenario.load_all(options.get("scenarios", "")))
	_build_layout()
	# The networked nodes sit beside this one, at the fixed paths the server routes RPCs to. Added once this
	# node has finished setting up, so neither is created while its parent is still building children.
	call_deferred("_link")
	var every := int(options.get("screenshot-every-ms", "0"))
	if every > 0:
		next_shot_ms = Time.get_ticks_msec() + every
	quit_at_ms = Time.get_ticks_msec() + int(options.get("quit-after-ms", "0")) if options.has("quit-after-ms") else 0
	start_scenario = options.get("start-scenario", "")
	start_at_ms = Time.get_ticks_msec() + 2500
	replay_at_ms = Time.get_ticks_msec() + int(options.get("start-replay-after-ms", "0")) if options.has("start-replay-after-ms") else 0


func _build_layout() -> void:
	var layer := CanvasLayer.new()
	layer.layer = -1
	add_child(layer)
	var background := ColorRect.new()
	background.color = Color(0.03, 0.035, 0.045)
	background.set_anchors_preset(Control.PRESET_FULL_RECT)
	layer.add_child(background)
	var root := VBoxContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("separation", 4)
	add_child(root)
	var views := HBoxContainer.new()
	views.size_flags_vertical = Control.SIZE_EXPAND_FILL
	views.size_flags_stretch_ratio = 1.7 if mode == "demo" else 1.35
	views.add_theme_constant_override("separation", 4)
	root.add_child(views)
	left = _pane(views, "PLAYER VIEW  ·  what the stock client draws")
	right = _pane(views, "SERVER / SECURITY VIEW  ·  what the server knows")
	panel_host = Control.new()
	panel_host.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel_host.size_flags_stretch_ratio = 1.0
	root.add_child(panel_host)
	hint = Label.new()
	hint.text = ""
	hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	hint.add_theme_font_size_override("font_size", 12)
	hint.add_theme_color_override("font_color", Color(0.5, 0.55, 0.6))
	root.add_child(hint)


func _pane(parent: HBoxContainer, title: String) -> SubViewport:
	var holder := VBoxContainer.new()
	holder.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	holder.add_theme_constant_override("separation", 2)
	parent.add_child(holder)
	var label := Label.new()
	label.text = title
	label.add_theme_font_size_override("font_size", 14)
	label.add_theme_color_override("font_color", Color(0.65, 0.78, 0.88))
	holder.add_child(label)
	var container := SubViewportContainer.new()
	container.stretch = true
	container.size_flags_vertical = Control.SIZE_EXPAND_FILL
	container.mouse_filter = Control.MOUSE_FILTER_IGNORE
	holder.add_child(container)
	var viewport := SubViewport.new()
	viewport.own_world_3d = true
	viewport.handle_input_locally = false
	viewport.msaa_3d = Viewport.MSAA_2X
	container.add_child(viewport)
	return viewport


func _link() -> void:
	var main := get_parent()
	client = Client.new()
	client.name = "Game"
	client.set("options", options)
	main.add_child(client)
	client.attach(left, null)
	operator = OperatorView.new()
	operator.name = "Operator"
	operator.set("options", options)
	main.add_child(operator)
	operator.attach(right, panel_host)
	if options.get("input", "human") == "human":
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


func _unhandled_input(event: InputEvent) -> void:
	if client == null:
		return
	client.feed_input(event)
	if not (event is InputEventKey and event.pressed and not event.echo):
		return
	var key := OS.get_keycode_string(event.keycode).to_lower()
	if keys.has(key):
		operator.exit_replay()
		operator.request("scenario", keys[key])
		return
	match key:
		"x":
			operator.request("end", "")
		"v":
			operator.cycle_camera()
		"tab":
			operator.cycle_tab()
		"f":
			operator.cycle_filter()
		"c":
			operator.copy_last_event()
		"i":
			var status: Dictionary = operator.status.get("ai", {})
			operator.set_ai(not bool(status.get("enabled", false)))
		"m":
			mode = "demo" if mode == "developer" else "developer"
			_rebuild_panels()
		"p":
			_screenshot()
		"r":
			if operator.replaying:
				operator.exit_replay()
			else:
				var last: String = operator.last_finished_match()
				if last != "" and not operator.enter_replay(last):
					hint.text = "no timeline for " + last
		"space":
			if operator.replaying:
				operator.toggle_replay_play()
		"left":
			if operator.replaying:
				operator.replay_step(-1000 if event.shift_pressed else -100)
		"right":
			if operator.replaying:
				operator.replay_step(1000 if event.shift_pressed else 100)
		"home":
			if operator.replaying:
				operator.replay_home()
		"end":
			if operator.replaying:
				operator.replay_end()
		"u":
			var feed: Dictionary = operator.latest
			operator.request("audio_query", "false" if bool(feed.get("audio_query", true)) else "true")
		"t":
			# T also starts the tracked scenario when a scenario file claims the key; otherwise it flips the stand-in.
			var feed: Dictionary = operator.latest
			operator.request("standin", "false" if String(feed.get("standin", "")) != "" else "true")


func _rebuild_panels() -> void:
	for child in panel_host.get_children():
		child.queue_free()
	operator.mode = mode
	operator.panels = {}
	operator.panel_titles = {}
	operator.build_panels(panel_host)


func _process(_delta: float) -> void:
	if operator != null and client != null:
		operator.client_fps = Engine.get_frames_per_second()
		var scenario_keys := []
		var ordered := keys.keys()
		ordered.sort_custom(func(a, b): return (a.is_valid_int() and not b.is_valid_int()) or (a.is_valid_int() == b.is_valid_int() and a < b))
		for key in ordered:
			scenario_keys.append("%s %s" % [key, String(keys[key]).replace("_", " ")])
		hint.text = "SCENARIOS  " + "   ".join(scenario_keys) + "\nKEYS  m %s mode · v camera · r replay · tab panels · x end match · esc mouse" % ("developer" if mode == "demo" else "demo")
	var now := Time.get_ticks_msec()
	if start_scenario != "" and operator != null and operator.accepted and now >= start_at_ms:
		operator.request("scenario", start_scenario)
		start_scenario = ""
	if replay_at_ms > 0 and now >= replay_at_ms and operator != null:
		replay_at_ms = 0
		var last: String = operator.last_finished_match()
		if last != "" and operator.enter_replay(last):
			operator.cycle_tab()
			operator.cycle_tab()
			operator.cycle_tab()
			operator.cycle_tab()
			operator.cycle_tab()
			operator.toggle_replay_play()
	if next_shot_ms > 0 and now >= next_shot_ms:
		next_shot_ms = now + int(options.get("screenshot-every-ms", "0"))
		_screenshot()
	if quit_at_ms > 0 and now >= quit_at_ms:
		get_tree().quit(0)


## A still of the whole window, into the run's operator folder. For recording and for the harness's own checks.
func _screenshot() -> void:
	var folder := String(options.get("run", "user://")).path_join("operator").path_join("shots")
	DirAccess.make_dir_recursive_absolute(folder)
	shots += 1
	var image := get_viewport().get_texture().get_image()
	var path := folder.path_join("lab-%03d.png" % shots)
	image.save_png(path)
	hint.text = "saved " + path
