extends SceneTree
## Headless check: a one-shot sound triggered from _ready() of a node in the
## starting scene (while the scene is still being set up) still plays.
## Run: godot --headless --path <demo> --script res://tests/ready_test.gd

func _initialize() -> void:
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	current_scene = main
	await process_frame
	await process_frame
	var playing := 0
	for c in main.get_children():
		if (c is AudioStreamPlayer or c is AudioStreamPlayer2D or c is AudioStreamPlayer3D) and c.playing:
			playing += 1
	print("RESULT ready_players_playing=%d" % playing)
	print("RESULT overall=%s" % ("PASS" if playing >= 1 else "FAIL"))
	main.queue_free()
	await process_frame
	quit(0 if playing >= 1 else 1)
