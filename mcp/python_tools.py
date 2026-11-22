"""On-demand CPython 3.14 extension control tools (feature-gated, default OFF)."""
from __future__ import annotations

import json
import re
from typing import Any

try:
    from . import render_tools as r, workspace_tools as w
except ImportError:
    import render_tools as r, workspace_tools as w

SCRIPT_NAME = {"type": "string", "minLength": 4, "maxLength": 67, "pattern": r"[A-Za-z0-9_-]{1,64}\.py"}
SCOPE = r.enum("package", "global")
LIST_SCOPE = r.enum("all", "package", "global")
IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 64, "pattern": r"[A-Za-z_][A-Za-z0-9_]{0,63}"}
PACKAGE = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"[A-Za-z0-9_.]+(?::[A-Za-z0-9_.:-]+)?"}
WHEN = r.enum("attached", "il2cpp_ready", "unity_ready")
AUTORUN_ENTRY = w.obj({
    "name": SCRIPT_NAME,
    "scope": SCOPE,
    "entry": IDENTIFIER,
    "when": WHEN,
    "enabled": w.B,
}, ("name",))

_ZMCP_SYNTAX_GUIDE = (
    "Pre-bound `zmcp` SDK (also injected as top-level globals in `python_run`; assign `_result = ...` or let last expression return): "
    "call `zmcp.help()` for full guide or `zmcp.help('il2cpp'|'mem'|'ui'|'hook'|'render'|'unreal'|'ClassRef'|'MethodRef'|'ObjectRef'|'WindowBuilder')` for exact signatures. "
    "Core syntax: "
    "1) IL2CPP: `zmcp.il2cpp.images()`, `zmcp.il2cpp.classes(img, filter='', namespace='')`, "
    "`cls = zmcp.il2cpp.class_of(img, cls_name, namespace='')` -> `cls.methods()`, `cls.fields()`, `m = cls.method(name, argc=None)`, `cls.invoke(name, args, instance=0x...)`; "
    "`m = zmcp.il2cpp.method(img, ns, cls_name, name, argc=None)` -> `m.address`, `m.invoke(args, instance=...)`, `m.call(...)`, `m.call_exact(...)`, `m.hook_return(val)`, `m.unhook()`, `m.disassemble(32)`, `m.decompile()`; "
    "`obj = zmcp.il2cpp.object(0x...)` -> `obj['field']`, `obj['field'] = val`, `obj.inspect()`, `obj.members()`, `obj.items()`, `obj.dict_get(k)`, `obj.dict_items()`, `obj.resources()`. "
    "2) Memory (`zmcp.mem`): `read(addr, n)`, `write(addr, bytes_or_hex)`, `read_i8/u8/i16/u16/i32/u32/i64/u64/ptr/bool/f32/f64/cstring/utf16(addr)`, `write_*(addr, val)`, "
    "`modules(filter='')`, `find_module(name)`, `address_info(addr)`, `resolve_address(expr)`, `search/search_value/search_exact/search_fuzzy/filter_value/results/clear_search`, "
    "`resolve_chain(base, [off1, off2])`, `read_chain/write_chain/scan_base`, `freeze(addr, val, type)/unfreeze(addr)/clear_freezes()`, `strings/search_strings/string_xrefs/string_write`. "
    "3) Hooks (`zmcp.hook`): `@zmcp.hook.il2cpp(m, mode='async'|'native'|'sync', replace_return=..., arg_filters=...)` or `@zmcp.hook.addr(addr, ...)`; in sync `cb(ctx)`: `ctx.args[i]`, `ctx.set_arg(i, v)`, `ctx.set_return(v)`, `ctx.skip_original()`. "
    "4) Overlay UI (`zmcp.ui`): `with zmcp.ui.window(id, title) as w:` -> `w.toggle/slider/button/select/combo/radio/input/text/separator/spacer/group/child/tree/tabs/tab/table/progress/plot/color(...)`, `@zmcp.changed(node_id)`, `zmcp.ui.toast(msg)`, `zmcp.ui.prompt_input(...)`. "
    "5) Lifecycle/Timers/Store: `@zmcp.on('attached'|'il2cpp_ready'|'unity_ready'|'exiting'|'ui:<id>')`, `@zmcp.every(1.0)`, `zmcp.log(...)`, `zmcp.store.get/set/delete`, `zmcp.main_thread(...)`. "
    "6) Domain facades: `zmcp.dobby`, `zmcp.asm`, `zmcp.decomp`, `zmcp.analysis`, `zmcp.bp`, `zmcp.debugger`, `zmcp.frida`, `zmcp.lua`, `zmcp.unity`, `zmcp.render`, `zmcp.unreal`, `zmcp.logic`, `zmcp.workspace`, `zmcp.native_lib`, `zmcp.diag`, plus all 287 typed tools in `zmcp.api.<tool>(...)` and dynamic `zmcp.mcp.<tool>(...)`."
)

