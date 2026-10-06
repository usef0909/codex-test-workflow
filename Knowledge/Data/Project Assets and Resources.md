# Project Assets and Resources

## Scene resource

### `Main.tscn`

`project.godot` selects `res://Main.tscn` as the main scene. `res://Main.tscn` has a `Main` `Node2D` root and instances `res://Player.tscn` as node `Player`. `res://Player.tscn` has a `Player` `CharacterBody2D` root. See [[Scene Inventory]].

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
### Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.
- Node `Sprite2D` in `res://Player.tscn` assigns `res://icon.svg` to `texture`.
- Scene `res://Player.tscn` exists.
<!-- /knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
## Tracked asset

### `icon.svg`

The project contains one SVG asset, referenced by `application/icon` in `project.godot`. Its SVG source draws a Godot-style emblem. The file's verified project role is the configured project icon.

### `icon.svg.import`

Godot import metadata associates the SVG with the `texture` importer and `CompressedTexture2D` type. It gives the resource UID `uid://eoo2rvik56kv` and points to an imported cache artifact beneath `res://.godot/imported/`. That generated destination is excluded by `.gitignore`.

## Other resources and data

`Player.tscn` attaches `Player.gd` to its `Player` root. Its `Sprite2D` node assigns `res://icon.svg` to `texture`.

See [[Development Configuration]] for project settings and [[Architecture Overview]] for the verified icon/import relationship.
