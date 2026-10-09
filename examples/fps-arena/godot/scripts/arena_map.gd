extends RefCounted
## The Arena's one compact training map, built the same way by the server, the player's client and the
## operator view, and the numbers all three share. Metres, degrees and milliseconds; y is up; z grows
## south; x grows east. Walls are 4 m tall and nobody can jump, so a sealed room's inside is never in
## anyone's line of sight. The server ticks at 60 Hz.
##
## Features, each named so a scenario card can point at it:
##   open lane         the south room, where the player spawns and the visible bot patrols
##   doorway           the gap in the middle wall (x -0.7..2.3 at z 0)
##   solid wall        the middle wall either side of the doorway, 4 m tall
##   corner            a solid block in the south-east, whose corner players clear
##   narrow corridor   a 1.9 m passage between two blocks in the north room
##   occluded room     a walled room in the north-west with one door, where a bot can disappear
##   challenge chamber a sealed room behind the doorway's left frame; the probe lives here and nowhere else

const TICK_HZ := 60
const TICK_MS := 1000.0 / TICK_HZ
const EVENT_EVERY_TICKS := 6  # one movement event per player every 100 ms
const SNAPSHOT_EVERY_TICKS := 3  # snapshots to clients at 20 Hz
const INTERP_DELAY_MS := 100.0  # clients draw remote bodies this far behind the newest snapshot

const EYE_HEIGHT := 1.6
const BODY_RADIUS := 0.4
const BODY_HEIGHT := 1.8
const CHEST_HEIGHT := 1.3
const RUN_SPEED := 5.0  # m/s on the ground
const SPEED_CAP := 5.5  # the cap the server declares on every movement sample (expected_max_ground_speed_mps)
const HEARING_RADIUS := 30.0

const SPAWN := Vector3(0, 0, 11)  # facing north, at the doorway
const SPAWN_YAW := 0.0

# Every wall: [name, center, size]. The name is for the operator view's labels only.
const WALLS := [
	["floor", Vector3(0, -0.5, 0), Vector3(32, 1, 32)],
	["outer north", Vector3(0, 2, -15.2), Vector3(30.8, 4, 0.4)],
	["outer south", Vector3(0, 2, 15.2), Vector3(30.8, 4, 0.4)],
	["outer west", Vector3(-15.2, 2, 0), Vector3(0.4, 4, 30.8)],
	["outer east", Vector3(15.2, 2, 0), Vector3(0.4, 4, 30.8)],
	# The middle wall: solid either side of the doorway (x -0.7..2.3 is open).
	["solid wall (west)", Vector3(-7.85, 2, 0), Vector3(14.3, 4, 0.6)],
	["solid wall (east)", Vector3(8.65, 2, 0), Vector3(12.7, 4, 0.6)],
	# The challenge chamber: sealed, north of the middle wall, its east wall is the doorway's left frame.
	["chamber east", Vector3(-0.9, 2, -1.85), Vector3(0.4, 4, 3.1)],
	["chamber west", Vector3(-3.5, 2, -1.85), Vector3(0.4, 4, 3.1)],
	["chamber north", Vector3(-2.2, 2, -3.2), Vector3(3.0, 4, 0.4)],
	# The corner block in the south-east.
	["corner block", Vector3(9.5, 2, 6.5), Vector3(3, 4, 3)],
	# The narrow corridor in the north room: two blocks, x 1.5..3.4 open between them.
	["corridor west block", Vector3(-1.0, 2, -9.5), Vector3(5, 4, 3)],
	["corridor east block", Vector3(5.4, 2, -9.5), Vector3(4, 4, 3)],
	# The occluded room in the north-west: one door on its east wall (z -10..-8).
	["room east (north part)", Vector3(-7.0, 2, -12.5), Vector3(0.4, 4, 5.0)],
	["room east (south part)", Vector3(-7.0, 2, -7.7), Vector3(0.4, 4, 0.6)],
	["room south", Vector3(-11.0, 2, -7.2), Vector3(7.6, 4, 0.4)],
]

# Where the probe's centre may be: inside the chamber, less the body's radius and the vision margin, so
# every point the server queries stays inside the chamber or its walls. One chamber, so placement_pick
# always lands here; the secret still picks the route, the heading and the delay.
const CHAMBER_MIN := Vector2(-2.65, -2.35)  # x, z
const CHAMBER_MAX := Vector2(-1.75, -0.95)
const CHAMBER_CENTER := Vector3(-2.2, 0, -1.65)
# The doorway's left frame edge, at eye height: the angle the holder stand-in keeps.
const FRAME_EDGE := Vector3(-0.7, 1.6, 0.3)
# Where the holder stands: the line from here through the frame edge runs through the chamber's centre.
const HOLDER_SPOT := Vector3(6.8, 0, 10.0)

const WORLD_LAYER := 1
const PLAYER_LAYER := 2

# The security lab palette, shared so screenshots of either view read the same.
const COLOR_FLOOR := Color(0.13, 0.14, 0.17)
const COLOR_WALL := Color(0.30, 0.33, 0.38)
const COLOR_CHAMBER := Color(0.34, 0.26, 0.40)
const COLOR_ENEMY := Color(0.85, 0.25, 0.20)
const COLOR_PROBE := Color(0.95, 0.55, 0.10)