TOOLS = [
    r.tool("python_status", "Report Python extension availability, supervisor state, host PID, loaded scripts, installed hooks, event drops and recent errors without spawning the host if idle. When writing Python code/scripts, use the `zmcp` SDK (`zmcp.help()` in `python_run` inspects any facade).", {}),
    r.tool("python_run", "Execute a transient Python snippet in the on-demand CPython 3.14 host with bounded timeout (lazily starts host when enabled). " + _ZMCP_SYNTAX_GUIDE, {"code": {"type": "string", "minLength": 1, "maxLength": 262144}, "timeout_ms": w.integer(500, 60000)}, ("code",)),
    r.tool("python_script_save", "Save or overwrite a fixed-root Python script in package or global scope under /data/adb/zygisk_il2cpp_mcp/python. Scripts import `zmcp` and define an entrypoint (default `def main():` or top-level `@zmcp.on(...)` / `@zmcp.hook...` decorators). " + _ZMCP_SYNTAX_GUIDE, {"name": SCRIPT_NAME, "code": {"type": "string", "minLength": 1, "maxLength": 262144}, "scope": SCOPE}, ("name", "code")),
    r.tool("python_script_get", "Read the source of a saved fixed-root Python script.", {"name": SCRIPT_NAME, "scope": SCOPE}, ("name",)),
    r.tool("python_script_list", "List saved fixed-root Python scripts in global and/or current package scopes.", {"scope": LIST_SCOPE}, ()),
    r.tool("python_script_remove", "Remove a saved fixed-root Python script.", {"name": SCRIPT_NAME, "scope": SCOPE}, ("name",)),
    r.tool("python_load", "Load a saved Python script into an isolated module namespace inside the CPython host and call its entrypoint.", {"name": SCRIPT_NAME, "scope": SCOPE, "entry": IDENTIFIER}, ("name",)),
    r.tool("python_unload", "Unload a loaded Python script module and clear hooks/timers/handlers owned by it.", {"name": SCRIPT_NAME}, ("name",)),
    r.tool("python_reload", "Unload then reload a saved Python script cleanly.", {"name": SCRIPT_NAME, "scope": SCOPE, "entry": IDENTIFIER}, ("name",)),
    r.tool("python_autorun_set", "Replace autorun entries for the current or specified package (up to 64 entries).", {"entries": w.array(AUTORUN_ENTRY, 64), "package": PACKAGE}, ("entries",)),
    r.tool("python_autorun_list", "Inspect configured Python autorun entries for one or all packages.", {"package": PACKAGE}, ()),
    r.tool("python_logs", "Read paged stderr/stdout and structured Python extension logs from the current or latest session.", {"after": w.integer(0, 1000000000), "limit": w.integer(1, 500)}, ()),
    r.tool("python_stop_all", "Stop all Python scripts, remove all Python-owned hooks, tear down the CPython host process and clear pending prompts.", {}),
]

BY_NAME = {tool["name"]: tool for tool in TOOLS}
FEATURES = ("python",)

for _ro in ("python_status", "python_script_get", "python_script_list", "python_autorun_list", "python_logs"):
    BY_NAME[_ro]["annotations"]["readOnlyHint"] = True
for _rw in ("python_run", "python_script_save", "python_script_remove", "python_load", "python_unload", "python_reload", "python_autorun_set", "python_stop_all"):
    BY_NAME[_rw]["annotations"]["destructiveHint"] = True

_OP_MAP = {
    "python_status": "status",
    "python_run": "run",
    "python_script_save": "script_save",
    "python_script_get": "script_get",
    "python_script_list": "script_list",
    "python_script_remove": "script_remove",
    "python_load": "load",
    "python_unload": "unload",
    "python_reload": "reload",
    "python_autorun_set": "autorun_set",
    "python_autorun_list": "autorun_list",
    "python_logs": "logs",
    "python_stop_all": "stop_all",
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.py$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PKG_RE = re.compile(r"^[A-Za-z0-9_.]+(?::[A-Za-z0-9_.:-]+)?$")


def features(name: str) -> tuple[str, ...]:
    if name in BY_NAME:
        return FEATURES
    raise KeyError(name)


def native_features(command: str) -> tuple[str, ...] | None:
    return FEATURES if command == "PY_CONTROL" or command.startswith("PY_") else None


def _validate_extra(name: str, args: dict[str, Any]) -> None:
    if "name" in args and not _NAME_RE.fullmatch(args["name"]):
        raise ValueError("invalid Python script name")
    if "entry" in args and not _IDENT_RE.fullmatch(args["entry"]):
        raise ValueError("invalid Python entry identifier")
    if "package" in args and not _PKG_RE.fullmatch(args["package"]):
        raise ValueError("invalid package name")
    if name == "python_autorun_set":
        for entry in args["entries"]:
            if not _NAME_RE.fullmatch(entry["name"]):
                raise ValueError("invalid script name in autorun entry")
            if "entry" in entry and not _IDENT_RE.fullmatch(entry["entry"]):
                raise ValueError("invalid entry identifier in autorun entry")


def encode(name: str, args: dict[str, Any]) -> str:
    if name not in BY_NAME or not isinstance(args, dict):
        raise ValueError("unknown python tool or invalid arguments")
    r.validate(args, BY_NAME[name]["inputSchema"], name)
    _validate_extra(name, args)
    payload = dict(args)
    payload["op"] = _OP_MAP[name]
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > 280000:
        raise ValueError("python payload exceeds 280000 UTF-8 bytes")
    return "PY_CONTROL " + r.text(raw)
