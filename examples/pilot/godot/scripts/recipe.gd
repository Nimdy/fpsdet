extends RefCounted
## fpsdet's challenge recipe (fpsdet.challenge/1, src/fpsdet/challenge_plan.py), in GDScript, so the game
## server derives each realization in its own process from the secret it holds. Byte for byte the same:
## tests/test_pilot.py checks this file against the Python implementation.
##
## Nothing here writes the secret or a realization anywhere. The server keeps both in memory.

const DERIVATION_RECIPE := "fpsdet.challenge/1"
const COMMITMENT_RECIPE := "fpsdet.challenge-commitment/1"
const PLAN_FORMAT := "fpsdet.challenge-plans/1"
const MIN_SECRET_BYTES := 32
# The parameters of occluded_motion_replay (versions 1 and 2), in the order fpsdet lists them.
const PARAMETERS := [
	["heading_offset_deg", 30, 330],
	["replay_delay_ms", 2000, 20000],
	["route_pick", 0, 4294967295],
	["placement_pick", 0, 4294967295],
]
# The public fields a commitment covers beside the realization, all but the commitment itself.
const COMMITTED := ["challenge_id", "challenge_type", "version", "game_id", "match_id", "subject_id", "counter", "nonce", "start_ms", "end_ms"]
const INTEGER_FIELDS := ["version", "counter", "start_ms", "end_ms"]


## The 4-byte big-endian length, then the bytes.
static func field(data: PackedByteArray) -> PackedByteArray:
	var out := PackedByteArray([(data.size() >> 24) & 255, (data.size() >> 16) & 255, (data.size() >> 8) & 255, data.size() & 255])
	out.append_array(data)
	return out


static func text_field(text: String) -> PackedByteArray:
	return field(text.to_utf8_buffer())


static func message(purpose: String, plan: Dictionary, index: int) -> PackedByteArray:
	var out := PackedByteArray()
	for part in [DERIVATION_RECIPE, purpose, plan.game_id, plan.match_id, plan.subject_id, plan.nonce, plan.challenge_type, str(plan.version), str(index)]:
		out.append_array(text_field(str(part)))
	return out


static func hmac(key: PackedByteArray, data: PackedByteArray) -> PackedByteArray:
	var context := HMACContext.new()
	context.start(HashingContext.HASH_SHA256, key)
	context.update(data)
	return context.finish()


static func sha256(data: PackedByteArray) -> PackedByteArray:
	var context := HashingContext.new()
	context.start(HashingContext.HASH_SHA256)
	context.update(data)
	return context.finish()


## A big-endian number of any length, modulo a modulus below 2**32, without overflowing 64 bits.
static func big_mod(data: PackedByteArray, modulus: int) -> int:
	var rest := 0
	for value in data:
		rest = (rest * 256 + value) % modulus
	return rest


## One plan record as fpsdet writes it, with every number an integer (JSON numbers parse as floats).
static func record(raw: Dictionary) -> Dictionary:
	var out := raw.duplicate()
	for name in INTEGER_FIELDS:
		out[name] = int(raw[name])
	return out


## Sorted keys, no spaces: the canonical JSON fpsdet commits to. Every value is a string or an integer.
static func canonical(fields: Dictionary) -> String:
	var keys := fields.keys()
	keys.sort()
	var parts := PackedStringArray()
	for key in keys:
		var value = fields[key]
		parts.append(JSON.stringify(str(key)) + ":" + (str(value) if typeof(value) == TYPE_INT else JSON.stringify(str(value))))
	return "{" + ",".join(parts) + "}"


static func challenge_id(secret: PackedByteArray, plan: Dictionary) -> String:
	return "ch-" + hmac(secret, message("id", plan, plan.counter)).hex_encode().substr(0, 24)


static func material(secret: PackedByteArray, plan: Dictionary) -> PackedByteArray:
	return hmac(secret, message("realization", plan, plan.counter))


static func parameters(realization_material: PackedByteArray) -> Dictionary:
	var out := {}
	for spec in PARAMETERS:
		var draw := hmac(realization_material, text_field(DERIVATION_RECIPE) + text_field("parameter") + text_field(spec[0]))
		out[spec[0]] = int(spec[1]) + big_mod(draw, int(spec[2]) - int(spec[1]) + 1)
	return out


static func commitment(plan: Dictionary, realization_material: PackedByteArray) -> String:
	var fields := {}
	for name in COMMITTED:
		fields[name] = plan[name]
	var body := text_field(COMMITMENT_RECIPE) + text_field(canonical(fields)) + field(realization_material)
	return "sha256:" + sha256(body).hex_encode()


## The secret from a hex file, or an empty array with the reason. The reason never contains any of it.
static func load_secret(path: String) -> Array:
	if path == "" or not FileAccess.file_exists(path):
		return [PackedByteArray(), "no challenge secret file"]
	var text := FileAccess.get_file_as_string(path).strip_edges()
	var key := text.hex_decode()
	if key.size() < MIN_SECRET_BYTES or key.hex_encode() != text.to_lower():
		return [PackedByteArray(), "the challenge secret is not at least 32 bytes of hex"]
	return [key, ""]


## The realization behind one public plan record, if this secret made it: [parameters, ""], or [{}, why not].
## The id and the commitment are derived again; a record that does not match is refused, never run.
static func realize(secret: PackedByteArray, plan: Dictionary) -> Array:
	if challenge_id(secret, plan) != plan.challenge_id:
		return [{}, "this secret does not plan challenge " + str(plan.challenge_id)]
	var made := material(secret, plan)
	if commitment(plan, made) != plan.commitment:
		return [{}, "the commitment of " + str(plan.challenge_id) + " is not what this secret makes"]
	return [parameters(made), ""]
