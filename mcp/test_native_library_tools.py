import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
try:
    from . import native_library_tools as tools
    from . import mcp_server
except ImportError:
    import native_library_tools as tools
    import mcp_server

class InjectorTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"plugin.so"
        data=bytearray(70000);data[:7]=b"\x7fELF\x02\x01\x01";struct.pack_into("<HH",data,16,3,183)
        self.data=bytes(data);self.path.write_bytes(self.data)
        self.calls=[];self.received=bytearray()

    def bridge(self,command,**kwargs):
        self.assertLessEqual(len(command.encode())+1,160*1024)
        options=json.loads(bytes.fromhex(command.split()[1]))
        self.calls.append(options)
        op=options["op"]
        if op=="list":return {"elf_bits":64,"elf_machine":183}
        if op=="begin":return {"id":"11-22-1"}
        if op=="chunk":
            self.assertEqual(options["offset"],len(self.received))
            self.received.extend(bytes.fromhex(options["hex"]))
            return {"id":"11-22-1","received":len(self.received)}
        if op=="commit":return {"id":"11-22-1","state":"loaded","pending":False}
        return {"id":"11-22-1","state":"cancelled"}

    def run_inject(self,**kw):
        return tools.execute("native_library_inject",{"path":str(self.path),"allow_execute":True,"wait_seconds":0,**kw},self.bridge)

    def test_upload_round_trip_and_hash(self):
        result=self.run_inject()
        self.assertEqual(self.received,self.data)
        self.assertEqual(result["state"],"loaded")
        self.assertEqual(result["sha256"],hashlib.sha256(self.data).hexdigest())
        self.assertEqual(sum(c["op"]=="commit" for c in self.calls),1)
        self.assertTrue(all(len(c["hex"])<=65536 for c in self.calls if c["op"]=="chunk"))

    def test_bad_digest_and_missing_ack_do_not_contact_target(self):
        with self.assertRaises(ValueError):self.run_inject(expected_sha256="0"*64)
        with self.assertRaises(ValueError):self.run_inject(allow_execute=False)
        self.assertEqual(self.calls,[])

    def test_abi_mismatch_prevents_upload(self):
        data=bytearray(self.data);data[4]=1;self.path.write_bytes(data)
        with self.assertRaises(ValueError):self.run_inject()
        self.assertEqual([c["op"] for c in self.calls],["list"])

    def test_failed_upload_cancels_but_never_commits(self):
        original=self.bridge
        def fail(command,**kw):
            if json.loads(bytes.fromhex(command.split()[1]))["op"]=="chunk":raise OSError("lost connection")
            return original(command,**kw)
        with self.assertRaises(OSError):
            tools.execute("native_library_inject",{"path":str(self.path),"allow_execute":True},fail)
        self.assertEqual(self.calls[-1]["op"],"cancel")

    def test_lost_commit_returns_id_and_never_retries(self):
        original=self.bridge
        def fail(command,**kw):
            if json.loads(bytes.fromhex(command.split()[1]))["op"]=="commit":raise OSError("target disconnected")
            return original(command,**kw)
        result=tools.execute("native_library_inject",{"path":str(self.path),"allow_execute":True},fail)
        self.assertEqual(result["state"],"completion_unknown")
        self.assertEqual(result["id"],"11-22-1")
        self.assertNotIn("cancel",[c["op"] for c in self.calls])

    def test_feature_and_raw_route_are_both_gated(self):
        self.assertEqual(mcp_server.tool_features("native_library_inject"),("native_libraries",))
        self.assertEqual(mcp_server.raw_command_features("NATIVE_LIBRARY_CONTROL"),("native_libraries",))
        registry=mcp_server.FeatureRegistry()
        self.assertTrue(registry.enabled("native_libraries"))

    def test_native_chunk_uses_larger_bounded_transport(self):
        source=Path(mcp_server.__file__).read_text(encoding="utf-8")
        self.assertIn('160*1024 if command.startswith("NATIVE_LIBRARY_CONTROL ")',source)
        native=(Path(__file__).resolve().parents[1]/"module/src/main/cpp/mcp/cmd_server.cpp").read_text(encoding="utf-8")
        self.assertIn('pending.rfind("NATIVE_LIBRARY_CONTROL ",0)==0?160*1024',native)

    def test_fresh_stdio_catalog_advertises_new_tools_without_target(self):
        messages=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"offline-test","version":"1"}}},
                  {"jsonrpc":"2.0","id":2,"method":"tools/list"}]
        result=subprocess.run([sys.executable,mcp_server.__file__,"--no-admin","--no-adb-forward",
            "--feature-config",str(Path(self.directory.name)/"features.json")],
            input="".join(json.dumps(m)+"\n" for m in messages),text=True,encoding="utf-8",capture_output=True,timeout=15,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        self.assertEqual(result.returncode,0,result.stderr)
        responses={m["id"]:m for line in result.stdout.splitlines() if "id" in (m:=json.loads(line))}
        catalog={t["name"]:t for t in responses[2]["result"]["tools"]}
        self.assertIn("native_library_inject",catalog)
        self.assertIn("native_library_status",catalog)
        self.assertIn("strategy",catalog["il2cpp_relation_find"]["inputSchema"]["properties"])

if __name__=="__main__":unittest.main()
