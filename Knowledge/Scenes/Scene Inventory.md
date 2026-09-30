# Scene Inventory

## Main scene

`Main.tscn` is the only tracked Godot scene in the inspected project. `project.godot` sets `run/main_scene` to `res://Main.tscn`, making it the configured project entry scene.

### Node tree

```text
Main (Node2D)
```

The scene contains only the root node. It has no child nodes, attached script, or signal connections. No gameplay behavior is defined in this scene.

See [[Project Overview]] and [[Architecture Overview]].

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
### Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.
- `res://Main.tscn` instances `res://Player.tscn` as node `Player`.
- `res://Player.tscn` contains child `Sprite2D` (Sprite2D) under `.`.
- `res://Player.tscn` has root `Player` of type `CharacterBody2D`.
<!-- /knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->