extends RefCounted
## The consented human pilot's arena (examples/human-pilot): two rooms joined by a doorway, a block whose
## corner players clear, a narrow passage, and four sealed probe rooms nobody can enter or see into.
## Each probe room sits behind a place players aim at: the doorway's left frame, the block's corner, the
## passage, and a quiet wall as the control. Metres; y is up; z grows south. Walls are 4 m tall and no one
## can jump, so a sealed room's inside is never in anyone's line of sight.

const SPAWN := Vector3(0, 0, 10)

# Every wall: [center, size].
const WALLS := [
	[Vector3(0, -0.5, 0), Vector3(34, 1, 34)],  # floor
	[Vector3(0, 2, -15.3), Vector3(31.2, 4, 0.6)],  # outer north
	[Vector3(0, 2, 15.3), Vector3(31.2, 4, 0.6)],  # outer south
	[Vector3(-15.3, 2, 0), Vector3(0.6, 4, 31.2)],  # outer west
	[Vector3(15.3, 2, 0), Vector3(0.6, 4, 31.2)],  # outer east
	[Vector3(-8.25, 2, 0), Vector3(13.5, 4, 0.6)],  # middle wall, west of the doorway (x -1.5..1.5 is open)
	[Vector3(8.25, 2, 0), Vector3(13.5, 4, 0.6)],  # middle wall, east of the doorway
	# Probe room A, door_edge: north of the middle wall, its east wall the doorway's left frame.
	[Vector3(-5.2, 2, -1.95), Vector3(0.4, 4, 3.9)],
	[Vector3(-1.7, 2, -1.95), Vector3(0.4, 4, 3.9)],
	[Vector3(-3.45, 2, -3.8), Vector3(3.9, 4, 0.4)],
	# Probe room B, corner: the block in the south room.
	[Vector3(8.2, 2, 5.5), Vector3(0.4, 4, 3.0)],
	[Vector3(10.8, 2, 5.5), Vector3(0.4, 4, 3.0)],
	[Vector3(9.5, 2, 4.2), Vector3(3.0, 4, 0.4)],
	[Vector3(9.5, 2, 6.8), Vector3(3.0, 4, 0.4)],
	# The passage in the north room (x -1.2..1.2 is open): a solid block to the west, probe room C to the east.
	[Vector3(-3.6, 2, -9), Vector3(4.8, 4, 3.0)],
	[Vector3(1.4, 2, -9), Vector3(0.4, 4, 3.0)],
	[Vector3(5.8, 2, -9), Vector3(0.4, 4, 3.0)],
	[Vector3(3.6, 2, -10.3), Vector3(4.8, 4, 0.4)],
	[Vector3(3.6, 2, -7.7), Vector3(4.8, 4, 0.4)],
	# Probe room D, long_wall: against the east outer wall of the north room.
	[Vector3(12.0, 2, -9), Vector3(0.4, 4, 8.0)],
	[Vector3(13.5, 2, -12.8), Vector3(3.0, 4, 0.4)],
	[Vector3(13.5, 2, -5.2), Vector3(3.0, 4, 0.4)],
]

# The probe rooms, in the order placement_pick indexes them: the tag, and where the body's centre may be
# (the inside, less the body's radius and the vision margin, so every point the server queries stays inside
# the room or its walls).
const ROOMS := [
	["door_edge", Vector2(-4.5, -3.1), Vector2(-2.4, -0.8)],
	["corner", Vector2(8.9, 4.9), Vector2(10.1, 6.1)],
	["chokepoint", Vector2(2.1, -9.6), Vector2(5.1, -8.4)],
	["long_wall", Vector2(12.7, -12.1), Vector2(14.5, -5.9)],
]

