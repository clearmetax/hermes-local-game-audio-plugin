extends Node2D

@onready var enemy: Node2D = $Enemy
@onready var label: Label = $Label


func _ready() -> void:
	enemy.died.connect(_on_enemy_died)


func _unhandled_input(event: InputEvent) -> void:
	var hit := false
	if event is InputEventMouseButton and event.pressed:
		hit = true
	elif event.is_action_pressed("ui_accept"):
		hit = true
	if hit and is_instance_valid(enemy):
		enemy.take_damage(1)


func _on_enemy_died() -> void:
	label.text = "Enemy defeated!"
