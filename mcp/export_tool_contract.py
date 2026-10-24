"""Offline export of legacy typed tools for the Python-free native adapter.

The exported definitions are consumed by both transports. --check is a release
guard: changing a typed schema requires regenerating and testing the native
adapter. --patch emits an apply_patch payload; it never edits sources itself.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
try:
    from . import mcp_server as server
except ImportError:
    import mcp_server as server

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "mcp/typed_tool_contract.inc"

def content():
    tools = getattr(server, "LEGACY_TOOLS", server.TOOLS)
    value = [{"tool": tool, "features": list(server.tool_features(tool["name"]))} for tool in tools]
    # ASCII output is intentional: Windows shells may otherwise transcode
    # Chinese defaults while emitting the generated patch.
    return 'R"typed(' + json.dumps(value, ensure_ascii=True, separators=(",", ":")) + ')typed"\n'

def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--patch",action="store_true")
    parser.add_argument("--write",action="store_true",help="Mechanically regenerate only mcp/typed_tool_contract.inc")
    args=parser.parse_args();text=content()
    if args.check:
        if not DEST.is_file() or DEST.read_text(encoding="utf-8")!=text:
            raise SystemExit("Typed MCP contract is stale; regenerate and run transport parity tests")
        print("Typed MCP contract matches all legacy tool definitions")
    elif args.write:
        DEST.write_text(text,encoding="utf-8",newline="\n")
        print("Regenerated typed MCP contract")
    elif args.patch:
        if DEST.exists():
            old=DEST.read_text(encoding="utf-8")
            print("*** Begin Patch\n*** Update File: "+str(DEST).replace("\\","/")+"\n@@\n"+"\n".join("-"+line for line in old.splitlines())+"\n"+"\n".join("+"+line for line in text.splitlines())+"\n*** End Patch")
        else:
            print("*** Begin Patch\n*** Add File: "+str(DEST).replace("\\","/")+"\n"+"\n".join("+"+line for line in text.splitlines())+"\n*** End Patch")
    else:print(text,end="")

if __name__=="__main__":main()
