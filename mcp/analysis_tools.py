"""Shared typed native tools; import never connects, invokes, or writes files."""
from __future__ import annotations
import json
from pathlib import Path
try:
    from . import render_tools as r
except ImportError:
    import render_tools as r

_raw = Path(__file__).with_name("shared_tools.inc").read_text(encoding="utf-8")
if not _raw.startswith('R"mcp(') or not _raw.strip().endswith(')mcp"'):
    raise ValueError("invalid shared MCP catalog envelope")
DOCUMENT = json.loads(_raw[len('R"mcp('):_raw.rfind(')mcp"')])
DEFINITIONS = {d["name"]: d for d in DOCUMENT["tools"]}

def tool(d):
    properties = {key: DOCUMENT["fields"][key] for key in d.get("parameters", [])}
    properties.update(d.get("extra_properties", {}))
    if "registers" in properties:
        properties["registers"] = {**properties["registers"], "properties": {key: DOCUMENT["fields"]["expected_pc"] for key in [*(f"x{i}" for i in range(31)), "pc", "sp"]}, "additionalProperties": False}
    return r.tool(d["name"], d["description"], properties, d.get("required", ()), readonly=d.get("readonly", True))

TOOLS = [tool(d) for d in DOCUMENT["tools"]]
BY_NAME = {t["name"]: t for t in TOOLS}

def encode(name: str, args: dict) -> str:
    definition = DEFINITIONS[name]
    r.validate(args, BY_NAME[name]["inputSchema"], "arguments")
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
    if name == "analyze_function" and args.get("decompile"):
        return ("decompiler",)
    if name.startswith("symbol_") and args.get("locator", {}).get("kind") in ("method", "field"):
        return ("il2cpp_metadata",)
    if name == "field_accesses" and "field_selector" in args:
        return ("il2cpp_metadata",)
    return ()
