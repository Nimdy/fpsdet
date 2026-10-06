extends Node3D
## The human client's controls, checked without a person (examples/human-pilot/study.py controls-check).
## The study client as participants get it, driven by keyboard and mouse events fed through Godot's own
## input pipeline: the consent key, mouse look, the arrow-key fallback, W A S D and the fire button.
## The server is the study server; nothing here is part of a study session.

const Checked = preload("res://scripts/controls_check_client.gd")
const Server = preload("res://scripts/study_server.gd")


func _ready() -> void:
	var options := {}
	for arg in OS.get_cmdline_user_args():
		var parts := arg.trim_prefix("--").split("=", true, 1)
		options[parts[0]] = parts[1] if parts.size() > 1 else "true"
	var node: Node = Server.new() if options.get("role", "") == "server" else Checked.new()
	node.name = "Game"
	node.set("options", options)
	add_child(node)
