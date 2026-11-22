"""Unit tests for python_* MCP tools, feature gating (default OFF), and UI persist/action extensions."""
from __future__ import annotations

import json
import unittest

from mcp import python_tools, workspace_tools
from mcp.mcp_server import BridgeError, ConnectionConfig, FeatureRegistry, McpServer, ToolDispatcher


class PythonToolsTests(unittest.TestCase):
    def test_default_feature_is_off_and_blocks_calls(self) -> None:
        reg = FeatureRegistry()
        self.assertFalse(reg.enabled("python"))
        disp = ToolDispatcher(ConnectionConfig(auto_adb_forward=False), reg)
        srv = McpServer(disp)
        listed = {t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]}
        for t in python_tools.TOOLS:
            self.assertNotIn(t["name"], listed)
        with self.assertRaisesRegex(BridgeError, "python"):
            disp.call("python_status", {})

    def test_enabling_python_feature_exposes_all_13_tools(self) -> None:
        reg = FeatureRegistry()
        reg.set("python", True)
        disp = ToolDispatcher(ConnectionConfig(auto_adb_forward=False), reg)
        srv = McpServer(disp)
        listed = {t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]["tools"]}
        self.assertEqual(len(python_tools.TOOLS), 13)
        for t in python_tools.TOOLS:
            self.assertIn(t["name"], listed)

    def test_encode_all_13_tools_and_reject_invalid(self) -> None:
        cases = [
            ("python_status", {}, "status"),
            ("python_run", {"code": "print(1)", "timeout_ms": 2000}, "run"),
            ("python_script_save", {"name": "godmode.py", "code": "x=1", "scope": "package"}, "script_save"),
            ("python_script_get", {"name": "godmode.py", "scope": "global"}, "script_get"),
            ("python_script_list", {"scope": "all"}, "script_list"),
            ("python_script_remove", {"name": "godmode.py"}, "script_remove"),
            ("python_load", {"name": "godmode.py", "scope": "package", "entry": "main"}, "load"),
            ("python_unload", {"name": "godmode.py"}, "unload"),
            ("python_reload", {"name": "godmode.py"}, "reload"),
            ("python_autorun_set", {"package": "com.example.game", "entries": [{"name": "godmode.py", "when": "il2cpp_ready", "enabled": True}]}, "autorun_set"),
            ("python_autorun_list", {}, "autorun_list"),
            ("python_logs", {"after": 0, "limit": 50}, "logs"),
            ("python_stop_all", {}, "stop_all"),
        ]
        for tool_name, args, op in cases:
            cmd = python_tools.encode(tool_name, args)
            self.assertTrue(cmd.startswith("PY_CONTROL "))
            decoded = json.loads(bytes.fromhex(cmd.split(" ", 1)[1]).decode("utf-8"))
            self.assertEqual(decoded["op"], op)

        for bad_tool, bad_args in [
            ("python_script_save", {"name": "../evil.py", "code": "1"}),
            ("python_script_save", {"name": "noext", "code": "1"}),
            ("python_load", {"name": "ok.py", "entry": "123bad"}),
            ("python_autorun_list", {"package": "../bad"}),
            ("python_autorun_set", {"entries": [{"name": "ok.py", "when": "never"}]}),
        ]:
            with self.assertRaises(ValueError, msg=(bad_tool, bad_args)):
                python_tools.encode(bad_tool, bad_args)

    def test_workspace_ui_persist_and_python_action(self) -> None:
        valid_tree = {
            "tree": {
                "windows": [{"id": "mod", "title": "Mod"}],
                "nodes": [
                    {
                        "id": "godmode",
                        "window": "mod",
                        "type": "toggle",
                        "label": "God",
                        "persist": True,
                        "action": {"python": "godmode.py", "entry": "on_toggle", "scope": "package"},
                    }
                ],
            }
        }
        cmd = workspace_tools.encode("overlay_apply_tree", valid_tree)
        self.assertTrue(cmd.startswith("UI_TREE_APPLY "))

        bad_both = {
            "node": {
                "id": "bad",
                "window": "mod",
                "type": "button",
                "action": {"command": "OVERLAY_RESET", "arguments": "", "python": "godmode.py"},
            }
        }
        with self.assertRaises(ValueError):
            workspace_tools.encode("overlay_upsert_node", bad_both)


if __name__ == "__main__":
    unittest.main()
