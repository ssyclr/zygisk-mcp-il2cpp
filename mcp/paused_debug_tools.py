"""External fixed-target pause debugger. Import/encoding never contacts a target."""
from __future__ import annotations
import json
try:
    from . import render_tools as r
    from . import register_tools
except ImportError:
    import render_tools as r
    import register_tools


def integer(low: int, high: int) -> dict:
    return {"type": "integer", "minimum": low, "maximum": high}


U64 = {"type": "string", "minLength": 1, "maxLength": 20,
       "description": "Exact unsigned decimal or 0x hexadecimal string, never a JSON number."}
TID = integer(1, 2**31 - 1)
REGISTERS = register_tools.schema()
TOOLS = [
    r.tool("debugger_status", "Read external ARM64 stopping-debugger capability, hardware permission state and leases. Separate from perf sampling. No attaching as a side effect.", readonly=True),
    r.tool("debugger_threads", "List threads of the bootstrapped target only; no arbitrary PID. Use tid AND thread_start_time for pause. Thread IDs may be reused. Reports current requester thread, which cannot be paused through this channel.",
           {"offset": integer(0, 2**31-1), "limit": integer(1, 256)}, readonly=True),
    r.tool("debugger_registers", "Read X0..X30, SP, PC, PSTATE and independently optional Q0..Q31/FPSR/FPCR, TLS and active SVE Z/P/FFR from an owned stopped ARM64 thread. Exact hex strings, fresh stop_id, unchanged lease. Extended data has a byte budget and explicit truncation. No automatic attaching or enabling CPU extensions.", {"tid": TID}, ("tid",), readonly=True),
    r.tool("debugger_backtrace", "Read a bounded ARM64 frame-pointer backtrace from an owned stopped thread. Reports raw LR, completeness and stop_reason; omitted frame pointers and PAC may stop unwind. No game calls and no lease renewal.",
           {"tid": TID, "max_frames": integer(1, 64)}, ("tid",), readonly=True),
    r.tool("debugger_control", "External root per-thread debugger: op=pause/resume/resume_all/step/step_over/step_out/continue/set_registers/renew/help. pause requires tid, expected_thread_start_time from debugger_threads and confirm=true; lease_ms defaults 5000, range1000..15000. One thread only; other threads may block on its locks. Reads never extend lease. Stopped-state mutations require current stop_id and confirm=true. set_registers also requires expected_pc and registers: X/W, PC/SP, FP/LR, Q/V/D/S/H/B, FPSR/FPCR, NZCV (only flags writable in PSTATE), TLS and active SVE Z/P/FFR; float values for S/D use 'float:1.5', others exact hex/decimal bits. Aliases may not overlap. PC must be executable/aligned, SP aligned/writable. Writes are read back and rollback attempted on failure. Missing extensions fail without disabling GPR operations. resume detaches; continue keeps owned breakpoints until the unchanged lease expires. Lease/disconnect/owner-exit cleanup preserves real signals and does not revert successful register edits. No privileged registers, forced CPU modes, security-policy changes or arbitrary PID. Use debugger_breakpoint_set.on_hit for automatic matched-hit edits. Avoid game API/frame waits while paused; inspect status after uncertain transport outcomes before retrying.",
           {"op": r.enum("pause", "resume", "resume_all", "step", "step_over", "step_out", "continue", "set_registers", "renew", "help"),
            "tid": TID, "expected_thread_start_time": U64, "stop_id": U64,
            "lease_ms": integer(1000, 15000), "confirm": {"type": "boolean"},
            "registers": REGISTERS, "expected_pc": U64}, ("op",)),
]
BY_NAME = {tool["name"]: tool for tool in TOOLS}
BY_NAME["debugger_control"]["annotations"]["destructiveHint"] = True


def _u64(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 20:
        raise ValueError(f"{label} must be an exact unsigned 64-bit string")
    hexadecimal = value.startswith(("0x", "0X"))
    digits = value[2:] if hexadecimal else value
    allowed = "0123456789abcdefABCDEF" if hexadecimal else "0123456789"
    if not digits or any(c not in allowed for c in digits):
        raise ValueError(f"invalid {label}")
    number = int(digits, 16 if hexadecimal else 10)
    if number > 2**64 - 1:
        raise ValueError(f"{label} exceeds unsigned 64-bit range")
    return hex(number)


def encode(name: str, args: dict) -> str:
    if name not in BY_NAME:
        raise ValueError("unknown pause debugger tool")
    r.validate(args, BY_NAME[name]["inputSchema"], "arguments")
    data = dict(args)
    if name == "debugger_control":
        op = data["op"]
        required = {
            "pause": {"tid", "expected_thread_start_time", "confirm"},
            "resume": {"tid"}, "resume_all": set(), "help": set(),
            "step": {"tid", "stop_id", "confirm"},
            "step_over": {"tid", "stop_id", "confirm"},
            "step_out": {"tid", "stop_id", "confirm"},
            "continue": {"tid", "stop_id", "confirm"},
            "renew": {"tid", "stop_id", "lease_ms", "confirm"},
            "set_registers": {"tid", "stop_id", "confirm", "expected_pc", "registers"},
        }[op] | {"op"}
        allowed = required | ({"lease_ms"} if op == "pause" else set())
        if not required <= data.keys() or not data.keys() <= allowed:
            raise ValueError(f"{op} requires {sorted(required)} and only accepts {sorted(allowed)}")
        if op in {"pause", "step", "step_over", "step_out", "continue", "renew", "set_registers"} and data["confirm"] is not True:
            raise ValueError("explicit confirm=true required")
        if op == "set_registers":
            if not data["registers"]:
                raise ValueError("registers must not be empty")
            data["registers"] = register_tools.normalize(data["registers"])
            for key, alignment in (("pc", 4), ("sp", 16)):
                if key in data["registers"]:
                    value = int(data["registers"][key], 16)
                    if not value or value % alignment:
                        raise ValueError(f"{key} must be nonzero and aligned to {alignment}")
    else:
        data["op"] = {"debugger_status": "status", "debugger_threads": "threads",
                      "debugger_registers": "registers", "debugger_backtrace": "backtrace"}[name]
    for key in ("stop_id", "expected_thread_start_time", "expected_pc"):
        if key in data:
            data[key] = _u64(data[key], key)
            if int(data[key], 16) == 0:
                raise ValueError(f"{key} must not be zero")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 32768:
        raise ValueError("debugger request too large")
    return "DEBUGGER_CONTROL " + encoded.hex()


def features(name: str) -> tuple[str, ...]:
    return ("breakpoint",)


def native_features(command: str) -> tuple[str, ...] | None:
    return ("breakpoint",) if command == "DEBUGGER_CONTROL" else None
