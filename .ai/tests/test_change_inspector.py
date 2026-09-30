import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "change_inspector.py"
SPEC = importlib.util.spec_from_file_location("change_inspector_under_test", SCRIPT)
assert SPEC and SPEC.loader
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)


MAIN_SCENE_FIXTURE = '''[gd_scene load_steps=2 format=3]

[ext_resource type="PackedScene" path="res://Player.tscn" id="1_player"]

[node name="Main" type="Node2D"]

[node name="Player" parent="." instance=ExtResource("1_player")]

[node name="Backdrop" type="Sprite2D" parent="."]
'''


class SceneInstanceInspectionTests(unittest.TestCase):
    def test_instance_node_uses_scene_path_without_guessing_runtime_type(self):
        scene = inspector.inspect_scene(MAIN_SCENE_FIXTURE)
        player = next(node for node in scene["nodes"] if node["name"] == "Player")

        self.assertNotIn("type", player)
        self.assertEqual(player["instance"], "res://Player.tscn")
        self.assertEqual(inspector.scene_node_type(player), "instance of res://Player.tscn")
        self.assertEqual(scene["instances"], [("Player", "res://Player.tscn")])

    def test_scene_tree_reports_instance_and_declared_child_types(self):
        scene = inspector.inspect_scene(MAIN_SCENE_FIXTURE)

        self.assertEqual(
            inspector.scene_nodes_lines(scene),
            [
                "- Main (Node2D)",
                "  - Player (instance of res://Player.tscn)",
                "  - Backdrop (Sprite2D)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
