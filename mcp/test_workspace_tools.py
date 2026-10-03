"""Protocol/schema regressions; uses a mock bridge, never connects to a phone."""
import json
import unittest
from unittest.mock import patch

from mcp import workspace_tools as wt, render_tools as rt
from mcp.mcp_server import ToolDispatcher, ConnectionConfig, BridgeError, TOOLS

SELECTION = {"kind": "memory", "address": "0x1234", "label": "测试"}
EXAMPLES = {
    "unity_resource_monitor": {"op": "status"},
    "frida_control": {"op": "status"},
    "il2cpp_generic_resolve": {"type": {"image": "mscorlib.dll", "namespace": "System.Collections.Generic", "class_name": "List`1"}, "type_arguments": [{"image": "mscorlib.dll", "namespace": "System", "class_name": "Int32"}]},
    "il2cpp_parameter_schema": {"image": "Assembly-CSharp.dll", "class": "Role", "token": 100663297},
    "workspace_browser": {"op": "create", "tab": {"name": "NPC", "query": "npc", "kind": 0}},
    "workspace_caller": {"op": "get", "method": {"image": "Assembly-CSharp.dll", "class": "Role", "token": 100663297}},
    "overlay_upsert_window": {"descriptor": {"id": "root", "title": "父窗口"}},
    "overlay_upsert_node": {"descriptor": {"id": "name", "window": "root", "type": "text", "text": "中文"}},
    "overlay_apply_tree": {"tree": {"windows": [{"id": "child", "parent": "root"}, {"id": "root"}],
        "nodes": [{"id": "tabs", "window": "root", "type": "tabs"}, {"id": "tab", "window": "root", "parent": "tabs", "type": "tab"}]}},
    "overlay_get_tree": {}, "overlay_remove_tree": {"kind": "window", "id": "root"},
    "overlay_tree_events": {}, "overlay_set_variable": {"id": "count", "value": {"values": [1, True, "中文"]}},
    "overlay_program_set": {"descriptor": {"id": "test", "source": "function on_frame(ctx)\nprint(#ctx.objects)\nend", "enabled": False, "allow_game_actions": False}},
    "overlay_program_list": {}, "overlay_program_get": {"id": "test"},
    "overlay_program_control": {"id": "test", "operation": "stop"}, "overlay_program_remove": {"id": "test"},
    "render_set_rule": {"descriptor": {"id": "health", "class_contains": "Npc", "bindings": {"hp": "health"}, "label": "{name}: {hp}"}},
    "render_list_rules": {}, "render_remove_rule": {"id": "health"}, "workspace_state": {},
    "workspace_navigate": {"selection": SELECTION}, "workspace_history": {"direction": "back"},
    "workspace_bookmark_set": {"id": "b", "selection": SELECTION}, "workspace_bookmark_remove": {"id": "b"}, "workspace_bookmark_open": {"id": "b"},
    "unity_hierarchy": {"mode": "scenes"}, "il2cpp_inspector_members": {"address": "0x1234"},
    "unity_object_resources": {"address": "0x1234"},
    "unity_resource_preview": {"address": "0x1234", "max_edge": 512},
    "unity_resource_export": {"address": "0x1234", "name": "texture"},
    "unity_material_properties": {"renderer": "0x1234", "material_index": 1},
    "unity_material_set_property": {"renderer": "0x1234", "property": "_DissolveColor", "value": [0, 1, 0, 1]},
    "unity_material_restore": {"renderer": "0x1234", "material_index": 1},
    "il2cpp_field_read": {"address": "0x1234", "field": "Role::hp"},
    "il2cpp_field_write": {"address": "0x1234", "field": "hp", "value": {"type": "i64", "value": "9223372036854775807"}},
    "il2cpp_call_exact": {"image": "Assembly-CSharp.dll", "class": "Role", "token": 100663297,
        "arguments": [True, {"enum": "Grounded"}, {"address": "0x1234"}, None]},
    "il2cpp_dictionary_items": {"address": "0x1234"}, "workspace_result": {"request_id": 1},
    "workspace_export_result": {"request_id": 1, "destination": "file", "filename": "object.json"},
    "workspace_export_text": {"text": "中文\n😀", "destination": "clipboard", "filename": "data.txt"},
    "workspace_export_job": {"owner": "workspace.decomp", "destination": "file", "filename": "function.c"},
    "workspace_export_logs": {"destination": "file", "filename": "logs.json", "filter": "IL2CPP"},
    "workspace_preset": {"operation": "save", "name": "default"},
}


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))

    def test_every_tool_has_example_and_dispatches(self):
        self.assertEqual(set(EXAMPLES), set(wt.BY_NAME))
        for name, arguments in EXAMPLES.items():
            with self.subTest(name=name), patch.object(self.dispatcher, "_json_call", return_value={"success": True}) as native, patch.object(self.dispatcher, "_notify_mcp_call"):
                result = self.dispatcher.call(name, arguments)
                self.assertEqual(result, {"success": True})
                native.assert_called_once_with(wt.encode(name, arguments, self.dispatcher._invoke_token))
                command = native.call_args.args[0]
                self.assertNotIn("\n", command)
                self.assertLess(len(command.encode()), 256*1024)

    def test_help_explains_every_new_tool(self):
        for name in EXAMPLES:
            with self.subTest(name=name):
                result = self.dispatcher.debug_help({"command": name})
                self.assertEqual(result["tool"], name)
                self.assertEqual(result["inputSchema"], wt.BY_NAME[name]["inputSchema"])
                self.assertGreater(len(result["description"]), 35)

    def test_no_duplicate_or_admin_tools(self):
        names = [t["name"] for t in TOOLS]
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse(set(names) & {"mcp_admin_info", "mcp_list_features", "mcp_set_all_features", "mcp_set_feature"})

    def test_history_supports_direct_path_selection_without_ambiguous_arguments(self):
        self.assertEqual(wt.encode("workspace_history",{"index":0}),"WORKSPACE_HISTORY 0")
        self.assertEqual(wt.encode("workspace_history",{"index":12}),"WORKSPACE_HISTORY 12")
        self.assertEqual(wt.encode("workspace_history",{"direction":"forward"}),"WORKSPACE_HISTORY forward")
        self.assertEqual(wt.encode("workspace_state",{"scope":"object"}),"WORKSPACE_STATE object")
        self.assertEqual(wt.encode("workspace_history",{"index":1,"scope":"object"}),"WORKSPACE_HISTORY 1 object")
        for args in ({},{"index":-1},{"index":True},{"index":64},{"direction":"back","index":2},{"index":0,"scope":"bad"}):
            with self.assertRaises(ValueError):wt.encode("workspace_history",args)

    def test_frame_calls_are_queued_and_i64_is_exact(self):
        command = wt.encode("il2cpp_field_write", EXAMPLES["il2cpp_field_write"], self.dispatcher._invoke_token)
        native, subcommand, encoded = command.split()
        self.assertEqual((native, subcommand), ("WORKSPACE_QUERY", "IL2CPP_FIELD_SET"))
        parts = bytes.fromhex(encoded).decode().split()
        self.assertEqual(bytes.fromhex(parts[2][1:]).decode(), "9223372036854775807")
        call = wt.encode("il2cpp_call_exact", EXAMPLES["il2cpp_call_exact"], self.dispatcher._invoke_token)
        self.assertEqual(call.split()[1], "IL2CPP_CALL_EXACT")
        self.assertTrue(bytes.fromhex(call.split()[2]).decode().endswith(" z"))

    def test_bad_inputs_rejected_before_transport(self):
        bad = [
            ("overlay_upsert_window", {"descriptor": {"id": "x", "script": "evil"}}),
            ("overlay_upsert_node", {"descriptor": {"id": "x", "action": {"command": "IL2CPP_CALL_EXACT", "arguments": "-"}}}),
            ("overlay_upsert_node", {"descriptor": {"id": "x", "binding": {"source": "object", "id": "o", "path": "position.x", "write": True}}}),
            ("overlay_apply_tree", {"tree": {"windows": [{"id": str(i)} for i in range(33)]}}),
            ("overlay_program_set", {"descriptor": {"id": "x", "source": "\x1bLua"}}),
            ("overlay_program_set", {"descriptor": {"id": "x", "source": "中" * 30000}}),
            ("workspace_export_text", {"text": "x", "destination": "file", "filename": "../bad.txt"}),
            ("workspace_export_text", {"text": "x", "destination": "file", "filename": "C:\\bad.txt"}),
            ("workspace_preset", {"operation": "save"}),
            ("workspace_preset", {"operation": "load", "name": "x", "json": "{}"}),
            ("unity_hierarchy", {"mode": "children"}),
            ("workspace_result", {"request_id": True}),
            ("render_set_rule", {"descriptor": {"id": "x", "bindings": {"hp": 1}}}),
            ("overlay_set_variable", {"id": "x", "value": ["x" * 4096]}),
        ]
        for name, args in bad:
            with self.subTest(name=name, args=str(args)[:140]), patch.object(self.dispatcher, "_json_call") as native, self.assertRaises(BridgeError):
                self.dispatcher._workspace_call(name, args)
            native.assert_not_called()

    def test_nested_json_unicode_exports(self):
        command = wt.encode("overlay_apply_tree", EXAMPLES["overlay_apply_tree"])
        self.assertEqual(json.loads(bytes.fromhex(command.split()[1])), EXAMPLES["overlay_apply_tree"]["tree"])
        command = wt.encode("workspace_export_text", EXAMPLES["workspace_export_text"])
        self.assertEqual(bytes.fromhex(command.split()[-1]).decode(), "中文\n😀")
        self.assertTrue(wt.encode("workspace_export_result", EXAMPLES["workspace_export_result"]).startswith("WORKSPACE_EXPORT_RESULT 1 file "))

    def test_program_restarts_inspect_saved_grant(self):
        with patch.object(self.dispatcher, "_json_call", side_effect=[{"allow_game_actions": False}, {"enabled": True}]) as native:
            self.assertEqual(self.dispatcher._workspace_call("overlay_program_control", {"id": "x", "operation": "start"}), {"enabled": True})
            self.assertEqual(native.call_args_list[0].args[0], "UI_PROGRAM_GET 78")

    def test_mesh_bezier_and_clip_validation(self):
        mesh = {"id": "mesh", "type": "mesh", "space": "screen", "points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                "indices": [0, 1, 2], "filled": True, "foreground": True, "clip_rect": [0, 0, 1, 1]}
        command = rt.encode("render_set_primitive", {"descriptor": mesh})
        self.assertEqual(json.loads(bytes.fromhex(command.split()[1])), mesh)
        for patch_value in ({"indices": [0, 1, 9]}, {"indices": [0, 1]}, {"clip_rect": [0, 0, 0, 1]}, {"points": [[100, 0, 0]] * 3}):
            with self.subTest(patch_value=patch_value), self.assertRaises(ValueError):
                rt.encode("render_set_primitive", {"descriptor": {**mesh, **patch_value}})
        rt.encode("render_set_primitive", {"descriptor": {"id": "curve", "type": "bezier", "points": [[0, 0, 0]] * 4}})


if __name__ == "__main__":
    unittest.main()
