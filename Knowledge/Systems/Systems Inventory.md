# Systems Inventory

## Current status

`res://Player.tscn` has a `Player` root of type `CharacterBody2D` and attaches `res://Player.gd` to that node. `res://Player.gd` exists, extends `CharacterBody2D`, exports `speed` with a default of `300.0`, defines `_physics_process`, and calls `move_and_slide()`.

This inventory describes the current tree. It does not imply that a system is planned.

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
### Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Input action `move_down` is configured with `physical key S (83)`.
- Input action `move_left` is configured with `physical key A (65)`.
- Input action `move_right` is configured with `physical key D (68)`.
- Input action `move_up` is configured with `physical key W (87)`.
- `res://Player.gd` calls `Input.get_vector()`.
- `res://Player.gd` calls `move_and_slide()`.
- `res://Player.gd` defines `func _physics_process(_delta: float) -> void:`.
- `res://Player.gd` extends `CharacterBody2D`.
<!-- /knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
## Verified project-level configuration

The project settings configure the 3D physics engine as `Jolt Physics`, the Windows rendering driver as `d3d12`, and the display stretch behavior as `canvas_items` with aspect `expand`. These are settings, not evidence of implemented physics, rendering, or gameplay systems.

## Not established

No player, input, combat, movement, UI, save/load, audio, networking, AI, or other gameplay behavior is documented because none can be verified from the available project files.

See [[Architecture Overview]], [[Scene Inventory]], and [[Development Configuration]].