## The floor and the walls, as static colliders on the world layer (and meshes, on a side that draws).
static func build(parent: Node3D, with_meshes: bool) -> void:
	for wall in WALLS:
		var body := StaticBody3D.new()
		body.name = String(wall[0]).replace(" ", "_").replace("(", "").replace(")", "")
		body.collision_layer = WORLD_LAYER
		body.collision_mask = 0
		body.position = wall[1]
		var shape := CollisionShape3D.new()
		var box := BoxShape3D.new()
		box.size = wall[2]
		shape.shape = box
		body.add_child(shape)
		if with_meshes:
			var mesh := MeshInstance3D.new()
			var cube := BoxMesh.new()
			cube.size = wall[2]
			var material := StandardMaterial3D.new()
			var name: String = wall[0]
			material.albedo_color = COLOR_FLOOR if name == "floor" else (COLOR_CHAMBER if name.begins_with("chamber") else COLOR_WALL)
			material.roughness = 0.9
			cube.material = material
			mesh.mesh = cube
			body.add_child(mesh)
			if name != "floor":
				mesh.add_child(_edges(wall[2]))
		parent.add_child(body)
	if with_meshes:
		parent.add_child(_grid())


## Thin bright edges on every wall, so the geometry reads in a dark room without textures.
static func _edges(size: Vector3) -> MeshInstance3D:
	var lines := ImmediateMesh.new()
	var h := size / 2.0
	var corners := [
		Vector3(-h.x, -h.y, -h.z), Vector3(h.x, -h.y, -h.z), Vector3(h.x, -h.y, h.z), Vector3(-h.x, -h.y, h.z),
		Vector3(-h.x, h.y, -h.z), Vector3(h.x, h.y, -h.z), Vector3(h.x, h.y, h.z), Vector3(-h.x, h.y, h.z),
	]
	var pairs := [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]]
	lines.surface_begin(Mesh.PRIMITIVE_LINES)
	for pair in pairs:
		lines.surface_add_vertex(corners[pair[0]])
		lines.surface_add_vertex(corners[pair[1]])
	lines.surface_end()
	var node := MeshInstance3D.new()
	node.mesh = lines
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.albedo_color = Color(0.55, 0.75, 0.88, 0.85)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	node.material_override = material
	return node


## A metre grid on the floor, drawn as lines just above it.
static func _grid() -> MeshInstance3D:
	var lines := ImmediateMesh.new()
	lines.surface_begin(Mesh.PRIMITIVE_LINES)
	for i in range(-15, 16):
		lines.surface_add_vertex(Vector3(i, 0.01, -15))
		lines.surface_add_vertex(Vector3(i, 0.01, 15))
		lines.surface_add_vertex(Vector3(-15, 0.01, i))
		lines.surface_add_vertex(Vector3(15, 0.01, i))
	lines.surface_end()
	var node := MeshInstance3D.new()
	node.mesh = lines
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.albedo_color = Color(0.26, 0.34, 0.4, 0.6)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	node.material_override = material
	return node


## A body capsule, drawn the same way for every remote body on a stock client.
static func body_mesh(color: Color) -> MeshInstance3D:
	var node := MeshInstance3D.new()
	var capsule := CapsuleMesh.new()
	capsule.radius = BODY_RADIUS
	capsule.height = BODY_HEIGHT
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 0.6
	capsule.material = material
	node.mesh = capsule
	return node


## Lights and sky for a side that draws.
static func light(parent: Node3D) -> void:
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55, 35, 0)
	sun.light_energy = 1.3
	sun.shadow_enabled = false  # no shadows; a game that draws them must query them as vision
	parent.add_child(sun)
	var environment := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.04, 0.045, 0.06)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.58, 0.62)
	env.ambient_light_energy = 1.1
	env.fog_enabled = true
	env.fog_light_color = Color(0.05, 0.06, 0.08)
	env.fog_density = 0.004
	environment.environment = env
	parent.add_child(environment)


## Yaw 0 looks towards -z (north, at the doorway); positive yaw turns left; positive pitch looks up.
static func aim_direction(yaw_deg: float, pitch_deg: float) -> Vector3:
	var yaw := deg_to_rad(yaw_deg)
	var pitch := deg_to_rad(pitch_deg)
	return Vector3(-sin(yaw) * cos(pitch), sin(pitch), -cos(yaw) * cos(pitch))


## The yaw and pitch, in degrees, that point from one point at another.
static func look_angles(from: Vector3, to: Vector3) -> Vector2:
	var d := to - from
	var yaw := rad_to_deg(atan2(-d.x, -d.z))
	var pitch := rad_to_deg(atan2(d.y, Vector2(d.x, d.z).length()))
	return Vector2(yaw, pitch)


static func t_ms(tick: int) -> int:
	return int(round(tick * TICK_MS))


static func v3(v: Vector3) -> Array:
	return [snappedf(v.x, 0.001), snappedf(v.y, 0.001), snappedf(v.z, 0.001)]


static func from_v3(a: Array) -> Vector3:
	return Vector3(float(a[0]), float(a[1]), float(a[2]))
