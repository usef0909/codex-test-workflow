# Architecture Overview

## Current shape

The inspected Godot project is a minimal project shell. Its tracked project directory contains configuration files and an icon asset/import record; it has no tracked scenes or scripts.

There is not yet a verifiable runtime architecture: no scene graph, scene transitions, code modules, autoloads, signals, custom resources, or gameplay systems appear in the project tree. The configured main scene is also absent.

## Verified relationships

- `project.godot` names `res://icon.svg` as the project icon.
- `icon.svg.import` identifies `icon.svg` as its source and records a generated import destination beneath `.godot/imported/`.
- `.gitignore` excludes `.godot/`, so the import destination is a generated/editor cache rather than a tracked project source file.
- The project settings select the `Jolt Physics` 3D physics engine and the Windows `d3d12` rendering driver.

These are configuration and asset relationships only; they do not establish gameplay or runtime architecture.

## Repository layout

The Godot project is contained in `(Game Name)/`. The project knowledge vault is a sibling repository directory at `Knowledge/`. Both are tracked in the same Git repository, but the vault is documentation and Obsidian configuration rather than part of the Godot project directory.

## Not yet established

No architecture decisions, runtime responsibilities, data flow, scene ownership, global state, event/signal patterns, or system boundaries can be derived because the project contains no corresponding implementation files.

See [[Development Configuration]], [[Scene Inventory]], [[Code Inventory]], [[Project Assets and Resources]], and [[Systems Inventory]].
