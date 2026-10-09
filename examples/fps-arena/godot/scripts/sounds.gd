extends RefCounted
## Two tiny sounds the client plays, made in memory at startup so the project ships no audio files: a
## footstep (a short low thud) and a gunshot (a short noise burst). Nothing else makes a sound; remote
## bodies make sound only when the server says so.

const RATE := 22050


static func footstep() -> AudioStreamWAV:
	return _wav(_thud(0.07, 140.0, 0.5))


static func gunshot() -> AudioStreamWAV:
	return _wav(_burst(0.09, 0.6))


static func _wav(samples: PackedFloat32Array) -> AudioStreamWAV:
	var data := PackedByteArray()
	data.resize(samples.size() * 2)
	for index in samples.size():
		var value := int(clampf(samples[index], -1.0, 1.0) * 32767.0)
		data.encode_s16(index * 2, value)
	var stream := AudioStreamWAV.new()
	stream.format = AudioStreamWAV.FORMAT_16_BITS
	stream.mix_rate = RATE
	stream.stereo = false
	stream.data = data
	return stream


static func _thud(seconds: float, frequency: float, volume: float) -> PackedFloat32Array:
	var out := PackedFloat32Array()
	var count := int(seconds * RATE)
	out.resize(count)
	for index in count:
		var t := float(index) / RATE
		var envelope := exp(-t * 40.0)
		out[index] = sin(TAU * frequency * t * (1.0 - t * 4.0)) * envelope * volume
	return out


static func _burst(seconds: float, volume: float) -> PackedFloat32Array:
	var out := PackedFloat32Array()
	var count := int(seconds * RATE)
	out.resize(count)
	var rng := RandomNumberGenerator.new()
	rng.seed = 7
	for index in count:
		var t := float(index) / RATE
		var envelope := exp(-t * 35.0)
		out[index] = rng.randf_range(-1.0, 1.0) * envelope * volume
	return out