# The bots of each mode: [waypoints, speed m/s, pause ms at each waypoint]. A bot with one waypoint stands still.
const MODES := {
	"free": [
		[[Vector3(-8, 0, 8), Vector3(6, 0, 10), Vector3(12.5, 0, 10), Vector3(12.5, 0, 2), Vector3(0, 0, 2), Vector3(0, 0, -5.5), Vector3(8, 0, -4),
		  Vector3(8, 0, -13.5), Vector3(-9, 0, -13.5), Vector3(-9, 0, -5.5), Vector3(0, 0, -5.5), Vector3(0, 0, 2)], 2.5, 0],
	],
	"combat": [
		[[Vector3(-10, 0, 4), Vector3(5, 0, 3), Vector3(5, 0, 12), Vector3(-10, 0, 12)], 4.0, 0],
		[[Vector3(-9, 0, -5.5), Vector3(8, 0, -4), Vector3(8, 0, -13.5), Vector3(-9, 0, -13.5)], 4.0, 0],
	],
	"angle_holding": [
		[[Vector3(6.5, 0, -3), Vector3(0.5, 0, -1.2), Vector3(6.5, 0, -3), Vector3(8, 0, -13.5), Vector3(0, 0, -13.5), Vector3(0, 0, -9),
		  Vector3(0, 0, -13.5), Vector3(8, 0, -13.5)], 4.5, 1500],
	],
	"sweep_search": [
		[[Vector3(12.5, 0, 5.5)], 0.0, 0],
		[[Vector3(-7, 0, -2.5)], 0.0, 0],
		[[Vector3(4, 0, -12.5)], 0.0, 0],
	],
	"tracking": [
		[[Vector3(6.5, 0, 8), Vector3(6.5, 0, 3), Vector3(12.5, 0, 3), Vector3(12.5, 0, 8), Vector3(6.5, 0, 8), Vector3(0, 0, 2), Vector3(0, 0, -5),
		  Vector3(0, 0, -9), Vector3(0, 0, -13.5), Vector3(9, 0, -13.5), Vector3(11, 0, -9), Vector3(9, 0, -4.5), Vector3(0, 0, -5), Vector3(0, 0, 2)], 2.0, 0],
	],
	"high_motion": [
		[[Vector3(-10, 0, 4), Vector3(5, 0, 3), Vector3(5, 0, 13), Vector3(-10, 0, 13)], 6.0, 0],
		[[Vector3(5, 0, 13), Vector3(-10, 0, 13), Vector3(-10, 0, 4), Vector3(5, 0, 3)], 6.0, 0],
		[[Vector3(-9, 0, -5.5), Vector3(8, 0, -4), Vector3(8, 0, -13.5), Vector3(-9, 0, -13.5)], 6.0, 0],
	],
	"stress": [
		[[Vector3(-12, 0, -12)], 0.0, 0],
	],
}

# Common angles, at head height, that the machine stand-ins pre-aim (dry run only; people aim where they like).
const ANGLES := [
	Vector3(-1.5, 1.6, 0.0), Vector3(1.5, 1.6, 0.0), Vector3(8.0, 1.6, 4.0), Vector3(8.0, 1.6, 7.0), Vector3(11.0, 1.6, 4.0),
	Vector3(-1.2, 1.6, -7.5), Vector3(1.2, 1.6, -7.5), Vector3(-5.4, 1.6, -4.0), Vector3(11.8, 1.6, -5.0),
]

const WORLD_LAYER := 1


static func build(parent: Node3D, with_meshes: bool) -> void:
	for wall in WALLS:
		var body := StaticBody3D.new()
		body.collision_layer = WORLD_LAYER
		body.collision_mask = 0
		body.position = wall[0]
		var shape := CollisionShape3D.new()
		var box := BoxShape3D.new()
		box.size = wall[1]
		shape.shape = box
		body.add_child(shape)
		if with_meshes:
			var mesh := MeshInstance3D.new()
			var cube := BoxMesh.new()
			cube.size = wall[1]
			var material := StandardMaterial3D.new()
			material.albedo_color = Color(0.35, 0.37, 0.33) if wall[0].y < 0 else Color(0.55, 0.5, 0.45)
			cube.material = material
			mesh.mesh = cube
			body.add_child(mesh)
		parent.add_child(body)
