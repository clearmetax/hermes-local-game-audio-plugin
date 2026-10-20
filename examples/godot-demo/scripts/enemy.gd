extends Node2D
## A target dummy. Click it (or press Space) to hit it; it dies at 0 HP.

signal died

@export var max_health := 3

var health := 0

@onready var body: ColorRect = $Body


func _ready() -> void:
	health = max_health


func take_damage(amount: int) -> void:
	if health <= 0:
		return
	health -= amount
	_flash()
	if health <= 0:
		die()


func die() -> void:
	died.emit()
	queue_free()


func _flash() -> void:
	body.color = Color.WHITE
	var tween := create_tween()
	tween.tween_property(body, "color", Color(0.85, 0.25, 0.3), 0.15)
