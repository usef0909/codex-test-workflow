# Development Configuration

Source: `(Game Name)/project.godot` and the project-root configuration files in the inspected `main` tree.

## Godot project settings

| Setting | Verified value | Why it matters |
|---|---|---|
| Configuration format | `config_version=5` | Identifies the project settings file format version; it is not the Godot engine version. |
| Project name | `codex workflow test` | Name configured for the project in the Godot application settings. |
| Feature tags | `4.7`, `Forward Plus` | Declares the project's Godot feature compatibility/rendering feature set. It does not prove the exact installed engine build. |
| Project icon | `res://icon.svg` | Selects the tracked SVG as the application/project icon. |
| Window stretch mode | `canvas_items` | Configures how canvas items are scaled with window stretching. |
| Window stretch aspect | `expand` | Configures the aspect handling for the stretch mode. |
| 3D physics engine | `Jolt Physics` | Selects Jolt for 3D physics in project settings. The project tree does not identify a separately vendored plugin or package. |
| Windows rendering driver | `d3d12` | Selects the Windows rendering driver setting. |

No main-scene setting is present in `project.godot`.

## Repository/editor configuration

- `.editorconfig`: root configuration, UTF-8 charset for all files.
- `.gitattributes`: automatic text detection and LF line endings for text files.
- `.gitignore`: excludes `.godot/` and `/android/`.
- `icon.svg.import`: imports `icon.svg` as a `CompressedTexture2D`; the destination is under the ignored `.godot/imported/` cache.

## Dependencies and tooling

The inspected project directory contains no addon/plugin directory, plugin enablement section, package manifest, lockfile, or separately tracked dependency declaration. The Jolt setting is verified as a project setting; whether it is supplied by the installed Godot distribution or another environment component is not determined by these files.

## Unknowns

No exact Godot patch version, editor installation, export presets, CI/build workflow, target platform list, external package inventory, or project-specific test setup is established by the inspected files.
