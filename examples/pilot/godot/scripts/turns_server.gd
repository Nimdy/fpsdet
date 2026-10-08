extends "res://scripts/study_server.gd"
## The study server running occluded_motion_replay/3: secret turns (docs/challenges.md, examples/turns-pilot).
##
## A subclass, so study_server.gd, the human pilot's frozen server, stays byte for byte what it was. Everything
## it does is inherited: the arena, the bots, the stock client, the vision rays and audio query, the per-moment
## verdict, the aim cone, the NDJSON. What changes is the probe and what the participant's events carry:
##
## - The body runs along a lane in a sealed probe room, across this client's line of sight. At each secret turn it sets off along the lane, the way
##   that turns its bearing from this client the turn's way, for the whole reaction window. Between turns it
##   eases back to where the next turn starts from, and before each turn it stands still, so a turn is the
##   only abrupt change in how its bearing moves.
## - One event at each turn's tick carries challenge_turn_index and challenge_turn_sign.
## - Every event naming the challenge carries view_yaw_deg, the participant's server-authoritative yaw.
##
## Only version 3 plans run here; version 1 and 2 plans are refused (they belong to study.tscn).

const Turns = preload("res://scripts/turns_recipe.gd")

const LANE_MAX_SPEED := 3.0  # m/s along the lane during a turn's reaction window
const SETTLE_MS := 50  # the body is still this long before each turn's lead window opens

var pending_turn := []


func _load_plan() -> void:
	var path: String = options.get("plan", "")
	if path == "" or not FileAccess.file_exists(path):
		_log({"kind": "plan", "status": "missing", "detail": "no challenge plan: no challenge runs in this session"})
		return
	var plan = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(plan) != TYPE_DICTIONARY or plan.get("format") != Recipe.PLAN_FORMAT or plan.get("game_id") != GAME_ID or plan.get("match_id") != match_id:
		_log({"kind": "plan", "status": "refused", "detail": "not a challenge plan for this game and session: no challenge runs"})
		return
	var loaded := Recipe.load_secret(options.get("secret-file", ""))
	var secret: PackedByteArray = loaded[0]
	if secret.is_empty():
		_log({"kind": "plan", "status": "refused", "detail": loaded[1] + ": no challenge runs"})
		return
	var accepted := []
	var refused := []
	for raw in plan.get("challenges", []):
		var record := Recipe.record(raw)
		if record.subject_id != participant or record.challenge_type != "occluded_motion_replay" or int(record.version) != Turns.VERSION or record.end_ms > match_ms:
			refused.append({"challenge_id": record.challenge_id, "detail": "not a version 3 challenge runnable in this session"})
			continue
		var realized := Recipe.realize(secret, record)
		if realized[1] != "":
			refused.append({"challenge_id": record.challenge_id, "detail": realized[1]})
			continue
		var parameters: Dictionary = realized[0]
		parameters.merge(Turns.turn_parameters(Recipe.material(secret, record)))
		var turns := Turns.schedule(record, parameters)
		if turns.is_empty():
			refused.append({"challenge_id": record.challenge_id, "detail": "the window cannot hold the turns"})
			continue
		parameters["schedule"] = turns
		challenges.append({"plan": record, "parameters": parameters, "ran": false})
		accepted.append({"challenge_id": record.challenge_id, "version": record.version, "start_ms": record.start_ms, "end_ms": record.end_ms})
	secret = PackedByteArray()
	_log({"kind": "plan", "status": "loaded", "accepted": accepted, "refused": refused})


func _begin(entry: Dictionary) -> void:
	var parameters: Dictionary = entry.parameters
	var room: Array = Study.ROOMS[int(parameters.placement_pick) % Study.ROOMS.size()]
	var low: Vector2 = room[1]
	var high: Vector2 = room[2]
	var center := Vector3((low.x + high.x) / 2, 0, (low.y + high.y) / 2)
	# The lane runs across this client's line of sight to the room, through its centre, as far as the room allows
	# either way: along the line of sight, the body would move without its bearing moving.
	var sight := center - _position(participant)
	sight.y = 0
	var axis := Vector3(-sight.z, 0, sight.x).normalized() if sight.length() > 0.01 else Vector3(1, 0, 0)
	var half := minf((high.x - low.x) / 2.0 / maxf(absf(axis.x), 0.001), (high.y - low.y) / 2.0 / maxf(absf(axis.z), 0.001))
	entry.ran = true
	parameters["fired"] = []
	active = {"entry": entry, "plan": entry.plan, "source": "lane", "room": room[0], "low": low, "high": high, "anchor": center,
		"heading": 0.0, "delay_ticks": 0, "base": center, "position": center, "yaw": 0.0, "ticks": 0, "first_ms": Arena.t_ms(tick),
		"axis": axis, "half": half, "speed": minf(LANE_MAX_SPEED, 2.0 * half / (Turns.REACT_TO_MS / 1000.0)),
		"at": 0.0, "velocity": 0.0, "next": 0, "ease": []}
	active.at = _start_of(0)
	active.position = center + axis * active.at
	accumulator = {}
	motion_window = []
	_log({"kind": "challenge_start", "challenge_id": entry.plan.challenge_id, "t_ms": Arena.t_ms(tick)})


