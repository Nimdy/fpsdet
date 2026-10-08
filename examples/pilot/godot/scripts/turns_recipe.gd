extends RefCounted
## occluded_motion_replay/3's secret turns (src/fpsdet/challenge.py TurnRule, src/fpsdet/challenge_plan.py
## turn_schedule), in GDScript, so the game server derives them in its own process from the secret it holds.
## recipe.gd is untouched: it derives the id, the material and the commitment as for every version; this file
## adds only what version 3 draws from that material. examples/turns-pilot/turns.py checks every turn the
## server ran against the Python implementation.

const Recipe = preload("res://scripts/recipe.gd")

const VERSION := 3
# TurnRule(), as fpsdet fixes it before any data.
const COUNT := 6
const MIN_GAP_MS := 1500
const REACT_FROM_MS := 150
const REACT_TO_MS := 600
const LEAD_MS := REACT_TO_MS - REACT_FROM_MS


## Version 3's own parameters: where in its slot each turn falls, and which way each turns.
static func turn_parameters(realization_material: PackedByteArray) -> Dictionary:
	var out := {}
	var names := []
	for index in COUNT:
		names.append(["turn_%d_pick" % index, 0, 4294967295])
	names.append(["turn_signs", 0, (1 << COUNT) - 1])
	for spec in names:
		var draw := Recipe.hmac(realization_material, Recipe.text_field(Recipe.DERIVATION_RECIPE) + Recipe.text_field("parameter") + Recipe.text_field(spec[0]))
		out[spec[0]] = int(spec[1]) + Recipe.big_mod(draw, int(spec[2]) - int(spec[1]) + 1)
	return out


## [[t_ms, sign], ...] in match time, or [] if the window cannot hold the turns.
static func schedule(plan: Dictionary, parameters: Dictionary) -> Array:
	var first: int = int(plan.start_ms) + LEAD_MS
	var slot: int = (int(plan.end_ms) - REACT_TO_MS - first) / COUNT
	if slot < MIN_GAP_MS:
		return []
	var out := []
	var signs: int = int(parameters.turn_signs)
	for index in COUNT:
		var at: int = first + index * slot + int(parameters["turn_%d_pick" % index]) % (slot - MIN_GAP_MS + 1)
		out.append([at, 1 if (signs >> index) & 1 else -1])
	return out
