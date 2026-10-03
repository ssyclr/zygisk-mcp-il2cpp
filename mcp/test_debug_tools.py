"""Mocked MCP contracts for interactive debugging; no device/build required."""
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from mcp import debug_tools as d, workspace_tools as w
from mcp.mcp_server import ToolDispatcher, ConnectionConfig, BridgeError, TOOLS

RECIPE = {"module": "libgame.so", "base_offset": "0x120", "offsets": ["0x20", -8]}
EXAMPLES = {
    "memory_search_tabs": {"op": "list"},
    "memory_batch_edit": {"tab_id": 1, "value": "7878", "confirm": True},
    "memory_chain_export": {"session_id": 7},
    "memory_chain_batch": {"chains": [RECIPE, "saved-one"]},
    "memory_chain_store": {"operation": "save", "id": "npc", "recipe": RECIPE},
    "memory_freeze_set": {"address": "0x1234", "type": "i64", "value": "9223372036854775807"},
    "memory_freeze_list": {}, "memory_freeze_control": {"operation": "pause", "id": 1},
    "dobby_trace_list": {}, "dobby_trace_control": {"address": "0x1234", "operation": "reset"},
    "dobby_trace_hits": {"address": "0x1234"}, "dobby_trace_hit": {"hit_id": 1},
    "breakpoint_control": {"address": "0x1234", "operation": "pause"},
    "il2cpp_type_graph": {"image": "Assembly-CSharp.dll", "namespace": "", "class_name": "Role"},
}

