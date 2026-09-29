# Architecture Overview

## Current shape

The inspected Godot project has a minimal runtime entry point: `Main.tscn` is configured as the main scene, and its entire node tree is a single `Node2D` root named `Main`. It has no child nodes, attached script, or signal connections.

This establishes the project's startup scene and root node type. It does not establish gameplay behavior, scene transitions, modules, or a broader runtime architecture.

## Verified relationships

- `project.godot` sets `run/main_scene` to `res://Main.tscn`.
- `Main.tscn` defines a root node named `Main` of type `Node2D`.
- `project.godot` names `res://icon.svg` as the project icon.
- `icon.svg.import` identifies `icon.svg` as its source and records a generated import destination beneath `.godot/imported/`.
- `.gitignore` excludes `.godot/`, so the import destination is a generated/editor cache rather than a tracked project source file.
- The project settings select the `Jolt Physics` 3D physics engine and the Windows `d3d12` rendering driver.

The main-scene/root-node choice was made to satisfy the requested minimal project entry point. No further rationale or gameplay role is encoded in the project files.

## Repository layout

The Godot project is contained in `(Game Name)/`. The project knowledge vault is a sibling repository directory at `Knowledge/`. Both are tracked in the same Git repository, but the vault is documentation and Obsidian configuration rather than part of the Godot project directory.

## Not yet established

No runtime responsibilities beyond the entry node, data flow, global state, event patterns, gameplay systems, or system boundaries are established by the current project files.

See [[Development Configuration]], [[Scene Inventory]], [[Code Inventory]], [[Project Assets and Resources]], and [[Systems Inventory]].
