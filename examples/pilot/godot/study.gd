extends Node3D
## The consented human pilot's entry point (examples/human-pilot). Run this scene, not main.tscn:
##   godot --path examples/pilot/godot res://study.tscn -- --role=server|client ...
## The P12 pilot (main.tscn) is untouched by it.

const Server = preload("res://scripts/study_server.gd")
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
