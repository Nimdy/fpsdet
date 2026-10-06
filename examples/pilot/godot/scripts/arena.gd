extends RefCounted
## The pilot's one small map, built the same way by the server and every client, and the numbers both
## sides share. Metres, degrees and milliseconds; y is up; the server ticks at 60 Hz.

const TICK_HZ := 60
const TICK_MS := 1000.0 / TICK_HZ
const EVENT_EVERY_TICKS := 6  # one movement event per player every 100 ms
const SNAPSHOT_EVERY_TICKS := 3  # snapshots to clients at 20 Hz
const INTERP_DELAY_MS := 100.0  # clients draw remote bodies this far behind the newest snapshot

const EYE_HEIGHT := 1.6
const BODY_RADIUS := 0.4
const BODY_HEIGHT := 1.8
const CHEST_HEIGHT := 1.3
const RUN_SPEED := 5.0

# The wall the probe hides behind: x -10..10, z -0.3..0.3, 4 m tall.
const WALL_CENTER := Vector3(0, 2, 0)
const WALL_SIZE := Vector3(20, 4, 0.6)

const SUBJECT_SPAWN := Vector3(0, 0, 12)
const ENEMY_SPAWN := Vector3(-6, 0, 7)
# The enemy's patrol, on the subject's side of the wall.
const PATROL := [Vector3(-6, 0, 7), Vector3(6, 0, 7), Vector3(6, 0, 4), Vector3(-6, 0, 4)]

# Where a probe may be: behind the wall from anywhere the subject stands, by a wide margin (docs/pilot.md).
const ZONE_MIN := Vector2(-5, -9)  # x, z
const ZONE_MAX := Vector2(5, -3)
const PLACEMENTS := [Vector3(-4, 0, -5), Vector3(-2, 0, -7), Vector3(0, 0, -4), Vector3(2, 0, -6), Vector3(4, 0, -5), Vector3(0, 0, -8)]
# Only for the exposed_vision scenario, which breaks the placement rule on purpose: in the open.
const EXPOSED_MIN := Vector2(1, 3)
const EXPOSED_MAX := Vector2(5, 6)
const EXPOSED_PLACEMENT := Vector3(3, 0, 4.5)

const WORLD_LAYER := 1
const PLAYER_LAYER := 2


## The floor and the wall, as static colliders on the world layer (and meshes, on a client that draws).
static func build(parent: Node3D, with_meshes: bool) -> void:
	_box(parent, Vector3(0, -0.5, 0), Vector3(40, 1, 40), with_meshes, Color(0.35, 0.37, 0.33))
	_box(parent, WALL_CENTER, WALL_SIZE, with_meshes, Color(0.55, 0.5, 0.45))


static func _box(parent: Node3D, center: Vector3, size: Vector3, with_mesh: bool, color: Color) -> void:
	var body := StaticBody3D.new()
	body.collision_layer = WORLD_LAYER
	body.collision_mask = 0
	body.position = center
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = size
	shape.shape = box
	body.add_child(shape)
	if with_mesh:
		var mesh := MeshInstance3D.new()
		var cube := BoxMesh.new()
		cube.size = size
		var material := StandardMaterial3D.new()
		material.albedo_color = color
		cube.material = material
		mesh.mesh = cube
		body.add_child(mesh)
	parent.add_child(body)


## Yaw 0 looks towards -z (north, at the wall); positive yaw turns left; positive pitch looks up.
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
