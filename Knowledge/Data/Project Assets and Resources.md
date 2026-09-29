# Project Assets and Resources

## Scene resource

### `Main.tscn`

The project contains a single scene resource. It defines a root node named `Main` of type `Node2D`, with no child nodes or attached script. `project.godot` references it as the main scene. See [[Scene Inventory]].

## Tracked asset

### `icon.svg`

The project contains one SVG asset, referenced by `application/icon` in `project.godot`. Its SVG source draws a Godot-style emblem. The file's verified project role is the configured project icon.

### `icon.svg.import`

Godot import metadata associates the SVG with the `texture` importer and `CompressedTexture2D` type. It gives the resource UID `uid://eoo2rvik56kv` and points to an imported cache artifact beneath `res://.godot/imported/`. That generated destination is excluded by `.gitignore`.

## Other resources and data

No scripts, custom resource files, data files, or other runtime assets are present in the inspected project tree.

See [[Development Configuration]] for project settings and [[Architecture Overview]] for the verified icon/import relationship.
