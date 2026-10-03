"""Window trees, frame-safe inspection, render rules and optional UI programs.

Only command encoding lives here. No code runs when this module is imported.
All descriptors are bounded; the native side validates IDs, parent graphs and types again.
"""
from __future__ import annotations

import json
from typing import Any, Callable
try:
    from . import render_tools as r
except ImportError:
    import render_tools as r

B = {"type": "boolean"}
ID, TEXT, COLOR = r.ID, r.TEXT, r.COLOR
SCALAR = {"type": ["string", "number", "boolean", "null"], "maxLength": 4096}
JSON_VALUE = {"type": ["object", "array", "string", "number", "boolean", "null"], "maxLength": 4096}
ADDRESS = {"type": "string", "minLength": 1, "maxLength": 32,
           "pattern": r"(?:0[xX][0-9a-fA-F]+|[0-9]+)"}
DEST = r.enum("clipboard", "file")
FILENAME = {"type": "string", "maxLength": 80, "pattern": r"[A-Za-z0-9_-]{1,64}\.(?:txt|json|cs|c|asm|lua|csv)"}
PRESET = {"type": "string", "maxLength": 64, "pattern": r"[A-Za-z0-9_-]{1,64}"}
ASSET_TYPE = r.enum("Texture2D", "Texture", "RenderTexture", "Material", "Shader", "Mesh", "AudioClip", "Sprite")

def integer(low: int, high: int) -> dict:
    return {"type": "integer", "minimum": low, "maximum": high}

def obj(fields: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": fields, "required": list(required), "additionalProperties": False}

def array(items: dict, maximum: int, minimum: int = 0) -> dict:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}

def descriptor(fields: dict) -> dict:
    return obj({"id": ID, **fields}, ("id",))

COMMON = {"visible": B, "order": r.number(-1000, 1000)}
WINDOW = descriptor({**COMMON, "title": TEXT, "parent": TEXT, "collapsed": B,
    "position": array(r.number(-32768, 32768), 2, 2), "size": array(r.number(0, 32768), 2, 2),
    "movable": B, "resizable": B, "alpha": r.number(.2, 1)})
LOCAL_ACTIONS = ("OVERLAY_SET", "OVERLAY_WINDOW", "OVERLAY_RESET", "RENDER_STYLE", "RENDER_CAMERA",
    "RENDER_OBJECT_SET", "RENDER_OBJECT_POSITION", "RENDER_OBJECT_REMOVE", "RENDER_REFRESH_CLASS",
    "RENDER_UNTRACK_CLASS", "RENDER_PRIMITIVE_SET", "RENDER_PRIMITIVE_REMOVE")
GAME_ACTIONS = ("IL2CPP_CALL_EXACT", "IL2CPP_FIELD_SET")
ACTION = {**obj({"command": r.enum(*LOCAL_ACTIONS, *GAME_ACTIONS),
    "arguments": {"type": "string", "maxLength": 4096}, "game_thread": B}, ("command", "arguments")),
    "type": ["object", "null"]}
BINDING = {**obj({"source": r.enum("object", "render_style", "variable"), "id": TEXT,
    "path": TEXT, "write": B}, ("source",)), "type": ["object", "null"]}
NODE = descriptor({**COMMON, "window": ID, "parent": TEXT,
    "type": r.enum("text", "button", "toggle", "slider", "select", "input", "separator", "spacer",
                   "group", "child", "tree", "tabs", "tab", "table", "progress", "plot", "color"),
    "label": TEXT, "text": {"type": "string", "maxLength": 4096}, "value": SCALAR,
    "min": r.number(-1e7, 1e7), "max": r.number(-1e7, 1e7), "options": array(TEXT, 64, 1),
    "values": array(r.number(-1e20, 1e20), 512), "enabled": B, "color": COLOR,
    "width": r.number(0, 32768), "height": r.number(0, 32768), "columns": integer(1, 8),
    "same_line": B, "tooltip": {"type": "string", "maxLength": 1024},
    "binding": BINDING, "action": ACTION, "event": TEXT})
TREE = obj({"windows": array(WINDOW, 32), "nodes": array(NODE, 256)})
PROGRAM = obj({"id": {**ID, "maxLength": 32, "pattern": r"[^:]+"}, "source": {"type": "string", "minLength": 1, "maxLength": 65536},
               "enabled": B, "allow_game_actions": B}, ("id",))
RULE = descriptor({"enabled": B, "priority": r.number(-1000, 1000), "group": TEXT,
    "class_contains": TEXT, "name_contains": TEXT, "tag": TEXT, "layers": array(integer(0, 31), 32),
    "active_only": B, "bindings": {"type": "object", "additionalProperties": TEXT},
    "label": {"type": "string", "maxLength": 1024}, "style": obj({k: v for k, v in r.STYLE_FIELDS.items() if k != "sample_hz"}),
    "condition": {**obj({"field": TEXT, "op": r.enum("eq", "ne", "gt", "ge", "lt", "le", "contains"), "value": SCALAR},
                        ("field", "op", "value")), "type": ["object", "null"]},
    "health": TEXT, "max_health": TEXT, "auto_bounds": B, "visible": B})
SELECTION = obj({"kind": r.enum("object", "memory", "method"), "address": ADDRESS, "label": TEXT,
    "image": TEXT, "namespace": TEXT, "class": TEXT, "method": TEXT,
    "token": integer(1, 2**32-1), "module": TEXT, "offset": ADDRESS, "occurrence": integer(1, 4096)}, ("kind", "address"))
PAGE = {"offset": integer(0, 4096), "limit": integer(1, 256)}
REQUEST_ID = integer(1, 2**53-1)
ARG = {"type": ["object", "string", "number", "boolean", "null"], "maxLength": 4096,
       "description": "Primitive, {enum:name}, {address:hex}, or {type:i64|u64|f32|string|bool|object|enum|null,value:...}. Use decimal text in typed i64/u64 for exact values."}
SESSION_ID = {"type": "string", "pattern": r"[1-9][0-9]{0,19}", "maxLength": 20}
DRAFT_VERSION = {"type": "string", "pattern": r"(?:0|[1-9][0-9]{0,19})", "maxLength": 20}
QUERY_FIELDS = {"name": {**TEXT, "maxLength": 96}, "image": {**TEXT, "maxLength": 256},
    "query": {**TEXT, "maxLength": 256}, "class_filter": {**TEXT, "maxLength": 256}, "kind": integer(0, 2)}
