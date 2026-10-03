from __future__ import annotations
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import render_tools as rt
from mcp.mcp_server import BridgeError, ConnectionConfig, FeatureRegistry, ToolDispatcher, tools_for_registry


EXAMPLES = {
    "overlay_status": {}, "overlay_reset": {},
    "overlay_set": {"key": "theme", "value": "classic"},
    "overlay_set_window": {"x": 10, "y": 20, "width": 400, "height": 600},
    "render_status": {}, "render_list_objects": {}, "render_clear_objects": {},
    "render_list_cameras": {}, "render_unbind_update": {}, "render_binding_status": {},
    "render_set_style": {"key": "color", "value": "b39dffff"},
    "render_add_object": {"id": "测试 对象", "label": "Player 中文"},
    "render_update_object": {"id": "a", "key": "label", "value": "新的名字\n第二行"},
    "render_set_object_position": {"id": "a", "position": [1, 2, 3]},
    "render_set_object_bones": {"id": "a", "segments": [[[1, 2, 3], [4, 5, 6]]]},
    "render_remove_object": {"id": "a"},
    "render_set_camera": {"mode": "address", "address": "0x1234"},
    "render_set_camera_matrix": {"matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
    "render_project": {"position": [0, 0, 1], "surface_width": 1920, "surface_height": 1080},
    "render_bind_update": {"image": "Assembly-CSharp.dll", "class": "Player", "method": "LateUpdate"},
    "render_find_objects": {"image": "Assembly-CSharp.dll", "class": "Player"},
    "render_track_class": {"id": "npc", "image": "Assembly-CSharp.dll", "class": "Npc"},
    "render_list_tracked_classes": {}, "render_untrack_class": {"id": "npc"},
    "render_refresh_class": {"id": "npc"}, "render_refresh_cameras": {},
    "render_projection_result": {"request_id": 1},
    "overlay_set_panel": {"descriptor": {"id": "custom", "title": "调试面板"}},
    "overlay_set_widget": {"descriptor": {"id": "names", "panel": "custom", "type": "toggle", "value": True,
        "action": {"command": "RENDER_STYLE", "arguments": "names {value}"}}},
    "overlay_list_custom_ui": {}, "overlay_remove_custom_ui": {"kind": "panel", "id": "custom"},
    "overlay_ui_events": {}, "overlay_call_logs": {}, "overlay_clear_call_logs": {},
    "render_set_primitive": {"descriptor": {"id": "label", "type": "text", "space": "screen",
        "points": [[.5, .2, 0]], "text": "中文"}},
    "render_list_primitives": {}, "render_remove_primitive": {"id": "label"}, "render_clear_primitives": {},
}


class RenderToolsTests(unittest.TestCase):
    def test_window_can_move_off_screen_without_schema_clamping(self):
        args = {"x": -120, "y": -48, "width": 1200, "height": 900}
        self.assertEqual(rt.encode("overlay_set_window", args), "OVERLAY_WINDOW -120 -48 1200 900")
        schema = rt.BY_NAME["overlay_set_window"]["inputSchema"]["properties"]
        self.assertEqual(schema["x"]["minimum"], -32768)
        self.assertEqual(schema["y"]["minimum"], -32768)

    def setUp(self):
        self.dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))

    def test_all_tools_have_encoders_runtime_routes_and_help(self):
        self.assertEqual(set(rt.BY_NAME), set(EXAMPLES))
        cpp = Path(__file__).resolve().parents[1] / "module/src/main/cpp"
        runtime = (cpp / "runtime_bridge.cpp").read_text(encoding="utf-8") + (cpp / "native_command_catalog.h").read_text(encoding="utf-8")
        for name, args in EXAMPLES.items():
            with self.subTest(name=name):
                encoded = rt.encode(name, args)
                self.assertNotIn("\n", encoded)
                command = encoded.split()[0]
                self.assertIn('{"' + command + '", "' + command, runtime)
                with patch.object(self.dispatcher, "_notify_mcp_call"), patch.object(self.dispatcher, "_json_call", return_value={"ok": True}) as call:
                    self.assertEqual({"ok": True}, self.dispatcher.call(name, args))
                    call.assert_called_once_with(encoded)

    def test_text_is_hex_encoded_including_newlines(self):
        encoded = rt.encode("render_add_object", EXAMPLES["render_add_object"]).split()
        self.assertEqual(bytes.fromhex(encoded[1]).decode(), "测试 对象")
        self.assertEqual(bytes.fromhex(encoded[3]).decode(), "Player 中文")
        self.assertEqual(rt.encode("render_find_objects", EXAMPLES["render_find_objects"]).split()[2], "-")
        self.assertEqual(rt.encode("render_find_objects", EXAMPLES["render_find_objects"]).split()[-1], "false")

    def test_matrix_layout_and_manual_bone_arity(self):
        self.assertEqual(len(rt.encode("render_set_camera_matrix", EXAMPLES["render_set_camera_matrix"]).split()), 24)
        self.assertEqual(len(rt.encode("render_set_object_bones", EXAMPLES["render_set_object_bones"]).split()), 8)
        self.assertEqual(rt.encode("render_set_object_bones", {"id": "a", "segments": []}), "RENDER_OBJECT_BONES 61")

    def test_malformed_values_rejected_without_target_call(self):
        invalid = [
            ("overlay_set", {"key": "theme", "value": "custom\nRENDER_CLEAR"}),
            ("overlay_set", {"key": "scale", "value": True}),
            ("overlay_set", {"key": "visible", "value": "true"}),
            ("overlay_set", {"key": "alpha", "value": float("nan")}),
            ("render_set_style", {"key": "color", "value": "FF00FF"}),
            ("render_set_style", {"key": "sample_hz", "value": 60}),
            ("render_set_style", {"key": "layer_mask", "value": 1.5}),
            ("render_add_object", {"id": ""}),
            ("render_add_object", {"id": "中" * 33}),
            ("render_add_object", {"id": "x", "address": -1}),
            ("render_add_object", {"id": "x", "address": 2**64}),
            ("render_add_object", {"id": "x", "address": "0x1\nPING"}),
            ("render_add_object", {"id": "x", "position": [1, 2]}),
            ("render_set_object_bones", {"id": "x", "segments": [[[0, 0, 0], [1, 1, 1]]] * 65}),
            ("render_set_camera", {"mode": "address"}),
            ("render_set_camera_matrix", {"matrix": [0] * 15}),
            ("render_set_camera_matrix", {"matrix": [0] * 16, "viewport": [1, 0, 1, 1]}),
            ("render_bind_update", {"image": "a", "class": "b", "method": "Destroy"}),
            ("render_find_objects", {"image": "a", "class": "b", "limit": True}),
            ("render_find_objects", {"image": "a", "class": "b", "include_inactive": 1}),
            ("render_status", {"unexpected": 1}),
        ]
        with patch.object(self.dispatcher, "_notify_mcp_call"), patch.object(self.dispatcher, "_json_call") as target:
            for name, args in invalid:
                with self.subTest(name=name, args=args), self.assertRaises(BridgeError):
                    self.dispatcher.call(name, args)
            target.assert_not_called()

    def test_all_settings_accept_expected_types(self):
        for fields, name in [(rt.STYLE_FIELDS, "render_set_style"), (rt.UI_FIELDS, "overlay_set"), (rt.OBJECT_FIELDS, "render_update_object")]:
            for key, schema in fields.items():
                value = (True if schema["type"] == "boolean" else schema["enum"][0] if "enum" in schema
                         else schema.get("minimum", 1) if schema["type"] in {"integer", "number"}
                         else "ffffffff" if key in {"color", "occluded_color"} else "中文")
                args = {"key": key, "value": value}
                if name == "render_update_object":
                    args["id"] = "x"
                self.assertTrue(rt.encode(name, args))

    def test_new_features_default_on_without_exposing_admin_tools(self):
        registry = FeatureRegistry()
        names = {entry["name"] for entry in tools_for_registry(registry)}
        self.assertTrue(set(rt.BY_NAME) <= names)
        self.assertTrue(registry.enabled("rendering"))
        self.assertTrue(registry.enabled("overlay_ui"))
        for forbidden in ("mcp_admin_info", "mcp_list_features", "mcp_set_all_features", "mcp_set_feature"):
            self.assertNotIn(forbidden, names)

    def test_descriptors_roundtrip_and_patch_preservation(self):
        for name in ("overlay_set_panel", "overlay_set_widget", "render_set_primitive"):
            encoded = rt.encode(name, EXAMPLES[name])
            data = json.loads(bytes.fromhex(encoded.split()[1]))
            self.assertEqual(data, EXAMPLES[name]["descriptor"])
            patch_data = {"id": "item", "visible": False}
            encoded = rt.encode(name, {"descriptor": patch_data})
            self.assertEqual(json.loads(bytes.fromhex(encoded.split()[1])), patch_data)
        encoded = rt.encode("overlay_set_widget", {"descriptor": {"id": "x", "action": None}})
        self.assertIsNone(json.loads(bytes.fromhex(encoded.split()[1]))["action"])

    def test_strict_nested_descriptors(self):
        bad = [
            ("overlay_set_panel", {"id": "p", "title": "x", "script": "bad"}),
            ("overlay_set_widget", {"id": "w", "action": {"command": "MEMORY_WRITE", "arguments": "0x0 ff"}}),
            ("overlay_set_widget", {"id": "w", "action": {"command": "RENDER_CLEAR", "arguments": "\nPING"}}),
            ("overlay_set_widget", {"id": "w", "action": {"command": "RENDER_CLEAR"}}),
            ("overlay_set_widget", {"id": "w", "options": ["a"] * 33}),
            ("overlay_set_widget", {"id": "w", "value": float("nan")}),
            ("render_set_primitive", {"id": "p", "type": "line", "points": [[0, 0, 0]]}),
            ("render_set_primitive", {"id": "p", "color": "12345zzz"}),
            ("render_set_primitive", {"id": "p", "space": "screen", "points": [[100, 200, 0]]}),
            ("render_set_primitive", {"id": "p", "points": [[0, 0, 0]] * 65}),
            ("render_set_primitive", {"id": "p", "points": [[0, 0, float("inf")]]}),
        ]
        with patch.object(self.dispatcher, "_notify_mcp_call"), patch.object(self.dispatcher, "_json_call") as call:
            for name, data in bad:
                with self.subTest(name=name, data=data), self.assertRaises(BridgeError):
                    self.dispatcher.call(name, {"descriptor": data})
            call.assert_not_called()

    def test_tracking_defaults_and_native_feature_mapping(self):
        encoded = rt.encode("render_track_class", EXAMPLES["render_track_class"]).split()
        self.assertEqual(encoded[-5:], ["0", "false", "true", "2000", "replace"])
        self.assertEqual(len(encoded), 10)
        for name, args in EXAMPLES.items():
            self.assertEqual(rt.native_features(rt.encode(name, args).split()[0]), rt.features(name))

    def test_unlimited_discovery_and_large_user_requested_counts(self):
        for name in ("render_find_objects","render_track_class"):
            for limit in (0,129,4096,100000):
                command=rt.encode(name,{**EXAMPLES[name],"limit":limit}).split()
                self.assertEqual(command[5] if name=="render_track_class" else command[4],str(limit))
            with self.assertRaises(ValueError):rt.encode(name,{**EXAMPLES[name],"limit":-1})

    def test_object_paging_is_not_a_registry_capacity_limit(self):
        self.assertEqual(rt.encode("render_list_objects",{}),"RENDER_OBJECTS")
        self.assertEqual(rt.encode("render_list_objects",{"offset":4096,"limit":128}),"RENDER_OBJECTS 4096 128")
        for args in ({"offset":-1},{"limit":0},{"limit":257}):
            with self.assertRaises(ValueError):rt.encode("render_list_objects",args)


if __name__ == "__main__":
    unittest.main()
