extends Node
## Entry point. Everything after "--" on the command line is ours:
##   --role=server|client|operator|lab, then the options each role reads.
## One process is one role, except the lab, which is a stock client and an operator view in one window
## (left: what the player sees; right: what the server knows). The server is authoritative; a client only
## sends player commands; an operator only receives the server's privileged feed and sends scenario requests.
##
## The networked nodes sit at fixed paths on every side, because Godot routes an RPC by node path:
##   Main/Game      the player link (arena_server.gd on the server, arena_client.gd on a client)
##   Main/Operator  the operator link (server_operator.gd on the server, operator_view.gd on an operator)
## A stock client has no Operator node, so nothing it runs can receive the operator feed.

const Server = preload("res://scripts/arena_server.gd")
const ServerOperator = preload("res://scripts/server_operator.gd")
const Client = preload("res://scripts/arena_client.gd")
const OperatorView = preload("res://scripts/operator_view.gd")
const Lab = preload("res://scripts/lab.gd")


func _ready() -> void:
	var options := {}
	for arg in OS.get_cmdline_user_args():
		var parts := arg.trim_prefix("--").split("=", true, 1)
		options[parts[0]] = parts[1] if parts.size() > 1 else "true"
	var role: String = options.get("role", "")
	match role:
		"server":
			var server := Server.new()
			server.name = "Game"
			server.set("options", options)
			add_child(server)
			var link := ServerOperator.new()
			link.name = "Operator"
			add_child(link)
		"client":
			var client := Client.new()
			client.name = "Game"
			client.set("options", options)
			add_child(client)
			client.attach(get_viewport(), null)
		"operator":
			var view := OperatorView.new()
			view.name = "Operator"
			view.set("options", options)
			add_child(view)
			view.attach(get_viewport(), null)
		"lab":
			var lab := Lab.new()
			lab.name = "Lab"
			lab.set("options", options)
			add_child(lab)
		_:
			push_error("pass --role=server, --role=client, --role=operator or --role=lab after --")
			get_tree().quit(2)
