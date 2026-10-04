import json
import unittest
try:
    from . import analysis_tools as tools, paused_debug_tools as paused, mcp_server as server
except ImportError:
    import analysis_tools as tools, paused_debug_tools as paused, mcp_server as server

def wire(name,args):
    command,hexdata=tools.encode(name,args).split(" ",1)
    return command,json.loads(bytes.fromhex(hexdata))

class EvidenceTools(unittest.TestCase):
    def test_strings_extend_existing_tools(self):
        for name,command,args in (("search_strings","MEMORY_STRING_SEARCH",{"query":"中文🎮","scope":"ranges","ranges":[{"start":"0x1000","end":"0x2000"}],"encoding":"utf16le"}),
                                  ("find_string_xrefs","MEMORY_STRING_XREFS",{"address":"0x1234","include_il2cpp":True,"cursor":"opaque"})):
            c,p=wire(name,args);self.assertEqual(c,command);self.assertEqual(p,args)
            self.assertEqual(sum(t["name"]==name for t in server.TOOLS),1)
    def test_scan_parameters_are_bounded(self):
        for extra in ({"limit":257},{"ranges":[]},{"ranges":[{"start":"0x1","end":"0x2"}]*65},{"encoding":"gbk"},{"cursor":17}):
            with self.assertRaises(ValueError):wire("search_strings",{"query":"abc",**extra})
    def test_write_compare_confirmation_and_modes(self):
        c,p=wire("string_write",{"address":"0x1000","value":"new","expected":"old","confirm":True});self.assertEqual(c,"MEMORY_STRING_WRITE")
        wire("string_write",{"mode":"managed_field","object_address":"0x1000","field":"name","value":"中","expected":"文","confirm":True})
        for args in ({"address":"0x1","value":"x","expected":"y"},{"value":"x","expected":"y","confirm":True},
                     {"mode":"managed_field","address":"0x1","value":"x","expected":"y","confirm":True}):
            with self.assertRaises(ValueError):wire("string_write",args)
    def test_read_queries_never_require_writing(self):
        for name in ("search_strings","find_string_xrefs","address_resolve","address_relations"):
            self.assertNotIn("memory_write",tools.features(name))
        self.assertNotIn("breakpoint",tools.extra_features("field_activity",{}))
        self.assertIn("breakpoint",tools.extra_features("field_activity",{"action":"start"}))
        self.assertIn("il2cpp_invoke",tools.extra_features("string_write",{"mode":"managed_field"}))
    def test_field_activity_modes(self):
        args={"object_address":"0x1000","field":"Base::health"};self.assertEqual(wire("field_activity",args)[0],"IL2CPP_FIELD_ACTIVITY")
        for action in ("start","stop"):
            with self.assertRaises(ValueError):wire("field_activity",{**args,"action":action})
        self.assertEqual(wire("field_activity",{"action":"poll","activity_id":"session-field-1","after_hit_id":"18446744073709551615"})[1]["after_hit_id"],"18446744073709551615")
    def test_deep_relationships_and_resume(self):
        wire("address_relations",{"source":"0x1","target":"0x2","max_depth":32,"max_nodes":100000,"array_limit":1000000})
        wire("address_relations",{"cursor":"same-session","limit":1})
        for args in ({},{"source":"0x1"},{"source":"0x1","target":"0x2","max_depth":33}):
            with self.assertRaises(ValueError):wire("address_relations",args)
    def test_on_hit_patch_named_http_python_schema(self):
        args={"tid":10,"address":"0x1000","stop_id":"3","confirm":True,"on_hit":{"registers":{"w0":"7878","s0":"float:1.5","q1":"0xffffffffffffffffffffffffffffffff","z2":"0x1234","nzcv":"0xa0000000"},"continue":True}}
        c,p=wire("debugger_breakpoint_set",args);self.assertEqual(c,"DEBUGGER_CONTROL");self.assertEqual(p["on_hit"]["registers"]["w0"],hex(7878));self.assertTrue(p["on_hit"]["continue"])
        for regs in ({"x0":"1","w0":"2"},{"d0":"float:nan"},{"q0":"0x"+"a"*33},{"x31":"1"},{"z0":"0x1","q0":"0x2"}):
            with self.assertRaises(ValueError):wire("debugger_breakpoint_set",{**args,"on_hit":{"registers":regs}})
    def test_manual_register_writes_share_formats(self):
        args={"op":"set_registers","tid":10,"stop_id":"1","expected_pc":"0x1000","confirm":True,"registers":{"s0":"float:2.5","h1":"0x3c00","tpidr_el0":"0x1234"}}
        self.assertIn("DEBUGGER_CONTROL",paused.encode("debugger_control",args))

if __name__=="__main__":unittest.main()
