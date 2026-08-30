from __future__ import annotations

import json
import socket
import threading
import unittest
import urllib.request
from unittest.mock import call, patch

from mcp.mcp_server import (
    BridgeError,
    ConnectionConfig,
    FeatureRegistry,
    HookSocketClient,
    McpServer,
    McpAdminServer,
    ToolDispatcher,
)


class OneShotHookServer:
    def __init__(self, response: str):
        self.response = response
        self.command = ""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self) -> None:
        connection, _ = self.socket.accept()
        with connection:
            data = bytearray()
            while not data.endswith(b"\n"):
                data.extend(connection.recv(1024))
            self.command = data.decode("utf-8").rstrip("\r\n")
            body = self.response.encode("utf-8")
            connection.sendall(f"OK {len(body)}\n".encode("ascii") + body)
        self.socket.close()

    def __enter__(self) -> "OneShotHookServer":
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.thread.join(timeout=2)


class HookSocketClientTests(unittest.TestCase):
    def test_command_protocol(self) -> None:
        with OneShotHookServer("PONG\n") as server:
            config = ConnectionConfig(port=server.port, auto_adb_forward=False)
            response = HookSocketClient(config).call("PING")
        self.assertEqual("PING", server.command)
        self.assertEqual("PONG", response)


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        config = ConnectionConfig(auto_adb_forward=False)
        self.server = McpServer(ToolDispatcher(config))

    def test_initialize_negotiates_latest_protocol(self) -> None:
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "unknown"},
            }
        )
        self.assertEqual("2025-11-25", response["result"]["protocolVersion"])

    def test_lists_runtime_hook_tools(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("raw_hook_call", names)
        self.assertIn("il2cpp_hook", names)
        self.assertIn("il2cpp_hook_return", names)
        self.assertIn("il2cpp_dump_file", names)
        self.assertIn("memory_read", names)
        self.assertIn("memory_write", names)
        self.assertIn("memory_read_value", names)
        self.assertIn("memory_write_value", names)
        self.assertIn("memory_list_modules", names)
        self.assertIn("memory_find_module", names)
        self.assertIn("memory_address_info", names)
        self.assertIn("memory_search", names)
        self.assertIn("memory_search_value", names)
        self.assertIn("memory_filter", names)
        self.assertIn("memory_filter_value", names)
        self.assertIn("memory_search_clear", names)
        self.assertIn("dobby_hook", names)
        self.assertIn("dobby_patch_code", names)
        self.assertIn("memory_search_exact", names)
        self.assertIn("memory_search_fuzzy", names)
        self.assertIn("memory_search_results", names)
        self.assertIn("runtime_capabilities", names)
        self.assertIn("debug_help", names)
        self.assertIn("lua_execute", names)
        self.assertIn("lua_logs", names)
        self.assertIn("assembly_patch", names)
        self.assertIn("decompiler_status", names)
        self.assertIn("decompile_function", names)
        self.assertIn("breakpoint_set", names)
        self.assertEqual(len(names), len(response["result"]["tools"]))
        self.assertIn("mcp_toast_set_enabled", names)
        self.assertIn("mcp_toast_show", names)
        self.assertNotIn("mcp_list_features", names)
        self.assertNotIn("mcp_set_feature", names)
        self.assertNotIn("mcp_set_all_features", names)
        self.assertNotIn("mcp_admin_info", names)
        self.assertIn("il2cpp_search", names)
        self.assertIn("il2cpp_list_fields", names)
        self.assertIn("il2cpp_object_inspect", names)
        self.assertIn("memory_resolve_pointer_chain", names)
        self.assertIn("memory_scan_base", names)
        self.assertIn("breakpoint_backtrace", names)
        self.assertIn("dobby_trace_backtrace", names)

        invoke_tool = next(tool for tool in response["result"]["tools"] if tool["name"] == "il2cpp_invoke")
        argument_variants = invoke_tool["inputSchema"]["properties"]["arguments"]["items"]["anyOf"]
        self.assertTrue(any(variant.get("type") == "object" for variant in argument_variants))

    def test_encodes_explicit_enum_arguments(self) -> None:
        member = "RoleSyncState.Walking"
        self.assertEqual("s" + member.encode("utf-8").hex(), ToolDispatcher._invoke_token({"enum": member}))
        self.assertEqual("n31", ToolDispatcher._invoke_token({"enum": 1}))

        with self.assertRaises(BridgeError):
            ToolDispatcher._invoke_token({"enum": 1.5})
        with self.assertRaises(BridgeError):
            ToolDispatcher._invoke_token({"enum": 1, "type": "RoleSyncState"})

        self.assertEqual("b1", ToolDispatcher._invoke_token({"type": "bool", "value": True}))
        self.assertEqual("n3432", ToolDispatcher._invoke_token({"type": "i32", "value": "0x2a"}))
        self.assertEqual("s307831323334", ToolDispatcher._invoke_token({"address": "0x1234"}))

    def test_feature_switches_filter_and_block_tools(self) -> None:
        registry = FeatureRegistry()
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False), registry)
        server = McpServer(dispatcher)
        self.assertTrue(all(item["enabled"] for item in registry.snapshot()["features"]))

        registry.set("memory_write", False)
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertNotIn("memory_write", names)
        self.assertNotIn("memory_write_value", names)
        self.assertNotIn("memory_write_pointer_chain", names)
        self.assertIn("memory_read", names)
        self.assertNotIn("mcp_set_feature", names)
        with self.assertRaisesRegex(BridgeError, "memory_write"):
            dispatcher.call("memory_write", {"address": "0x1000", "hex_bytes": "00"})
        with self.assertRaisesRegex(BridgeError, "unknown tool"):
            dispatcher.call("mcp_set_feature", {"feature": "memory_write", "enabled": True})

    def test_browser_admin_switches_feature(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        registry = FeatureRegistry()
        admin = McpAdminServer(registry, "127.0.0.1", port)
        admin.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                page = response.read().decode("utf-8")
            self.assertIn("MCP 功能控制", page)
            self.assertNotIn("{{", page)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/features", timeout=2) as response:
                payload = json.loads(response.read())
            self.assertTrue(payload["default_enabled"])
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/features/trace",
                data=b'{"enabled":false}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                result = json.loads(response.read())
            self.assertFalse(result["enabled"])
            self.assertFalse(registry.enabled("trace"))
        finally:
            admin.stop()

    def test_raw_command_cannot_bypass_feature_switch(self) -> None:
        registry = FeatureRegistry()
        registry.set("il2cpp_hook", False)
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False), registry)
        with self.assertRaisesRegex(BridgeError, "il2cpp_hook"):
            dispatcher.raw_hook_call({"command": "IL2CPP_HOOK 00"})
        registry.set("decompiler", False)
        with self.assertRaisesRegex(BridgeError, "decompiler"):
            dispatcher.raw_hook_call({"command": "DECOMP_STATUS"})

    def test_il2cpp_search_and_field_commands(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", return_value={"results": []}) as json_call:
            dispatcher.il2cpp_search(
                {
                    "entity": "method",
                    "query": "Move",
                    "image_filter": "Assembly-CSharp",
                    "namespace_filter": "Game",
                    "class_filter": "Role",
                    "match_mode": "prefix",
                    "case_sensitive": False,
                    "offset": 20,
                    "limit": 50,
                }
            )
        json_call.assert_called_once_with(
            "IL2CPP_SEARCH method 4d6f7665 417373656d626c792d435368617270 47616d65 526f6c65 prefix 0 20 50",
            timeout=60.0,
        )

        with patch.object(dispatcher, "_json_call", return_value={"fields": []}) as json_call:
            dispatcher.il2cpp_list_fields(
                {"image": "A.dll", "namespace": "N", "class_name": "C", "include_inherited": True}
            )
        json_call.assert_called_once_with("IL2CPP_FIELDS 412e646c6c 4e 43 - 1 0 200")

    def test_pointer_chain_resolution(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        module = {"load_bias": "0x1000", "start": "0x1100", "end": "0x3000"}
        with patch.object(dispatcher, "memory_find_module", return_value=module), patch.object(
            dispatcher,
            "memory_read_value",
            side_effect=[{"value": "0x2000"}, {"value": "0x3000"}],
        ):
            result = dispatcher.memory_resolve_pointer_chain(
                {
                    "module": "libgame.so",
                    "base_offset": "0x20",
                    "offsets": ["0x10", "-0x8"],
                    "pointer_size": 8,
                }
            )
        self.assertEqual("0x2ff8", result["address"])
        self.assertEqual("0x1020", result["steps"][0]["read_at"])

    def test_multithread_base_scan_command(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", return_value={"session_id": 9}) as json_call:
            result = dispatcher.memory_scan_base(
                {
                    "target_address": "0x12345678",
                    "start": "0x1000",
                    "end": "0x9000",
                    "pointer_size": 8,
                    "workers": 4,
                    "memory_types": ["heap", "anonymous"],
                    "max_results": 200,
                }
            )
        json_call.assert_called_once_with(
            "MEMORY_POINTER_SCAN_MT 0x1000 0x9000 0x12345678 8 200 4 686561702c616e6f6e796d6f7573",
            timeout=300.0,
        )
        self.assertEqual(4, result["workers"])

    def test_memory_read_and_write_commands(self) -> None:
        with OneShotHookServer('{"address":"0x1000","size":2,"hex":"01ff"}') as server:
            dispatcher = ToolDispatcher(ConnectionConfig(port=server.port, auto_adb_forward=False))
            result = dispatcher.memory_read({"address": "0x1000", "size": 2})
        self.assertEqual("MEMORY_READ 0x1000 2", server.command)
        self.assertEqual("01ff", result["hex"])

        response = '{"written":true,"verified":true,"address":"0x1000","size":2,"previous_hex":"0000"}'
        with OneShotHookServer(response) as server:
            dispatcher = ToolDispatcher(ConnectionConfig(port=server.port, auto_adb_forward=False))
            result = dispatcher.memory_write({"address": "0x1000", "hex_bytes": "01FF"})
        self.assertEqual("MEMORY_WRITE 0x1000 01FF", server.command)
        self.assertTrue(result["verified"])
        self.assertEqual("0000", result["previous_hex"])

        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with self.assertRaises(BridgeError):
            dispatcher.memory_read({"address": "0x1000", "size": 0})
        with self.assertRaises(BridgeError):
            dispatcher.memory_write({"address": "0x1000", "hex_bytes": "123"})

    def test_typed_memory_values(self) -> None:
        encoded, normalized = ToolDispatcher._encode_memory_value("i32", "0x12345678")
        self.assertEqual("78563412", encoded)
        self.assertEqual(0x12345678, normalized)
        self.assertEqual("0x12345678", ToolDispatcher._decode_memory_value("ptr32", encoded))

        with OneShotHookServer('{"address":"0x2000","size":4,"hex":"78563412"}') as server:
            dispatcher = ToolDispatcher(ConnectionConfig(port=server.port, auto_adb_forward=False))
            result = dispatcher.memory_read_value(
                {"address": "0x2000", "value_type": "i32"}
            )
        self.assertEqual("MEMORY_READ 0x2000 4", server.command)
        self.assertEqual(0x12345678, result["value"])

        response = '{"written":true,"verified":true,"address":"0x2000","size":4,"previous_hex":"00000000"}'
        with OneShotHookServer(response) as server:
            dispatcher = ToolDispatcher(ConnectionConfig(port=server.port, auto_adb_forward=False))
            result = dispatcher.memory_write_value(
                {"address": "0x2000", "value_type": "f32", "value": 1.5}
            )
        self.assertEqual("MEMORY_WRITE 0x2000 0000c03f", server.command)
        self.assertEqual(0.0, result["previous_value"])

        with self.assertRaises(BridgeError):
            ToolDispatcher._encode_memory_value("u8", 256)

    def test_module_lookup_commands(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", return_value={"modules": []}) as json_call:
            dispatcher.memory_list_modules({"name_filter": "libunity", "limit": 10})
        json_call.assert_called_once_with("MEMORY_MODULES 6c6962756e697479 10")

        with patch.object(dispatcher, "_json_call", return_value={"start": "0x1000"}) as json_call:
            dispatcher.memory_find_module({"module_name": "libfoo.so", "occurrence": 2})
        json_call.assert_called_once_with("MEMORY_MODULE_FIND 6c6962666f6f2e736f 2")

        with patch.object(dispatcher, "_json_call", return_value={"region": {}}) as json_call:
            dispatcher.memory_address_info({"address": "0x1234"})
        json_call.assert_called_once_with("MEMORY_ADDRESS_INFO 0x1234")

    def test_memory_search_and_filter_commands(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        module = {"start": "0x1000", "end": "0x2000"}
        search_result = {"session_id": 7, "addresses": ["0x1100"]}
        with patch.object(dispatcher, "_json_call", side_effect=[module, search_result]) as json_call:
            result = dispatcher.memory_search(
                {
                    "module_name": "libfoo.so",
                    "occurrence": 2,
                    "pattern": "48 8B ?? A?",
                    "max_results": 20,
                }
            )
        self.assertEqual(7, result["session_id"])
        self.assertEqual(
            call("MEMORY_MODULE_FIND 6c6962666f6f2e736f 2"),
            json_call.call_args_list[0],
        )
        self.assertEqual(
            "MEMORY_SEARCH 0x1000 0x2000 343838423f3f413f 20 1 616c6c",
            json_call.call_args_list[1].args[0],
        )

        with patch.object(dispatcher, "_json_call", return_value={"result_count": 1}) as json_call:
            dispatcher.memory_filter({"session_id": 7, "mode": "changed"})
        self.assertEqual("MEMORY_FILTER 7 changed -", json_call.call_args.args[0])

        with patch.object(dispatcher, "_json_call", return_value={"result_count": 1}) as json_call:
            dispatcher.memory_filter_value(
                {"session_id": 7, "mode": "equals", "value_type": "i32", "value": 123}
            )
        self.assertEqual("MEMORY_FILTER 7 equals 3762303030303030", json_call.call_args.args[0])

        with self.assertRaises(BridgeError):
            dispatcher.memory_search(
                {"pattern": "12", "module_name": "libfoo.so", "start_address": "0x1000"}
            )

    def test_new_memory_search_commands(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", return_value={"session_id": 8}) as json_call:
            dispatcher.memory_search_fuzzy(
                {
                    "start_address": "0x1000",
                    "end_address": "0x2000",
                    "value_size": 4,
                    "max_results": 50,
                    "memory_types": ["heap", "anonymous"],
                }
            )
        self.assertEqual(
            "MEMORY_SEARCH_FUZZY 0x1000 0x2000 4 50 4 686561702c616e6f6e796d6f7573",
            json_call.call_args.args[0],
        )

        with patch.object(dispatcher, "_json_call", return_value={"results": []}) as json_call:
            dispatcher.memory_search_results({"session_id": 8, "offset": 10, "limit": 20})
        json_call.assert_called_once_with("MEMORY_SEARCH_RESULTS 8 10 20")

    def test_lua_assembly_breakpoint_and_help_commands(self) -> None:
        dispatcher = ToolDispatcher(ConnectionConfig(auto_adb_forward=False))
        with patch.object(dispatcher, "_json_call", return_value={"success": True}) as json_call:
            dispatcher.lua_execute({"script": "print('a')\nprint('b')", "timeout": 12})
        json_call.assert_called_once_with(
            "LUA_EXEC 7072696e7428276127290a7072696e742827622729", timeout=12.0
        )

        with patch.object(dispatcher, "_json_call", return_value={"bytes_hex": "1f2003d5"}) as json_call:
            dispatcher.assembly_assemble({"instruction": "nop"})
        json_call.assert_called_once_with("ASM_ASSEMBLE 6e6f70")

        with patch.object(dispatcher, "_json_call", return_value={"pseudocode": "void sub_1234() {}"}) as json_call:
            dispatcher.decompile_function({"address": "0x1234"})
        json_call.assert_called_once_with(
            "DECOMP_DECOMPILE 0x1234 256 256 262144 1 1", timeout=60.0
        )

        with self.assertRaises(BridgeError):
            dispatcher.decompile_function({"address": "0x1234", "size": 7})

        with patch.object(dispatcher, "_json_call", return_value={"set": True}) as json_call:
            dispatcher.breakpoint_set({"address": "0x1234", "type": "x"})
        json_call.assert_called_once_with("BREAKPOINT_SET 0x1234 x 4")

        with patch.object(dispatcher, "_json_call", return_value={"command": "ASM_PATCH"}) as json_call:
            help_result = dispatcher.debug_help({"command": "assembly_patch"})
        json_call.assert_not_called()
        self.assertEqual("assembly_patch", help_result["tool"])

    def test_connection_info_has_structured_content(self) -> None:
        response = self.server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "connection_info", "arguments": {}},
            }
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(27184, result["structuredContent"]["port"])
        json.loads(result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
