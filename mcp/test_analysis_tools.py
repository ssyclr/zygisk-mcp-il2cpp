import json
import unittest
try:
    from . import analysis_tools as a, mcp_server as s, paused_debug_tools as p
except ImportError:
    import analysis_tools as a, mcp_server as s, paused_debug_tools as p

class AnalysisTools(unittest.TestCase):
    def payload(self, name, args):
        command, data = a.encode(name, args).split(" ", 1)
        return command, json.loads(bytes.fromhex(data))

    def test_catalog_registered_without_duplicate_names(self):
        self.assertEqual(len(s.TOOLS), len(s.TOOL_BY_NAME))
        self.assertTrue(set(a.BY_NAME) <= set(s.TOOL_BY_NAME))

    def test_precision_and_single_native_wire(self):
        command, payload = self.payload("xrefs_to", {"address":"0xffffffffffffffff"})
        self.assertEqual(command,"ANALYSIS_QUERY")
        self.assertEqual(payload,{"op":"xrefs_to","address":"0xffffffffffffffff"})
        with self.assertRaises(ValueError): a.encode("xrefs_to", {"address":2**64-1})

    def test_independent_optional_decompiler_feature(self):
        self.assertEqual(a.features("analyze_function"),("assembly","memory_read"))
        self.assertEqual(a.extra_features("analyze_function",{"decompile":True}),("decompiler",))
        self.assertEqual(a.extra_features("analyze_function",{}),())

    def test_inventory_uses_frame_queue_on_both_transports(self):
        for name,args,op in (
            ("render_instance_inventory",{"refresh":True,"query":"NPC","limit":256},"list"),
            ("render_inventory_items",{"entry_id":"class:0x1000","offset":32},"objects"),
            ("render_inventory_set",{"entry_id":"collection:0x2000","enabled":False},"render"),
            ("render_inventory_status",{"task_id":12,"cancel":True},"render_status"),
        ):
            command,inner,hex_args=a.encode(name,args).split()
            self.assertEqual((command,inner),("WORKSPACE_QUERY","IL2CPP_RENDER_INVENTORY"))
            payload=json.loads(bytes.fromhex(bytes.fromhex(hex_args).decode("ascii")))
            self.assertEqual(payload,{**args,"op":op})
            self.assertIn("rendering",a.features(name))
            self.assertIn("il2cpp_objects",a.features(name))
        for name,args in (
            ("render_inventory_set",{"entry_id":"class:0x1000"}),
            ("render_instance_inventory",{"limit":257}),
            ("render_instance_inventory",{"refresh":1}),
            ("render_inventory_items",{"entry_id":"x","pid":123}),
        ):
            with self.assertRaises(ValueError):a.encode(name,args)

    def test_no_arbitrary_pid_or_query_injection(self):
        for args in ({"address":"0x1000","pid":1},{"address":"0x1000","size":-1},{"address":"0x1000","limit":True}):
            with self.assertRaises(ValueError): a.encode("xrefs_from",args)
        _, payload=self.payload("search_strings",{"module":"libfoo.so","query":"中文\nquery"})
        self.assertEqual(payload["query"],"中文\nquery")

    def test_budget_and_pagination_boundaries(self):
        a.encode("list_functions",{"module":"libfoo.so","scan_mb":256,"timeout_ms":10000,"limit":1024})
        for key,value in (("scan_mb",257),("timeout_ms",10001),("limit",1025)):
            with self.assertRaises(ValueError): a.encode("list_functions",{"module":"libfoo.so",key:value})

    def test_exact_persistent_locators(self):
        _, payload=self.payload("symbol_bind",{"name":"player_update","locator":{"kind":"method","image":"Assembly-CSharp.dll","namespace":"","class_name":"Player","name":"Update","token":"0x6000100"}})
        self.assertEqual(payload["locator"]["token"],"0x6000100")
        self.assertEqual(a.encode("crash_diagnose",{}),"CRASH_DIAGNOSE")

    def test_upload_never_implicitly_commits(self):
        command,payload=self.payload("native_library_upload",{"operation":"chunk","id":"u1","chunk_offset":0,"hex":"7f454c46"})
        self.assertEqual(command,"NATIVE_LIBRARY_CONTROL")
        self.assertEqual(payload,{"op":"chunk","id":"u1","offset":0,"hex":"7f454c46"})
        with self.assertRaises(ValueError): a.encode("native_library_upload",{"operation":"commit","id":"u1","allow_execute":False})

    def test_stopping_breakpoint_schema(self):
        args={"tid":11,"address":"0x1000","stop_id":"1","confirm":True,"condition":{"register":"x0","op":"eq","value":"0x1"},"after_hits":"5"}
        command,payload=self.payload("debugger_breakpoint_set",args)
        self.assertEqual(command,"DEBUGGER_CONTROL")
        self.assertEqual(payload["op"],"breakpoint_set")
        for key in ("confirm","stop_id"):
            wrong=dict(args);del wrong[key]
            with self.assertRaises(ValueError): a.encode("debugger_breakpoint_set",wrong)

    def test_step_over_out_keep_existing_control(self):
        for op in ("step_over","step_out","continue"):
            wire=p.encode("debugger_control",{"op":op,"tid":11,"stop_id":"2","confirm":True})
            self.assertEqual(json.loads(bytes.fromhex(wire.split()[1]))["op"],op)
            with self.assertRaises(ValueError): p.encode("debugger_control",{"op":op,"tid":11})

    def test_il2cpp_symbol_configuration_named_wire(self):
        config="module=libA83F19.so\nallow_offsets=1\nil2cpp_domain_get=0x123456\n"
        command,payload=self.payload("il2cpp_symbols_set",{"config":config,"confirm":True})
        self.assertEqual(command,"IL2CPP_SYMBOLS_CONTROL")
        self.assertEqual(payload,{"config":config,"confirm":True,"op":"set"})
        self.assertEqual(a.features("il2cpp_symbols_set"),("il2cpp_metadata",))
        for name in ("get","list_apis"):
            command,payload=self.payload("il2cpp_symbols_"+name,{})
            self.assertEqual(payload,{"op":name})
            self.assertEqual(command,"IL2CPP_SYMBOLS_CONTROL")
        for args in ({"config":config},{"config":config,"confirm":False},{"config":config,"confirm":True,"pid":1}):
            with self.assertRaises(ValueError): a.encode("il2cpp_symbols_set",args)
        with self.assertRaises(ValueError): a.encode("il2cpp_symbols_reset",{})
        with self.assertRaises(ValueError): a.encode("il2cpp_symbols_validate",{"config":"x"*16385})

if __name__=="__main__": unittest.main()
