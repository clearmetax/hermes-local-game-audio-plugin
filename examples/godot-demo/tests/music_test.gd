extends SceneTree
## Headless check for background music:
##  1. every WAV in res://audio/music imports with a loop (from its embedded smpl loop points);
##  2. starting the main scene creates a playing, looping HLGA_Music player.
## Run: godot --headless --path <demo> --script res://tests/music_test.gd

func _initialize() -> void:
	var ok := true
	var dir := DirAccess.open("res://audio/music")
	if dir:
		for f in dir.get_files():
			if f.ends_with(".wav"):
				var s: AudioStreamWAV = load("res://audio/music/" + f)
				var looped := s.loop_mode != AudioStreamWAV.LOOP_DISABLED
				print("RESULT import file=%s loop_mode=%d loop_end=%d length=%.2f" % [f, s.loop_mode, s.loop_end, s.get_length()])
				ok = ok and looped
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	current_scene = main
	await process_frame
	await process_frame
	var player := main.get_node_or_null("HLGA_Music") as AudioStreamPlayer
	if player == null:
		print("RESULT music player=missing")
		ok = false
	else:
		var loops := false
		if player.stream is AudioStreamWAV:
			loops = (player.stream as AudioStreamWAV).loop_mode != AudioStreamWAV.LOOP_DISABLED
		elif "loop" in player.stream:
			loops = player.stream.get("loop")
		print("RESULT music player=found playing=%s loops=%s bus=%s" % [player.playing, loops, player.bus])
		ok = ok and player.playing and loops
	print("RESULT overall=%s" % ("PASS" if ok else "FAIL"))
	main.queue_free()
	await process_frame
	quit(0 if ok else 1)
