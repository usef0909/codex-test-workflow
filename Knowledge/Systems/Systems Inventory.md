# Systems Inventory

## Current status

No gameplay or technical runtime systems are implemented in the inspected project files. The project has a single bare entry scene, `Main.tscn`, whose root is a `Node2D` named `Main`; it has no children or attached script.

This inventory describes the current tree. It does not imply that a system is planned.

## Verified project-level configuration

The project settings configure the 3D physics engine as `Jolt Physics`, the Windows rendering driver as `d3d12`, and the display stretch behavior as `canvas_items` with aspect `expand`. These are settings, not evidence of implemented physics, rendering, or gameplay systems.

## Not established

No player, input, combat, movement, UI, save/load, audio, networking, AI, or other gameplay behavior is documented because none can be verified from the available project files.

See [[Architecture Overview]], [[Scene Inventory]], and [[Development Configuration]].
