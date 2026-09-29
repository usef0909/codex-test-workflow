# Project Decisions and Unestablished Areas

## Established entry-scene decision

The project uses `Main.tscn` as its configured entry scene, with a `Node2D` root named `Main`. This is reflected in `project.godot` and the scene file. The requested change specified this minimal form; no broader design rationale or gameplay purpose is established by the project.

## Still unestablished

This page records areas where the inspected project files do not establish a decision. It does not make or propose those decisions.

- Game identity and design: not established by the placeholder directory name or configured project name.
- Scene flow after the main scene: no other scenes or transitions are present.
- Runtime architecture and system boundaries beyond the entry scene: no implementation exists from which to infer them.
- Target platforms and export process: no export presets or build workflow are present in the inspected project tree.
- External dependencies: no package manifests, vendored addons, or plugin enablement settings are present. The exact source of the configured Jolt physics engine is not identified by these files.
- Testing and validation workflow: no test code or project-specific test configuration is present.

The project settings do explicitly select a feature set, display behavior, 3D physics engine, Windows rendering driver, and main scene; these are recorded in [[Development Configuration]]. No broader rationale for those settings is stated in the project files.
