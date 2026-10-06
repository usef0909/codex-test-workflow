# Change Records

## Add minimal main scene

Created `(Game Name)/Main.tscn` with one root node, `Main` of type `Node2D`, and no children or attached script. Updated `(Game Name)/project.godot` so `run/main_scene` points to `res://Main.tscn`.

The change establishes a project entry scene and a minimal root node. It adds no gameplay behavior, other systems, or scene relationships. The Knowledge notes were updated to reflect the current project state.

See [[Scene Inventory]], [[Architecture Overview]], and [[Development Configuration]].

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36ca9afea5c611645abadb642 -->
## Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Commit `5472bacbe12818379b9a20771885b63ea4725ea0` records `added` of `(Game Name)/Player.gd`.
- Commit `5472bacbe12818379b9b20771885b63ea4725ea0` records `added` of `(Game Name)/Player.tscn`.
- Commit `5472bacbe12818379b9a20771885b63ea4725ea0` records `modified` of `(Game Name)/Main.tscn`.
- Commit `5472bacbe12818379b9a20771885b63ea4725ea0` records `modified` of `(Game Name)/project.godot`.

## Add player movement feature

Commit `5472bacbe12818379b9a20771885b63ea4725ea0` added `Player.gd` and `Player.tscn`, and modified `Main.tscn` and `project.godot`. `Main.tscn` now instances `Player.tscn` as node `Player`. The `Player` scene has a `CharacterBody2D` root and attaches `Player.gd`.
