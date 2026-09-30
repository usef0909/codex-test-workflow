# Architecture Overview

## Current shape

`Main.tscn` remains configured as the main scene and has a `Main` `Node2D` root. It instances `Player.tscn` as node `Player`; `Player.tscn` has a `Player` `CharacterBody2D` root with `Player.gd` attached to node `Player`.

This establishes the project's startup scene and root node type. It does not establish gameplay behavior, scene transitions, modules, or a broader runtime architecture.

## Verified relationships

- `project.godot` sets `run/main_scene` to `res://Main.tscn`.
- `Main.tscn` defines a root node named `Main` of type `Node2D`.
- `project.godot` names `res://icon.svg` as the project icon.
- `icon.svg.import` identifies `icon.svg` as its source and records a generated import destination beneath `.godot/imported/`.
- `.gitignore` excludes `.godot/`, so the import destination is a generated/editor cache rather than a tracked project source file.
- The project settings select the `Jolt Physics` 3D physics engine and the Windows `d3d12` rendering driver.

The main-scene/root-node choice was made to satisfy the requested minimal project entry point. No further rationale or gameplay role is encoded in the project files.

<!-- knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
### Verified additions from commit `5472bacbe12818379b9a20771885b63ea4725ea0`
- Input action `move_down` is configured with `physical key S (83)`.
- Input action `move_left` is configured with `physical key A (65)`.
- Input action `move_right` is configured with `physical key D (68)`.
- Input action `move_up` is configured with `physical key W (87)`.
- Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.
- `res://Main.tscn` instances `res://Player.tscn` as node `Player`.
- `res://Player.gd` calls `Input.get_vector()`.
<!-- /knowledge-applier:72aeea4cdb3bb1c509fc70a070ff5811bebc36c40ca9afea5c611645abadb642 -->
## Repository layout

The Godot project is contained in `(Game Name)/`. The project knowledge vault is a sibling repository directory at `Knowledge/`. Both are tracked in the same Git repository, but the vault is documentation and Obsidian configuration rather than part of the Godot project directory.

## Not yet established

No runtime responsibilities beyond the entry node, data flow, global state, event patterns, gameplay systems, or system boundaries are established by the current project files.

See [[Development Configuration]], [[Scene Inventory]], [[Code Inventory]], [[Project Assets and Resources]], and [[Systems Inventory]].