QUERY_CONFIG = obj({"format": r.enum("il2cpp-workbench-queries"), "schema": integer(1, 1),
    "tabs": array(obj(QUERY_FIELDS, tuple(QUERY_FIELDS)), 512, 1)}, ("format", "schema", "tabs"))
EXACT_METHOD = obj({"image": {**TEXT, "minLength": 1, "maxLength": 512}, "namespace": {**TEXT, "maxLength": 512},
    "class": {**TEXT, "minLength": 1, "maxLength": 512}, "token": integer(1, 2**32-1)}, ("image", "class", "token"))

TOOLS = [
    r.tool("workspace_browser", "Manage the manual IL2CPP search tabs without losing other searches. op=list/config/create/update/duplicate/remove/save/load/restore. Tab kind: 0 class, 1 method, 2 field. create accepts query fields; update needs the FULL tab from list, including decimal-string id/version for compare-and-swap. duplicate/remove use id; optional remove version. save/load require safe name<=48; restore requires config. Only pure queries persist; no object addresses, calls or hooks are replayed. Up to 512 tabs. Does not perform the search itself; use existing il2cpp search tools for results.", {"op": r.enum("list", "config", "create", "update", "duplicate", "remove", "save", "load", "restore"), "tab": obj({**QUERY_FIELDS, "id": SESSION_ID, "version": SESSION_ID}), "id": SESSION_ID, "version": SESSION_ID, "name": {**PRESET, "maxLength": 48}, "config": QUERY_CONFIG}, ("op",)),
    r.tool("workspace_caller", "Read per-method argument drafts and bounded call history shared with the manual caller, or set_draft/clear_history. Uses exact image/namespace/class/token; op=get first to obtain version (decimal string, 0 if absent). set_draft needs version and equal-length values/kinds arrays<=32: 0 numeric token, 1 text/enum/reference, 2 boolean, 3 null, 4 struct JSON. Draft input is plain text, not marshaled yet. Never executes a method; use existing il2cpp_call_exact explicitly. History retains latest 5 calls/method within global bounds; result_reference is required for managed-object navigation and addresses can expire. Drafts/history never persist across restart.", {"op": r.enum("get", "set_draft", "clear_history"), "method": EXACT_METHOD, "version": DRAFT_VERSION, "values": array({"type": "string", "maxLength": 4096}, 32), "kinds": array(integer(0, 4), 32)}, ("op", "method")),
    r.tool("overlay_upsert_window", "Create/patch an independent native ImGui window. parent='' is a root window; parent=another window ID nests a child window. Native collapse, drag, resize and close remain usable. Maximum 32 windows, depth 8; IDs shared with nodes. Omitted fields preserve existing settings.", {"descriptor": WINDOW}, ("descriptor",)),
    r.tool("overlay_upsert_node", "Create/patch an ImGui control/container. New nodes need id/window/type. Parents must be containers in the same window; tabs accepts only tab children. Types include table, tabs, child, tree, plot, progress, color and Java input. Object binding paths: name/address/position.x|y|z/enabled/field.<DeclaringType::field>. Missing/stale bindings disable controls. write defaults false; object writes only field paths, queued on bound game frame. Events support Lua logic. action executes only on human interaction; game actions require game_thread=true. Placeholders {value}, {value_hex}, {value_token}.", {"descriptor": NODE}, ("descriptor",)),
    r.tool("overlay_apply_tree", "Atomically patch multiple windows/nodes. Parents may follow children within this batch; final graph must be acyclic, depth <=8. Maximum 256 controls and 64 KiB JSON per request. Does not erase entries absent from the batch. Use this to create parent windows with tabs/tables and nested controls together.", {"tree": TREE}, ("tree",)),
    r.tool("overlay_get_tree", "Read all independent windows and nodes, including native position/collapse and manual value changes. Built-in pages are separate.", readonly=True),
    r.tool("overlay_remove_tree", "Remove a window or node and ALL descendant windows/controls. Leaves built-in UI, legacy panels and render objects unchanged.", {"kind": r.enum("window", "node"), "id": ID}, ("kind", "id")),
    r.tool("overlay_tree_events", "Read the latest 256 control events after a sequence without consuming them. Event includes node/name/value/time_ms; keep latest_sequence. Distinct from legacy overlay_ui_events.", {"after_sequence": integer(0, 2**53-1)}, readonly=True),
    r.tool("overlay_set_variable", "Set a shared UI binding variable by ID; up to 128 variables, <=4 KiB JSON each. Does not write target memory. Bind nodes with source=variable and matching id.", {"id": ID, "value": JSON_VALUE}, ("id", "value")),
    r.tool("overlay_program_set", "Create/patch a Lua UI program. Define function on_frame(ctx). ctx has objects, game_frame, time_ms, delta_ms, selection, variables, events and async results. ui.window/node/variable/remove and render.primitive/remove emit validated descriptors. Local IDs become program:<id>:<localId> (localId <=48 bytes); events return local node IDs. State persists until restart. No io/os/ffi/package/debug/JIT; 8 MiB, 100k Lua instructions, cooperative 12ms budget and 128 operations/frame. Errors stop only this program. New programs default stopped; enabled=true starts. runtime.call(command,args) requires explicit allow_game_actions=true and the frame queue allowlist. Presets always restore programs stopped and revoke this grant.", {"descriptor": PROGRAM}, ("descriptor",)),
    r.tool("overlay_program_list", "Read UI programs, running/error status, frame cost, memory and recent print logs. No source code returned.", readonly=True),
    r.tool("overlay_program_get", "Read a UI program including its Lua source and errors. UI programs use independent states, not lua_execute's global VM.", {"id": {**ID, "maxLength": 32}}, ("id",), readonly=True),
    r.tool("overlay_program_control", "Start/stop/restart a UI program. Start and restart create a fresh Lua state; stop retains last UI/graphics. Already accepted game-frame requests are not undone. Failed programs require an explicit start/restart.", {"id": {**ID, "maxLength": 32}, "operation": r.enum("start", "stop", "restart")}, ("id", "operation")),
    r.tool("overlay_program_remove", "Stop and remove one program definition. Keeps its last display; remove namespaced windows/nodes/primitives separately. Does not reverse game writes.", {"id": {**ID, "maxLength": 32}}, ("id",)),
    r.tool("render_set_rule", "Create/patch a render rule (up to 32): class/name/group/tag/layers, active-only, scalar condition, label templates, style overrides, field bindings, health/max_health and automatic Renderer/Collider bounds. Higher priority applies later. Alias bindings use field names; label {name}/{type}/{address}/{distance} plus bound aliases. Fields are sampled on every bound game frame, not the drawing thread. Unsupported components/bones/occlusion degrade independently.", {"descriptor": RULE}, ("descriptor",)),
    r.tool("render_list_rules", "Read rendering filters, live field recipes and appearance rules.", readonly=True),
    r.tool("render_remove_rule", "Remove one display rule; does not modify game objects or other rules.", {"id": ID}, ("id",)),
    r.tool("workspace_state", "Read UI selection, history, navigation_revision and frame binding. scope=global (default) is combined browsing history; object is the independent inspector path, unaffected by opening code.", {"scope": r.enum("global", "object")}, readonly=True),
    r.tool("workspace_navigate", "Navigate the manual workspace to an object inspector, method or memory address. Target data is queried only when its manual page requests it.", {"selection": SELECTION}, ("selection",)),
    r.tool("workspace_history", "Navigate history: supply direction back/forward OR index from workspace_state using the SAME scope (global default, or independent object path). Ancestor selection preserves forward entries until a new branch is opened. No game actions.", {"direction": r.enum("back", "forward"), "index": integer(0,63), "scope": r.enum("global", "object")}),
    r.tool("workspace_bookmark_set", "Bookmark a live selection. Presets keep only symbolic method/module+offset bookmarks; raw object pointers are deliberately not persisted across restart.", {"id": ID, "selection": SELECTION}, ("id", "selection")),
    r.tool("workspace_bookmark_remove", "Remove one workspace bookmark; does not change memory, hooks or display objects.", {"id": ID}, ("id",)),
    r.tool("workspace_bookmark_open", "Resolve and open a saved bookmark. Method recipes use image/type/metadata token; module recipes use module name/path, 1-based occurrence (default 1), and offset from module start. Checks the resolved address lies in an actual mapped region. Does not invoke the method or restore object identities.", {"id": ID}, ("id",)),
    r.tool("unity_hierarchy", "Queue scenes/roots/children/components/instances on the automatically bound game frame. target=scene index for roots, object address for children/components, CLASS metadata address for instances, 0 for scenes. Instance discovery is UnityEngine.Object only and does not add rendering. Returns pending/request_id; poll workspace_result. Unsupported scene or instance APIs affect only that query.", {"mode": r.enum("scenes", "roots", "children", "components", "instances"), "target": ADDRESS, **PAGE}, ("mode",)),
    r.tool("unity_object_resources", "Queue Unity resource inspection. mode=object (default): live address required; GameObject/Component -> components, Renderer -> shared materials (index is material_index), Material -> Shader/main texture, MeshFilter/SkinnedMeshRenderer -> shared Mesh. Texture/Mesh metadata only, no pixel/geometry dumps or clones; offset<=4096. mode=renderers: address=0 finds scene Renderers, or use a GameObject/Component subtree; query filters name/type, include_inactive defaults true. Scans <=256 candidates per request; continue at next_offset while has_more, even with no matches. Live results may change across pages; include_inactive reports effective support. Discovery does not add ESP objects or write materials. Missing APIs/types disable only that capability with reason/diagnostics. Poll workspace_result; export JSON with workspace_export_result.", {"address": ADDRESS, "mode": r.enum("object", "renderers"), "query": r.TEXT, "include_inactive": {"type": "boolean"}, "offset": integer(0, 10000000), "limit": integer(1, 128)}, ("address",)),
    r.tool("unity_material_properties", "Queue actual Shader property names/types/values for a Renderer material slot (0-based). Float/Range, Color, Vector, Texture, Integer where supported. Values identify shared_material vs property_block; effective_known=false means effective override cannot be read on this Unity version. last_written is history, not proof of current value. Includes writable/reason and has_saved_override. Shader globals and undeclared names are not editable. Poll workspace_result. Does not change rendering.", {"renderer": ADDRESS, "material_index": integer(0, 255), "offset": integer(0, 4096), "limit": integer(1, 128)}, ("renderer",)),
    r.tool("unity_material_set_property", "Queue a declared Shader property override on ONE Renderer/material slot using MaterialPropertyBlock, never shared Material writes. Query unity_material_properties first. value: finite number for Float/Range/Integer, four numbers for Color RGBA/Vector XYZW, live texture address string for Texture (dimension checked). No keyword, shader, global uniform or render-state injection. Effect depends on existing Shader and may be overwritten by the game; no per-frame reapplication. First write snapshots the slot for restore (max 64 slots). Unsupported API/property is disabled independently. Poll workspace_result and check result.applied; accepted is not applied.", {"renderer": ADDRESS, "material_index": integer(0, 255), "property": {**TEXT, "minLength": 1, "maxLength": 256}, "value": {"type": ["number", "string", "array"], "maxLength": 32, "minItems": 4, "maxItems": 4, "items": r.number(-1e20, 1e20)}}, ("renderer", "property", "value")),
    r.tool("unity_material_restore", "Queue restoration of the entire Renderer/material-slot property block captured before its first bridge edit; not an individual-property undo. This also replaces intervening game changes to that indexed block. If originally empty, clears the indexed block to reveal normal Renderer/material values. Refuses changed Material/Shader identities or expired objects. No persistent replay on restart. Poll workspace_result and check result.restored.", {"renderer": ADDRESS, "material_index": integer(0, 255)}, ("renderer",)),
    r.tool("il2cpp_inspector_members", "Queue inherited fields with values, exact methods and property accessors without calling getters. Either address inspects an object, or image/class/namespace selects a type with optional address for compatible instance values. Without an instance, type mode returns static values only. Includes type_info, reference_address and storage_address for navigation. Uses automatic frame binding; poll workspace_result.", {"address": ADDRESS, "image": TEXT, "namespace": TEXT, "class": ID, **PAGE}),
    r.tool("il2cpp_field_read", "Queue one instance/static field read using a live object. Field may be DeclaringType::name to disambiguate inheritance. Returns request_id; result includes exact value_text for 64-bit values.", {"address": ADDRESS, "field": TEXT}, ("address", "field")),
    r.tool("il2cpp_field_write", "Queue an explicit field write; rejects constants/readonly fields and incompatible references. Scalars use KittyMemory, managed references use IL2CPP GC write barriers. Supports enum names and typed arguments. Poll workspace_result for actual success/before/after; acceptance is not completion.", {"address": ADDRESS, "field": TEXT, "value": ARG}, ("address", "field", "value")),
    r.tool("il2cpp_call_exact", "Queue an exact overload by declaring image/namespace/class and metadata token (from inspector). Supports up to 32 typed arguments, enum names, references and static calls (instance=0). Invocation may change game state. Struct/byref support is limited by existing marshaler; unsupported signatures return an error. Result via workspace_result.", {"image": TEXT, "namespace": TEXT, "class": ID, "token": integer(1, 2**32-1), "instance": ADDRESS, "arguments": array(ARG, 32)}, ("image", "class", "token")),
    r.tool("il2cpp_dictionary_items", "Queue Dictionary enumeration as key/value pairs using its managed enumerator, not assumed layouts. Offset <=4096, limit <=128; generic stripping or missing accessors returns an isolated error. Poll workspace_result.", {"address": ADDRESS, **PAGE}, ("address",)),
    r.tool("workspace_result", "Fetch a queued frame request. Check pending first, then success/error/result. Only latest 32 requests retained; unexecuted requests expire after 5 seconds and never execute later.", {"request_id": REQUEST_ID}, ("request_id",), readonly=True),
    r.tool("workspace_export_result", "Export a completed queued result on the TARGET device to clipboard or private files/zygisk_il2cpp_mcp/exports. Returns success/path/bytes only, not the data. Clipboard <=256 KiB; file <=8 MiB. Does not read clipboard.", {"request_id": REQUEST_ID, "destination": DEST, "filename": FILENAME}, ("request_id", "destination", "filename")),
    r.tool("workspace_export_text", "Export supplied UTF-8 text to target clipboard/private exports. Filename is a safe stem plus supported extension, not a path. <=112 KiB per MCP request. Returns status/path only. For large cached inspector results use workspace_export_result.", {"text": {"type": "string", "maxLength": 112*1024}, "destination": DEST, "filename": FILENAME}, ("text", "destination", "filename")),
    r.tool("workspace_export_job", "Export the latest completed manual UI job by owner without transferring its contents through MCP. Useful for inspector/assembly/decompiler output; errors if owner has no completed successful result.", {"owner": {"type": "string", "maxLength": 128}, "destination": DEST, "filename": FILENAME}, ("owner", "destination", "filename")),
    r.tool("workspace_export_logs", "Export the target's current native call log, optionally filtering by text/failure/remote. Clipboard <=256 KiB; larger logs should use file. Returns success/path only, not the log contents. Does not clear the log.", {"destination": DEST, "filename": FILENAME, "filter": TEXT, "failures_only": B, "remote_only": B}, ("destination", "filename")),
    r.tool("workspace_preset", "Manage target-private UI/render presets: list, data, save, load or import. name uses ASCII safe stem; import accepts JSON text <=112 KiB and saves without applying. load checks target package+version, restores display/rules, stops Lua programs, and starts native logic only when auto_start=true. Runtime object handles and game writes are not restored. A preset named default auto-loads on next overlay startup. Storage files/zygisk_il2cpp_mcp/presets; save/load return status/path only. Symbolic bookmarks must be resolved again after restart.", {"operation": r.enum("list", "data", "save", "load", "import"), "name": PRESET, "json": {"type": "string", "maxLength": 112*1024}}, ("operation",)),
]
TYPE_SELECTION = obj({"image": TEXT, "namespace": TEXT, "class_name": TEXT}, ("image", "class_name"))
TOOLS.extend([
    r.tool("unity_resource_monitor", "Start/stop or read incremental loaded-resource observations on the game frame. op=start accepts type/query/interval_ms(500..60000)/max_candidates(64..65536). Uses periodic snapshot differences, NOT lossless load/unload hooks. Events: added/changed/no_longer_observed. Partial passes never emit disappearance. Events ring 512; use decimal-string after_sequence/next_sequence; check dropped_events/partial/error. clear clears event history, not the monitor. Never loads game assets. Poll workspace_result.", {"op": r.enum("start", "stop", "status", "events", "clear"), "type": ASSET_TYPE, "query": TEXT, "interval_ms": integer(500,60000), "max_candidates": integer(64,65536), "after_sequence": DRAFT_VERSION, "limit": integer(1,256)}, ("op",)),
    r.tool("il2cpp_generic_resolve", "Resolve a closed generic type and optionally a method via runtime reflection. type is the generic definition; type_arguments and method_arguments are 1..16 exact type selections. Optional token selects the method. Returns generic_handle for existing il2cpp_call_exact and il2cpp_parameter_schema. Handles are process-session scoped, at most 64. This does NOT JIT missing AOT instances: stripped APIs, unavailable instantiations or failed constraints disable only this operation. Poll workspace_result.", {"type": TYPE_SELECTION, "token": integer(1,2**32-1), "type_arguments": array(TYPE_SELECTION,16,1), "method_arguments": array(TYPE_SELECTION,16,1)}, ("type",)),
    r.tool("il2cpp_parameter_schema", "Describe exact method parameters, enum members, byref flags and recursively validated unmanaged struct fields (depth8/size1024). Use {fields:{...}} for struct values, {number:'...'} for exact integer struct fields; omitted fields are zero. Structs containing managed references are rejected. Existing il2cpp_call_exact handles invocation and returns arguments_after for ref/out. Optional generic_handle selects a closed method. Poll workspace_result.", {"image": TEXT, "namespace": TEXT, "class": TEXT, "token": integer(1,2**32-1), "generic_handle": SESSION_ID}, ("image","class","token")),
    r.tool("frida_control", "Optional embedded ARM64 Frida Gum, no GumJS/server required. op=status/list/events/attach/stalk/stop/stop_all. attach: executable function address, optional backtrace, duration_ms default60000; captures enter/leave, x0..x30/sp/pc/nzcv, integer/pointer args/return, accurate backtrace if available. stalk: target thread_id, call/return and optional blocks, duration default5000. All instrumentation expires within duration(100..600000ms). stop needs id. Events bounded1024, limit<=128, decimal-string cursors; check cursor_gap/dropped_contention. Counters may exceed retained snapshots. Not software breakpoints, no automatic target changes or JavaScript. Up to128 tasks per process session. Does not coexist with Dobby at the same entry address.", {"op": r.enum("status","attach","stalk","list","events","stop","stop_all"), "address": ADDRESS, "thread_id": integer(1,2**31-1), "duration_ms": integer(100,600000), "backtrace": B, "blocks": B, "id": integer(1,128), "after_sequence": DRAFT_VERSION, "limit": integer(1,128)}, ("op",)),
])
BY_NAME = {t["name"]: t for t in TOOLS}
BY_NAME["il2cpp_call_exact"]["inputSchema"]["properties"]["generic_handle"] = SESSION_ID
BY_NAME["il2cpp_call_exact"]["description"] += " Also supports unmanaged structs as {fields:{field:value}}, ref/out arguments with arguments_after in the result, and optional generic_handle from il2cpp_generic_resolve. No fabricated AOT methods."
BY_NAME["il2cpp_inspector_members"]["inputSchema"]["properties"]["return_handle"] = SESSION_ID
BY_NAME["il2cpp_inspector_members"]["description"] += (
    " Alternatively pass return_handle from il2cpp_call_exact: resolve a bounded session weak GC handle and inspect"
    " in the same game-frame request. Cannot combine with address/type selection. Collected/expired objects fail safely;")
