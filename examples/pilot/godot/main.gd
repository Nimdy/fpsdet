extends Node3D
## Entry point. Everything after "--" on the command line is ours:
##   --role=server|client, then the options server.gd and client.gd read.
## One process is one role. The server is authoritative; a client only sends player commands.

const Server = preload("res://scripts/server.gd")
const Client = preload("res://scripts/client.gd")


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
	node.name = "Game"  # the same path on both sides, so remote procedure calls find each other
	node.set("options", options)
	add_child(node)
