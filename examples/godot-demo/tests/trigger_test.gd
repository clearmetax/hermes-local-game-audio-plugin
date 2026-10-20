extends SceneTree
## Headless check: does hitting the enemy start an AudioStreamPlayer?
## Run: godot --headless --path <demo> --script res://tests/trigger_test.gd
## Prints RESULT lines and exits 0 when every hit/death started a sound.

func _initialize() -> void:
	var main: Node = load("res://scenes/main.tscn").instantiate()
	root.add_child(main)
	current_scene = main
	await process_frame
	var enemy: Node = main.get_node("Enemy")
	var ok := true
	for i in range(enemy.max_health):
		var before := _players(main).size()
		enemy.take_damage(1)
		var started := _players(main).filter(func(p): return p.playing)
		var new_count := _players(main).size() - before
		print("RESULT hit=%d new_players=%d playing=%d" % [i + 1, new_count, started.size()])
		if new_count < 1 or started.is_empty():
			ok = false
		await process_frame
	print("RESULT overall=%s" % ("PASS" if ok else "FAIL"))
	main.queue_free()
	await process_frame
	quit(0 if ok else 1)


func _players(n: Node) -> Array:
	var out := []
	for c in n.get_children():
		if c is AudioStreamPlayer or c is AudioStreamPlayer2D or c is AudioStreamPlayer3D:
			out.append(c)
	return out
