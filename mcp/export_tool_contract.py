"""Offline export of legacy typed tools for the Python-free native adapter.

The exported definitions are consumed by both transports. --check is a release
guard: changing a typed schema requires regenerating and testing the native
adapter. --patch emits an apply_patch payload; it never edits sources itself.
"""
from __future__ import annotations
import argparse
import json
import keyword
from pathlib import Path
from typing import Any
try:
    from . import mcp_server as server
except ImportError:
    import mcp_server as server

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "mcp/typed_tool_contract.inc"
DEFAULT_PY_API_DIR = ROOT / "extensions/python/host/zmcp/api"

def content():
    tools = getattr(server, "LEGACY_TOOLS", server.TOOLS)
    value = [{"tool": tool, "features": list(server.tool_features(tool["name"]))} for tool in tools]
    # ASCII output is intentional: Windows shells may otherwise transcode
    # Chinese defaults while emitting the generated patch.
    return 'R"typed(' + json.dumps(value, ensure_ascii=True, separators=(",", ":")) + ')typed"\n'


def _py_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "Any"
    kind = schema.get("type")
    if isinstance(kind, list):
        mapped = []
        for k in kind:
            if k == "null":
                mapped.append("None")
            else:
                mapped.append(_py_type({"type": k, **{pk: pv for pk, pv in schema.items() if pk != "type"}}))
        return " | ".join(dict.fromkeys(mapped)) or "Any"
    if kind == "string":
        return "str"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float | int"
    if kind == "boolean":
        return "bool"
    if kind == "array":
        item_t = _py_type(schema.get("items"))
        return f"list[{item_t}]"
    if kind == "object":
        return "dict[str, Any]"
    return "Any"


def _py_param_name(raw_name: str, used: set[str]) -> str:
    cand = raw_name if (raw_name.isidentifier() and not keyword.iskeyword(raw_name)) else (raw_name.replace("-", "_") + "_")
    if not cand.isidentifier() or keyword.iskeyword(cand):
        cand = f"arg_{abs(hash(raw_name))}"
    while cand in used:
        cand += "_"
    used.add(cand)
    return cand


def _tool_signature(tool: dict[str, Any]) -> tuple[list[tuple[str, str, str, bool]], str]:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required_set = set(schema.get("required") or ())
    used: set[str] = {"caller"}
    ordered = [k for k in props if k in required_set] + [k for k in props if k not in required_set]
    params: list[tuple[str, str, str, bool]] = []
    for raw in ordered:
        p_name = _py_param_name(raw, used)
        p_type = _py_type(props[raw])
        is_req = raw in required_set
        params.append((raw, p_name, p_type if is_req else f"{p_type} | None", is_req))
    desc = " ".join(str(tool.get("description") or "").split())
    return params, desc


def python_api_files() -> dict[str, str]:
    tools = list(getattr(server, "LEGACY_TOOLS", server.TOOLS))
    names = [t["name"] for t in tools]

    tools_lines = [
        '"""Auto-generated typed MCP tool wrappers from mcp/export_tool_contract.py."""',
        "# DO NOT EDIT BY HAND. Regenerate with:",
        "#   python mcp/export_tool_contract.py --write-python extensions/python/host/zmcp/api",
        "from __future__ import annotations",
        "",
        "from typing import Any, Callable",
        "",
        "_UNSET: Any = object()",
        "_CALLER: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None",
        "",
        "def set_caller(fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None) -> None:",
        "    global _CALLER",
        "    _CALLER = fn",
        "",
        "def _invoke(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:",
        "    if _CALLER is None:",
        '        raise RuntimeError("zmcp runtime caller is not initialized; run inside zmcp host")',
        "    return _CALLER(tool_name, args)",
        "",
    ]

    stub_lines = [
        "# Auto-generated type stubs from mcp/export_tool_contract.py.",
        "from __future__ import annotations",
        "",
        "from typing import Any, Callable",
        "",
        "def set_caller(fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None) -> None: ...",
        "",
    ]

    for tool in tools:
        tname = tool["name"]
        params, desc = _tool_signature(tool)
        sig_parts: list[str] = []
        stub_parts: list[str] = []
        for _raw, pname, ptype, is_req in params:
            if is_req:
                sig_parts.append(f"{pname}: {ptype}")
                stub_parts.append(f"{pname}: {ptype}")
            else:
                sig_parts.append(f"{pname}: {ptype} = _UNSET")
                stub_parts.append(f"{pname}: {ptype} = ...")
        sig = ", ".join(sig_parts)
        stub_sig = ", ".join(stub_parts)
        safe_doc = desc.replace('"""', '\\"\\"\\"')

        tools_lines.append(f"def {tname}({sig}) -> dict[str, Any]:")
        tools_lines.append(f'    """{safe_doc}"""')
        if not params:
            tools_lines.append(f'    return _invoke("{tname}", {{}})')
        else:
            tools_lines.append("    _args: dict[str, Any] = {}")
            for raw, pname, _ptype, is_req in params:
                if is_req:
                    tools_lines.append(f'    _args[{raw!r}] = {pname}')
                else:
                    tools_lines.append(f'    if {pname} is not _UNSET and {pname} is not None:')
                    tools_lines.append(f'        _args[{raw!r}] = {pname}')
            tools_lines.append(f'    return _invoke("{tname}", _args)')
        tools_lines.append("")

        stub_lines.append(f"def {tname}({stub_sig}) -> dict[str, Any]: ...")

    init_lines = [
        '"""Auto-generated MCP API namespace."""',
        "from __future__ import annotations",
        "",
        "from .tools import *  # noqa: F401,F403",
        "from .tools import set_caller",
        "",
        "__all__ = [",
        '    "set_caller",',
        *[f'    {name!r},' for name in names],
        "]",
        "",
    ]

    init_stub_lines = [
        "from .tools import *  # noqa: F403",
        "from .tools import set_caller as set_caller",
        "",
    ]

    return {
        "__init__.py": "\n".join(init_lines),
        "tools.py": "\n".join(tools_lines),
        "tools.pyi": "\n".join(stub_lines) + "\n",
        "__init__.pyi": "\n".join(init_stub_lines),
    }


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--patch",action="store_true")
    parser.add_argument("--write",action="store_true",help="Mechanically regenerate only mcp/typed_tool_contract.inc")
    parser.add_argument("--write-python",nargs="?",const=str(DEFAULT_PY_API_DIR),metavar="DIR",help="Regenerate Python API wrappers and .pyi stubs")
    parser.add_argument("--check-python",nargs="?",const=str(DEFAULT_PY_API_DIR),metavar="DIR",help="Verify Python API wrappers and .pyi stubs are up to date")
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
    elif args.write_python:
        out_dir=Path(args.write_python)
        out_dir.mkdir(parents=True,exist_ok=True)
        files=python_api_files()
        for rel,body in files.items():
            (out_dir/rel).write_text(body,encoding="utf-8",newline="\n")
        print(f"Generated {len(server.TOOLS)} Python MCP wrappers in {out_dir}")
    elif args.check_python:
        out_dir=Path(args.check_python)
        files=python_api_files()
        for rel,body in files.items():
            target=out_dir/rel
            if not target.is_file() or target.read_text(encoding="utf-8")!=body:
                raise SystemExit(f"Python API file is stale or missing: {target}")
        print(f"Python API matches all {len(server.TOOLS)} tool definitions")
    else:print(text,end="")

if __name__=="__main__":main()