BY_NAME["unity_object_resources"]["inputSchema"]["properties"].update({
    "mode": r.enum("object", "renderers", "assets"), "asset_type": ASSET_TYPE})
BY_NAME["unity_object_resources"]["description"] += (
    " mode=assets: address=0, asset_type required; enumerate already loaded Unity resources (including inactive/internal),"
    " filter names/types with query and continue next_offset. This is an on-demand, best-effort live view, not a stable snapshot"
    " or load/unload event stream. No asset loading or game writes; unavailable types return isolated diagnostics.")
for _tool in (
    r.tool("unity_resource_preview", "Queue a detached snapshot on the game frame. kind=texture(default): readable CPU pixels or Unity GPU Blit/ReadPixels for compressed/non-readable textures, RenderTexture and Sprite whole atlas. max_edge16..1024; source<=4M pixels. kind=model: Mesh/MeshFilter/SkinnedMeshRenderer geometry, <=131072 vertices/131072 triangles. Overlay shows independent orbit/zoom geometry window (no material shader). Latest one snapshot per kind. Returns metadata only, not pixels/geometry. GPU path uses temporary Unity objects and restores active render target; may stall. Missing APIs fail only this operation. Poll workspace_result.", {"address": ADDRESS, "kind": r.enum("texture","model"), "max_edge": integer(16,1024)}, ("address",)),
    r.tool("unity_resource_export", "Export a live resource to target-private files/zygisk_il2cpp_mcp/exports. kind=texture(default) produces PNG through built-in zlib encoder, independent of stripped EncodeToPNG. CPU/GPU readback; Sprite whole atlas. kind=model produces complete bounded OBJ local-space geometry (no materials/UV). Safe ASCII name stem, no path or extension. Returns success/path/bytes only, not file data. Poll workspace_result; queue acceptance is not completion.", {"address": ADDRESS, "kind": r.enum("texture","model"), "name": PRESET}, ("address",)),
):
    TOOLS.append(_tool)
    BY_NAME[_tool["name"]] = _tool
