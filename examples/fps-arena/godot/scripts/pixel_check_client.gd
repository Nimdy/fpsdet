extends "res://scripts/arena_client.gd"
## Qualification instrumentation, kept apart from the stock client: the stock client as players get it, plus a
## pixel check of every drawn body (examples/fps-arena/harness/arena.py qualify, the player pixel proof).
##
## Every two seconds it freezes the frame, draws it, then draws it once more with each remote body hidden in
## turn and counts the pixels that changed. A body that changes no pixel was not visible on this client's
## screen, whatever the server sent. The method is the P12 pilot's (examples/pilot/godot/scripts/client.gd).
## It knows nothing of challenges: it checks every body the same way, and the harness names the probe afterwards
## from the server's own record. Nothing here is part of a lab session or of scoring.
##
## Options, beside the stock client's: --check-every-ms --shots

var frozen := false
var checking := false
var next_check_ms := 0
var checks := 0
var shots_dir := ""


func _ready() -> void:
	super._ready()
	shots_dir = options.get("shots", "")


func _process(delta: float) -> void:
	if frozen:
		return
	super._process(delta)
	if not draws or not connected or checking:
		return
	var now := Time.get_ticks_msec()
	if now >= next_check_ms and latest_ms > 0:
		next_check_ms = now + int(options.get("check-every-ms", "2000"))
		_pixel_check()


## Freeze the frame, draw it, then draw it once more with each remote body hidden in turn.
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


func _quit(why: String) -> void:
	if log_file != null and log_file.is_open():
		_log({"kind": "pixel_summary", "pixel_checks": checks})
	super._quit(why)