func _update_challenge() -> void:
	var now := Arena.t_ms(tick)
	if active.is_empty():
		for entry in challenges:
			if not entry.ran and entry.plan.start_ms <= now and now <= entry.plan.end_ms:
				_begin(entry)
				break
		if active.is_empty():
			return
	if now > active.plan.end_ms:
		_end("window over")
		return
	var turns: Array = active.entry.parameters.schedule
	var index: int = active.next
	if index < turns.size() and now >= int(turns[index][0]):
		# A secret turn: set off along the lane the way that moves this client's bearing to the body the turn's way.
		var way: int = int(turns[index][1])
		active.velocity = way * _bearing_way() * float(active.speed)
		active.ease = []
		active.next = index + 1
		pending_turn = [index, way]
		active.entry.parameters.fired.append([index, now, way])
	var last: int = active.next - 1
	if last >= 0 and now < int(turns[last][0]) + Turns.REACT_TO_MS:
		active.at = clampf(float(active.at) + float(active.velocity) / Arena.TICK_HZ, -float(active.half), float(active.half))
	elif active.next < turns.size():
		# Ease back to where the next turn starts from, and be still before its lead window opens. The ease leaves
		# at the speed the turn ran at and arrives at rest (a cubic Hermite curve), so the body's bearing never
		# changes pace abruptly here: only a turn does that.
		var due: int = int(turns[active.next][0]) - Turns.LEAD_MS - SETTLE_MS
		if active.ease.is_empty():
			active.ease = [now, float(active.at), maxi(due, now + 1), _start_of(active.next), float(active.velocity)]
		var ease: Array = active.ease
		var span := float(int(ease[2]) - int(ease[0])) / 1000.0
		var u := clampf(float(now - int(ease[0])) / (span * 1000.0), 0.0, 1.0)
		var h00 := 2 * u * u * u - 3 * u * u + 1
		var h10 := u * u * u - 2 * u * u + u
		var h01 := -2 * u * u * u + 3 * u * u
		active.at = clampf(h00 * float(ease[1]) + h10 * span * float(ease[4]) + h01 * float(ease[3]), -float(active.half), float(active.half))
	var position: Vector3 = active.anchor + active.axis * float(active.at)
	active.position = position
	probe_history.append([tick, position])
	active.ticks += 1
	perf.probe_ticks += 1


## +1 when moving the body along +axis turns this client's bearing to it up (in the yaw fpsdet reads), else -1.
func _bearing_way() -> float:
	var eye := _eye(participant)
	var chest: Vector3 = active.anchor + active.axis * float(active.at) + Vector3(0, Arena.CHEST_HEIGHT, 0)
	var now := Arena.look_angles(eye, chest).x
	var moved := Arena.look_angles(eye, chest + active.axis * 0.05).x
	return 1.0 if wrapf(moved - now, -180.0, 180.0) >= 0.0 else -1.0


## Where along the lane turn ``index`` starts from: the far end from where it will go.
func _start_of(index: int) -> float:
	var turns: Array = active.entry.parameters.schedule
	if index >= turns.size():
		return float(active.at)
	return -float(turns[index][1]) * _bearing_way() * float(active.half)


func _observe() -> void:
	super._observe()
	if pending_turn.is_empty():
		return
	if not active.is_empty():
		_emit(participant, {"event_type": "movement", "challenge_turn_index": pending_turn[0], "challenge_turn_sign": pending_turn[1]})
	pending_turn = []


func _challenge_fields() -> Dictionary:
	var fields := super._challenge_fields()
	fields["view_yaw_deg"] = snappedf(float(players[participant].yaw), 0.001)
	return fields