for _name in ("il2cpp_field_write", "il2cpp_call_exact", "unity_material_set_property", "unity_material_restore"):
    BY_NAME[_name]["annotations"]["destructiveHint"] = True

SIMPLE = {"overlay_get_tree": "UI_TREE_GET", "overlay_program_list": "UI_PROGRAM_LIST",
          "render_list_rules": "RENDER_RULES", "workspace_state": "WORKSPACE_STATE"}
DESCRIPTORS = {"overlay_upsert_window": ("UI_WINDOW_SET", "descriptor"),
    "overlay_upsert_node": ("UI_NODE_SET", "descriptor"), "overlay_apply_tree": ("UI_TREE_APPLY", "tree"),
    "overlay_program_set": ("UI_PROGRAM_SET", "descriptor"), "render_set_rule": ("RENDER_RULE_SET", "descriptor")}
OBJECT_FEATURES = ("il2cpp_metadata", "il2cpp_objects", "il2cpp_invoke")
GAME_WRITE_FEATURES = (*OBJECT_FEATURES, "memory_write")

def _json(value: Any, limit: int = 65536) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > limit:
        raise ValueError(f"JSON exceeds {limit} UTF-8 bytes; split the operation")
    return r.text(raw)

def _node(value: dict) -> None:
    action = value.get("action")
    if action:
        if any(c in action["arguments"] for c in "\r\n"):
            raise ValueError("action arguments must be a single line")
        if action["command"] in GAME_ACTIONS and not action.get("game_thread"):
            raise ValueError("game actions require game_thread=true")
        if action.get("game_thread") and action["command"] not in GAME_ACTIONS:
            raise ValueError("unsupported game-thread action")
    binding = value.get("binding")
    if binding and binding["source"] == "object":
        if not binding.get("id") or not binding.get("path"):
            raise ValueError("object binding requires id/path")
        if binding.get("write") and not binding["path"].startswith("field."):
            raise ValueError("object writes require a field. path")
    if value.get("type") in {"slider", "progress"} and "min" in value and "max" in value and value["min"] >= value["max"]:
        raise ValueError("max must exceed min")

