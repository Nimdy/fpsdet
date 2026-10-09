extends Node
## The server's end of the operator link (Main/Operator). An operator proves itself with the run's token,
## then receives the server's feed and may ask for a scenario or an option. Every call is forwarded to the
## server (Main/Game); nothing here decides anything. The same RPC names and annotations are on the operator
## side (operator_view.gd); a stock client has no node at this path.


@rpc("any_peer", "reliable")
func operator_hello(token: String) -> void:
	var peer := multiplayer.get_remote_sender_id()
	var accepted: bool = get_node("../Game").operator_join(peer, token)
	operator_welcome.rpc_id(peer, accepted)


@rpc("any_peer", "reliable")
func operator_request(what: String, value: String) -> void:
	get_node("../Game").operator_request(multiplayer.get_remote_sender_id(), what, value)


@rpc("authority", "reliable")
func operator_welcome(_accepted: bool) -> void:
	pass


@rpc("authority", "unreliable_ordered")
func operator_state(_state: Dictionary) -> void:
	pass
