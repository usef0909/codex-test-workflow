# Code Inventory

## Tracked code

`Player.gd` extends `CharacterBody2D` and exports `speed` with a default value of `300.0`. It defines `_physics_process`. The Inspector detects calls to `Input.get_vector()` and `move_and_slide()` in the script.

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
### Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Script `res://Player.gd` exists.
- `res://Player.gd` calls `Input.get_vector()`.
- `res://Player.gd` calls `move_and_slide()`.
- `res://Player.gd` defines `func _physics_process(_delta: float) -> void:`.
- `res://Player.gd` exports `speed` using `@export var speed: float = 300.0`.
- `res://Player.gd` extends `CharacterBody2D`.
<!-- /knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
## Classes, autoloads, and signals

- No custom script classes are verifiable.
- No autoload/singleton entries are configured in `project.godot`.
- No signal declarations or connections are present; the only scene has no signal connections and there are no scripts.

## Conventions

The project root's `.editorconfig` sets UTF-8 as the character encoding. No language/style conventions, formatter, lint configuration, or code organization pattern are established by the inspected files.

See [[Architecture Overview]] and [[Development Configuration]].
