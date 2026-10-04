#!/usr/bin/env python3
"""Dependency-free stdio MCP bridge for the Zygisk IL2CPP command socket."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

if __package__:
    from . import (render_tools, workspace_tools, debug_tools, logic_tools,
                   relationship_tools, paused_debug_tools, task_tools, workflow_tools, native_library_tools, analysis_tools)
else:
    import render_tools, workspace_tools, debug_tools, logic_tools
    import relationship_tools, paused_debug_tools, task_tools, workflow_tools, native_library_tools
    import analysis_tools


SERVER_NAME = "zygisk-il2cpp-mcp"
SERVER_VERSION = "2.7.9"
LATEST_PROTOCOL = "2025-11-25"
SUPPORTED_PROTOCOLS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
}
MAX_HEADER_BYTES = 16 * 1024
MAX_BODY_BYTES = 4 * 1024 * 1024

FEATURES: dict[str, str] = {
    "connection": "Connection and target availability tools",
    "ui": "Clipboard, input box, and in-app Toast tools",
    "overlay_ui": "In-game ImGui language, theme, window and visibility controls",
    "rendering": "Java SurfaceView object visualization, styles, cameras and game-frame sampling",
    "il2cpp_metadata": "IL2CPP status, dump, metadata listing, and fuzzy search",
    "il2cpp_invoke": "IL2CPP managed method invocation",
    "il2cpp_objects": "IL2CPP object, List, array, and Dictionary inspection",
    "il2cpp_hook": "IL2CPP method hooks",
    "memory_maps": "Module maps, address ownership, and base resolution",
    "memory_read": "Native memory reads",
    "memory_write": "Native memory writes",
    "memory_search": "Exact, fuzzy, and filtered memory searches",
    "pointer_chain": "Base scans and multi-level pointer-chain resolution",
    "dobby": "Native Dobby hooks, symbols, and code patches",
    "frida": "Optional embedded Frida Gum instrumentation and Stalker",
    "trace": "Dobby execution tracing and trace backtraces",
    "lua": "Embedded LuaJIT execution",
    "assembly": "Assembly, disassembly, and instruction patching",
    "decompiler": "Ghidra-native ARM64 C pseudocode decompilation",
    "native_libraries": "Explicit custom SO upload/loading in the connected target",
    "breakpoint": "Hardware breakpoints, watchpoints, hits, and backtraces",
    "diagnostics": "Runtime capabilities, help, and raw bridge commands",
}

class FeatureRegistry:
    """Thread-safe MCP-side feature flags. Every feature defaults to enabled."""

    def __init__(self, config_path: str | os.PathLike[str] | None = None):
        self._lock = threading.RLock()
        self._states = {name: True for name in FEATURES}
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._listeners: list[Callable[[], None]] = []
        self._load()

    def _load(self) -> None:
        if not self._config_path or not self._config_path.is_file():
            return
        try:
            payload = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        states = payload.get("features", payload) if isinstance(payload, dict) else {}
        if not isinstance(states, dict):
            return
        for name, enabled in states.items():
            if name in self._states and isinstance(enabled, bool):
                self._states[name] = enabled

    def _save(self) -> None:
        if not self._config_path:
            return
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._config_path.with_suffix(self._config_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"features": self._states}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self._config_path)

    def subscribe(self, listener: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def enabled(self, name: str) -> bool:
        with self._lock:
            return bool(self._states.get(name, False))

    def require(self, tool_name: str) -> None:
        disabled = [name for name in tool_features(tool_name) if not self.enabled(name)]
        if disabled:
            raise BridgeError(
                f"MCP feature disabled for {tool_name}: {', '.join(disabled)}"
            )

    def set(self, name: str, enabled: bool) -> dict[str, Any]:
        if name not in FEATURES:
            raise BridgeError(f"unknown MCP feature: {name}")
        if not isinstance(enabled, bool):
            raise BridgeError("enabled must be a boolean")
        with self._lock:
            changed = self._states[name] != enabled
            self._states[name] = enabled
            if changed:
                self._save()
            listeners = list(self._listeners) if changed else []
        for listener in listeners:
            listener()
        return {"feature": name, "enabled": enabled, "changed": changed}

    def set_all(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise BridgeError("enabled must be a boolean")
        with self._lock:
            changed = any(value != enabled for value in self._states.values())
            for name in self._states:
                self._states[name] = enabled
            if changed:
                self._save()
            listeners = list(self._listeners) if changed else []
        for listener in listeners:
            listener()
        return {"enabled": enabled, "changed": changed, "feature_count": len(self._states)}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "default_enabled": True,
                "features": [
                    {
                        "name": name,
                        "enabled": self._states[name],
                        "description": description,
                    }
                    for name, description in FEATURES.items()
                ],
            }


class BridgeError(RuntimeError):
    """A connection, protocol, or hook-call failure suitable for an MCP result."""


def _single_line(value: Any, field: str, *, allow_pipe: bool = True) -> str:
    text = str(value)
    if "\n" in text or "\r" in text or "\x00" in text:
        raise BridgeError(f"{field} must be a single line")
    if not allow_pipe and "|" in text:
        raise BridgeError(f"{field} cannot contain '|'")
    return text


@dataclass
class ConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 27184
    timeout: float = 5.0
    auto_adb_forward: bool = True
    adb_path: str = "adb"
    adb_serial: str | None = None
    target_session: str | None = None
    target_process: dict[str, Any] | None = None

    def validate(self) -> None:
        if not self.host:
            raise BridgeError("host cannot be empty")
        if not 1 <= self.port <= 65535:
            raise BridgeError("port must be between 1 and 65535")
        if not 0.1 <= self.timeout <= 300:
            raise BridgeError("timeout must be between 0.1 and 300 seconds")


class HookSocketClient:
    """Client for the native `OK <length>\n<body>` command protocol."""

    def __init__(self, config: ConnectionConfig, *, route_process: bool = False,
                 on_target: Callable[[dict[str, Any]], None] | None = None):
        self.config = config
        self.route_process = route_process
        self.on_target = on_target

    @staticmethod
    def _recv_line(sock: socket.socket) -> str:
        data = bytearray()
        while len(data) < MAX_HEADER_BYTES:
            chunk = sock.recv(1)
            if not chunk:
                raise BridgeError("hook service closed the connection")
            if chunk == b"\n":
                return data.decode("utf-8", errors="strict")
            data.extend(chunk)
        raise BridgeError("hook service returned an oversized response header")

    @staticmethod
    def _recv_exact(sock: socket.socket, length: int) -> bytes:
        if length < 0 or length > MAX_BODY_BYTES:
            raise BridgeError("hook service returned an invalid response length")
        data = bytearray()
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                raise BridgeError("hook service closed before the response completed")
            data.extend(chunk)
        return bytes(data)

    def call(self, command: str, *, timeout: float | None = None, retry_forward: bool = True,
             _query_diagnostics: bool = True, _process_routing: bool = True) -> str:
        command = _single_line(command, "command").strip()
        if not command:
            raise BridgeError("command cannot be empty")
        command_limit = (8*1024*1024+64 if command.startswith("WORKSPACE_BROWSER ") else
                         160*1024 if command.startswith("NATIVE_LIBRARY_CONTROL ") else 64*1024)
        if len(command.encode("utf-8")) > command_limit:
            raise BridgeError("command is too long")

        diagnostic = _query_diagnostics and command.split()[0] in {"IL2CPP_FIND_METHOD", "IL2CPP_METHODS"}
        wire_command = f"MCP_QUERY_V1 {command}" if diagnostic else command
        routed = _process_routing and (self.route_process or self.config.target_session is not None) and command != "MCP_TARGETS"
        if routed:
            session = self.config.target_session or "auto"
            if not isinstance(session, str) or not session or len(session) > 160 or any(c not in "0123456789abcdef-" for c in session) and session != "auto":
                raise BridgeError("invalid target process session")
            wire_command = f"MCP_ROUTE {session} {wire_command}"
        last_stage: dict[str, Any] | None = None
        connected = False
        try:
            with socket.create_connection(
                (self.config.host, self.config.port),
                timeout=timeout or self.config.timeout,
            ) as sock:
                connected = True
                sock.settimeout(timeout or self.config.timeout)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(wire_command.encode("utf-8") + b"\n")
                header = self._recv_line(sock)
                if routed and header.startswith("MCP_TARGET_INFO "):
                    try:
                        target = json.loads(header[len("MCP_TARGET_INFO "):])
                    except (ValueError, TypeError) as exc:
                        raise BridgeError("invalid target process identity") from exc
                    if (not isinstance(target, dict) or type(target.get("pid")) is not int or target["pid"] <= 1
                            or not isinstance(target.get("process_name"), str) or not target["process_name"]
                            or not isinstance(target.get("session"), str) or not target["session"]
                            or len(target["session"]) > 160
                            or any(c not in "0123456789abcdef-" for c in target["session"])):
                        raise BridgeError("invalid target process identity")
                    if self.config.target_session and target["session"] != self.config.target_session:
                        raise BridgeError("target process identity changed; command was not replayed")
                    if self.on_target:
                        self.on_target(target)
                    header = self._recv_line(sock)
                elif routed and header == "ERR UNKNOWN_COMMAND" and self.config.target_session is None:
                    # Old module rejected the outer routing verb, so the inner
                    # command (including mutations) was NOT executed. No other
                    # error/disconnect can trigger this compatibility fallback.
                    return self.call(command, timeout=timeout, retry_forward=False,
                                     _query_diagnostics=_query_diagnostics, _process_routing=False)
                elif routed and not header.startswith("ERR "):
                    raise BridgeError("process router omitted target identity; command was not replayed")
                stage_count = 0
                while diagnostic and header.startswith("MCP_STAGE "):
                    stage_count += 1
                    if stage_count > 4097:
                        raise BridgeError("too many native diagnostic stages")
                    try:
                        stage = json.loads(header[len("MCP_STAGE "):])
                    except (ValueError, TypeError) as exc:
                        raise BridgeError("invalid native diagnostic stage") from exc
                    if not isinstance(stage, dict) or not isinstance(stage.get("stage"), str):
                        raise BridgeError("invalid native diagnostic stage")
                    last_stage = stage
                    header = self._recv_line(sock)
                if header.startswith("ERR "):
                    if diagnostic and last_stage is None and header == "ERR UNKNOWN_COMMAND":
                        # Old module rejected the envelope, so the read-only query was not run.
                        return self.call(command, timeout=timeout, retry_forward=False, _query_diagnostics=False,
                                         _process_routing=_process_routing)
                    raise BridgeError(header[4:].strip() or "hook call failed")
                if not header.startswith("OK "):
                    raise BridgeError(f"unexpected hook response: {header!r}")
                try:
                    body_length = int(header[3:].strip())
                except ValueError as exc:
                    raise BridgeError(f"invalid hook response header: {header!r}") from exc
                return self._recv_exact(sock, body_length).decode("utf-8", errors="strict").rstrip("\n")
        except BridgeError as exc:
            if last_stage is not None:
                raise BridgeError(f"{command.split()[0]}: {exc}; last_native_stage="
                                  + json.dumps(last_stage, ensure_ascii=False)
                                  + ". This is a stage breadcrumb, not a confirmed crash backtrace; do not replay automatically.") from exc
            raise
        except OSError as exc:
            if not connected and retry_forward and self._can_auto_forward():
                self._adb_forward()
                return self.call(command, timeout=timeout, retry_forward=False, _query_diagnostics=_query_diagnostics,
                                 _process_routing=_process_routing)
            if connected:
                raise BridgeError(
                    f"{command.split()[0]} transport failed after connecting to "
                    f"{self.config.host}:{self.config.port}: {exc}. "
                    "The target may be busy, disconnected or restarted; the command was not replayed. "
                    "Check ping/status before reusing runtime addresses."
                    + (" last_native_stage=" + json.dumps(last_stage, ensure_ascii=False) if last_stage is not None else "")
                ) from exc
            raise BridgeError(
                f"cannot connect to hook service at {self.config.host}:{self.config.port}: {exc}"
            ) from exc

    def _can_auto_forward(self) -> bool:
        return self.config.auto_adb_forward and self.config.host in {"127.0.0.1", "localhost", "::1"}

    def _adb_forward(self) -> None:
        args = [self.config.adb_path]
        if self.config.adb_serial:
            args.extend(["-s", self.config.adb_serial])
        args.extend(["forward", f"tcp:{self.config.port}", f"tcp:{self.config.port}"])
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BridgeError(f"adb forward failed: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "unknown adb error").strip()
            raise BridgeError(f"adb forward failed: {detail}")


EMPTY_SCHEMA = {"type": "object", "additionalProperties": False}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "process_list",
        "title": "List injected processes",
        "description": "List live injected main/subprocesses at this gateway with PID, process_name and session. Does not select or change a target. Requires module 2.6.2+; only configured and successfully registered processes appear.",
        "inputSchema": EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "process_select",
        "title": "Select injected process",
        "description": "Select by exact process_name and/or PID from process_list. Pins this MCP service to that process lifetime for ALL subsequent tools. Multiple matches are rejected; exit/restart requires explicit reselection and discarding old addresses/handles. Switching does not stop hooks/freezes in the previous process.",
        "inputSchema": {
            "type": "object", "properties": {
                "pid": {"type": "integer", "minimum": 2, "maximum": 2147483647},
                "process_name": {"type": "string", "minLength": 1, "maxLength": 1024},
            }, "anyOf": [{"required": ["pid"]}, {"required": ["process_name"]}],
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "process_current",
        "title": "Verify current process",
        "description": "Return the live PID, UID, exact process_name and session of the current target. A sole target is automatically pinned; multiple targets require process_select. Does not silently follow a restarted process.",
        "inputSchema": EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "ping",
        "title": "Check hook connection",
        "description": "Check whether the target game hook service is reachable.",
        "inputSchema": EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "connection_info",
        "title": "Get connection settings",
        "description": "Return the current host, port, timeout, and ADB forwarding settings.",
        "inputSchema": EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "configure_connection",
        "title": "Configure hook connection",
        "description": "Change the hook service connection for later MCP calls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Hook service host."},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 300},
                "auto_adb_forward": {"type": "boolean"},
                "adb_serial": {"type": ["string", "null"]},
            },
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "get_clipboard",
        "title": "Read game clipboard",
        "description": "Read clipboard text through the injected IL2CPP hook.",
        "inputSchema": EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mcp_toast_status",
        "title": "Get MCP Toast status",
        "description": "Return whether in-app JNI Toast notifications for MCP calls are enabled.",
        "inputSchema": EMPTY_SCHEMA,
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "mcp_toast_set_enabled",
        "title": "Enable or disable MCP Toast",
        "description": "Control automatic in-app JNI Toast notifications that show MCP tool calls.",
        "inputSchema": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "mcp_toast_show",
        "title": "Show an in-app Toast",
        "description": "Immediately show custom text inside the target app through JNI, independent of the automatic switch.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "maxLength": 1000}},
            "required": ["message"],
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "show_input_box",
        "title": "Show Unity input box",
        "description": "Open the hooked Unity TouchScreenKeyboard without waiting for completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "default": "输入"},
                "hint": {"type": "string", "default": "请输入内容"},
                "prefill": {"type": "string", "default": ""},
            },
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "wait_input",
        "title": "Wait for Unity input",
        "description": "Wait for the currently open Unity input box and return its result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 300000, "default": 120000},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "input_and_wait",
        "title": "Show input box and wait",
        "description": "Open the Unity input box, wait for completion, and return the entered text. Other MCP requests remain available while waiting. After the box opens, a concurrent push_input_result can complete it. Only one input wait/dialog operation may be active per MCP dispatcher; a competing one returns INPUT_REQUEST_BUSY.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "default": "输入"},
                "hint": {"type": "string", "default": "请输入内容"},
                "prefill": {"type": "string", "default": ""},
                "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 300000, "default": 120000},
            },
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "push_input_result",
        "title": "Push input result",
        "description": "Push a result into the hook input state, primarily for automation and testing. May run concurrently with input_and_wait/wait_input once the input box is open; do not push before SHOW_INPUT_BOX has initialized the input state.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
    {
        "name": "raw_hook_call",
        "title": "Call raw hook command",
        "description": "Send any single-line native socket command, including commands added by future hooks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "A single native command line without CR/LF."},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 300},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "annotations": {"openWorldHint": False},
    },
]

METHOD_LOOKUP_PROPERTIES: dict[str, Any] = {
    "image": {"type": "string", "description": "IL2CPP image name, for example Assembly-CSharp.dll."},
    "namespace": {"type": "string", "description": "Exact namespace; use an empty string for the global namespace."},
    "class_name": {"type": "string", "description": "Exact class name."},
    "method": {"type": "string", "description": "Exact method name."},
    "parameter_count": {"type": "integer", "minimum": -1, "default": -1},
}

RETURN_KIND_SCHEMA = {
    "type": "string",
    "enum": ["void", "bool", "i64", "u64", "ptr", "f32", "f64"],
    "description": "Native ABI return kind used by the generated Dobby replacement stub.",
}

MEMORY_VALUE_FORMATS: dict[str, str] = {
    "bool": "B",
    "i8": "b",
    "u8": "B",
    "i16": "h",
    "u16": "H",
    "i32": "i",
    "u32": "I",
    "i64": "q",
    "u64": "Q",
    "f32": "f",
    "f64": "d",
    "ptr32": "I",
    "ptr64": "Q",
}

MEMORY_VALUE_KIND_SCHEMA = {
    "type": "string",
    "enum": list(MEMORY_VALUE_FORMATS),
    "description": "Little-endian value type. Choose ptr32 or ptr64 to match the target process ABI.",
}

MEMORY_REGION_TYPES = [
    "anonymous", "heap", "stack", "app_code", "system_code",
    "app_data", "ashmem", "java", "other",
]

MEMORY_REGION_TYPES_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "enum": MEMORY_REGION_TYPES},
    "uniqueItems": True,
    "minItems": 1,
    "description": "One or more /proc/self/maps region classes. Omit to scan every readable class.",
}

TOOLS.extend(
    [
        {
            "name": "il2cpp_status",
            "title": "Get IL2CPP runtime status",
            "description": "Probe IL2CPP without requiring thread attach. ready means basic exports only, not every feature. metadata_query_supported indicates an enumeration backend; metadata_query_ready indicates native enumeration or at least one verified legacy image. compatibility reports lazy managed-reflection cache verification, array/object layout validation, and precise errors. Status itself never invokes reflection or allocates target objects. Includes the separate stripped-runtime TypeDatabase diagnostics.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_dump_file",
            "title": "Dump IL2CPP metadata to app storage",
            "description": "Dump fields, properties, methods and RVAs into a new private files/zygisk_il2cpp_mcp/il2cpp_dump_*.cs file. Optional exact image, namespace and class_name filters; namespace=empty selects the global namespace. No arguments dumps all metadata. Returns success, path and class_count only; never returns dump contents. Empty selections fail without creating a dump.",
            "inputSchema": {"type": "object", "properties": debug_tools.TYPE_SELECTION, "additionalProperties": False},
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "il2cpp_list_images",
            "title": "List IL2CPP images",
            "description": "List IL2CPP assembly image names. Uses the validated metadata TypeDatabase when runtime exports are stripped.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 256}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_unity_catalog",
            "title": "List Unity compatibility metadata",
            "description": "List Unity scene, component, renderer, or asset type declarations from the validated TypeDatabase. Works when il2cpp_* exports are stripped. Results are metadata declarations, never fabricated live instances; protected names include stable aliases plus raw metadata names.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["all", "scene", "component", "asset", "renderer"],
                        "default": "all",
                    },
                    "query": {"type": "string", "default": ""},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_list_classes",
            "title": "List IL2CPP classes",
            "description": "List or filter classes in an IL2CPP image. Prefers native exports. Older runtimes lacking image enumeration use validated core-library reflection over already loaded assemblies (cached per image); errors are explicit, never silently empty. Fully stripped runtimes use the separate validated metadata TypeDatabase.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": METHOD_LOOKUP_PROPERTIES["image"],
                    "namespace_filter": {"type": "string", "default": ""},
                    "name_filter": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200},
                },
                "required": ["image"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_list_methods",
            "title": "List IL2CPP methods",
            "description": "List methods, validated native addresses/RVAs, parameter types, and return types for a class. On stripped runtimes, unavailable registration data is represented safely instead of invoking guessed APIs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": METHOD_LOOKUP_PROPERTIES["image"],
                    "namespace": METHOD_LOOKUP_PROPERTIES["namespace"],
                    "class_name": METHOD_LOOKUP_PROPERTIES["class_name"],
                    "name_filter": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200},
                },
                "required": ["image", "namespace", "class_name"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_find_method",
            "title": "Resolve IL2CPP method",
            "description": "Resolve an IL2CPP method to metadata and, when validated registration data is available, its native address and RVA. Supports the read-only stripped-runtime TypeDatabase.",
            "inputSchema": {
                "type": "object",
                "properties": METHOD_LOOKUP_PROPERTIES,
                "required": ["image", "namespace", "class_name", "method"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_invoke",
            "title": "Invoke IL2CPP method",
            "description": "Invoke a static or addressed instance IL2CPP method through il2cpp_runtime_invoke. Supports primitive, string, null, pointer, and enum arguments. Enums accept an underlying integer, a member name such as Walking or RoleSyncState.Walking, or an explicit {\"enum\": \"RoleSyncState.Walking\"} value.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **METHOD_LOOKUP_PROPERTIES,
                    "instance_address": {"type": "string", "default": "0x0"},
                    "arguments": {
                        "type": "array",
                        "items": {
                            "anyOf": [
                                {"type": ["string", "number", "boolean", "null"]},
                                {
                                    "type": "object",
                                    "properties": {
                                        "enum": {
                                            "type": ["string", "integer"],
                                            "description": "Enum member name (optionally Type.Member), flags joined by |, or its underlying integer value.",
                                        }
                                    },
                                    "required": ["enum"],
                                    "additionalProperties": False,
                                },
                            ]
                        },
                        "maxItems": 64,
                        "default": [],
                    },
                },
                "required": ["image", "namespace", "class_name", "method"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "il2cpp_hook",
            "title": "Hook IL2CPP method",
            "description": "Resolve an IL2CPP method and install a Dobby hook to an explicit native replacement address; returns the original trampoline address. Stripped runtimes are supported only after CodeRegistration and the method address pass structural/executable validation.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **METHOD_LOOKUP_PROPERTIES,
                    "replacement_address": {"type": "string"},
                },
                "required": ["image", "namespace", "class_name", "method", "replacement_address"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "il2cpp_hook_return",
            "title": "Hook IL2CPP method return",
            "description": "Resolve an IL2CPP method and install a Dobby replacement that returns a fixed ABI value, skipping the entire method body for all instances. Stripped runtimes require a structurally validated CodeRegistration/method address. Calling again updates our existing same-ABI fixed return without rehooking; other hook/trace owners are not overwritten.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **METHOD_LOOKUP_PROPERTIES,
                    "return_kind": RETURN_KIND_SCHEMA,
                    "value": {"type": ["string", "number", "boolean", "null"]},
                },
                "required": ["image", "namespace", "class_name", "method", "return_kind"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "il2cpp_unhook",
            "title": "Remove IL2CPP method hook",
            "description": "Resolve an IL2CPP method and restore it with DobbyDestroy.",
            "inputSchema": {
                "type": "object",
                "properties": METHOD_LOOKUP_PROPERTIES,
                "required": ["image", "namespace", "class_name", "method"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "memory_backend_status",
            "title": "Get memory backend status",
            "description": "Report the selected data-memory backend: local KittyMemory or authenticated external root-companion driver. Driver status is lazy and includes reason/transport. Selection and device node are configured in WebUI and apply after target restart; other IL2CPP/hooks/code patches remain local.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_read",
            "title": "Read target-process memory",
            "description": "Read 1 to 65536 bytes from a fully readable mapped range in the target process. Returns lowercase hexadecimal bytes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Absolute target-process address, normally hexadecimal."},
                    "size": {"type": "integer", "minimum": 1, "maximum": 65536},
                },
                "required": ["address", "size"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_write",
            "title": "Write target-process memory",
            "description": "Write 1 to 65536 hexadecimal bytes to a fully readable and writable mapped range in the target process. Returns the previous bytes and verifies the write. Use dobby_patch_code for executable code pages.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Absolute target-process address, normally hexadecimal."},
                    "hex_bytes": {
                        "type": "string",
                        "minLength": 2,
                        "maxLength": 131072,
                        "pattern": "^[0-9A-Fa-f]+$",
                    },
                },
                "required": ["address", "hex_bytes"],
                "additionalProperties": False,
            },
            "annotations": {"destructiveHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_read_value",
            "title": "Read a typed memory value",
            "description": "Read one little-endian boolean, integer, floating-point, or 32/64-bit pointer value from target-process memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "value_type": MEMORY_VALUE_KIND_SCHEMA,
                },
                "required": ["address", "value_type"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_write_value",
            "title": "Write a typed memory value",
            "description": "Encode and write one little-endian boolean, integer, floating-point, or 32/64-bit pointer value. Returns the previous typed value.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "value_type": MEMORY_VALUE_KIND_SCHEMA,
                    "value": {"type": ["string", "number", "boolean"]},
                },
                "required": ["address", "value_type", "value"],
                "additionalProperties": False,
            },
            "annotations": {"destructiveHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_list_modules",
            "title": "List loaded native modules",
            "description": "List executable modules in the target process with path, occurrence index, start/end, load bias, size, and region count. Duplicate basenames receive occurrence values starting at 1.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name_filter": {"type": "string", "default": ""},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 256},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_find_module",
            "title": "Find a loaded module instance",
            "description": "Resolve an exact module basename or full path and its 1-based occurrence. Returns module start/end and every mapped region.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module_name": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1},
                },
                "required": ["module_name"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_address_info",
            "title": "Locate an address in memory maps",
            "description": "Return the exact mapped region containing an address and, when applicable, the owning module, module occurrence, and relative offsets.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_search",
            "title": "Search target-process memory",
            "description": "Search a module instance or explicit address range for a byte pattern. Supports byte and nibble wildcards such as '48 8B ?? A?' and creates a filterable search session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Hexadecimal byte pattern; ? is a wildcard nibble."},
                    "module_name": {"type": "string", "description": "Exact basename or full path. Mutually exclusive with explicit range."},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1},
                    "start_address": {"type": "string"},
                    "end_address": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 1024},
                    "alignment": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1},
                    "memory_types": MEMORY_REGION_TYPES_SCHEMA,
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_search_value",
            "title": "Search for a typed memory value",
            "description": "Encode a little-endian typed value and search a module instance or explicit range. Returns a filterable search session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "value_type": MEMORY_VALUE_KIND_SCHEMA,
                    "value": {"type": ["string", "number", "boolean"]},
                    "module_name": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1},
                    "start_address": {"type": "string"},
                    "end_address": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 1024},
                    "alignment": {"type": "integer", "minimum": 1, "maximum": 4096, "description": "Defaults to the encoded value size."},
                    "memory_types": MEMORY_REGION_TYPES_SCHEMA,
                },
                "required": ["value_type", "value"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_search_exact",
            "title": "Exact-search multiple value types",
            "description": "Search multiple encodings. Also accepts GG-style expressions: 100;200:512 (unordered), 100;200::512 (ordered), 10~20 inclusive range, B/W/D/Q/F/E suffixes; hex supports wildcards, utf8/utf16 literal text. Advanced expressions/options use the shared typed engine and return sessions plus legacy searches. Group results are witnessed members, not OR matches. scan_mb/timeout_ms bound work; check truncated/stop_reason. For tabs/selection use memory_search_tabs. Ordinary scalar calls retain their legacy protocol.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "value_types": {"type": "array", "items": render_tools.enum(*MEMORY_VALUE_FORMATS, "hex", "utf8", "utf16"), "minItems": 1, "uniqueItems": True},
                    "value": {"type": ["string", "number", "boolean"]},
                    "module_name": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1},
                    "start_address": {"type": "string"},
                    "end_address": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 1024},
                    "alignment": {"type": "integer", "minimum": 1, "maximum": 4096},
                    "memory_types": MEMORY_REGION_TYPES_SCHEMA,
                    "scan_mb": {"type": "integer", "minimum": 1, "maximum": 512},
                    "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 60000},
                },
                "required": ["value_types", "value"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_search_fuzzy",
            "title": "Start a fuzzy/unknown-value search",
            "description": "Snapshot aligned values without requiring an initial exact value, then narrow the session with changed, unchanged, increased, decreased, equals, or not-equals filters.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "value_size": {"type": "integer", "enum": [1, 2, 4, 8]},
                    "module_name": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 1},
                    "start_address": {"type": "string"},
                    "end_address": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 10000},
                    "alignment": {"type": "integer", "minimum": 1, "maximum": 4096},
                    "memory_types": MEMORY_REGION_TYPES_SCHEMA,
                },
                "required": ["value_size"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_filter",
            "title": "Filter memory search results",
            "description": "Narrow a search session by equals/not_equals pattern, changed/unchanged snapshot, or unsigned little-endian increased/decreased comparison. Retained snapshots update after each filter.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "integer", "minimum": 1},
                    "mode": {"type": "string", "enum": ["equals", "not_equals", "changed", "unchanged", "increased", "decreased"]},
                    "pattern": {"type": "string", "description": "Required for equals/not_equals; byte length must equal the original search pattern."},
                },
                "required": ["session_id", "mode"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_filter_value",
            "title": "Filter search results by typed value",
            "description": "Refine a shared typed/group search session. Omit value_type to use each native row's type, including mixed groups; modes: equals/not_equals/greater/less/changed/unchanged/increased/decreased/increased_by/decreased_by. value required only for comparison/delta modes; accepts joint/range expressions. Retains old candidates on read/incomplete-group failures. For legacy byte searches, supply value_type with scalar equals/not_equals to retain the original protocol.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "integer", "minimum": 1},
                    "mode": render_tools.enum("equals", "not_equals", "greater", "less", "changed", "unchanged", "increased", "decreased", "increased_by", "decreased_by"),
                    "value_type": MEMORY_VALUE_KIND_SCHEMA,
                    "value": {"type": ["string", "number", "boolean"]},
                },
                "required": ["session_id", "mode"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_search_results",
            "title": "Page memory-search results",
            "description": "Return a bounded page of addresses and current snapshot bytes from a search session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "integer", "minimum": 1},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_search_clear",
            "title": "Clear a memory search session",
            "description": "Release one native memory-search session and its saved snapshots.",
            "inputSchema": {
                "type": "object",
                "properties": {"session_id": {"type": "integer", "minimum": 1}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "dobby_version",
            "title": "Get Dobby version",
            "description": "Return the embedded Dobby runtime version.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "dobby_resolve_symbol",
            "title": "Resolve native symbol",
            "description": "Resolve an exported symbol in already loaded modules, without loading libraries or parsing static ELF section tables. image is an exact basename or full loaded path; use full paths for duplicate basenames. Missing exports return address 0x0. Ambiguous global matches return an error. The legacy tool name is retained; this no longer invokes Dobby's unbounded internal-symbol fallback.",
            "inputSchema": {
                "type": "object",
                "properties": {"image": {"type": "string", "default": ""}, "symbol": {"type": "string"}},
                "required": ["symbol"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "dobby_hook",
            "title": "Install raw Dobby hook",
            "description": "Call DobbyHook with explicit target and replacement addresses; returns the original trampoline address.",
            "inputSchema": {
                "type": "object",
                "properties": {"target_address": {"type": "string"}, "replacement_address": {"type": "string"}},
                "required": ["target_address", "replacement_address"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "dobby_hook_return",
            "title": "Install fixed-return Dobby hook",
            "description": "Install a generated fixed-return stub at a raw executable address, skipping the original body. Calling again updates our existing same-ABI fixed return atomically without rehooking. Other hook/trace owners are not overwritten; a different ABI requires explicit removal first. The caller must choose the correct native return ABI.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_address": {"type": "string"},
                    "return_kind": RETURN_KIND_SCHEMA,
                    "value": {"type": ["string", "number", "boolean", "null"]},
                },
                "required": ["target_address", "return_kind"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "dobby_instrument",
            "title": "Instrument native address",
            "description": "Install DobbyInstrument. mode=backtrace (default) captures per-hit frame-pointer backtraces; counter counts without walking the stack. Manage using dobby_trace_list/control/hits.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}, "mode": {"type": "string", "enum": ["backtrace", "counter"]}},
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "dobby_trace_get",
            "title": "Read instrumentation count",
            "description": "Return the execution count for a Dobby-instrumented address.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "dobby_patch_code",
            "title": "Patch native code",
            "description": "Apply hexadecimal machine-code bytes with KittyMemory (maximum 4096 bytes); restore page permissions and flush the instruction cache. The tool name is retained for compatibility; save original bytes yourself for rollback.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}, "hex_bytes": {"type": "string", "pattern": "^[0-9A-Fa-f]+$"}},
                "required": ["address", "hex_bytes"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "dobby_destroy",
            "title": "Destroy Dobby hook",
            "description": "Restore a Dobby hook or instrumentation point using DobbyDestroy. Does not undo standalone byte/assembly patches; write saved original bytes to roll those back.",
            "inputSchema": {
                "type": "object",
                "properties": {"target_address": {"type": "string"}},
                "required": ["target_address"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "dobby_list_hooks",
            "title": "List active Dobby hooks",
            "description": "List fixed-return hooks, raw hooks, and instrumentation counters installed through this bridge.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "debug_help",
            "title": "Explain native debug commands",
            "description": "List custom native bridge commands or return usage and an explanation for one command.",
            "inputSchema": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "Optional native command such as ASM_PATCH or LUA_EXEC."}},
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "runtime_capabilities",
            "title": "Get optional runtime capabilities",
            "description": "Report memory backend, LuaJIT, assembly, breakpoint, Dobby, and IL2CPP availability without eagerly loading optional subsystems.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "lua_status",
            "title": "Get LuaJIT status",
            "description": "Report LuaJIT availability and VM state without creating the VM.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "lua_execute",
            "title": "Execute LuaJIT script",
            "description": "Lazily create the embedded LuaJIT VM and execute a script with safe memory, module, call, Dobby hook, and instrumentation APIs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "maxLength": 262144},
                    "timeout": {"type": "number", "minimum": 0.1, "maximum": 300, "default": 30},
                },
                "required": ["script"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "lua_reset",
            "title": "Reset LuaJIT VM",
            "description": "Remove hooks owned by Lua and replace the LuaJIT VM with a clean lazy state.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "lua_logs",
            "title": "Read LuaJIT callback logs",
            "description": "Read persistent output from scripts, hook callbacks, and main-thread call closures, optionally clearing it after reading.",
            "inputSchema": {
                "type": "object",
                "properties": {"clear": {"type": "boolean", "default": True}},
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "assembly_status",
            "title": "Get assembly engine status",
            "description": "Report whether the ARM64 AsmJit/Capstone/Dobby assembly engine is available.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "assembly_assemble",
            "title": "Validate and assemble ARM64 code",
            "description": "Validate one or multiple newline-separated AArch64 instructions and return machine-code bytes without changing memory. Each non-empty line must encode exactly one instruction.",
            "inputSchema": {
                "type": "object",
                "properties": {"instruction": {"type": "string", "maxLength": 16384}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "assembly_disassemble",
            "title": "Disassemble ARM64 memory",
            "description": "Read through KittyMemory and disassemble an ARM64 memory range with Capstone.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "size": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 64},
                    "max_instructions": {"type": "integer", "minimum": 1, "maximum": 256, "default": 16},
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "assembly_patch",
            "title": "Assemble and patch ARM64 code",
            "description": "Validate one or multiple newline-separated AArch64 instructions, then patch them contiguously downward from the executable address with KittyMemory. Validation failure writes nothing.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}, "instruction": {"type": "string", "maxLength": 16384}},
                "required": ["address", "instruction"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "decompiler_status",
            "title": "Get pseudocode engine status",
            "description": "Report nonblocking Ghidra worker readiness, busy state and exact initialization errors. Loading and analysis are isolated in a bounded child; loaded does not mean loaded in the Unity process.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "decompile_function",
            "title": "Decompile ARM64 function",
            "description": "Decompile a bounded readable/executable ARM64 function using a code snapshot and bounded referenced-data snapshots. Default metadata_mode=cached invokes no target IL2CPP APIs: reuses previously queried method names/proven primitive signatures, otherwise native inference. resolve_known explicitly enriches ONLY previously queried exact methods with class/field types using target runtime APIs; opt in only on compatible runtimes. Never scans all IL2CPP classes implicitly. Returns many-to-many source_map for synchronized code navigation. Native engine remains in-process; not a crash-isolated worker.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "Runtime function address."},
                    "size": {"type": "integer", "minimum": 4, "maximum": 65536, "multipleOf": 4, "default": 256},
                    "max_instructions": {"type": "integer", "minimum": 1, "maximum": 4096, "default": 256},
                    "max_output_bytes": {"type": "integer", "minimum": 256, "maximum": 1048576, "default": 262144},
                    "optimize": {"type": "boolean", "default": True},
                    "stop_at_return": {"type": "boolean", "default": True, "description": "Stop at the first linear RET when an exact function size is unavailable."},
                    "metadata_mode": {"type": "string", "enum": ["cached", "resolve_known"], "default": "cached"},
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "breakpoint_status",
            "title": "Get hardware breakpoint status",
            "description": "Report ARM64 perf hardware breakpoint support and lazy initialization state.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "breakpoint_set",
            "title": "Set hardware breakpoint/watchpoint",
            "description": "Set a non-stopping execution, read, write, or read/write ARM64 perf hardware breakpoint for all current and future target threads.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "type": {"type": "string", "enum": ["x", "r", "w", "rw"]},
                    "length": {"type": "integer", "enum": [1, 2, 4, 8], "default": 4},
                },
                "required": ["address", "type"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "breakpoint_list",
            "title": "List hardware breakpoints",
            "description": "List active hardware breakpoints and watchpoints.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "breakpoint_hits",
            "title": "Read breakpoint hits",
            "description": "Page captured breakpoint hits with thread id and ARM64 registers; omit address to include all breakpoints.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 512, "default": 100},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "breakpoint_clear",
            "title": "Clear hardware breakpoint",
            "description": "Clear a hardware breakpoint/watchpoint from every target thread.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}},
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "breakpoint_clear_all",
            "title": "Clear all hardware breakpoints",
            "description": "Clear every hardware breakpoint/watchpoint while leaving all other runtime features active.",
            "inputSchema": EMPTY_SCHEMA,
            "annotations": {"openWorldHint": False},
        },
    ]
)

_invoke_schema = next(tool["inputSchema"] for tool in TOOLS if tool["name"] == "il2cpp_invoke")
_invoke_schema["properties"]["arguments"]["items"]["anyOf"].extend(
    [
        {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "null", "bool", "i8", "u8", "i16", "u16", "i32", "u32",
                        "i64", "u64", "f32", "f64", "string", "address", "object", "enum",
                    ],
                },
                "value": {},
            },
            "required": ["type", "value"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"address": {"type": ["string", "integer"]}},
            "required": ["address"],
            "additionalProperties": False,
        },
    ]
)

TOOLS.extend(
    [
        {
            "name": "il2cpp_search",
            "title": "Fuzzy-search IL2CPP metadata",
            "description": "Search classes, methods, or fields across one or every IL2CPP image with filters and pagination, including validated stripped-runtime metadata fallback.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "enum": ["class", "method", "field"]},
                    "query": {"type": "string", "default": ""},
                    "image_filter": {"type": "string", "default": ""},
                    "namespace_filter": {"type": "string", "default": ""},
                    "class_filter": {"type": "string", "default": ""},
                    "match_mode": {"type": "string", "enum": ["contains", "prefix", "exact"], "default": "contains"},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
                },
                "required": ["entity"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_list_fields",
            "title": "List IL2CPP fields",
            "description": "List field types, offsets, flags, and static/literal state for an exact IL2CPP class. Stripped-runtime fallback returns validated reachable fields and reports when inherited traversal is unavailable.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": METHOD_LOOKUP_PROPERTIES["image"],
                    "namespace": METHOD_LOOKUP_PROPERTIES["namespace"],
                    "class_name": METHOD_LOOKUP_PROPERTIES["class_name"],
                    "name_filter": {"type": "string", "default": ""},
                    "include_inherited": {"type": "boolean", "default": False},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
                },
                "required": ["image", "namespace", "class_name"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_call",
            "title": "Call IL2CPP method with arguments",
            "description": "Alias of il2cpp_invoke with validated primitive, string, enum, null, and object-address arguments.",
            "inputSchema": _invoke_schema,
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "il2cpp_object_inspect",
            "title": "Inspect IL2CPP object fields",
            "description": "Load a live IL2CPP object by address and return its class plus a bounded page of field values.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "include_inherited": {"type": "boolean", "default": True},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 512, "default": 100},
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_list_items",
            "title": "Load IL2CPP List or array items",
            "description": "Read a bounded page from an IL2CPP List<T> or one-dimensional array through runtime metadata.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0, "maximum": 1000000, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 256, "default": 50},
                },
                "required": ["address"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "il2cpp_dictionary_get",
            "title": "Load IL2CPP Dictionary value",
            "description": "Invoke Dictionary<TKey,TValue>.get_Item for a typed key and return the value or managed exception.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "key": {},
                },
                "required": ["address", "key"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_resolve_address",
            "title": "Resolve module base plus offset",
            "description": "Resolve an ASLR-safe address from module name, duplicate occurrence, and signed offset.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 1024, "default": 1},
                    "offset": {"type": ["string", "integer"], "default": "0x0"},
                    "base_kind": {"type": "string", "enum": ["load_bias", "start"], "default": "load_bias"},
                },
                "required": ["module"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_resolve_pointer_chain",
            "title": "Resolve multi-level pointer chain",
            "description": "Resolve module base or an absolute base followed by bounded signed offsets and pointer dereferences.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"},
                    "occurrence": {"type": "integer", "minimum": 1, "maximum": 1024, "default": 1},
                    "base_address": {"type": ["string", "integer"]},
                    "base_offset": {"type": ["string", "integer"], "default": "0x0"},
                    "offsets": {"type": "array", "items": {"type": ["string", "integer"]}, "maxItems": 32, "default": []},
                    "pointer_size": {"type": "integer", "enum": [4, 8], "default": 8},
                    "dereference_final": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_read_pointer_chain",
            "title": "Read value through pointer chain",
            "description": "Resolve a pointer chain and read a typed value at its final address.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"}, "occurrence": {"type": "integer", "minimum": 1, "default": 1},
                    "base_address": {"type": ["string", "integer"]}, "base_offset": {"type": ["string", "integer"], "default": "0x0"},
                    "offsets": {"type": "array", "items": {"type": ["string", "integer"]}, "maxItems": 32, "default": []},
                    "pointer_size": {"type": "integer", "enum": [4, 8], "default": 8},
                    "value_type": MEMORY_VALUE_KIND_SCHEMA,
                },
                "required": ["value_type"], "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "memory_write_pointer_chain",
            "title": "Write value through pointer chain",
            "description": "Resolve a pointer chain and write a typed value at its final address.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"}, "occurrence": {"type": "integer", "minimum": 1, "default": 1},
                    "base_address": {"type": ["string", "integer"]}, "base_offset": {"type": ["string", "integer"], "default": "0x0"},
                    "offsets": {"type": "array", "items": {"type": ["string", "integer"]}, "maxItems": 32, "default": []},
                    "pointer_size": {"type": "integer", "enum": [4, 8], "default": 8},
                    "value_type": MEMORY_VALUE_KIND_SCHEMA, "value": {},
                },
                "required": ["value_type", "value"], "additionalProperties": False,
            },
            "annotations": {"openWorldHint": False},
        },
        {
            "name": "memory_scan_base",
            "title": "Scan for pointers to a base or address",
            "description": "Multi-thread pointer scan for module/base/absolute address. Existing exact one-level search is unchanged when max_depth/max_offset are omitted. Supply max_depth 1..15 or max_offset 0..1048576 to build revalidated module-relative chains (max_results <=1000). Chain mode scans aligned pointers and positive offsets, with explicit partial-results limits; omit start/end to scan selected regions. Use memory_chain_store/batch to save/load returned recipes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"}, "occurrence": {"type": "integer", "minimum": 1, "default": 1},
                    "target_address": {"type": ["string", "integer"]}, "target_offset": {"type": ["string", "integer"], "default": "0x0"},
                    "pointer_size": {"type": "integer", "enum": [4, 8], "default": 8},
                    "start": {"type": ["string", "integer"]}, "end": {"type": ["string", "integer"]},
                    "scan_module": {"type": "string"}, "scan_occurrence": {"type": "integer", "minimum": 1, "default": 1},
                    "memory_types": MEMORY_REGION_TYPES_SCHEMA,
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 1000},
                    "workers": {"type": "integer", "minimum": 0, "maximum": 32, "default": 0, "description": "Worker threads; 0 selects an automatic value of at least two."},
                    "max_depth": {"type": "integer", "minimum": 1, "maximum": 15},
                    "max_offset": {"type": ["string", "integer"], "description": "Maximum positive per-level offset, 0..1048576; enables chain mode."},
                    "budget_mb": {"type": "integer", "minimum": 16, "maximum": 4096, "description": "Chain scan total read budget in MiB (default 512); enables chain mode."},
                    "candidate_limit": {"type": "integer", "minimum": 1024, "maximum": 65536, "description": "Chain candidates per level (default 16384); partial results include stop_reasons."},
                    "time_ms": {"type": "integer", "minimum": 1000, "maximum": 30000, "description": "Chain scan time budget in ms (default 15000)."},
                },
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "breakpoint_backtrace",
            "title": "Read breakpoint-hit backtrace",
            "description": "Return one hardware breakpoint hit's frame-pointer backtrace, PC/SP/LR, hit count, thread and captured ARM64 X0-X30/SP/PC registers; resolve frame modules when enabled. Registers are a read-only hit-time snapshot from external root perf sampling, not a paused live thread or an editable register context.",
            "inputSchema": {
                "type": "object",
                "properties": {"hit_id": {"type": "integer", "minimum": 1}, "max_frames": {"type": "integer", "minimum": 1, "maximum": 64, "default": 32}},
                "required": ["hit_id"], "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
        {
            "name": "dobby_trace_backtrace",
            "title": "Read Dobby trace backtrace",
            "description": "Read the latest register/frame-pointer backtrace captured by a Dobby instrumentation trace.",
            "inputSchema": {
                "type": "object",
                "properties": {"address": {"type": "string"}, "max_frames": {"type": "integer", "minimum": 1, "maximum": 32, "default": 16}},
                "required": ["address"], "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        },
    ]
)


TOOLS.extend(render_tools.TOOLS)
TOOLS.extend(workspace_tools.TOOLS)
TOOLS.extend(debug_tools.TOOLS)
TOOLS.extend(logic_tools.TOOLS)
TOOLS.extend(relationship_tools.TOOLS)
TOOLS.extend(paused_debug_tools.TOOLS)
TOOLS.extend(task_tools.TOOLS)
TOOLS.extend(workflow_tools.TOOLS)
TOOLS.extend(native_library_tools.TOOLS)
_existing_names = {t["name"] for t in TOOLS}
TOOLS.extend(t for t in analysis_tools.TOOLS if t["name"] not in _existing_names)
TOOLS = [analysis_tools.BY_NAME.get(t["name"], t) for t in TOOLS]
TOOL_BY_NAME = {tool["name"].lower(): tool for tool in TOOLS}


def tool_features(name: str) -> tuple[str, ...]:
    if name in analysis_tools.BY_NAME:
        return analysis_tools.features(name)
    if name in native_library_tools.BY_NAME:
        return ("native_libraries",)
    if name in workflow_tools.BY_NAME:
        return workflow_tools.features(name)
    if name in task_tools.BY_NAME:
        return task_tools.features(name)
    if name in paused_debug_tools.BY_NAME:
        return paused_debug_tools.features(name)
    if name in relationship_tools.BY_NAME:
        return relationship_tools.features(name)
    if name in logic_tools.BY_NAME:
        return logic_tools.features(name)
    if name in debug_tools.BY_NAME:
        return debug_tools.features(name)
    if name in workspace_tools.BY_NAME:
        return workspace_tools.features(name)
    if name in render_tools.BY_NAME:
        return render_tools.features(name)
    if name in {"ping", "connection_info", "configure_connection", "process_list", "process_select", "process_current"}:
        return ("connection",)
    if name.startswith("mcp_toast_") or name in {
        "get_clipboard", "show_input_box", "wait_input", "input_and_wait", "push_input_result"
    }:
        return ("ui",)
    if name in {"il2cpp_invoke", "il2cpp_call"}:
        return ("il2cpp_metadata", "il2cpp_invoke")
    if name in {"il2cpp_list_items", "il2cpp_dictionary_get"}:
        return ("il2cpp_metadata", "il2cpp_invoke", "il2cpp_objects")
    if name == "il2cpp_object_inspect":
        return ("il2cpp_metadata", "il2cpp_objects")
    if name.startswith("il2cpp_hook") or name == "il2cpp_unhook":
        return ("il2cpp_metadata", "il2cpp_hook")
    if name.startswith("il2cpp_"):
        return ("il2cpp_metadata",)
    if name in {"memory_read", "memory_read_value"}:
        return ("memory_read",)
    if name in {"memory_write", "memory_write_value"}:
        return ("memory_write",)
    if name in {"memory_resolve_pointer_chain", "memory_read_pointer_chain"}:
        return ("memory_maps", "memory_read", "pointer_chain")
    if name == "memory_write_pointer_chain":
        return ("memory_maps", "memory_read", "memory_write", "pointer_chain")
    if name == "memory_scan_base":
        return ("memory_maps", "memory_search", "pointer_chain")
    if name.startswith("memory_search") or name.startswith("memory_filter"):
        return ("memory_search",)
    if name.startswith("memory_"):
        return ("memory_maps",)
    if name in {"dobby_instrument", "dobby_trace_get", "dobby_trace_backtrace"}:
        return ("dobby", "trace")
    if name.startswith("dobby_"):
        return ("dobby",)
    if name.startswith("lua_"):
        return ("lua",)
    if name.startswith("assembly_"):
        return ("assembly",)
    if name == "decompiler_status" or name == "decompile_function":
        return ("decompiler",)
    if name.startswith("breakpoint_"):
        return ("breakpoint",)
    if name in {"debug_help", "runtime_capabilities", "raw_hook_call"}:
        return ("diagnostics",)
    return ("connection",)


def raw_command_features(native_name: str) -> tuple[str, ...]:
    """Conservative feature classification for the raw native escape hatch."""
    native_name = native_name.upper()
    if native_name == "ANALYSIS_QUERY":
        return ("assembly", "memory_read")
    if native_name == "CRASH_DIAGNOSE":
        return ("diagnostics",)
    if native_name == "NATIVE_LIBRARY_CONTROL":
        return ("native_libraries",)
    evidence = {
        "MEMORY_STRING_SEARCH": ("memory_search", "memory_read", "il2cpp_metadata", "il2cpp_objects"),
        "MEMORY_STRING_XREFS": ("assembly", "memory_read", "il2cpp_metadata", "il2cpp_objects"),
        "MEMORY_STRING_WRITE": ("memory_read", "memory_write", "il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke"),
        "IL2CPP_STRING_SET_SAFE": ("memory_read", "memory_write", "il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke"),
        "IL2CPP_FIELD_ACTIVITY": ("il2cpp_metadata", "il2cpp_objects", "memory_read", "assembly", "breakpoint"),
        "IL2CPP_ADDRESS_RESOLVE": ("il2cpp_metadata", "il2cpp_objects", "memory_read"),
        "MEMORY_ADDRESS_RELATIONS": ("il2cpp_metadata", "il2cpp_objects", "pointer_chain", "memory_read"),
    }
    if native_name in evidence:
        return evidence[native_name]
    # Exact extension protocols must run before workspace_tools' generic
    # WORKSPACE_* fallback.
    exact = (logic_tools.native_features(native_name) or debug_tools.native_features(native_name) or
             relationship_tools.native_features(native_name) or paused_debug_tools.native_features(native_name) or
             task_tools.native_features(native_name) or workflow_tools.native_features(native_name) or
             workspace_tools.native_features(native_name))
    if exact is not None:
        return exact
    if native_name.startswith(("RENDER_", "OVERLAY_")):
        return render_tools.native_features(native_name)
    if native_name.startswith("IL2CPP_"):
        if native_name in {"IL2CPP_HOOK", "IL2CPP_HOOK_RETURN", "IL2CPP_UNHOOK"}:
            return ("il2cpp_metadata", "il2cpp_hook")
        if native_name == "IL2CPP_INVOKE":
            return ("il2cpp_metadata", "il2cpp_invoke")
        if native_name == "IL2CPP_OBJECT_INSPECT":
            return ("il2cpp_metadata", "il2cpp_objects")
        if native_name in {"IL2CPP_LIST_ITEMS", "IL2CPP_DICTIONARY_GET"}:
            return ("il2cpp_metadata", "il2cpp_invoke", "il2cpp_objects")
        return ("il2cpp_metadata",)
    if native_name.startswith("MEMORY_"):
        if native_name == "MEMORY_READ":
            return ("memory_read",)
        if native_name == "MEMORY_WRITE":
            return ("memory_write",)
        if native_name == "MEMORY_POINTER_SCAN_MT":
            return ("memory_maps", "memory_search", "pointer_chain")
        if native_name.startswith("MEMORY_SEARCH") or native_name == "MEMORY_FILTER":
            return ("memory_search",)
        return ("memory_maps",)
    if native_name.startswith("DOBBY_"):
        return ("dobby", "trace") if "TRACE" in native_name or native_name == "DOBBY_INSTRUMENT" else ("dobby",)
    if native_name.startswith("BREAKPOINT_"):
        return ("breakpoint",)
    if native_name.startswith("LUA_"):
        return ("lua",)
    if native_name.startswith("ASM_"):
        if native_name == "ASM_PATCH":
            return ("assembly", "memory_write")
        return ("assembly", "memory_read") if native_name == "ASM_DISASSEMBLE" else ("assembly",)
    if native_name.startswith("DECOMP_"):
        return ("decompiler", "memory_read") if native_name == "DECOMP_DECOMPILE" else ("decompiler",)
    return ("diagnostics",)


def tools_for_registry(registry: FeatureRegistry) -> list[dict[str, Any]]:
    return [
        tool for tool in TOOLS
        if all(registry.enabled(feature) for feature in tool_features(tool["name"]))
    ]


class McpAdminServer:
    def __init__(self, registry: FeatureRegistry, host: str, port: int, token: str | None = None):
        if not 1 <= port <= 65535:
            raise BridgeError("admin port must be between 1 and 65535")
        if host not in {"127.0.0.1", "localhost", "::1"} and not token:
            raise BridgeError("a non-loopback MCP admin host requires --admin-token")
        self.registry = registry
        self.host = host
        self.port = port
        self.token = token or ""
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        display_host = "127.0.0.1" if self.host in {"localhost", "::1"} else self.host
        return f"http://{display_host}:{self.port}/"

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ZygiskMcpAdmin/1.0"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _authorized(self) -> bool:
                if not owner.token:
                    return True
                authorization = self.headers.get("Authorization", "")
                return authorization == f"Bearer {owner.token}" or self.headers.get("X-MCP-Token") == owner.token

            def _send_json(self, status: int, payload: Any) -> None:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read_json(self) -> dict[str, Any]:
                if self.headers.get_content_type() != "application/json":
                    raise BridgeError("Content-Type must be application/json")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise BridgeError("invalid Content-Length") from exc
                if length < 0 or length > 64 * 1024:
                    raise BridgeError("request body is too large")
                try:
                    value = json.loads(self.rfile.read(length) or b"{}")
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise BridgeError("invalid JSON request") from exc
                if not isinstance(value, dict):
                    raise BridgeError("JSON request must be an object")
                return value

            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/features":
                    if not self._authorized():
                        self._send_json(401, {"error": "unauthorized"})
                        return
                    self._send_json(200, owner.registry.snapshot())
                    return
                if path != "/":
                    self._send_json(404, {"error": "not found"})
                    return
                page = owner._html_page().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def do_POST(self) -> None:
                if not self._authorized():
                    self._send_json(401, {"error": "unauthorized"})
                    return
                path = urlparse(self.path).path
                try:
                    payload = self._read_json()
                    if path == "/api/features/all":
                        result = owner.registry.set_all(payload.get("enabled"))
                    elif path.startswith("/api/features/"):
                        name = path[len("/api/features/"):]
                        result = owner.registry.set(name, payload.get("enabled"))
                    else:
                        self._send_json(404, {"error": "not found"})
                        return
                except (BridgeError, OSError) as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, result)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="mcp-admin", daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def _html_page(self) -> str:
        token_required = "true" if self.token else "false"
        page = """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>MCP 功能控制</title><style>
