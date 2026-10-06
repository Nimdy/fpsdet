extends "res://scripts/study_client.gd"
## The study client, unchanged, with a script that presses its keys and moves its mouse, and writes what each
## did. It reads the client's own state (where it looks, where the server says it stands); the server's
## telemetry shows whether it moved and fired.

var report := {}


func _ready() -> void:
	super()
	_drive()


func _key(code: Key, down: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = down
	Input.parse_input_event(event)


func _button(down: bool) -> void:
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = down
	Input.parse_input_event(event)


func _wait(seconds: float) -> void:
	await get_tree().create_timer(seconds).timeout


func _drive() -> void:
	await _wait(1.0)
	_key(KEY_Y, true)
	_key(KEY_Y, false)
	await _wait(2.0)
	report["consent_and_connect"] = agreed and connected
	report["mouse_captured"] = Input.mouse_mode == Input.MOUSE_MODE_CAPTURED
	var before := look
	for step in 20:
		var motion := InputEventMouseMotion.new()
		motion.relative = Vector2(10, -2)
		Input.parse_input_event(motion)
		await get_tree().process_frame
	report["mouse_look_yaw_deg"] = snappedf(wrapf(look.x - before.x, -180.0, 180.0), 0.01)
	report["mouse_look_pitch_deg"] = snappedf(look.y - before.y, 0.01)
	before = look
	_key(KEY_LEFT, true)
	await _wait(0.5)
	_key(KEY_LEFT, false)
	report["arrow_keys_yaw_deg"] = snappedf(wrapf(look.x - before.x, -180.0, 180.0), 0.01)
	var start: Vector3 = me.position
	_key(KEY_W, true)
	await _wait(1.0)
	_key(KEY_W, false)
	await _wait(0.3)
	report["w_moved_m"] = snappedf(me.position.distance_to(start), 0.01)
	_button(true)
	await _wait(0.6)
	_button(false)
	report["fire_button_held"] = true
	await _wait(1.0)
	report["fps"] = Engine.get_frames_per_second()
	_log({"kind": "controls_check", "report": report})
	_quit("controls checked")
