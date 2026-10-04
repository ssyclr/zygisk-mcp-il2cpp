"""Shared typed native tools; import never connects, invokes, or writes files."""
from __future__ import annotations
import json
from pathlib import Path
try:
    from . import render_tools as r
    from . import register_tools
except ImportError:
    import render_tools as r
    import register_tools

_raw = Path(__file__).with_name("shared_tools.inc").read_text(encoding="utf-8")
if not _raw.startswith('R"mcp(') or not _raw.strip().endswith(')mcp"'):
    raise ValueError("invalid shared MCP catalog envelope")
DOCUMENT = json.loads(_raw[len('R"mcp('):_raw.rfind(')mcp"')])
DEFINITIONS = {d["name"]: d for d in DOCUMENT["tools"]}

def tool(d):
    properties = {key: DOCUMENT["fields"][key] for key in d.get("parameters", [])}
    properties.update(d.get("extra_properties", {}))
    if "registers" in properties:
        properties["registers"] = register_tools.schema()
    if "on_hit" in properties:
        properties["on_hit"] = {**properties["on_hit"],"properties":{"registers":register_tools.schema(),"continue":{"type":"boolean","default":False}}}
    return r.tool(d["name"], d["description"], properties, d.get("required", ()), readonly=d.get("readonly", True))

TOOLS = [tool(d) for d in DOCUMENT["tools"]]
BY_NAME = {t["name"]: t for t in TOOLS}

def encode(name: str, args: dict) -> str:
    definition = DEFINITIONS[name]
    r.validate(args, BY_NAME[name]["inputSchema"], "arguments")
    if "on_hit" in args:
        args={**args,"on_hit":{**args["on_hit"],"registers":register_tools.normalize(args["on_hit"]["registers"])}}
    if name == "field_activity":
        action=args.get("action","inspect")
        required={"inspect":{"object_address","field"},"start":{"object_address","field","confirm"},"poll":{"activity_id"},"stop":{"activity_id","confirm"},"list":set(),"help":set()}[action]
        if not required <= args.keys(): raise ValueError(f"{action} requires {sorted(required)}")
        if action in ("start","stop") and args["confirm"] is not True: raise ValueError("confirm=true required")
    if name == "address_relations" and not args.get("cursor") and not {"source","target"} <= args.keys():
        raise ValueError("source and target, or cursor required")
    if name == "string_write":
        managed=args.get("mode")=="managed_field"
        required={"object_address","field"} if managed else {"address"}
        prohibited={"address","encoding","capacity_bytes"} if managed else {"object_address","field"}
        if not required <= args.keys() or prohibited & args.keys(): raise ValueError("string write address/field parameters do not match mode")
    if name == "field_accesses" and not ("field_offset" in args or "field_selector" in args):
        raise ValueError("field_offset or field_selector required")
    command = definition.get("command", "ANALYSIS_QUERY")
    if command == "CRASH_DIAGNOSE":
        return command + (" previous" if args.get("previous") else "")
    if "tokens" in definition:
        values = [str(args[key]).lower() if isinstance(args[key], bool) else str(args[key]) for key in definition["tokens"]]
        if any(not value or any(c.isspace() for c in value) or "\0" in value for value in values):
            raise ValueError("invalid native token")
        return command + " " + " ".join(values)
    payload = {definition.get("renames", {}).get(k, k): v for k, v in args.items()}
    if "op" in definition:
        payload["op"] = definition["op"]
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > 65536:
        raise ValueError("analysis request too large")
    if definition.get("game_frame"):
        return "WORKSPACE_QUERY " + command + " " + data.hex().encode("ascii").hex()
    return command + " " + data.hex()

def features(name: str) -> tuple[str, ...]:
    return tuple(DEFINITIONS[name]["features"])

def extra_features(name: str, args: dict) -> tuple[str, ...]:
    if name in ("search_strings", "find_string_xrefs") and args.get("include_il2cpp"):
        return ("il2cpp_metadata", "il2cpp_objects")
    if name == "string_write" and args.get("mode") == "managed_field":
        return ("il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke")
    if name == "field_activity":
        return (("breakpoint",) if args.get("action", "inspect") != "inspect" else ()) + (("assembly",) if "module" in args or "code_address" in args else ())
    if name == "analyze_function" and args.get("decompile"):
        return ("decompiler",)
    if name.startswith("symbol_") and args.get("locator", {}).get("kind") in ("method", "field"):
        return ("il2cpp_metadata",)
    if name == "field_accesses" and "field_selector" in args:
        return ("il2cpp_metadata",)
    return ()