body{{margin:0;background:#0b1020;color:#e8ecf5;font:15px system-ui,sans-serif}}main{{max-width:920px;margin:auto;padding:32px 18px}}h1{{margin:0 0 8px}}p{{color:#aeb8ca}}.bar{{display:flex;gap:10px;margin:22px 0}}button{{border:0;border-radius:9px;padding:10px 14px;cursor:pointer}}.on{{background:#36d399}}.off{{background:#fb7185}}#list{{display:grid;gap:10px}}.row{{display:flex;align-items:center;justify-content:space-between;gap:16px;background:#151d33;padding:14px;border-radius:12px}}.name{{font-weight:650}}.desc{{font-size:13px;color:#9ca8bd;margin-top:4px}}input{{width:46px;height:24px;accent-color:#36d399}}#status{{min-height:20px;color:#7dd3fc}}</style></head><body><main><h1>MCP 功能控制</h1><p>全部功能默认开启。关闭后该功能会从 Agent 的 tools/list 中移除，并在调用端再次拦截。</p><div class=\"bar\"><button class=\"on\" onclick=\"setAll(true)\">全部开启</button><button class=\"off\" onclick=\"setAll(false)\">全部关闭</button></div><div id=\"status\"></div><div id=\"list\"></div></main><script>
const tokenRequired=__TOKEN_REQUIRED__;let token=localStorage.getItem('mcpAdminToken')||'';if(tokenRequired&&!token){{token=prompt('请输入 MCP 管理令牌')||'';localStorage.setItem('mcpAdminToken',token)}}
const headers=()=>Object.assign({{'Content-Type':'application/json'}},token?{{'Authorization':'Bearer '+token}}:{{}});
async function load(){{const r=await fetch('/api/features',{{headers:headers()}});const d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);list.innerHTML=d.features.map(f=>`<div class=\"row\"><div><div class=\"name\">${{escapeHtml(f.name)}}</div><div class=\"desc\">${{escapeHtml(f.description)}}</div></div><input type=\"checkbox\" ${{f.enabled?'checked':''}} onchange=\"setOne('${{f.name}}',this.checked)\"></div>`).join('');status.textContent='已加载 '+d.features.length+' 个功能开关'}
async function send(path,enabled){{const r=await fetch(path,{{method:'POST',headers:headers(),body:JSON.stringify({{enabled}})}});const d=await r.json();if(!r.ok)throw Error(d.error||r.statusText);await load()}}
function setOne(name,enabled){{send('/api/features/'+encodeURIComponent(name),enabled).catch(showError)}}function setAll(enabled){{send('/api/features/all',enabled).catch(showError)}}function showError(e){{status.textContent='错误：'+e.message}}function escapeHtml(v){{const d=document.createElement('div');d.textContent=v;return d.innerHTML}}load().catch(showError)
</script></body></html>"""
        return page.replace("__TOKEN_REQUIRED__", token_required).replace("{{", "{").replace("}}", "}")


class ToolDispatcher:
    def __init__(
        self,
        config: ConnectionConfig,
        registry: FeatureRegistry | None = None,
    ):
        config.validate()
        self.config = config
        self.registry = registry or FeatureRegistry()
        self._lock = threading.RLock()
        self._call_context = threading.local()
        self._input_lock = threading.RLock()

    def _client(self) -> HookSocketClient:
        config = getattr(self._call_context, "config", self.config)
        return HookSocketClient(config, route_process=True,
                                on_target=lambda target: self._remember_target(config, target))

    def _remember_target(self, config: ConnectionConfig, target: dict[str, Any]) -> None:
        with self._lock:
            # Preserve the snapshot for multi-command tools, even if another
            # request selects a different process while this operation runs.
            config.target_session = target["session"]
            config.target_process = dict(target)
            if (self.config.target_session is None and
                    (self.config.host, self.config.port, self.config.adb_serial) ==
                    (config.host, config.port, config.adb_serial)):
                self.config = replace(self.config, target_session=target["session"], target_process=dict(target))

    def _info(self, config: ConnectionConfig | None = None) -> dict[str, Any]:
        config = config or getattr(self._call_context, "config", self.config)
        return {
            "host": config.host,
            "port": config.port,
            "timeout": config.timeout,
            "auto_adb_forward": config.auto_adb_forward,
            "adb_serial": config.adb_serial,
            "target_process": config.target_process,
        }

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "ping": self.ping,
            "connection_info": self.connection_info,
            "configure_connection": self.configure_connection,
            "process_list": self.process_list,
            "process_select": self.process_select,
            "process_current": self.process_current,
            "get_clipboard": self.get_clipboard,
            "show_input_box": self.show_input_box,
            "wait_input": self.wait_input,
            "input_and_wait": self.input_and_wait,
            "push_input_result": self.push_input_result,
            "raw_hook_call": self.raw_hook_call,
            "mcp_toast_status": self.mcp_toast_status,
            "mcp_toast_set_enabled": self.mcp_toast_set_enabled,
            "mcp_toast_show": self.mcp_toast_show,
            "il2cpp_status": self.il2cpp_status,
            "il2cpp_dump_file": self.il2cpp_dump_file,
            "il2cpp_list_images": self.il2cpp_list_images,
            "il2cpp_unity_catalog": self.il2cpp_unity_catalog,
            "il2cpp_list_classes": self.il2cpp_list_classes,
            "il2cpp_list_methods": self.il2cpp_list_methods,
            "il2cpp_list_fields": self.il2cpp_list_fields,
            "il2cpp_search": self.il2cpp_search,
            "il2cpp_find_method": self.il2cpp_find_method,
            "il2cpp_invoke": self.il2cpp_invoke,
            "il2cpp_call": self.il2cpp_invoke,
            "il2cpp_object_inspect": self.il2cpp_object_inspect,
            "il2cpp_list_items": self.il2cpp_list_items,
            "il2cpp_dictionary_get": self.il2cpp_dictionary_get,
            "il2cpp_hook": self.il2cpp_hook,
            "il2cpp_hook_return": self.il2cpp_hook_return,
            "il2cpp_unhook": self.il2cpp_unhook,
            "memory_backend_status": self.memory_backend_status,
            "memory_read": self.memory_read,
            "memory_write": self.memory_write,
            "memory_read_value": self.memory_read_value,
            "memory_write_value": self.memory_write_value,
            "memory_list_modules": self.memory_list_modules,
            "memory_find_module": self.memory_find_module,
            "memory_address_info": self.memory_address_info,
            "memory_resolve_address": self.memory_resolve_address,
            "memory_resolve_pointer_chain": self.memory_resolve_pointer_chain,
            "memory_read_pointer_chain": self.memory_read_pointer_chain,
            "memory_write_pointer_chain": self.memory_write_pointer_chain,
            "memory_scan_base": self.memory_scan_base,
            "memory_search": self.memory_search,
            "memory_search_value": self.memory_search_value,
            "memory_search_exact": self.memory_search_exact,
            "memory_search_fuzzy": self.memory_search_fuzzy,
            "memory_filter": self.memory_filter,
            "memory_filter_value": self.memory_filter_value,
            "memory_search_results": self.memory_search_results,
            "memory_search_clear": self.memory_search_clear,
            "dobby_version": self.dobby_version,
            "dobby_resolve_symbol": self.dobby_resolve_symbol,
            "dobby_hook": self.dobby_hook,
            "dobby_hook_return": self.dobby_hook_return,
            "dobby_instrument": self.dobby_instrument,
            "dobby_trace_get": self.dobby_trace_get,
            "dobby_trace_backtrace": self.dobby_trace_backtrace,
            "dobby_patch_code": self.dobby_patch_code,
            "dobby_destroy": self.dobby_destroy,
            "dobby_list_hooks": self.dobby_list_hooks,
            "debug_help": self.debug_help,
            "runtime_capabilities": self.runtime_capabilities,
            "lua_status": self.lua_status,
            "lua_execute": self.lua_execute,
            "lua_reset": self.lua_reset,
            "lua_logs": self.lua_logs,
            "assembly_status": self.assembly_status,
            "assembly_assemble": self.assembly_assemble,
            "assembly_disassemble": self.assembly_disassemble,
            "assembly_patch": self.assembly_patch,
            "decompiler_status": self.decompiler_status,
            "decompile_function": self.decompile_function,
            "breakpoint_status": self.breakpoint_status,
            "breakpoint_set": self.breakpoint_set,
            "breakpoint_list": self.breakpoint_list,
            "breakpoint_hits": self.breakpoint_hits,
            "breakpoint_backtrace": self.breakpoint_backtrace,
            "breakpoint_clear": self.breakpoint_clear,
            "breakpoint_clear_all": self.breakpoint_clear_all,
        }
        method = methods.get(name)
        if name in render_tools.BY_NAME:
            method = lambda args: self._render_call(name, args)
        if name in debug_tools.BY_NAME:
            method = lambda args: self._debug_call(name, args)
        if name in workspace_tools.BY_NAME:
            method = lambda args: self._workspace_call(name, args)
        if name in logic_tools.BY_NAME:
            method = lambda args: self._logic_call(name, args)
        if name in relationship_tools.BY_NAME:
            method = lambda args: self._relationship_call(name, args)
        if name in paused_debug_tools.BY_NAME:
            method = lambda args: self._paused_debug_call(name, args)
        if name in task_tools.BY_NAME:
            method = lambda args: self._task_call(name, args)
        if name in workflow_tools.BY_NAME:
            method = lambda args: self._workflow_call(name, args)
        if name in native_library_tools.BY_NAME:
            method = lambda args: self._native_library_call(name, args)
        if name in analysis_tools.BY_NAME and name not in paused_debug_tools.BY_NAME and name not in {"memory_read", "memory_write"}:
            method = lambda args: self._analysis_call(name, args)
        if method is None:
            raise BridgeError(f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise BridgeError("tool arguments must be an object")
        self.registry.require(name)
        # Native commands already serialize their own runtime mutations. Do not
        # hold a dispatcher-wide lock across WAIT_INPUT: its producer must run.
        previous = getattr(self._call_context, "config", None)
        with self._lock:
            self._call_context.config = replace(self.config)
        try:
            self._notify_mcp_call(name, arguments)
            result = method(arguments)
            target = self._call_context.config.target_process
            if target and name not in {"process_list", "configure_connection", "connection_info"}:
                result.setdefault("target_process", dict(target))
            return result
        finally:
            if previous is None:
                del self._call_context.config
            else:
                self._call_context.config = previous

    def _analysis_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = analysis_tools.encode(name, arguments)
            required = analysis_tools.extra_features(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        disabled = [f for f in required if not self.registry.enabled(f)]
        if disabled:
            raise BridgeError("analysis depends on disabled MCP features: " + ", ".join(disabled))
        return self._json_call(command, timeout=max(self.config.timeout, 30.0))

    def _native_library_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return native_library_tools.execute(name, arguments, self._json_call)
        except (ValueError, OSError) as exc:
            raise BridgeError(str(exc)) from exc

    def _logic_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = logic_tools.encode(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        return self._json_call(command)

    def _relationship_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = relationship_tools.encode(name, arguments)
            required = relationship_tools.extra_features(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        disabled = sorted(feature for feature in required if not self.registry.enabled(feature))
        if disabled:
            raise BridgeError("relationship operation depends on disabled MCP features: " + ", ".join(disabled))
        return self._json_call(command, timeout=max(self.config.timeout, 15.0))

    def _paused_debug_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = paused_debug_tools.encode(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        return self._json_call(command, timeout=max(self.config.timeout, 30.0))

    def _task_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = task_tools.encode(name, arguments)
            required = task_tools.extra_features(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        disabled = sorted(feature for feature in required if not self.registry.enabled(feature))
        if disabled:
            raise BridgeError("background task depends on disabled MCP features: " + ", ".join(disabled))
        return self._json_call(command)

    def _workflow_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = workflow_tools.encode(name, arguments)
            required = workflow_tools.extra_features(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        disabled = sorted(feature for feature in required if not self.registry.enabled(feature))
        if disabled:
            raise BridgeError("workflow operation depends on disabled MCP features: " + ", ".join(disabled))
        # Root journal I/O has a 30-second bounded transport timeout; include
        # room for command intent/outcome persistence around explicit exports.
        minimum_timeout = 60.0 if name in {"journal_status", "journal_query", "journal_export", "diagnostic_export"} else 30.0
        return self._json_call(command, timeout=max(self.config.timeout, minimum_timeout))

    def _debug_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = debug_tools.encode(name, arguments)
        except (ValueError, OverflowError) as exc:
            raise BridgeError(str(exc)) from exc
        if name in {"memory_search_tabs", "memory_batch_edit"}:
            return self._json_call(command, timeout=max(self.config.timeout, 75.0))
        return self._json_call(command)

    def _render_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = render_tools.encode(name, arguments)
        except (ValueError, OverflowError) as exc:
            raise BridgeError(str(exc)) from exc
        return self._json_call(command)

    def _workspace_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            command = workspace_tools.encode(name, arguments, self._invoke_token)
            required = workspace_tools.extra_features(name, arguments)
        except (ValueError, OverflowError, RecursionError) as exc:
            raise BridgeError(str(exc)) from exc
        # Starting an existing program must respect its saved game-action grant,
        # including a descriptor patch that omits the grant field.
        if name == "overlay_program_control" and arguments["operation"] != "stop":
            old = self._json_call("UI_PROGRAM_GET " + self._hex_text(arguments["id"]))
            if old.get("allow_game_actions"):
                required.update(workspace_tools.GAME_WRITE_FEATURES)
        elif name == "overlay_program_set":
            descriptor = arguments["descriptor"]
            if "allow_game_actions" not in descriptor:
                definitions = self._json_call("UI_PROGRAM_LIST")
                if any(p.get("id") == descriptor["id"] and p.get("allow_game_actions") for p in definitions.get("programs", [])):
                    required.update(workspace_tools.GAME_WRITE_FEATURES)
        disabled = sorted(feature for feature in required if not self.registry.enabled(feature))
        if disabled:
            raise BridgeError("operation depends on disabled MCP features: " + ", ".join(disabled))
        return self._json_call(command)

    def _notify_mcp_call(self, name: str, arguments: dict[str, Any]) -> None:
        if name in {"connection_info", "configure_connection", "process_list", "process_select", "process_current", "mcp_toast_set_enabled", "mcp_toast_show"}:
            return
        message = json.dumps(
            {"tool": name, "arguments": arguments},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(message) > 1000:
            message = message[:997] + "..."
        try:
            self._client().call(f"UI_TOAST_NOTIFY {self._hex_text(message)}")
        except BridgeError:
            # Toast reporting must never prevent the requested MCP operation.
            pass

    def ping(self, _: dict[str, Any]) -> dict[str, Any]:
        response = self._client().call("PING")
        if response != "PONG":
            raise BridgeError(f"unexpected ping response: {response!r}")
        return {"connected": True, **self._info(), "response": response}

    def connection_info(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._info()

    def process_list(self, _: dict[str, Any]) -> dict[str, Any]:
        # Discovery remains available when a previously selected process died.
        response = self._client().call("MCP_TARGETS")
        try:
            result = json.loads(response)
        except (ValueError, TypeError) as exc:
            raise BridgeError("invalid process registry response; module 2.6.2+ required") from exc
        if not isinstance(result, dict) or result.get("protocol") != 1 or not isinstance(result.get("processes"), list):
            raise BridgeError("invalid process registry response")
        for target in result["processes"]:
            if (not isinstance(target, dict) or type(target.get("pid")) is not int or target["pid"] <= 1
                    or not isinstance(target.get("process_name"), str) or not target["process_name"]
                    or not isinstance(target.get("session"), str) or not target["session"]
                    or len(target["session"]) > 160
                    or any(c not in "0123456789abcdef-" for c in target["session"])):
                raise BridgeError("invalid process registry entry")
        return result

    def process_select(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args or set(args) - {"pid", "process_name"}:
            raise BridgeError("provide pid and/or exact process_name")
        if "pid" in args and (type(args["pid"]) is not int or not 2 <= args["pid"] <= 2147483647):
            raise BridgeError("pid must be an integer between 2 and 2147483647")
        if "process_name" in args and (not isinstance(args["process_name"], str) or not 1 <= len(args["process_name"]) <= 1024):
            raise BridgeError("process_name must be a non-empty exact name")
        with self._lock:
            rows = self.process_list({})["processes"]
            matches = [row for row in rows if all(row[key] == value for key, value in args.items())]
            if len(matches) != 1:
                raise BridgeError("process selection is missing or ambiguous; call process_list and select an exact PID")
            selected = matches[0]
            candidate = replace(self.config, target_session=selected["session"], target_process=dict(selected))
            if HookSocketClient(candidate, route_process=True).call("PING") != "PONG":
                raise BridgeError("selected process did not answer PING; selection unchanged")
            self.config = candidate
            if hasattr(self._call_context, "config"):
                self._call_context.config = replace(candidate)
            return {"selected": True, "target_process": dict(selected),
                    "notice": "Discard addresses and handles from the previous process; its hooks/freezes remain unchanged."}

    def process_current(self, _: dict[str, Any]) -> dict[str, Any]:
        result = self._json_call("MCP_PROCESS_INFO")
        target = getattr(self._call_context, "config", self.config).target_process
        if target:
            result.update(target)  # Gateway boot-qualified session is authoritative.
        return result

    def configure_connection(self, args: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._configure_connection_locked(args)

    def _configure_connection_locked(self, args: dict[str, Any]) -> dict[str, Any]:
        candidate = replace(self.config)
        if "host" in args:
            if not isinstance(args["host"], str):
                raise BridgeError("host must be a string")
            candidate.host = args["host"]
        if "port" in args:
            if isinstance(args["port"], bool):
                raise BridgeError("port must be an integer")
            candidate.port = int(args["port"])
        if "timeout" in args:
            if isinstance(args["timeout"], bool):
                raise BridgeError("timeout must be a number")
            candidate.timeout = float(args["timeout"])
        if "auto_adb_forward" in args:
            if not isinstance(args["auto_adb_forward"], bool):
                raise BridgeError("auto_adb_forward must be a boolean")
            candidate.auto_adb_forward = args["auto_adb_forward"]
        if "adb_serial" in args:
            serial = args["adb_serial"]
            if serial is not None and not isinstance(serial, str):
                raise BridgeError("adb_serial must be a string or null")
            candidate.adb_serial = None if serial is None or serial.strip() == "" else serial
        candidate.validate()
        if (candidate.host, candidate.port, candidate.adb_serial) != (self.config.host, self.config.port, self.config.adb_serial):
            candidate.target_session = None
            candidate.target_process = None
        self.config = candidate
        return self._info(candidate)

    def get_clipboard(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"text": self._client().call("GET_CLIPBOARD")}

    def mcp_toast_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("UI_TOAST_STATUS")

    def mcp_toast_set_enabled(self, args: dict[str, Any]) -> dict[str, Any]:
        enabled = args.get("enabled")
        if not isinstance(enabled, bool):
            raise BridgeError("enabled must be a boolean")
        return self._json_call(f"UI_TOAST_SET {'true' if enabled else 'false'}")

    def mcp_toast_show(self, args: dict[str, Any]) -> dict[str, Any]:
        message = self._required_text(args, "message")
        if not message:
            raise BridgeError("message cannot be empty")
        if len(message) > 1000:
            raise BridgeError("message cannot exceed 1000 characters")
        return self._json_call(f"UI_TOAST_SHOW {self._hex_text(message)}")

    @staticmethod
    def _input_args(args: dict[str, Any]) -> tuple[str, str, str]:
        title = _single_line(args.get("title", "输入"), "title", allow_pipe=False)
        hint = _single_line(args.get("hint", "请输入内容"), "hint", allow_pipe=False)
        prefill = _single_line(args.get("prefill", ""), "prefill", allow_pipe=False)
        return title, hint, prefill

    def show_input_box(self, args: dict[str, Any]) -> dict[str, Any]:
        title, hint, prefill = self._input_args(args)
        with self._input_session():
            response = self._client().call(f"SHOW_INPUT_BOX {title}|{hint}|{prefill}")
        return {"shown": response == "INPUT_BOX_SHOWN", "response": response}

    @contextmanager
    def _input_session(self):
        if not self._input_lock.acquire(blocking=False):
            raise BridgeError("INPUT_REQUEST_BUSY: another input request is waiting; use push_input_result to complete it")
        try:
            yield
        finally:
            self._input_lock.release()

    def _input_timeout(self, args: dict[str, Any]) -> int:
        return self._bounded_integer(args.get("timeout_ms", 120000), "timeout_ms", 100, 300000)

    def wait_input(self, args: dict[str, Any]) -> dict[str, Any]:
        timeout_ms = self._input_timeout(args)
        with self._input_session():
            text = self._client().call(f"WAIT_INPUT {timeout_ms}", timeout=timeout_ms / 1000 + 5)
        return {"text": text}

    def input_and_wait(self, args: dict[str, Any]) -> dict[str, Any]:
        self._input_timeout(args)  # Invalid waits must not open/reset the input box.
        with self._input_session():
            shown = self.show_input_box(args)
            if not shown["shown"]:
                raise BridgeError(f"input box did not open: {shown['response']}")
            return self.wait_input(args)

    def push_input_result(self, args: dict[str, Any]) -> dict[str, Any]:
        text = _single_line(args.get("text", ""), "text")
        response = self._client().call(f"INPUT_RESULT {text}")
        return {"accepted": response == "OK", "response": response}

    def raw_hook_call(self, args: dict[str, Any]) -> dict[str, Any]:
        command = _single_line(args.get("command", ""), "command").strip()
        if not command:
            raise BridgeError("command is required")
        native_name = command.split(None, 1)[0].upper()
        if native_name in {"MCP_QUERY_V1", "MCP_ROUTE", "MCP_TARGETS", "MCP_TARGET_INFO"}:
            raise BridgeError(f"{native_name} is an internal envelope; use process_list/process_select or the original native command")
        raw_features = raw_command_features(native_name)
        # Opt-in metadata enrichment must not bypass its feature gate via raw calls.
        if native_name == "DECOMP_DECOMPILE" and command.split()[-1] == "resolve_known":
            raw_features += ("il2cpp_metadata",)
        disabled = [feature for feature in raw_features if not self.registry.enabled(feature)]
        if disabled:
            raise BridgeError(
                f"raw command {native_name} is disabled by MCP feature: {', '.join(disabled)}"
            )
        timeout = float(args.get("timeout", self.config.timeout))
        if not 0.1 <= timeout <= 300:
            raise BridgeError("timeout must be between 0.1 and 300 seconds")
        return {"response": self._client().call(command, timeout=timeout)}

    @staticmethod
    def _hex_text(value: Any) -> str:
        encoded = str(value).encode("utf-8").hex()
        return encoded or "-"

    @staticmethod
    def _required_text(args: dict[str, Any], name: str) -> str:
        if name not in args:
            raise BridgeError(f"missing required argument: {name}")
        if not isinstance(args[name], str):
            raise BridgeError(f"{name} must be a string")
        return _single_line(args[name], name)

    @staticmethod
    def _address(value: Any, name: str) -> str:
        text = _single_line(value, name).strip()
        try:
            parsed = int(text, 0)
        except ValueError as exc:
            raise BridgeError(f"{name} must be a hexadecimal or decimal address") from exc
        if parsed < 0 or parsed > (1 << 64) - 1:
            raise BridgeError(f"{name} is out of range")
        return hex(parsed)

    def _json_call(self, command: str, *, timeout: float | None = None) -> dict[str, Any]:
        response = self._client().call(command, timeout=timeout)
        try:
            value = json.loads(response)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"native bridge returned invalid JSON for {command.split()[0]}: {response!r}") from exc
        # These two legacy native endpoints deliberately return arrays, also
        # consumed directly by ImGui. Adapt only their documented list results
        # to MCP's object-shaped structuredContent; keep the native wire stable.
        words = command.split()
        if isinstance(value, list) and words[0] == "MEMORY_CHAIN_BATCH":
            if not all(isinstance(item, dict) and isinstance(item.get("success"), bool) for item in value):
                raise BridgeError("MEMORY_CHAIN_BATCH returned invalid result items")
            succeeded = sum(item["success"] for item in value)
            return {"results": value, "total": len(value), "succeeded": succeeded, "failed": len(value) - succeeded}
        if isinstance(value, list) and words[0] == "MEMORY_CHAIN_STORE" and len(words) == 2:
            try:
                selection = json.loads(bytes.fromhex(words[1]))
            except (ValueError, UnicodeDecodeError):
                selection = None
            if isinstance(selection, dict) and selection.get("operation") == "list":
                if not all(isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("recipe"), dict) for item in value):
                    raise BridgeError("MEMORY_CHAIN_STORE returned invalid saved-chain items")
                return {"chains": value, "total": len(value)}
        if not isinstance(value, dict):
            raise BridgeError("native bridge returned a non-object JSON result")
        return value

    def _lookup_words(self, args: dict[str, Any]) -> list[str]:
        return [
            self._hex_text(self._required_text(args, "image")),
            self._hex_text(self._required_text(args, "namespace")),
            self._hex_text(self._required_text(args, "class_name")),
            self._hex_text(self._required_text(args, "method")),
            str(int(args.get("parameter_count", -1))),
        ]

    @staticmethod
    def _return_value(args: dict[str, Any]) -> str:
        kind = str(args.get("return_kind", ""))
        value = args.get("value")
        if kind == "void":
            return "0"
        if kind == "bool":
            if isinstance(value, bool):
                return "true" if value else "false"
            text = str(value).lower()
            if text in {"true", "1"}:
                return "true"
            if text in {"false", "0"}:
                return "false"
            raise BridgeError("bool hook value must be true or false")
        if value is None:
            raise BridgeError(f"value is required for return kind {kind}")
        return _single_line(value, "value")

    @classmethod
    def _invoke_token(cls, value: Any) -> str:
        if isinstance(value, dict):
            if set(value) == {"fields"}:
                if not isinstance(value["fields"], dict):
                    raise BridgeError("struct fields must be an object")
                workspace_tools._bounded_json(value)
                encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                if len(encoded.encode("utf-8")) > 4096:
                    raise BridgeError("struct argument exceeds 4096 UTF-8 bytes")
                return "j" + cls._hex_text(encoded)
            if set(value) == {"enum"}:
                value = value["enum"]
                if isinstance(value, bool) or not isinstance(value, (str, int)):
                    raise BridgeError("enum argument must be a member name or integer value")
            elif set(value) == {"address"}:
                value = cls._address(value["address"], "argument address")
            elif set(value) == {"type", "value"}:
                kind = value["type"]
                typed_value = value["value"]
                if kind == "null":
                    if typed_value is not None:
                        raise BridgeError("a null argument value must be null")
                    return "z"
                if kind == "bool":
                    if not isinstance(typed_value, bool):
                        raise BridgeError("a bool argument value must be true or false")
                    return "b1" if typed_value else "b0"
                if kind in {"address", "object"}:
                    value = cls._address(typed_value, "argument address")
                elif kind == "string":
                    if not isinstance(typed_value, str):
                        raise BridgeError("a string argument value must be a string")
                    value = typed_value
                elif kind == "enum":
                    if isinstance(typed_value, bool) or not isinstance(typed_value, (str, int)):
                        raise BridgeError("an enum argument value must be a member name or integer")
                    value = typed_value
                elif kind in {"i8", "u8", "i16", "u16", "i32", "u32", "i64", "u64", "f32", "f64"}:
                    if isinstance(typed_value, bool) or not isinstance(typed_value, (int, float, str)):
                        raise BridgeError(f"a {kind} argument value must be numeric")
                    try:
                        value = float(typed_value) if kind.startswith("f") else int(str(typed_value), 0)
                    except ValueError as exc:
                        raise BridgeError(f"invalid {kind} argument value") from exc
                else:
                    raise BridgeError(f"unsupported typed argument: {kind}")
            else:
                raise BridgeError(
                    "argument objects must contain enum, address, or exactly type and value"
                )
        if value is None:
            return "z"
        if isinstance(value, bool):
            return "b1" if value else "b0"
        if isinstance(value, (int, float)):
            return "n" + str(value).encode("ascii").hex()
        return "s" + str(value).encode("utf-8").hex()

    def il2cpp_status(self, _: dict[str, Any]) -> dict[str, Any]:
        # First probe on a stripped runtime may build a bounded read-only
        # metadata database and scan the identified module's data mappings.
        return self._json_call("IL2CPP_STATUS", timeout=max(self.config.timeout, 30.0))

    def il2cpp_dump_file(self, args: dict[str, Any]) -> dict[str, Any]:
        try:
            selected = debug_tools.selection(args)
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
        suffix = " " + workspace_tools._json(selected, 8192) if selected else ""
        return self._json_call("IL2CPP_DUMP_FILE" + suffix, timeout=300.0)

    def il2cpp_list_images(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", 256))
        if not 1 <= limit <= 5000:
            raise BridgeError("limit must be between 1 and 5000")
        return self._json_call(f"IL2CPP_IMAGES {limit}")

    def il2cpp_unity_catalog(self, args: dict[str, Any]) -> dict[str, Any]:
        category = str(args.get("category", "all"))
        if category not in {"all", "scene", "component", "asset", "renderer"}:
            raise BridgeError("category must be all, scene, component, asset, or renderer")
        query = self._hex_text(_single_line(args.get("query", ""), "query"))
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 1000000)
        limit = self._bounded_integer(args.get("limit", 100), "limit", 1, 1000)
        return self._json_call(
            f"IL2CPP_UNITY_CATALOG {category} {query} {offset} {limit}",
            timeout=max(self.config.timeout, 60.0),
        )

    def il2cpp_list_classes(self, args: dict[str, Any]) -> dict[str, Any]:
        image = self._hex_text(self._required_text(args, "image"))
        namespace_filter = self._hex_text(_single_line(args.get("namespace_filter", ""), "namespace_filter"))
        name_filter = self._hex_text(_single_line(args.get("name_filter", ""), "name_filter"))
        limit = int(args.get("limit", 200))
        if not 1 <= limit <= 5000:
            raise BridgeError("limit must be between 1 and 5000")
        return self._json_call(f"IL2CPP_CLASSES {image} {namespace_filter} {name_filter} {limit}")

    def il2cpp_list_methods(self, args: dict[str, Any]) -> dict[str, Any]:
        image = self._hex_text(self._required_text(args, "image"))
        namespace = self._hex_text(self._required_text(args, "namespace"))
        class_name = self._hex_text(self._required_text(args, "class_name"))
        name_filter = self._hex_text(_single_line(args.get("name_filter", ""), "name_filter"))
        limit = int(args.get("limit", 200))
        if not 1 <= limit <= 5000:
            raise BridgeError("limit must be between 1 and 5000")
        return self._json_call(f"IL2CPP_METHODS {image} {namespace} {class_name} {name_filter} {limit}")

    def il2cpp_list_fields(self, args: dict[str, Any]) -> dict[str, Any]:
        image = self._hex_text(self._required_text(args, "image"))
        namespace = self._hex_text(self._required_text(args, "namespace"))
        class_name = self._hex_text(self._required_text(args, "class_name"))
        name_filter = self._hex_text(_single_line(args.get("name_filter", ""), "name_filter"))
        inherited = args.get("include_inherited", False)
        if not isinstance(inherited, bool):
            raise BridgeError("include_inherited must be a boolean")
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 1000000)
        limit = self._bounded_integer(args.get("limit", 200), "limit", 1, 1000)
        return self._json_call(
            f"IL2CPP_FIELDS {image} {namespace} {class_name} {name_filter} "
            f"{'1' if inherited else '0'} {offset} {limit}"
        )

    def il2cpp_search(self, args: dict[str, Any]) -> dict[str, Any]:
        entity = str(args.get("entity", ""))
        if entity not in {"class", "method", "field"}:
            raise BridgeError("entity must be class, method, or field")
        mode = str(args.get("match_mode", "contains"))
        if mode not in {"contains", "prefix", "exact"}:
            raise BridgeError("match_mode must be contains, prefix, or exact")
        case_sensitive = args.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise BridgeError("case_sensitive must be a boolean")
        values = [
            self._hex_text(_single_line(args.get("query", ""), "query")),
            self._hex_text(_single_line(args.get("image_filter", ""), "image_filter")),
            self._hex_text(_single_line(args.get("namespace_filter", ""), "namespace_filter")),
            self._hex_text(_single_line(args.get("class_filter", ""), "class_filter")),
        ]
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 1000000)
        limit = self._bounded_integer(args.get("limit", 100), "limit", 1, 1000)
        return self._json_call(
            "IL2CPP_SEARCH " + " ".join(
                [entity, *values, mode, "1" if case_sensitive else "0", str(offset), str(limit)]
            ),
            timeout=max(self.config.timeout, 60.0),
        )

    def il2cpp_find_method(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("IL2CPP_FIND_METHOD " + " ".join(self._lookup_words(args)))

    def il2cpp_invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        words = self._lookup_words(args)
        instance = self._address(args.get("instance_address", "0x0"), "instance_address")
        values = args.get("arguments", [])
        if not isinstance(values, list) or len(values) > 64:
            raise BridgeError("arguments must be an array with at most 64 values")
        encoded = [self._invoke_token(value) for value in values]
        command = "IL2CPP_INVOKE " + " ".join(words + [instance, str(len(encoded)), *encoded])
        return self._json_call(command, timeout=max(self.config.timeout, 30.0))

    def il2cpp_object_inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(args.get("address"), "address")
        if int(address, 16) == 0:
            raise BridgeError("object address cannot be zero")
        inherited = args.get("include_inherited", True)
        if not isinstance(inherited, bool):
            raise BridgeError("include_inherited must be a boolean")
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 1000000)
        limit = self._bounded_integer(args.get("limit", 100), "limit", 1, 512)
        return self._json_call(
            f"IL2CPP_OBJECT_INSPECT {address} {'1' if inherited else '0'} {offset} {limit}"
        )

    def il2cpp_list_items(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(args.get("address"), "address")
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 1000000)
        limit = self._bounded_integer(args.get("limit", 50), "limit", 1, 256)
        return self._json_call(f"IL2CPP_LIST_ITEMS {address} {offset} {limit}")

    def il2cpp_dictionary_get(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(args.get("address"), "address")
        token = self._invoke_token(args.get("key"))
        return self._json_call(f"IL2CPP_DICTIONARY_GET {address} {token}")

    def il2cpp_hook(self, args: dict[str, Any]) -> dict[str, Any]:
        words = self._lookup_words(args)
        replacement = self._address(
            self._required_text(args, "replacement_address"), "replacement_address"
        )
        return self._json_call("IL2CPP_HOOK " + " ".join(words + [replacement]))

    def il2cpp_hook_return(self, args: dict[str, Any]) -> dict[str, Any]:
        words = self._lookup_words(args)
        kind = self._required_text(args, "return_kind")
        value = self._return_value(args)
        return self._json_call("IL2CPP_HOOK_RETURN " + " ".join(words + [kind, value]))

    def il2cpp_unhook(self, args: dict[str, Any]) -> dict[str, Any]:
        method = self.il2cpp_find_method(args)
        address = method.get("address")
        if not isinstance(address, str):
            raise BridgeError("resolved method did not contain an address")
        return self._json_call(f"DOBBY_DESTROY {self._address(address, 'address')}")

    def memory_read(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        raw_size = args.get("size")
        if isinstance(raw_size, bool):
            raise BridgeError("size must be an integer between 1 and 65536")
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            raise BridgeError("size must be an integer between 1 and 65536") from None
        if not 1 <= size <= 65536:
            raise BridgeError("size must be an integer between 1 and 65536")
        return self._json_call(f"MEMORY_READ {address} {size}")

    def memory_backend_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("MEMORY_BACKEND_STATUS")

    def memory_write(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        hex_bytes = self._required_text(args, "hex_bytes").strip()
        if (
            not hex_bytes
            or len(hex_bytes) % 2
            or len(hex_bytes) > 131072
            or any(character not in "0123456789abcdefABCDEF" for character in hex_bytes)
        ):
            raise BridgeError("hex_bytes must contain 1 to 65536 complete bytes")
        return self._json_call(f"MEMORY_WRITE {address} {hex_bytes}")

    @staticmethod
    def _memory_value_format(value_type: Any) -> tuple[str, int]:
        kind = _single_line(value_type, "value_type")
        format_code = MEMORY_VALUE_FORMATS.get(kind)
        if format_code is None:
            raise BridgeError(f"unsupported memory value_type: {kind}")
        return kind, struct.calcsize("<" + format_code)

    @classmethod
    def _decode_memory_value(cls, value_type: Any, hex_bytes: str) -> Any:
        kind, size = cls._memory_value_format(value_type)
        try:
            data = bytes.fromhex(hex_bytes)
        except ValueError:
            raise BridgeError("native bridge returned invalid hexadecimal memory data") from None
        if len(data) != size:
            raise BridgeError(f"native bridge returned {len(data)} bytes for {kind}, expected {size}")
        value = struct.unpack("<" + MEMORY_VALUE_FORMATS[kind], data)[0]
        if kind == "bool":
            return bool(value)
        if kind.startswith("ptr"):
            return f"0x{value:x}"
        return value

    @classmethod
    def _encode_memory_value(cls, value_type: Any, value: Any) -> tuple[str, Any]:
        kind, _ = cls._memory_value_format(value_type)
        if kind == "bool":
            if isinstance(value, bool):
                parsed: Any = value
            elif value in (0, 1, "0", "1"):
                parsed = bool(int(value))
            else:
                raise BridgeError("bool memory value must be true, false, 0, or 1")
        elif kind in {"f32", "f64"}:
            if isinstance(value, bool):
                raise BridgeError(f"{kind} memory value must be numeric")
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                raise BridgeError(f"{kind} memory value must be numeric") from None
            if not math.isfinite(parsed):
                raise BridgeError(f"{kind} memory value must be finite")
        else:
            if isinstance(value, bool):
                raise BridgeError(f"{kind} memory value must be an integer")
            try:
                if isinstance(value, str):
                    parsed = int(value, 0)
                elif isinstance(value, int):
                    parsed = value
                elif isinstance(value, float) and value.is_integer():
                    parsed = int(value)
                else:
                    raise ValueError
            except (TypeError, ValueError):
                raise BridgeError(f"{kind} memory value must be an integer or integer string") from None
        try:
            encoded = struct.pack("<" + MEMORY_VALUE_FORMATS[kind], parsed)
        except (struct.error, OverflowError):
            raise BridgeError(f"memory value is outside the range of {kind}") from None
        return encoded.hex(), cls._decode_memory_value(kind, encoded.hex())

    def memory_read_value(self, args: dict[str, Any]) -> dict[str, Any]:
        kind, size = self._memory_value_format(args.get("value_type"))
        result = self.memory_read({"address": args.get("address"), "size": size})
        hex_bytes = result.get("hex")
        if not isinstance(hex_bytes, str):
            raise BridgeError("native memory read did not return hexadecimal data")
        return {
            **result,
            "value_type": kind,
            "value": self._decode_memory_value(kind, hex_bytes),
        }

    def memory_write_value(self, args: dict[str, Any]) -> dict[str, Any]:
        kind, _ = self._memory_value_format(args.get("value_type"))
        hex_bytes, normalized_value = self._encode_memory_value(kind, args.get("value"))
        result = self.memory_write({"address": args.get("address"), "hex_bytes": hex_bytes})
        previous_hex = result.get("previous_hex")
        if not isinstance(previous_hex, str):
            raise BridgeError("native memory write did not return previous hexadecimal data")
        return {
            **result,
            "value_type": kind,
            "value": normalized_value,
            "previous_value": self._decode_memory_value(kind, previous_hex),
        }

    @staticmethod
    def _bounded_integer(value: Any, name: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise BridgeError(f"{name} must be an integer between {minimum} and {maximum}")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise BridgeError(f"{name} must be an integer between {minimum} and {maximum}") from None
        if not minimum <= parsed <= maximum:
            raise BridgeError(f"{name} must be an integer between {minimum} and {maximum}")
        return parsed

    def memory_list_modules(self, args: dict[str, Any]) -> dict[str, Any]:
        name_filter = _single_line(args.get("name_filter", ""), "name_filter")
        limit = self._bounded_integer(args.get("limit", 256), "limit", 1, 4096)
        return self._json_call(f"MEMORY_MODULES {self._hex_text(name_filter)} {limit}")

    def memory_find_module(self, args: dict[str, Any]) -> dict[str, Any]:
        module_name = self._required_text(args, "module_name")
        if not module_name:
            raise BridgeError("module_name cannot be empty")
        occurrence = self._bounded_integer(args.get("occurrence", 1), "occurrence", 1, 4096)
        return self._json_call(
            f"MEMORY_MODULE_FIND {self._hex_text(module_name)} {occurrence}"
        )

    def memory_address_info(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        return self._json_call(f"MEMORY_ADDRESS_INFO {address}")

    @staticmethod
    def _signed_offset(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise BridgeError(f"{name} must be a signed integer or hexadecimal string")
        try:
            parsed = value if isinstance(value, int) else int(str(value), 0)
        except (TypeError, ValueError):
            raise BridgeError(f"{name} must be a signed integer or hexadecimal string") from None
        if not -(1 << 63) <= parsed < (1 << 64):
            raise BridgeError(f"{name} is outside the supported address range")
        return parsed

    @staticmethod
    def _checked_address(value: int, name: str = "address") -> str:
        maximum = (1 << 64) - 1
        if not 0 <= value <= maximum:
            raise BridgeError(f"{name} is outside the supported 64-bit address range")
        return hex(value)

    def memory_resolve_address(self, args: dict[str, Any]) -> dict[str, Any]:
        module_name = self._required_text(args, "module")
        occurrence = self._bounded_integer(args.get("occurrence", 1), "occurrence", 1, 1024)
        module = self.memory_find_module({"module_name": module_name, "occurrence": occurrence})
        base_kind = str(args.get("base_kind", "load_bias"))
        if base_kind not in {"load_bias", "start"}:
            raise BridgeError("base_kind must be load_bias or start")
        base_text = module.get(base_kind)
        if not isinstance(base_text, str):
            raise BridgeError(f"module lookup did not return {base_kind}")
        base = int(self._address(base_text, base_kind), 16)
        offset = self._signed_offset(args.get("offset", "0x0"), "offset")
        address = self._checked_address(base + offset)
        return {
            "module": module,
            "base_kind": base_kind,
            "base": hex(base),
            "offset": hex(offset) if offset >= 0 else f"-0x{-offset:x}",
            "address": address,
        }

    def memory_resolve_pointer_chain(self, args: dict[str, Any]) -> dict[str, Any]:
        has_module = isinstance(args.get("module"), str) and bool(args.get("module"))
        has_base = "base_address" in args and args.get("base_address") is not None
        if has_module == has_base:
            raise BridgeError("provide exactly one of module or base_address")
        if has_module:
            resolved = self.memory_resolve_address(
                {
                    "module": args["module"],
                    "occurrence": args.get("occurrence", 1),
                    "offset": args.get("base_offset", "0x0"),
                    "base_kind": "load_bias",
                }
            )
            current = int(resolved["address"], 16)
            origin: dict[str, Any] = resolved
        else:
            base = int(self._address(args.get("base_address"), "base_address"), 16)
            base_offset = self._signed_offset(args.get("base_offset", "0x0"), "base_offset")
            current = int(self._checked_address(base + base_offset), 16)
            origin = {"base": hex(base), "base_offset": base_offset}
        raw_offsets = args.get("offsets", [])
        if not isinstance(raw_offsets, list) or len(raw_offsets) > 32:
            raise BridgeError("offsets must be an array with at most 32 entries")
        pointer_size = self._bounded_integer(args.get("pointer_size", 8), "pointer_size", 4, 8)
        if pointer_size not in {4, 8}:
            raise BridgeError("pointer_size must be 4 or 8")
        pointer_type = f"ptr{pointer_size * 8}"
        steps: list[dict[str, Any]] = []
        for index, raw_offset in enumerate(raw_offsets):
            read_at = self._checked_address(current)
            read = self.memory_read_value({"address": read_at, "value_type": pointer_type})
            pointer_text = read.get("value")
            if not isinstance(pointer_text, str):
                raise BridgeError(f"pointer read at step {index} did not return an address")
            pointer = int(pointer_text, 16)
            offset = self._signed_offset(raw_offset, f"offsets[{index}]")
            current = int(self._checked_address(pointer + offset, f"pointer step {index}"), 16)
            steps.append(
                {
                    "index": index,
                    "read_at": read_at,
                    "pointer": hex(pointer),
                    "offset": hex(offset) if offset >= 0 else f"-0x{-offset:x}",
                    "address": hex(current),
                }
            )
        dereference_final = args.get("dereference_final", False)
        if not isinstance(dereference_final, bool):
            raise BridgeError("dereference_final must be a boolean")
        if dereference_final:
            read_at = self._checked_address(current)
            read = self.memory_read_value({"address": read_at, "value_type": pointer_type})
            pointer_text = read.get("value")
            if not isinstance(pointer_text, str):
                raise BridgeError("final pointer read did not return an address")
            current = int(pointer_text, 16)
            steps.append(
                {"index": len(steps), "read_at": read_at, "pointer": hex(current), "offset": "0x0", "address": hex(current), "final_dereference": True}
            )
        return {
            "pointer_size": pointer_size,
            "origin": origin,
            "steps": steps,
            "address": hex(current),
        }

    def memory_read_pointer_chain(self, args: dict[str, Any]) -> dict[str, Any]:
        chain_args = dict(args)
        value_type = chain_args.pop("value_type", None)
        chain_args["dereference_final"] = False
        chain = self.memory_resolve_pointer_chain(chain_args)
        value = self.memory_read_value({"address": chain["address"], "value_type": value_type})
        return {"chain": chain, "read": value}

    def memory_write_pointer_chain(self, args: dict[str, Any]) -> dict[str, Any]:
        chain_args = dict(args)
        value_type = chain_args.pop("value_type", None)
        value = chain_args.pop("value", None)
        chain_args["dereference_final"] = False
        chain = self.memory_resolve_pointer_chain(chain_args)
        written = self.memory_write_value(
            {"address": chain["address"], "value_type": value_type, "value": value}
        )
        return {"chain": chain, "write": written}

    def memory_scan_base(self, args: dict[str, Any]) -> dict[str, Any]:
        chain_mode = any(key in args for key in ("max_depth", "max_offset", "budget_mb", "candidate_limit", "time_ms"))
        has_module = isinstance(args.get("module"), str) and bool(args.get("module"))
        has_target = "target_address" in args and args.get("target_address") is not None
        if has_module == has_target:
            raise BridgeError("provide exactly one of module or target_address")
        target_offset = self._signed_offset(args.get("target_offset", "0x0"), "target_offset")
        if has_module:
            target_info = self.memory_resolve_address(
                {
                    "module": args["module"],
                    "occurrence": args.get("occurrence", 1),
                    "offset": target_offset,
                    "base_kind": "load_bias",
                }
            )
            target = target_info["address"]
        else:
            raw_target = int(self._address(args.get("target_address"), "target_address"), 16)
            target = self._checked_address(raw_target + target_offset, "target address")
            target_info = {"address": target}
        pointer_size = self._bounded_integer(args.get("pointer_size", 8), "pointer_size", 4, 8)
        if pointer_size not in {4, 8}:
            raise BridgeError("pointer_size must be 4 or 8")
        max_results = self._bounded_integer(args.get("max_results", 1000), "max_results", 1, 10000)
        workers = self._bounded_integer(args.get("workers", 0), "workers", 0, 32)
        if workers == 0:
            workers = min(32, max(2, os.cpu_count() or 4))
        memory_types = self._memory_region_types(args)
        if isinstance(args.get("scan_module"), str) and args.get("scan_module"):
            scan_module = self.memory_find_module(
                {
                    "module_name": args["scan_module"],
                    "occurrence": args.get("scan_occurrence", 1),
                }
            )
            start = scan_module.get("start")
            end = scan_module.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                raise BridgeError("scan module lookup did not return a valid address range")
        elif chain_mode and "start" not in args and "end" not in args:
            start, end = "0x1", "0xffffffffffffffff"
        else:
            if "start" not in args or "end" not in args:
                raise BridgeError("provide scan_module or both start and end")
            start = self._address(args["start"], "start")
            end = self._address(args["end"], "end")
        if chain_mode:
            depth = self._bounded_integer(args.get("max_depth", 3), "max_depth", 1, 15)
            max_offset = self._signed_offset(args.get("max_offset", 4096), "max_offset")
            if not 0 <= max_offset <= 1048576 or max_results > 1000:
                raise BridgeError("chain mode requires max_offset 0..1048576 and max_results <=1000")
            options = {"target_address": target, "start": start, "end": end, "pointer_size": pointer_size,
                "max_depth": depth, "max_offset": str(max_offset), "max_results": max_results, "workers": workers, "regions": memory_types}
            for key, default, low, high in (("budget_mb", 512, 16, 4096), ("candidate_limit", 16384, 1024, 65536), ("time_ms", 15000, 1000, 30000)):
                options[key] = self._bounded_integer(args.get(key, default), key, low, high)
            result = self._json_call("MEMORY_CHAIN_SCAN " + workspace_tools._json(options, 8192), timeout=max(self.config.timeout, options["time_ms"] / 1000.0 + 15.0))
            return {"target": target_info, "pointer_size": pointer_size, "workers": workers, "search": result}
        result = self._json_call(
            f"MEMORY_POINTER_SCAN_MT {start} {end} {target} {pointer_size} "
            f"{max_results} {workers} {self._hex_text(memory_types)}",
            timeout=max(self.config.timeout, 300.0),
        )
        return {
            "target": target_info,
            "pointer_size": pointer_size,
            "workers": workers,
            "search": result,
        }

    @staticmethod
    def _memory_search_pattern(value: Any) -> str:
        if not isinstance(value, str):
            raise BridgeError("pattern must be a hexadecimal string")
        compact = "".join(value.split())
        if (
            not compact
            or len(compact) % 2
            or len(compact) > 512
            or any(character not in "0123456789abcdefABCDEF?" for character in compact)
        ):
            raise BridgeError("pattern must contain 1 to 256 hexadecimal/wildcard bytes")
        return compact

    def _memory_search_range(self, args: dict[str, Any]) -> tuple[str, str]:
        has_module = "module_name" in args
        has_start = "start_address" in args
        has_end = "end_address" in args
        if has_module:
            if has_start or has_end:
                raise BridgeError("module_name cannot be combined with start_address/end_address")
            module = self.memory_find_module(
                {
                    "module_name": args.get("module_name"),
                    "occurrence": args.get("occurrence", 1),
                }
            )
            start = module.get("start")
            end = module.get("end")
            if not isinstance(start, str) or not isinstance(end, str):
                raise BridgeError("module lookup did not return a valid address range")
            return start, end
        if not has_start or not has_end:
            raise BridgeError("provide module_name or both start_address and end_address")
        return (
            self._address(args.get("start_address"), "start_address"),
            self._address(args.get("end_address"), "end_address"),
        )

    @staticmethod
    def _memory_region_types(args: dict[str, Any]) -> str:
        raw = args.get("memory_types")
        if raw is None:
            return "all"
        if not isinstance(raw, list) or not raw:
            raise BridgeError("memory_types must be a non-empty array when provided")
        selected: list[str] = []
        for value in raw:
            if not isinstance(value, str) or value not in MEMORY_REGION_TYPES:
                raise BridgeError(f"unsupported memory type: {value!r}")
            if value not in selected:
                selected.append(value)
        return ",".join(selected)

    def memory_search(self, args: dict[str, Any]) -> dict[str, Any]:
        pattern = self._memory_search_pattern(args.get("pattern"))
        start, end = self._memory_search_range(args)
        max_results = self._bounded_integer(
            args.get("max_results", 1024), "max_results", 1, 10000
        )
        alignment = self._bounded_integer(args.get("alignment", 1), "alignment", 1, 4096)
        memory_types = self._memory_region_types(args)
        return self._json_call(
            f"MEMORY_SEARCH {start} {end} {self._hex_text(pattern)} {max_results} "
            f"{alignment} {self._hex_text(memory_types)}",
            timeout=max(self.config.timeout, 300.0),
        )

    def memory_search_value(self, args: dict[str, Any]) -> dict[str, Any]:
        kind, value_size = self._memory_value_format(args.get("value_type"))
        pattern, normalized_value = self._encode_memory_value(kind, args.get("value"))
        search_args = {
            key: value
            for key, value in args.items()
            if key not in {"value_type", "value"}
        }
        search_args["pattern"] = pattern
        search_args.setdefault("alignment", value_size)
        result = self.memory_search(search_args)
        return {**result, "value_type": kind, "value": normalized_value}

    def memory_search_exact(self, args: dict[str, Any]) -> dict[str, Any]:
        value_types = args.get("value_types")
        if not isinstance(value_types, list) or not value_types:
            raise BridgeError("value_types must be a non-empty array")
        if len(value_types) > len(MEMORY_VALUE_FORMATS):
            raise BridgeError("too many value_types")
        if any(not isinstance(item, str) for item in value_types):
            raise BridgeError("value_types must contain type names")
        raw = args.get("value")
        if not isinstance(raw, (str, int, float, bool)) or (isinstance(raw, float) and not math.isfinite(raw)):
            raise BridgeError("value must be text or a finite number/boolean")
        expression = isinstance(raw, str) and (any(c in raw for c in ";~:") or (bool(raw) and raw[-1:] in "BWDQFE" and not raw.lower().startswith("0x")))
        advanced = expression or any(t in {"hex", "utf8", "utf16"} for t in value_types) or "scan_mb" in args or "timeout_ms" in args
        if advanced:
            types = []
            for item in value_types:
                if item in {"hex", "utf8", "utf16"}: kind = item
                else:
                    kind, _ = self._memory_value_format(item)
                    kind = {"ptr32": "u32", "ptr64": "u64"}.get(kind, kind)
                if kind not in types: types.append(kind)
            value = ("true" if raw else "false") if isinstance(raw, bool) else str(raw)
            if isinstance(raw, float) and raw.is_integer(): value = str(int(raw))
            query = {"mode": "exact", "types": types, "value": value,
                     "regions": self._memory_region_types(args), "max_results": args.get("max_results", 1024)}
            for key in ("alignment", "scan_mb", "timeout_ms"):
                if key in args: query[key] = args[key]
            if any(key in args for key in ("module_name", "start_address", "end_address")):
                query["start"], query["end"] = self._memory_search_range(args)
            try:
                render_tools.validate(query, debug_tools.SEARCH_QUERY, "query")
            except ValueError as exc: raise BridgeError(str(exc)) from exc
            result = self._json_call("MEMORY_SEARCH_TYPED " + workspace_tools._json(query, 16384), timeout=max(self.config.timeout, 75.0))
            searches = result.get("sessions", [])
            return {**result, "exact": True, "searches": searches, "search_count": len(searches)}
        searches: list[dict[str, Any]] = []
        seen: set[str] = set()
        common = {key: value for key, value in args.items() if key not in {"value_types", "value"}}
        for raw_kind in value_types:
            kind, _ = self._memory_value_format(raw_kind)
            if kind in seen:
                continue
            seen.add(kind)
            searches.append(self.memory_search_value({
                **common,
                "value_type": kind,
                "value": args.get("value"),
            }))
        return {"exact": True, "searches": searches, "search_count": len(searches)}

    def memory_search_fuzzy(self, args: dict[str, Any]) -> dict[str, Any]:
        value_size = self._bounded_integer(args.get("value_size"), "value_size", 1, 8)
        if value_size not in {1, 2, 4, 8}:
            raise BridgeError("value_size must be 1, 2, 4, or 8")
        start, end = self._memory_search_range(args)
        max_results = self._bounded_integer(
            args.get("max_results", 10000), "max_results", 1, 10000
        )
        alignment = self._bounded_integer(
            args.get("alignment", value_size), "alignment", 1, 4096
        )
        memory_types = self._memory_region_types(args)
        return self._json_call(
            f"MEMORY_SEARCH_FUZZY {start} {end} {value_size} {max_results} "
            f"{alignment} {self._hex_text(memory_types)}",
            timeout=max(self.config.timeout, 300.0),
        )

    def memory_filter(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = self._bounded_integer(
            args.get("session_id"), "session_id", 1, (1 << 64) - 1
        )
        mode = _single_line(args.get("mode", ""), "mode")
        allowed = {"equals", "not_equals", "changed", "unchanged", "increased", "decreased"}
        if mode not in allowed:
            raise BridgeError("unsupported memory filter mode")
        if mode in {"equals", "not_equals"}:
            pattern = self._memory_search_pattern(args.get("pattern"))
        else:
            if "pattern" in args:
                raise BridgeError(f"pattern is not used by {mode} filtering")
            pattern = ""
        return self._json_call(
            f"MEMORY_FILTER {session_id} {mode} {self._hex_text(pattern)}",
            timeout=max(self.config.timeout, 60.0),
        )

    def memory_filter_value(self, args: dict[str, Any]) -> dict[str, Any]:
        mode = _single_line(args.get("mode", ""), "mode")
        comparisons = {"equals", "not_equals", "greater", "less", "increased_by", "decreased_by"}
        if mode not in comparisons | {"changed", "unchanged", "increased", "decreased"}:
            raise BridgeError("unsupported typed filter mode")
        if (mode in comparisons) != ("value" in args):
            raise BridgeError("value is required only for comparison/delta filtering")
        raw = args.get("value", "")
        expression = isinstance(raw, str) and (any(c in raw for c in ";~:") or (bool(raw) and raw[-1:] in "BWDQFE" and not raw.lower().startswith("0x")))
        if "value_type" not in args or expression or mode not in {"equals", "not_equals"}:
            session = self._bounded_integer(args.get("session_id"), "session_id", 1, (1 << 64) - 1)
            if not isinstance(raw, (str, int, float, bool)) or (isinstance(raw, float) and not math.isfinite(raw)):
                raise BridgeError("value must be text or a finite number/boolean")
            value = ("true" if raw else "false") if isinstance(raw, bool) else str(raw)
            if isinstance(raw, float) and raw.is_integer(): value = str(int(raw))
            if len(value.encode("utf-8")) > 2048: raise BridgeError("filter value too long")
            return self._json_call(f"MEMORY_FILTER_TYPED {session} {mode} {self._hex_text(value)}", timeout=max(self.config.timeout, 75.0))
        kind, _ = self._memory_value_format(args.get("value_type"))
        pattern, normalized_value = self._encode_memory_value(kind, args.get("value"))
        result = self.memory_filter(
            {"session_id": args.get("session_id"), "mode": mode, "pattern": pattern}
        )
        return {**result, "value_type": kind, "value": normalized_value}

    def memory_search_clear(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = self._bounded_integer(
            args.get("session_id"), "session_id", 1, (1 << 64) - 1
        )
        return self._json_call(f"MEMORY_SEARCH_CLEAR {session_id}")

    def memory_search_results(self, args: dict[str, Any]) -> dict[str, Any]:
        session_id = self._bounded_integer(
            args.get("session_id"), "session_id", 1, (1 << 64) - 1
        )
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 100000)
        limit = self._bounded_integer(args.get("limit", 100), "limit", 1, 1000)
        return self._json_call(f"MEMORY_SEARCH_RESULTS {session_id} {offset} {limit}")

    def dobby_version(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("DOBBY_VERSION")

    def dobby_resolve_symbol(self, args: dict[str, Any]) -> dict[str, Any]:
        image = self._hex_text(_single_line(args.get("image", ""), "image"))
        symbol = self._hex_text(self._required_text(args, "symbol"))
        return self._json_call(f"DOBBY_RESOLVE {image} {symbol}")

    def dobby_hook(self, args: dict[str, Any]) -> dict[str, Any]:
        target = self._address(self._required_text(args, "target_address"), "target_address")
        replacement = self._address(self._required_text(args, "replacement_address"), "replacement_address")
        return self._json_call(f"DOBBY_HOOK {target} {replacement}")

    def dobby_hook_return(self, args: dict[str, Any]) -> dict[str, Any]:
        target = self._address(self._required_text(args, "target_address"), "target_address")
        kind = self._required_text(args, "return_kind")
        value = self._return_value(args)
        return self._json_call(f"DOBBY_HOOK_RETURN {target} {kind} {value}")

    def dobby_instrument(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        mode = args.get("mode", "backtrace")
        if mode not in {"backtrace", "counter"}:
            raise BridgeError("mode must be backtrace or counter")
        return self._json_call(f"DOBBY_INSTRUMENT {address}" + (f" {mode}" if "mode" in args else ""))

    def dobby_trace_get(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        return self._json_call(f"DOBBY_TRACE_GET {address}")

    def _resolve_backtrace_modules(self, result: dict[str, Any]) -> dict[str, Any]:
        raw_frames = result.get("frames")
        if not isinstance(raw_frames, list):
            return result
        resolve_modules = self.registry.enabled("memory_maps")
        resolved_frames: list[dict[str, Any]] = []
        for index, raw_frame in enumerate(raw_frames):
            address = raw_frame.get("address") if isinstance(raw_frame, dict) else raw_frame
            frame: dict[str, Any] = {"index": index, "address": address}
            if isinstance(raw_frame, dict):
                frame.update(raw_frame)
            if resolve_modules and isinstance(address, str):
                try:
                    info = self.memory_address_info({"address": address})
                except BridgeError as exc:
                    frame["module_error"] = str(exc)
                else:
                    frame["mapping"] = info
            resolved_frames.append(frame)
        return {**result, "frames": resolved_frames}

    def dobby_trace_backtrace(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        max_frames = self._bounded_integer(args.get("max_frames", 16), "max_frames", 1, 32)
        result = self._json_call(f"DOBBY_TRACE_BACKTRACE {address} {max_frames}")
        return self._resolve_backtrace_modules(result)

    def dobby_patch_code(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        hex_bytes = self._required_text(args, "hex_bytes").strip()
        if (
            not hex_bytes
            or len(hex_bytes) % 2
            or len(hex_bytes) > 8192
            or any(character not in "0123456789abcdefABCDEF" for character in hex_bytes)
        ):
            raise BridgeError("hex_bytes must contain 1 to 4096 complete bytes")
        return self._json_call(f"DOBBY_PATCH {address} {hex_bytes}")

    def dobby_destroy(self, args: dict[str, Any]) -> dict[str, Any]:
        target = self._address(self._required_text(args, "target_address"), "target_address")
        return self._json_call(f"DOBBY_DESTROY {target}")

    def dobby_list_hooks(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("DOBBY_LIST_HOOKS")

    def debug_help(self, args: dict[str, Any]) -> dict[str, Any]:
        requested = _single_line(args.get("command", ""), "command").strip()
        if not requested:
            visible = tools_for_registry(self.registry)
            return {"source": "local_mcp_catalog", "tools": [
                {"name": tool["name"], "features": list(tool_features(tool["name"]))}
                for tool in visible
            ], "hint": "debug_help(command=<tool name>) returns its description and schema"}
        tool = TOOL_BY_NAME.get(requested.lower())
        if tool is not None:
            self.registry.require(tool["name"])
            return {
                "tool": tool["name"], "title": tool.get("title"),
                "description": tool.get("description"),
                "inputSchema": tool.get("inputSchema", EMPTY_SCHEMA),
                "features": list(tool_features(tool["name"])),
                "source": "local_mcp_catalog",
            }
        command = requested.upper()
        aliases = {
            "MEMORY_SEARCH_FUZZY": "MEMORY_SEARCH_FUZZY",
            "MEMORY_SEARCH_RESULTS": "MEMORY_SEARCH_RESULTS",
            "MEMORY_FILTER": "MEMORY_FILTER",
            "LUA_STATUS": "LUA_STATUS",
            "LUA_EXECUTE": "LUA_EXEC",
            "LUA_LOGS": "LUA_LOGS",
            "LUA_RESET": "LUA_RESET",
            "ASSEMBLY_STATUS": "ASM_STATUS",
            "ASSEMBLY_ASSEMBLE": "ASM_ASSEMBLE",
            "ASSEMBLY_DISASSEMBLE": "ASM_DISASSEMBLE",
            "ASSEMBLY_PATCH": "ASM_PATCH",
            "DECOMPILER_STATUS": "DECOMP_STATUS",
            "DECOMPILE_FUNCTION": "DECOMP_DECOMPILE",
            "BREAKPOINT_STATUS": "BREAKPOINT_STATUS",
            "BREAKPOINT_SET": "BREAKPOINT_SET",
            "BREAKPOINT_LIST": "BREAKPOINT_LIST",
            "BREAKPOINT_HITS": "BREAKPOINT_HITS",
            "BREAKPOINT_BACKTRACE": "BREAKPOINT_BACKTRACE",
            "DOBBY_TRACE_BACKTRACE": "DOBBY_TRACE_BACKTRACE",
            "IL2CPP_SEARCH": "IL2CPP_SEARCH",
            "IL2CPP_LIST_FIELDS": "IL2CPP_FIELDS",
            "IL2CPP_OBJECT_INSPECT": "IL2CPP_OBJECT_INSPECT",
            "IL2CPP_LIST_ITEMS": "IL2CPP_LIST_ITEMS",
            "IL2CPP_DICTIONARY_GET": "IL2CPP_DICTIONARY_GET",
            "BREAKPOINT_CLEAR": "BREAKPOINT_CLEAR",
            "BREAKPOINT_CLEAR_ALL": "BREAKPOINT_CLEAR_ALL",
            "RUNTIME_CAPABILITIES": "CAPABILITIES",
            "DEBUG_HELP": "HELP",
        }
        command = aliases.get(command, command)
        disabled = [feature for feature in raw_command_features(command) if not self.registry.enabled(feature)]
        if disabled:
            raise BridgeError(f"help topic {command} is disabled by MCP feature: {', '.join(disabled)}")
        return self._json_call(f"HELP {command}")

    def runtime_capabilities(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("CAPABILITIES")

    def lua_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("LUA_STATUS")

    def lua_execute(self, args: dict[str, Any]) -> dict[str, Any]:
        if "script" not in args or not isinstance(args["script"], str):
            raise BridgeError("script must be a string")
        script = args["script"]
        if "\x00" in script:
            raise BridgeError("script cannot contain NUL bytes")
        if len(script.encode("utf-8")) > 256 * 1024:
            raise BridgeError("script exceeds 256 KiB")
        timeout = float(args.get("timeout", 30))
        if not 0.1 <= timeout <= 300:
            raise BridgeError("timeout must be between 0.1 and 300 seconds")
        return self._json_call(f"LUA_EXEC {self._hex_text(script)}", timeout=timeout)

    def lua_reset(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("LUA_RESET")

    def lua_logs(self, args: dict[str, Any]) -> dict[str, Any]:
        clear = args.get("clear", True)
        if not isinstance(clear, bool):
            raise BridgeError("clear must be a boolean")
        return self._json_call(f"LUA_LOGS {1 if clear else 0}")

    def assembly_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("ASM_STATUS")

    def assembly_assemble(self, args: dict[str, Any]) -> dict[str, Any]:
        instruction = args.get("instruction")
        if not isinstance(instruction, str) or "\x00" in instruction:
            raise BridgeError("instruction must be text without NUL bytes")
        instruction = instruction.strip()
        if not instruction or len(instruction.encode("utf-8")) > 16384:
            raise BridgeError("instruction must contain 1 to 16384 UTF-8 bytes")
        return self._json_call(f"ASM_ASSEMBLE {self._hex_text(instruction)}")

    def assembly_disassemble(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        size = self._bounded_integer(args.get("size", 64), "size", 1, 4096)
        maximum = self._bounded_integer(
            args.get("max_instructions", 16), "max_instructions", 1, 256
        )
        return self._json_call(f"ASM_DISASSEMBLE {address} {size} {maximum}")

    def assembly_patch(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        instruction = args.get("instruction")
        if not isinstance(instruction, str) or "\x00" in instruction:
            raise BridgeError("instruction must be text without NUL bytes")
        instruction = instruction.strip()
        if not instruction or len(instruction.encode("utf-8")) > 16384:
            raise BridgeError("instruction must contain 1 to 16384 UTF-8 bytes")
        return self._json_call(f"ASM_PATCH {address} {self._hex_text(instruction)}")

    def decompiler_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("DECOMP_STATUS")

    def decompile_function(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        size = self._bounded_integer(args.get("size", 256), "size", 4, 65536)
        if size % 4:
            raise BridgeError("size must be a multiple of 4 for ARM64")
        maximum = self._bounded_integer(
            args.get("max_instructions", 256), "max_instructions", 1, 4096
        )
        max_output = self._bounded_integer(
            args.get("max_output_bytes", 262144), "max_output_bytes", 256, 1048576
        )
        optimize = args.get("optimize", True)
        stop_at_return = args.get("stop_at_return", True)
        if not isinstance(optimize, bool) or not isinstance(stop_at_return, bool):
            raise BridgeError("optimize and stop_at_return must be booleans")
        metadata_mode = args.get("metadata_mode", "cached")
        if metadata_mode not in {"cached", "resolve_known"}:
            raise BridgeError("metadata_mode must be cached or resolve_known")
        if metadata_mode == "resolve_known":
            self.registry.require("il2cpp_find_method")
        return self._json_call(
            f"DECOMP_DECOMPILE {address} {size} {maximum} {max_output} "
            f"{1 if optimize else 0} {1 if stop_at_return else 0}" + (" resolve_known" if metadata_mode == "resolve_known" else ""),
            timeout=max(self.config.timeout, 60.0),
        )

    def breakpoint_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("BREAKPOINT_STATUS")

    def breakpoint_set(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        kind = self._required_text(args, "type")
        if kind not in {"x", "r", "w", "rw"}:
            raise BridgeError("type must be x, r, w, or rw")
        length = self._bounded_integer(args.get("length", 4), "length", 1, 8)
        if length not in {1, 2, 4, 8}:
            raise BridgeError("length must be 1, 2, 4, or 8")
        return self._json_call(f"BREAKPOINT_SET {address} {kind} {length}")

    def breakpoint_list(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("BREAKPOINT_LIST")

    def breakpoint_hits(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(args.get("address", "0"), "address")
        offset = self._bounded_integer(args.get("offset", 0), "offset", 0, 1000000)
        limit = self._bounded_integer(args.get("limit", 100), "limit", 1, 512)
        return self._json_call(f"BREAKPOINT_HITS {address} {offset} {limit}")

    def breakpoint_backtrace(self, args: dict[str, Any]) -> dict[str, Any]:
        hit_id = self._bounded_integer(args.get("hit_id"), "hit_id", 1, (1 << 63) - 1)
        max_frames = self._bounded_integer(args.get("max_frames", 32), "max_frames", 1, 64)
        result = self._json_call(f"BREAKPOINT_BACKTRACE {hit_id} {max_frames}")
        return self._resolve_backtrace_modules(result)

    def breakpoint_clear(self, args: dict[str, Any]) -> dict[str, Any]:
        address = self._address(self._required_text(args, "address"), "address")
        return self._json_call(f"BREAKPOINT_CLEAR {address}")

    def breakpoint_clear_all(self, _: dict[str, Any]) -> dict[str, Any]:
        return self._json_call("BREAKPOINT_CLEAR_ALL")


class McpServer:
    def __init__(self, dispatcher: ToolDispatcher):
        self.dispatcher = dispatcher
        self.registry = dispatcher.registry
        self._write_lock = threading.Lock()
        self._stdio_active = False
        self.registry.subscribe(self._notify_tools_changed)

    def _write_message(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()

    def _notify_tools_changed(self) -> None:
        if self._stdio_active:
            self._write_message({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    @staticmethod
    def _success(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    @staticmethod
    def _tool_result(value: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
        text = value.get("error") if is_error else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": str(text)}],
            "isError": is_error,
        }
        if not is_error:
            result["structuredContent"] = value
        return result

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
        method = message.get("method")
        request_id = message.get("id")
        is_notification = "id" not in message

        if is_notification:
            return None
        if method == "initialize":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return self._error(request_id, -32602, "Invalid params")
            requested = params.get("protocolVersion")
            protocol = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
            return self._success(
                request_id,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": "Zygisk Runtime MCP Bridge",
                        "version": SERVER_VERSION,
                        "description": "Controls IL2CPP, native memory, LuaJIT, Dobby, assembly, and breakpoints in an injected Android process.",
                    },
                    "instructions": "Use ping first. The target game must be running; ADB forwarding is automatic by default.",
                },
            )
        if method == "ping":
            return self._success(request_id, {})
        if method == "tools/list":
            return self._success(request_id, {"tools": tools_for_registry(self.registry)})
        if method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return self._error(request_id, -32602, "Invalid params")
            try:
                name = params.get("name", "")
                if not isinstance(name, str):
                    raise BridgeError("tool name must be a string")
                arguments = params.get("arguments") or {}
                value = self.dispatcher.call(name, arguments)
                return self._success(request_id, self._tool_result(value))
            except Exception as exc:
                return self._success(request_id, self._tool_result({"error": str(exc)}, is_error=True))
        return self._error(request_id, -32601, f"Method not found: {method}")

    def run_stdio(self) -> None:
        self._stdio_active = True
        slots = threading.BoundedSemaphore(16)
        input_slots = threading.BoundedSemaphore(4)

        def complete(message, capacity):
            try:
                response = self.handle(message)
                if response is not None:
                    self._write_message(response)
            finally:
                capacity.release()

        try:
            # Keep a separate producer lane: long decompiles/WAIT_INPUT calls
            # must not occupy every worker needed by push_input_result.
            with ThreadPoolExecutor(max_workers=8, thread_name_prefix="mcp-call") as workers, \
                 ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-input-push") as inputs:
                for raw_line in sys.stdin.buffer:
                    if not raw_line.strip():
                        continue
                    try:
                        message = json.loads(raw_line)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        response = self._error(None, -32700, "Parse error", str(exc))
                    else:
                        if isinstance(message, dict) and message.get("method") == "tools/call" and "id" in message:
                            params = message.get("params")
                            is_push = isinstance(params, dict) and params.get("name") == "push_input_result"
                            pool, capacity = (inputs, input_slots) if is_push else (workers, slots)
                            if capacity.acquire(blocking=False):
                                try:
                                    pool.submit(complete, message, capacity)
                                except BaseException:
                                    capacity.release()
                                    raise
                                continue
                            response = self._success(message["id"], self._tool_result({"error": "MCP_SERVER_BUSY: retry after an active call completes"}, is_error=True))
                        else:
                            response = self.handle(message)
                    if response is not None:
                        self._write_message(response)
        finally:
            self._stdio_active = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MCP bridge for Zygisk IL2CPP hooks")
    parser.add_argument("--host", default=os.environ.get("ZYGISK_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ZYGISK_MCP_PORT", "27184")))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("ZYGISK_MCP_TIMEOUT", "5")))
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"), help="ADB executable path")
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"), help="ADB device serial")
    parser.add_argument("--no-adb-forward", action="store_true", help="Do not create adb forward automatically")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Run on the target Android machine and connect directly; disables ADB forwarding",
    )
    parser.add_argument("--admin-host", default=os.environ.get("ZYGISK_MCP_ADMIN_HOST", "127.0.0.1"))
    parser.add_argument(
        "--admin-port",
        type=int,
        default=int(os.environ.get("ZYGISK_MCP_ADMIN_PORT", "27185")),
        help="Browser feature-control port (default: 27185)",
    )
    parser.add_argument("--admin-token", default=os.environ.get("ZYGISK_MCP_ADMIN_TOKEN"))
    parser.add_argument("--no-admin", action="store_true", help="Disable the browser feature-control server")
    parser.add_argument(
        "--feature-config",
        default=os.environ.get(
            "ZYGISK_MCP_FEATURE_CONFIG",
            str(Path(__file__).with_name("mcp_features.json")),
        ),
        help="JSON file used to persist MCP feature switches",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = ConnectionConfig(
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        auto_adb_forward=not (args.no_adb_forward or args.direct),
        adb_path=args.adb,
        adb_serial=args.serial,
    )
    try:
        config.validate()
    except BridgeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    registry = FeatureRegistry(args.feature_config)
    if not args.no_admin:
        try:
            admin = McpAdminServer(registry, args.admin_host, args.admin_port, args.admin_token)
            admin.start()
            print(f"MCP browser control: {admin.url}", file=sys.stderr)
        except (BridgeError, OSError) as exc:
            print(f"MCP browser control disabled: {exc}", file=sys.stderr)
    McpServer(ToolDispatcher(config, registry)).run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