def _bounded_json(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("JSON nesting exceeds 12")
    if isinstance(value, dict):
        if len(value) > 256:
            raise ValueError("too many JSON object members")
        for key, child in value.items():
            if not isinstance(key, str) or len(key.encode("utf-8")) > 256 or "\0" in key:
                raise ValueError("invalid JSON key")
            _bounded_json(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 512:
            raise ValueError("too many JSON array entries")
        for child in value:
            _bounded_json(child, depth + 1)
    elif isinstance(value, str) and "\0" in value:
        raise ValueError("NUL is not supported")

def encode(name: str, args: dict, invoke: Callable[[Any], str] | None = None) -> str:
    if name not in BY_NAME or not isinstance(args, dict):
        raise ValueError("unknown workspace tool or invalid arguments")
    r.validate(args, BY_NAME[name]["inputSchema"], name)
    if name in {"workspace_browser", "workspace_caller"}:
        data = dict(args)
        operation = args["op"]
        if name == "workspace_browser":
            fields = {"list": {"op"}, "config": {"op"}, "create": {"op", "tab"}, "update": {"op", "tab"},
                      "duplicate": {"op", "id"}, "remove": {"op", "id", "version"}, "save": {"op", "name"},
                      "load": {"op", "name"}, "restore": {"op", "config"}}[operation]
            required = fields - ({"version"} if operation == "remove" else set())
            if set(args) - fields or required - set(args):
                raise ValueError("fields do not match browser operation")
            if operation == "update" and set(args["tab"]) != {*QUERY_FIELDS, "id", "version"}:
                raise ValueError("update requires the complete tab and its current id/version")
            if operation == "create" and ({"id", "version"} & set(args["tab"])):
                raise ValueError("create cannot specify an existing tab identity")
            command = "WORKSPACE_BROWSER"
        else:
            required = {"op", "method"} | ({"version", "values", "kinds"} if operation == "set_draft" else set())
            if set(args) != required:
                raise ValueError("fields do not match caller operation")
            if operation == "set_draft" and len(args["values"]) != len(args["kinds"]):
                raise ValueError("draft values/kinds must have equal length")
            data["method"] = {"namespace": "", **args["method"]}
            command = "WORKSPACE_CALLER"
        _bounded_json(data)
        return command + " " + _json(data, 4*1024*1024 if name == "workspace_browser" else 65536)
    if name == "workspace_state":
        return "WORKSPACE_STATE" + (" " + args["scope"] if "scope" in args else "")
    if name in SIMPLE:
        return SIMPLE[name]
    if name in DESCRIPTORS:
        command, key = DESCRIPTORS[name]
        data = args[key]
        if name == "overlay_upsert_node":
            _node(data)
        if name == "overlay_apply_tree":
            for node in data.get("nodes", []):
                _node(node)
        if name == "overlay_program_set" and data.get("source", "").startswith("\x1b"):
            raise ValueError("compiled Lua bytecode is not accepted")
        if name == "render_set_rule":
            bindings = data.get("bindings", {})
            if len(bindings) > 32:
                raise ValueError("maximum 32 field bindings per rule")
            for alias, field in bindings.items():
                r.validate(alias, {**ID, "maxLength": 64}, "binding alias")
                r.validate(field, {**TEXT, "minLength": 1}, "bound field")
        return command + " " + _json(data, 80*1024 if name == "overlay_program_set" else 65536)
    if name == "overlay_remove_tree":
        return f"UI_TREE_REMOVE {args['kind']} {r.text(args['id'])}"
    if name == "overlay_tree_events":
        return f"UI_TREE_EVENTS {args.get('after_sequence', 0)}"
    if name == "overlay_set_variable":
        _bounded_json(args["value"])
        _json(args["value"], 4096)
        return "UI_VARIABLE_SET " + _json(args)
    if name in {"overlay_program_get", "overlay_program_remove", "render_remove_rule", "workspace_bookmark_remove", "workspace_bookmark_open"}:
        command = {"overlay_program_get": "UI_PROGRAM_GET", "overlay_program_remove": "UI_PROGRAM_REMOVE",
                   "render_remove_rule": "RENDER_RULE_REMOVE", "workspace_bookmark_remove": "WORKSPACE_BOOKMARK_REMOVE", "workspace_bookmark_open": "WORKSPACE_BOOKMARK_OPEN"}[name]
        return command + " " + r.text(args["id"])
    if name == "overlay_program_control":
        return f"UI_PROGRAM_CONTROL {r.text(args['id'])} {args['operation']}"
    if name in {"workspace_navigate", "workspace_bookmark_set"}:
        selection = dict(args["selection"])
        selection["address"] = r.address(selection["address"])
        if selection["address"] == "0x0":
            raise ValueError("selection needs a nonzero address")
        if name == "workspace_navigate":
            return "WORKSPACE_NAVIGATE " + _json(selection, 4096)
        return f"WORKSPACE_BOOKMARK_SET {r.text(args['id'])} {_json(selection, 4096)}"
    if name == "workspace_history":
        if ("direction" in args) == ("index" in args):
            raise ValueError("Supply exactly one of direction or index")
        return "WORKSPACE_HISTORY " + str(args.get("direction", args.get("index"))) + (" " + args["scope"] if "scope" in args else "")
    if name == "workspace_result":
        return f"WORKSPACE_RESULT {args['request_id']}"
    if name == "workspace_export_logs":
        return " ".join(["WORKSPACE_EXPORT_LOG", args["destination"], r.text(args["filename"]), r.text(args.get("filter", "")),
                         r.token(args.get("failures_only", False)), r.token(args.get("remote_only", False))])
    if name.startswith("workspace_export_"):
        head = {"workspace_export_result": ("WORKSPACE_EXPORT_RESULT", str(args.get("request_id", ""))),
                "workspace_export_text": ("WORKSPACE_EXPORT_TEXT", ""),
                "workspace_export_job": ("WORKSPACE_EXPORT_JOB", r.text(args.get("owner", "")))}[name]
        words = [head[0]] + ([head[1]] if head[1] else []) + [args["destination"], r.text(args["filename"])]
        if name == "workspace_export_text":
            words.append(r.text(args["text"]))
        return " ".join(words)
    if name == "workspace_preset":
        operation = args["operation"]
        if operation in {"list", "data"}:
            if len(args) != 1:
                raise ValueError("list/data accept only operation")
            return "WORKSPACE_PRESET_" + operation.upper()
        if "name" not in args or (operation == "import") != ("json" in args):
            raise ValueError("save/load need name; import needs name and json")
        words = ["WORKSPACE_PRESET_" + operation.upper(), r.text(args["name"])]
        if operation == "import":
            parsed = json.loads(args["json"])
            if not isinstance(parsed, dict):
                raise ValueError("preset must be a JSON object")
            _bounded_json(parsed)
            words.append(_json(parsed, 112*1024))
        return " ".join(words)
    frame = ""
    page = f"{args.get('offset', 0)} {args.get('limit', 32)}"
    if name == "frida_control":
        op = args["op"]
        allowed = {"status":{"op"}, "list":{"op"}, "stop_all":{"op"}, "stop":{"op","id"},
                   "events":{"op","after_sequence","limit"}, "attach":{"op","address","duration_ms","backtrace"},
                   "stalk":{"op","thread_id","duration_ms","blocks"}}[op]
        if set(args)-allowed or (op=="stop" and "id" not in args) or (op=="stalk" and "thread_id" not in args):
            raise ValueError("invalid fields for Frida operation")
        options = dict(args)
        if op == "attach":
            options["address"] = r.address(args.get("address", "0"))
            if int(options["address"],16)==0 or int(options["address"],16)%4:
                raise ValueError("Frida requires nonzero aligned ARM64 function address")
        return "FRIDA_CONTROL " + _json(options,8192)
    if name == "unity_resource_monitor":
        allowed = {"start":{"op","type","query","interval_ms","max_candidates"}, "stop":{"op"}, "status":{"op"}, "clear":{"op"}, "events":{"op","after_sequence","limit"}}[args["op"]]
        if set(args)-allowed: raise ValueError("invalid fields for monitor operation")
        frame = "UNITY_ASSET_MONITOR " + _json(args,8192)
    elif name == "il2cpp_generic_resolve":
        if "method_arguments" in args and "token" not in args: raise ValueError("method_arguments needs token")
        frame = "IL2CPP_GENERIC_RESOLVE " + _json(args,8192)
    elif name == "il2cpp_parameter_schema":
        frame = " ".join(["IL2CPP_PARAMETER_SCHEMA",r.text(args["image"]),r.text(args.get("namespace","")),r.text(args["class"]),str(args["token"])])
        if "generic_handle" in args: frame += " g" + args["generic_handle"]
    elif name in {"unity_resource_preview", "unity_resource_export"}:
        target = r.address(args["address"])
        if target == "0x0":
            raise ValueError("a live resource address is required")
        if args.get("kind") == "model":
            if "max_edge" in args: raise ValueError("max_edge applies only to textures")
            frame = f"UNITY_MODEL_SNAPSHOT {target}" if name == "unity_resource_preview" else f"UNITY_MODEL_EXPORT {target} {r.text(args.get('name','mesh'))}"
        elif name == "unity_resource_preview":
            frame = f"UNITY_ASSET_SNAPSHOT {target} {args.get('max_edge', 512)}"
        else:
            frame = f"UNITY_ASSET_EXPORT {target} png {r.text(args.get('name', 'texture'))}"
    elif name == "unity_object_resources" and args.get("mode") == "assets":
        if "asset_type" not in args or r.address(args["address"]) != "0x0" or "include_inactive" in args:
            raise ValueError("assets mode requires address=0 and asset_type; include_inactive is not supported")
        frame = f"UNITY_ASSETS_LIST {r.text(args['asset_type'])} {r.text(args.get('query', ''))} {page}"
    elif name == "unity_object_resources" or name.startswith("unity_material_"):
        if "asset_type" in args:
            raise ValueError("asset_type is only supported in assets mode")
        target = r.address(args["address" if name == "unity_object_resources" else "renderer"])
        discovery = name == "unity_object_resources" and args.get("mode", "object") == "renderers"
        if target == "0x0" and not discovery:
            raise ValueError("a live object/renderer address is required")
        if name == "unity_object_resources":
            if discovery:
                frame = f"UNITY_RENDERERS {target} {r.text(args.get('query', ''))} {str(args.get('include_inactive', True)).lower()} {page}"
            else:
                if args.get("offset", 0) > 4096 or any(k in args for k in ("query", "include_inactive")):
                    raise ValueError("object mode requires offset<=4096 and does not accept renderer discovery filters")
                frame = f"UNITY_OBJECT_RESOURCES {target} {page}"
        else:
            head = f"{target} {args.get('material_index', 0)}"
            if name == "unity_material_properties":
                frame = f"UNITY_MATERIAL_PROPERTIES {head} {page}"
            elif name == "unity_material_restore":
                frame = f"UNITY_MATERIAL_RESTORE {head}"
            else:
                value = args["value"]
                if isinstance(value, str):
                    value = r.address(value)
                    if value == "0x0":
                        raise ValueError("Texture requires a live nonzero address")
                elif isinstance(value, (float, int)):
                    r.validate(value, r.number(-1e20, 1e20), "value")
                frame = f"UNITY_MATERIAL_SET {head} {r.text(args['property'])} {_json(value, 4096)}"
    elif name == "unity_hierarchy":
        target = r.address(args.get("target", "0"))
        if args["mode"] in {"children", "components", "instances"} and target == "0x0":
            raise ValueError("children/components require a target address")
        # Scene indices are parsed as decimal by the native hierarchy query.
        if args["mode"] in {"scenes", "roots"}:
            target = str(int(target, 16))
        frame = f"UNITY_HIERARCHY {args['mode']} {target} {page}"
    elif name == "il2cpp_inspector_members" and "return_handle" in args:
        if any(k in args for k in ("address", "image", "namespace", "class")):
            raise ValueError("return_handle cannot be combined with address or type selection")
        frame = f"IL2CPP_RETURN_MEMBERS {args['return_handle']} {page}"
    elif name in {"il2cpp_inspector_members", "il2cpp_dictionary_items"}:
        typed = name == "il2cpp_inspector_members" and any(k in args for k in ("image", "namespace", "class"))
        if typed:
            if not args.get("image") or not args.get("class"):
                raise ValueError("type inspection requires image and class")
            frame = f"IL2CPP_TYPE_MEMBERS {r.text(args['image'])} {r.text(args.get('namespace', ''))} {r.text(args['class'])} {r.address(args.get('address', '0'))} {page}"
        else:
            if "address" not in args or r.address(args["address"]) == "0x0":
                raise ValueError("object inspection requires a nonzero address")
            command = "IL2CPP_OBJECT_MEMBERS" if name == "il2cpp_inspector_members" else "IL2CPP_DICTIONARY_ITEMS"
            frame = f"{command} {r.address(args['address'])} {page}"
    elif name in {"il2cpp_field_read", "il2cpp_field_write"}:
        if not args["field"]:
            raise ValueError("field is required")
        command = "IL2CPP_FIELD_GET" if name == "il2cpp_field_read" else "IL2CPP_FIELD_SET"
        frame = f"{command} {r.address(args['address'])} {r.text(args['field'])}"
        if name == "il2cpp_field_write":
            if invoke is None:
                raise ValueError("invocation encoder unavailable")
            frame += " " + invoke(args["value"])
    elif name == "il2cpp_call_exact":
        if not args["image"] or invoke is None:
            raise ValueError("image and invocation encoder required")
        values = args.get("arguments", [])
        frame = " ".join(["IL2CPP_CALL_EXACT", r.text(args["image"]), r.text(args.get("namespace", "")),
            r.text(args["class"]), str(args["token"]), r.address(args.get("instance", "0")), str(len(values)), *map(invoke, values)])
        if "generic_handle" in args: frame += " g" + args["generic_handle"]
    if not frame:
        raise ValueError(f"unimplemented workspace encoder {name}")
    command, arguments = frame.split(" ", 1)
    if len(arguments.encode("utf-8")) > 65536:
        raise ValueError("frame query exceeds 64 KiB")
    return f"WORKSPACE_QUERY {command} {r.text(arguments)}"

def features(name: str) -> tuple[str, ...]:
    if name == "frida_control": return ("frida", "trace")
    if name == "unity_resource_monitor": return ("rendering", *OBJECT_FEATURES)
    if name in {"unity_material_set_property", "unity_material_restore"}:
        return ("rendering", *GAME_WRITE_FEATURES)
    if name in {"unity_object_resources", "unity_material_properties", "unity_resource_preview", "unity_resource_export"}:
        return ("rendering", *OBJECT_FEATURES)
    if name == "workspace_bookmark_open":
        return ("overlay_ui", "il2cpp_metadata", "memory_maps")
    if name.startswith("overlay_program_"):
        return ("overlay_ui", "lua", "rendering")
    if name.startswith("overlay_"):
        return ("overlay_ui",)
    if name.startswith("render_"):
        return ("rendering",)
    if name == "il2cpp_field_write":
        return GAME_WRITE_FEATURES
    if name.startswith("il2cpp_") or name == "unity_hierarchy":
        return OBJECT_FEATURES
    if name.startswith("workspace_export_"):
        return ("overlay_ui", "ui")
    if name == "workspace_preset":
        return ("overlay_ui", "rendering")
    return ("overlay_ui",)

def extra_features(name: str, args: dict) -> set[str]:
    result: set[str] = set()
    nodes = [args.get("descriptor", {})] if name == "overlay_upsert_node" else args.get("tree", {}).get("nodes", []) if name == "overlay_apply_tree" else []
    for node in nodes:
        binding, action = node.get("binding") or {}, node.get("action") or {}
        if binding.get("source") == "object":
            result.update(OBJECT_FEATURES)
            if binding.get("write"):
                result.add("memory_write")
        elif binding.get("source") == "render_style":
            result.add("rendering")
        if action:
            result.update(native_features(action["command"]) or r.native_features(action["command"]))
    if name == "render_set_rule":
        data = args.get("descriptor", {})
        if any(data.get(k) for k in ("bindings", "condition", "auto_bounds", "health", "max_health", "class_contains", "tag", "layers")):
            result.update(OBJECT_FEATURES)
    if name == "overlay_program_set" and args.get("descriptor", {}).get("allow_game_actions"):
        result.update(GAME_WRITE_FEATURES)
    return result

def native_features(command: str) -> tuple[str, ...] | None:
    if command == "IL2CPP_RENDER_INVENTORY": return ("rendering", *OBJECT_FEATURES)
    if command == "FRIDA_CONTROL": return ("frida", "trace")
    if command in {"UNITY_MODEL_SNAPSHOT","UNITY_MODEL_EXPORT","UNITY_ASSET_MONITOR"}: return ("rendering", *OBJECT_FEATURES)
    if command in {"IL2CPP_GENERIC_RESOLVE","IL2CPP_PARAMETER_SCHEMA"}: return OBJECT_FEATURES
    if command in {"UNITY_MATERIAL_SET", "UNITY_MATERIAL_RESTORE"}:
        return ("rendering", *GAME_WRITE_FEATURES)
    if command in {"UNITY_RENDERERS", "UNITY_OBJECT_RESOURCES", "UNITY_MATERIAL_PROPERTIES", "UNITY_ASSETS_LIST", "UNITY_ASSET_SNAPSHOT", "UNITY_ASSET_EXPORT"}:
        return ("rendering", *OBJECT_FEATURES)
    if command == "WORKSPACE_BOOKMARK_OPEN":
        return features("workspace_bookmark_open")
    if command == "RENDER_RULE_SET":
        return ("rendering", *OBJECT_FEATURES)
    if command == "IL2CPP_FIELD_SET":
        return GAME_WRITE_FEATURES
    if command in {"IL2CPP_FIELD_GET", "IL2CPP_CALL_EXACT", "IL2CPP_OBJECT_MEMBERS", "IL2CPP_RETURN_MEMBERS", "IL2CPP_TYPE_MEMBERS", "IL2CPP_DICTIONARY_ITEMS", "UNITY_HIERARCHY"}:
        return OBJECT_FEATURES
    if command.startswith("UI_PROGRAM_"):
        return ("overlay_ui", "rendering", "lua", *GAME_WRITE_FEATURES) if command in {"UI_PROGRAM_SET", "UI_PROGRAM_CONTROL"} else ("overlay_ui", "rendering", "lua")
    if command in {"UI_NODE_SET", "UI_TREE_APPLY"}:
        return ("overlay_ui", "rendering", *GAME_WRITE_FEATURES) # Raw descriptors can embed bindings/actions.
    if command.startswith("UI_TREE_") or command in {"UI_WINDOW_SET", "UI_VARIABLE_SET"}:
        return ("overlay_ui",)
    if command == "WORKSPACE_QUERY":
        # Raw envelopes can contain relationship/snapshot readers as well as
        # game-frame writes. Keep the escape hatch conservatively gated.
        return ("overlay_ui", "rendering", "memory_read", "memory_maps", "pointer_chain", *GAME_WRITE_FEATURES)
    if command.startswith("WORKSPACE_EXPORT_"):
        return ("overlay_ui", "ui")
    if command.startswith("WORKSPACE_PRESET_"):
        return ("overlay_ui", "rendering")
    if command.startswith("WORKSPACE_"):
        return ("overlay_ui",)
    return None
