extends Node3D
## occluded_motion_replay/3's entry point (examples/turns-pilot): the study arena and stock client, with the
## secret-turns server. study.tscn and main.tscn are untouched by it.
##   godot --path examples/pilot/godot res://turns.tscn -- --role=server|client ...

const Server = preload("res://scripts/turns_server.gd")
const Client = preload("res://scripts/study_client.gd")


func _ready() -> void:
	var options := {}
	for arg in OS.get_cmdline_user_args():
		var parts := arg.trim_prefix("--").split("=", true, 1)
		options[parts[0]] = parts[1] if parts.size() > 1 else "true"
	var role: String = options.get("role", "")
	var node: Node
	if role == "server":
		node = Server.new()
	elif role == "client":
		node = Client.new()
	else:
		push_error("pass --role=server or --role=client after --")
		get_tree().quit(2)
		return
	node.name = "Game"
	node.set("options", options)
	add_child(node)
