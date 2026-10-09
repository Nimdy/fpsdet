extends RefCounted
## Scenario definitions (examples/fps-arena/scenarios/*.json), as the server, the client and the operator
## read them. A scenario says what the arena does: where the player starts, which bots run, which fault or
## stand-in is switched on and when, whether the audio query runs, whether a challenge is planned. It also
## says what fpsdet is expected to make of it. The expectation is metadata for the card and the harness:
## nothing here ever writes a finding, a decision or an eligibility. Those come from fpsdet alone.

const FORMAT := "fpsdet.arena-scenario/1"
const LOBBY := "lobby"


## Every scenario file in a folder, by id, in key order. Returns {} and pushes an error if the folder is unreadable.
static func load_all(folder: String) -> Dictionary:
	var out := {}
	var dir := DirAccess.open(folder)
	if dir == null:
		push_error("cannot open the scenarios folder " + folder)
		return out
	for name in dir.get_files():
		if not name.ends_with(".json"):
			continue
		var parsed = JSON.parse_string(FileAccess.get_file_as_string(folder.path_join(name)))
		if typeof(parsed) != TYPE_DICTIONARY or parsed.get("format") != FORMAT:
			push_error("not a scenario file: " + name)
			continue
		out[parsed["id"]] = parsed
	return out


## The lobby: free play, no telemetry, no scenario. What the lab shows before any scenario is chosen.
static func lobby() -> Dictionary:
	return {
		"format": FORMAT, "id": LOBBY, "title": "Lobby", "key": "0", "duration_ms": 0,
		"instructions": "Free play. Press 1 to 9, or a letter key, to start a scenario. Esc frees the mouse.",
		"subject": {"spawn": [0, 0, 11], "yaw": 0, "autopilot": "tracker"},
		"bots": [{"id": "arena-bot-a", "path": [[-10, 4], [10, 4], [10, 8], [-10, 8]], "speed": 3.0, "pause_ms": 0, "fires": false}],
		"audio_query": true, "faults": {}, "standin": {}, "card": {}, "expected": {},
	}


## Which keys start which scenarios: the key each file declares, so the operator panel and the client agree.
static func key_map(scenarios: Dictionary) -> Dictionary:
	var out := {}
	for id in scenarios:
		out[String(scenarios[id].get("key", ""))] = id
	return out


## Scenarios in the order their keys sort (digits first, then letters).
static func ordered(scenarios: Dictionary) -> Array:
	var ids := scenarios.keys()
	ids.sort_custom(func(a, b):
		var ka := String(scenarios[a].get("key", "z"))
		var kb := String(scenarios[b].get("key", "z"))
		var da := ka.is_valid_int()
		var db := kb.is_valid_int()
		if da != db:
			return da
		return ka < kb)
	return ids


## Whether a fault or stand-in window is open at a match time. A window with no bounds is open all match.
static func window_open(spec: Dictionary, t_ms: int) -> bool:
	if spec.is_empty():
		return false
	var from := int(spec.get("from_ms", 0))
	var to := int(spec.get("to_ms", 1 << 30))
	return from <= t_ms and t_ms <= to


## One line for the operator panel: the phase the match is in.
static func phase(scenario: Dictionary, t_ms: int) -> String:
	if scenario.get("id", LOBBY) == LOBBY:
		return "lobby"
	var faults: Dictionary = scenario.get("faults", {})
	var standin: Dictionary = scenario.get("standin", {})
	var active := []
	if window_open(faults, t_ms):
		for name in faults:
			if name not in ["from_ms", "to_ms"]:
				active.append(name)
	if window_open(standin, t_ms):
		active.append("stand-in: " + String(standin.get("kind", "")))
	if active.is_empty():
		return "baseline play"
	return ", ".join(active)
