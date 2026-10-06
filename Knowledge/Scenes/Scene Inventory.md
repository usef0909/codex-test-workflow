# Scene Inventory

## Main scene

`Main.tscn` is the configured Godot entry scene. `project.godot` sets `run/main_scene` to `res://Main.tscn`.

### Node tree

```text
Main.tscn (entry scene)
└─ Main (Node2D)
   └─ Player (instance of res://Player.tscn)

Player.tscn
└─ Player (CharacterBody2D)
   └─ Sprite2D (Sprite2D)
```

`Main.tscn` instances `Player.tscn` as node `Player`. `Player.tscn` has a `Player` `CharacterBody2D` root and attaches `Player.gd` to that root.

See [[Project Overview]] and [[Architecture Overview]].

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
### Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.
- `res://Main.tscn` instances `res://Player.tscn` as node `Player`.
- `res://Player.tscn` contains child `Sprite2D` (Sprite2D) under `.`.
- `res://Player.tscn` has root `Player` of type `CharacterBody2D`.
<!-- /knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36ca9afea5c611645abadb642 -->