class DebugToolsTests(unittest.TestCase):
    def test_breakpoint_backtrace_preserves_register_snapshot(self):
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        snapshot = {"hit_id": 9, "count": 7, "tid": 123, "frames": [],
                    "pc": "0x1000", "registers": [hex(i) for i in range(33)],
                    "register_kind": "arm64_integer_snapshot"}
        with patch.object(dispatcher, "_json_call", return_value=snapshot) as native:
            result = dispatcher.breakpoint_backtrace({"hit_id": 9})
            native.assert_called_once_with("BREAKPOINT_BACKTRACE 9 32")
        self.assertEqual(result["registers"], snapshot["registers"])
        self.assertEqual(result["count"], 7)
        self.assertEqual(result["hit_id"], 9)

    def setUp(self):
        self.dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))

    def test_every_tool_dispatches_through_common_logging(self):
        self.assertEqual(set(EXAMPLES), set(d.BY_NAME))
        for name, args in EXAMPLES.items():
            with self.subTest(name=name), patch.object(self.dispatcher, "_json_call", return_value={"ok": True}) as native, patch.object(self.dispatcher, "_notify_mcp_call") as toast:
                self.assertEqual(self.dispatcher.call(name, args), {"ok": True})
                if name in {"memory_search_tabs", "memory_batch_edit"}:
                    native.assert_called_once_with(d.encode(name, args), timeout=max(self.dispatcher.config.timeout, 75.0))
                else:
                    native.assert_called_once_with(d.encode(name, args))
                toast.assert_called_once_with(name, args)

    def test_all_tools_have_help_and_no_duplicate_names(self):
        names = [t["name"] for t in TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for name in EXAMPLES:
            self.assertEqual(self.dispatcher.debug_help({"command": name})["inputSchema"], d.BY_NAME[name]["inputSchema"])
            self.assertGreater(len(d.BY_NAME[name]["description"]), 60)
        self.assertNotIn("mcp_set_feature", names)

    def test_native_commands_have_help(self):
        cpp = Path(__file__).resolve().parents[1] / "module/src/main/cpp"
        runtime = (cpp / "native_command_catalog.h").read_text(encoding="utf-8")
        for name, args in EXAMPLES.items():
            command = d.encode(name, args).split()[0]
            self.assertIn('{"' + command + '",', runtime)

    def test_dump_extends_existing_tool_and_keeps_body_out(self):
        for args in ({}, {"namespace": ""}, {"image": "Assembly-CSharp.dll", "namespace": "Game", "class_name": "Npc"}):
            with self.subTest(args=args), patch.object(self.dispatcher, "_json_call", return_value={"success": True, "path": "/private/dump.cs", "class_count": 1}) as native:
                result = self.dispatcher.il2cpp_dump_file(args)
                command = native.call_args.args[0]
                self.assertEqual(native.call_args.kwargs, {"timeout": 300.0})
                self.assertEqual(command.split()[0], "IL2CPP_DUMP_FILE")
                if args: self.assertEqual(json.loads(bytes.fromhex(command.split()[1])), args)
                else: self.assertEqual(command, "IL2CPP_DUMP_FILE")
                self.assertNotIn("contents", result)
        for invalid in ({"class_name": ""}, {"namespace": 5}, {"filename": "../bad"}, {"image": "a\0b"}):
            with self.subTest(invalid=invalid), self.assertRaises(BridgeError):
                self.dispatcher.il2cpp_dump_file(invalid)

    def test_type_inspection_reuses_existing_tool(self):
        args = {"image": "Assembly-CSharp.dll", "namespace": "", "class": "Role", "limit": 256}
        command = w.encode("il2cpp_inspector_members", args)
        self.assertEqual(command.split()[1], "IL2CPP_TYPE_MEMBERS")
        self.assertTrue(bytes.fromhex(command.split()[2]).decode().endswith("0x0 0 256"))
        command = w.encode("unity_hierarchy", {"mode": "instances", "target": "0x1000"})
        self.assertTrue(bytes.fromhex(command.split()[2]).decode().startswith("instances 0x1000"))

    def test_counter_and_backtrace_modes_keep_legacy_default(self):
        for args, command in (({"address": "0x1000"}, "DOBBY_INSTRUMENT 0x1000"),
                              ({"address": "0x1000", "mode": "counter"}, "DOBBY_INSTRUMENT 0x1000 counter")):
            with patch.object(self.dispatcher, "_json_call") as native:
                self.dispatcher.dobby_instrument(args)
                native.assert_called_once_with(command)
        with self.assertRaises(BridgeError): self.dispatcher.dobby_instrument({"address": "0x1000", "mode": "invalid"})

    def test_freeze_exact_integer_text_and_all_controls(self):
        args = EXAMPLES["memory_freeze_set"]
        self.assertEqual(json.loads(bytes.fromhex(d.encode("memory_freeze_set", args).split()[1]))["value"], args["value"])
        for op, expected in (("pause", "MEMORY_FREEZE_CONTROL 1 false"), ("resume", "MEMORY_FREEZE_CONTROL 1 true"), ("remove", "MEMORY_FREEZE_REMOVE 1")):
            self.assertEqual(d.encode("memory_freeze_control", {"operation": op, "id": 1}), expected)
        self.assertEqual(d.encode("memory_freeze_control", {"operation": "clear"}), "MEMORY_FREEZE_CLEAR")

    def test_chain_scan_reuses_memory_scan_base(self):
        with patch.object(self.dispatcher, "_json_call", return_value={"chains": [], "truncated": False}) as native:
            result = self.dispatcher.memory_scan_base({"target_address": "0x1000", "max_depth": 3, "max_offset": "0x80", "workers": 4})
            command = native.call_args.args[0]
            self.assertEqual(command.split()[0], "MEMORY_CHAIN_SCAN")
            options = json.loads(bytes.fromhex(command.split()[1]))
            self.assertEqual(options["workers"], 4)
            self.assertEqual(options["max_offset"], "128")
            self.assertEqual(options["max_depth"], 3)
            self.assertIn("chains", result["search"])
        with patch.object(self.dispatcher, "_json_call") as native:
            self.dispatcher.memory_scan_base({"target_address": "0x1000", "start": "0x1000", "end": "0x2000", "workers": 2})
            self.assertTrue(native.call_args.args[0].startswith("MEMORY_POINTER_SCAN_MT "))

    def test_chain_recipe_normalization_and_batch_order(self):
        command = d.encode("memory_chain_batch", {"chains": [RECIPE, "saved-one", {"base_address": 4096, "offsets": [8]}]})
        data = json.loads(bytes.fromhex(command.split()[1]))
        self.assertEqual(data[0]["offsets"], ["32", "-8"])
        self.assertEqual(data[0]["base_offset"], "288")
        self.assertEqual(data[1], "saved-one")
        self.assertEqual(data[2]["base_address"], "0x1000")
        self.assertEqual(data[2]["pointer_size"], 8)

    def test_pointer_export_returns_receipt_and_never_fetches_pages(self):
        self._isolate_export_transport()
        receipt = {"success": True, "path": "/private/exports/pointer-scan-7.json", "count": 1000}
        for kind in ("chains", "pointers"):
            with self.subTest(kind=kind), patch.object(self.dispatcher, "_json_call", return_value=receipt) as native:
                result = self.dispatcher.call("memory_chain_export", {"session_id": 7, "scan_kind": kind})
                native.assert_called_once()
                command = native.call_args.args[0]
                self.assertEqual(command.split()[0], "MEMORY_CHAIN_EXPORT")
                self.assertEqual(json.loads(bytes.fromhex(command.split()[1])), {"session_id": 7, "scan_kind": kind})
                self.assertEqual(result, receipt)
                self.assertEqual(set(result), {"success", "path", "count"})
        default = json.loads(bytes.fromhex(d.encode("memory_chain_export", {"session_id": 7}).split()[1]))
        self.assertEqual(default["scan_kind"], "chains")

    def test_pointer_export_handles_empty_cache_and_failure_without_rescan(self):
        self._isolate_export_transport()
        with patch.object(self.dispatcher, "_json_call", return_value={"success": True, "path": "/private/empty.json", "count": 0}) as native:
            self.assertEqual(self.dispatcher.call("memory_chain_export", {"session_id": 8})["count"], 0)
            native.assert_called_once()
        for failure in ("CHAIN_SCAN_SESSION_NOT_FOUND_OR_EXPIRED", "NOT_A_POINTER_SCAN_SESSION",
                        "POINTER_EXPORT_WRITE_FAILED", "POINTER_EXPORT_ATOMIC_PUBLISH_UNAVAILABLE"):
            with self.subTest(failure=failure), patch.object(self.dispatcher, "_json_call", side_effect=BridgeError(failure)) as native:
                with self.assertRaisesRegex(BridgeError, failure):
                    self.dispatcher.call("memory_chain_export", {"session_id": 8})
                native.assert_called_once()

    def test_pointer_export_rejects_pages_paths_invalid_ids_before_native(self):
        self._isolate_export_transport()
        for args in ({}, {"session_id": 0}, {"session_id": True}, {"session_id": 2**53},
                     {"session_id": "7"}, {"session_id": 7, "scan_kind": "all"},
                     {"session_id": 7, "offset": 0}, {"session_id": 7, "limit": 20},
                     {"session_id": 7, "path": "../../overwrite.json"}):
            with self.subTest(args=args), patch.object(self.dispatcher, "_json_call") as native:
                with self.assertRaises(BridgeError): self.dispatcher.call("memory_chain_export", args)
                native.assert_not_called()

    def _isolate_export_transport(self):
        # Keep these new contract tests fully offline, including automatic Toasts.
        for target in (patch.object(self.dispatcher, "_notify_mcp_call"),
                       patch("socket.create_connection", side_effect=AssertionError("offline test attempted a socket")),
                       patch("subprocess.run", side_effect=AssertionError("offline test attempted a subprocess"))):
            target.start()
            self.addCleanup(target.stop)

    def test_chain_budget_options_select_existing_chain_transport(self):
        with patch.object(self.dispatcher, "_json_call", return_value={}) as native:
            self.dispatcher.memory_scan_base({"target_address": "0x1000", "budget_mb": 1024,
                "candidate_limit": 32768, "time_ms": 30000})
            command = native.call_args.args[0]
            self.assertTrue(command.startswith("MEMORY_CHAIN_SCAN "))
            options = json.loads(bytes.fromhex(command.split()[1]))
            self.assertEqual((options["budget_mb"], options["candidate_limit"], options["time_ms"]), (1024, 32768, 30000))
            self.assertGreaterEqual(native.call_args.kwargs["timeout"], 45)

    def test_invalid_chain_budgets_do_not_reach_native(self):
        for key, value in (("budget_mb", 0), ("budget_mb", 4097), ("candidate_limit", 65537), ("time_ms", 999), ("time_ms", 30001)):
            with self.subTest(key=key, value=value), patch.object(self.dispatcher, "_json_call") as native:
                with self.assertRaises(BridgeError):
                    self.dispatcher.memory_scan_base({"target_address": "0x1000", key: value})
                native.assert_not_called()

    def test_malformed_requests_do_not_reach_transport(self):
        invalid = [
            ("memory_freeze_set", {"address": "0", "type": "i32", "value": "1"}),
            ("memory_freeze_set", {"address": "0x1000", "type": "i32", "value": 1}),
            ("memory_freeze_control", {"operation": "pause"}),
            ("memory_freeze_control", {"operation": "clear", "id": 2}),
            ("dobby_trace_hits", {"address": "0x1000", "limit": 257}),
            ("il2cpp_type_graph", {}),
            ("il2cpp_type_graph", {"namespace": "Game", "depth": 5}),
            ("memory_chain_batch", {"chains": []}),
            ("memory_chain_batch", {"chains": [{"module": "x", "base_address": "0x1000"}]}),
            ("memory_chain_batch", {"chains": [{"module": "x", "offsets": ["9223372036854775808"]}]}),
            ("memory_chain_store", {"operation": "save", "id": "x", "recipe": {"base_address": "0x1000"}}),
            ("memory_chain_store", {"operation": "list", "id": "x"}),
        ]
        for name, args in invalid:
            with self.subTest(name=name, args=args), patch.object(self.dispatcher, "_json_call") as native, self.assertRaises(BridgeError):
                self.dispatcher._debug_call(name, args)
            native.assert_not_called()

    def test_existing_switch_applies_to_new_debug_routes(self):
        self.dispatcher.registry.set("trace", False)
        with patch.object(self.dispatcher, "_json_call") as native, self.assertRaises(BridgeError):
            self.dispatcher.call("dobby_trace_list", {})
        native.assert_not_called()
        self.assertEqual(d.native_features("MEMORY_VALUE_WRITE"), ("memory_read", "memory_write"))
        self.assertIn("pointer_chain", d.native_features("MEMORY_CHAIN_SCAN"))

if __name__ == "__main__":
    unittest.main()
