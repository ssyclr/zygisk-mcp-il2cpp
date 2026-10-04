"""Render/UI tool catalog and strict, dependency-free command encoding.

Keeping schemas and encoders here makes new controls reusable by exported MCP.zip.
No target-side Python, JavaScript injection, or arbitrary UI code evaluation.
"""
from __future__ import annotations

import math
import json
import re
from typing import Any


def enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def number(low: float, high: float) -> dict[str, Any]:
    return {"type": "number", "minimum": low, "maximum": high}


TEXT = {"type": "string", "maxLength": 256}
ID = {"type": "string", "minLength": 1, "maxLength": 96}
ADDRESS = {"type": ["string", "integer"], "description": "Hexadecimal or decimal address; 0 selects a manual object."}
VEC3 = {"type": "array", "items": number(-1e7, 1e7), "minItems": 3, "maxItems": 3}
COLOR = {"type": "string", "minLength": 8, "maxLength": 8, "pattern": "^[0-9a-fA-F]{8}$", "description": "Eight hexadecimal RRGGBBAA digits; object 00000000 inherits the global color."}
STYLE_FIELDS = {
    **{key: {"type": "boolean"} for key in ("enabled", "lines", "names", "distance", "count", "bones")},
    "box": enum("none", "2d", "corner", "3d"),
    "line_origin": enum("top", "center", "bottom"),
    "color": COLOR,
    "thickness": number(.5, 10), "text_size": number(10, 64),
    "max_distance": number(1, 1e7), "sample_hz": number(1, 30),

}
UI_FIELDS = {
    "language": enum("zh", "en"), "theme": enum("classic", "dark", "light"),
    "visible": {"type": "boolean"}, "collapsed": {"type": "boolean"},
    "scale": number(.5, 2), "alpha": number(.2, 1),
    "page_rows": {"type": "integer", "minimum": 1, "maximum": 100000},
    **{key: {"type": "boolean"} for key in ("mcp_log", "trace_log", "breakpoint_log", "browser_split")},
}
OBJECT_FIELDS = {
    "enabled": {"type": "boolean"}, "label": TEXT, "color": COLOR,
    **{key: number(.001, 10000) for key in ("height", "width", "depth")},
}


def tool(name: str, description: str, properties: dict | None = None,
         required: tuple[str, ...] = (), *, readonly: bool = False) -> dict:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": readonly, "destructiveHint": False, "openWorldHint": False}}


def setting_properties(fields: dict) -> dict:
    return {"key": enum(*fields), "value": {"type": ["string", "number", "boolean"],
            "description": "Must match the chosen key's type/range. See tool description or debug_help."}}


def descriptor(fields: dict) -> dict:
    return {"type": "object", "properties": {"id": ID, **fields},
            "required": ["id"], "additionalProperties": False}


COMMON = {"visible": {"type": "boolean"}, "order": number(-1000, 1000)}
PANEL = descriptor({**COMMON, "title": TEXT})
UI_ACTIONS = (
    "OVERLAY_SET", "OVERLAY_WINDOW", "OVERLAY_RESET",
    "RENDER_STYLE", "RENDER_CAMERA", "RENDER_MATRIX", "RENDER_OBJECT_ADD", "RENDER_OBJECT_SET",
    "RENDER_OBJECT_REMOVE", "RENDER_OBJECT_POSITION", "RENDER_OBJECT_BONES", "RENDER_CLEAR",
    "RENDER_FIND_OBJECTS", "RENDER_TRACK_CLASS", "RENDER_UNTRACK_CLASS", "RENDER_REFRESH_CLASS",
    "RENDER_BIND_UPDATE", "RENDER_UNBIND_UPDATE", "RENDER_REFRESH_CAMERAS",
)
ACTION = {"type": ["object", "null"], "properties": {
    "command": enum(*UI_ACTIONS), "arguments": {"type": "string", "maxLength": 4096}},
    "required": ["command", "arguments"], "additionalProperties": False}
WIDGET = descriptor({
    **COMMON, "panel": ID, "type": enum("text", "button", "toggle", "slider", "select", "input"),
    "label": TEXT, "text": {"type": "string", "maxLength": 4096},
    "value": {"type": ["number", "boolean", "string"], "maxLength": 4096},
    "min": number(-1e7, 1e7), "max": number(-1e7, 1e7),
    "options": {"type": "array", "minItems": 1, "maxItems": 32, "items": {**TEXT, "minLength": 1}},
    "disabled": {"type": "boolean"}, "color": COLOR, "action": ACTION,
})
PRIMITIVE = descriptor({
    **COMMON, "type": enum("text", "line", "polyline", "rect", "circle", "triangle", "quad", "polygon", "bezier", "mesh"),
    "space": enum("screen", "world"),
    "points": {"type": "array", "minItems": 1, "maxItems": 64, "items": VEC3},
    "text": {"type": "string", "maxLength": 4096}, "color": COLOR,
    "thickness": number(.5, 10), "font_size": number(10, 96), "radius": number(1, 4096),
    "filled": {"type": "boolean"}, "closed": {"type": "boolean"},
    "foreground": {"type": "boolean"},
    "indices": {"type": "array", "minItems": 3, "maxItems": 384, "items": {"type": "integer", "minimum": 0, "maximum": 63}},
    "clip_rect": {"type": "array", "minItems": 4, "maxItems": 4, "items": number(0, 1)},
})


TOOLS = [
    tool("overlay_status", "Read shared ImGui UI state. Drawing uses the Java SurfaceView. Newer targets report renderer.touch_events, captured_touch_events, rendered_frames and last_frame_age_ms: compare two samples to distinguish missing input from a stalled renderer. input_route identifies window_callback or java_input_windows (NativeActivity); input_windows and input_error diagnose the independent Java touch windows. These counters contain no touch coordinates or input text.", readonly=True),
    tool("overlay_set", "Set language zh/en, theme classic/dark/light, visible/collapsed boolean, scale .5..2, alpha .2..1, page_rows 1..100000 (large memory pages load visible rows on demand; other remote views use bounded batches), browser_split for side-by-side class browsing, or mcp_log/trace_log/breakpoint_log booleans for transparent on-screen logs. Settings auto-save/load privately. Does not hide world-space object rendering or override the WebUI startup injection switch.", setting_properties(UI_FIELDS), ("key", "value")),
    tool("overlay_set_window", "Move/resize native ImGui window in SurfaceView pixels; negative/off-screen positions are allowed without forced display clamping. Native titlebar collapse and resize remain usable.", {"x": number(-32768, 32768), "y": number(-32768, 32768), "width": number(200, 32768), "height": number(200, 32768)}, ("x", "y", "width", "height")),
    tool("overlay_reset", "Restore visible Chinese ImGui Classic purple-theme defaults; does not delete render objects."),
    tool("render_status", "Read styles, camera freshness, optional bones capabilities, queued discovery progress and errors. Missing Unity support never disables manual geometry or ImGui.", readonly=True),
    tool("render_set_style", "Set global render appearance: lines/names/distance/count/bones, box none/2d/corner/3d, line_origin top/center/bottom, color RRGGBBAA, thickness .5..10, text_size 10..64, max_distance 1..1e7. Objects update every game frame; legacy sample_hz is ignored. Occlusion sampling has been removed.", setting_properties(STYLE_FIELDS), ("key", "value")),
    tool("render_list_objects", "Page registered render-only objects, dimensions, sampled positions/age and bone segments. Default offset=0/limit=256; returns total/returned/has_more. No fixed object capacity; the limit is per response only. Does not enumerate all game objects; use render_find_objects first.", {"offset":{"type":"integer","minimum":0,"maximum":2147483647},"limit":{"type":"integer","minimum":1,"maximum":256}}, readonly=True),
    tool("render_add_object", "Add one visualization with a unique ID. address=0 uses a manual world position and supports non-IL2CPP games with render_set_camera_matrix. A UnityEngine.Object address requires render_bind_update to sample its transform. Boxes are world-axis-aligned with the supplied height/width/depth; does not modify the game.", {"id": ID, "address": ADDRESS, "label": TEXT, "position": VEC3, "height": number(.001, 10000), "width": number(.001, 10000), "depth": number(.001, 10000)}, ("id",)),
    tool("render_update_object", "Change a visualization's enabled flag, UTF-8 label, RRGGBBAA color (00000000 inherits), or positive height/width/depth; never writes the game object.", {"id": ID, **setting_properties(OBJECT_FIELDS)}, ("id", "key", "value")),
    tool("render_set_object_position", "Update a MANUAL object's world position. Live Unity objects are sampled and cannot be moved through this tool.", {"id": ID, "position": VEC3}, ("id", "position")),
    tool("render_set_object_bones", "Replace a manual object's world-space line segments (up to 64). Each segment is [fromXYZ,toXYZ]. Empty array clears bones; Unity humanoid bones are collected automatically if available.", {"id": ID, "segments": {"type": "array", "maxItems": 64, "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": VEC3}}}, ("id", "segments")),
    tool("render_remove_object", "Remove one render entry only, never destroys a game object.", {"id": ID}, ("id",)),
    tool("render_clear_objects", "Clear visualized objects AND automatic tracked classes; does not change game objects, primitives or the frame hook."),
    tool("render_set_camera", "Select Unity main/current/custom-address camera. Requires explicit frame binding; Camera.current may be null in Update, so main is preferred. Manual matrices use render_set_camera_matrix instead.", {"mode": enum("main", "current", "address"), "address": ADDRESS}, ("mode",)),
    tool("render_list_cameras", "Read up to 64 camera addresses cached by the bound Unity game frame (refreshed about every two seconds); choose an address via render_set_camera.", readonly=True),
    tool("render_set_camera_matrix", "Set a manual camera for any target, no Unity or EGL hook. matrix is 16 COLUMN-MAJOR floats of projection * worldToCamera; camera_position controls distance labels; viewport is normalized bottom-left [x,y,width,height], default [0,0,1,1]. Positive clip W faces the camera.", {"matrix": {"type": "array", "minItems": 16, "maxItems": 16, "items": number(-1e7, 1e7)}, "camera_position": VEC3, "viewport": {"type": "array", "minItems": 4, "maxItems": 4, "items": number(0, 1)}}, ("matrix",)),
    tool("render_project", "Project a world point to top-left pixels. Matrix cameras return immediately. WorldToScreenPoint fallback queues a game-frame query and returns pending/request_id; fetch render_projection_result. Never invokes Unity from an MCP thread.", {"position": VEC3, "surface_width": number(1, 32768), "surface_height": number(1, 32768)}, ("position", "surface_width", "surface_height"), readonly=True),
    tool("render_bind_update", "Explicitly Dobby-instrument a known, executing IL2CPP MonoBehaviour zero-argument void LateUpdate (preferred), Update or FixedUpdate. Sampling runs at its entry on the game thread, NOT in Java/GLES renderer. Uses weak handles and per-game-frame sampling (Time.frameCount deduplication when available); unavailable APIs return errors without affecting other tools. Only one binding; unbind before changing it.", {"image": TEXT, "namespace": TEXT, "class": ID, "method": enum("LateUpdate", "Update", "FixedUpdate")}, ("image", "class", "method")),
    tool("render_unbind_update", "Remove only the render-owned game-frame hook, release weak handles and expire live camera data. Manual objects/UI keep working."),
    tool("render_binding_status", "Read render frame hook address, sampling thread ID and last sample age. bound=true with no thread means the chosen method has not executed yet.", readonly=True),
    tool("render_find_objects", "Queue Unity FindObjectsOfType for a class; limit=0 (default) means all matches. include_inactive defaults true, matching the IL2CPP instance browser; older Unity may support active objects only. Requires frame binding, not a camera or enabled rendering. Returns acceptance, NOT completion; poll render_status.discovery_pending/discovery_error, then render_list_objects. Discovery allocates an array; large populations can be costly.", {"image": TEXT, "namespace": TEXT, "class": ID, "limit": {"type": "integer", "minimum": 0, "maximum": 2147483647}, "include_inactive": {"type": "boolean", "default": True}}, ("image", "class")),
    tool("render_track_class", "Add/update continuous class discovery. Requires frame binding. Up to 16 classes; no fixed object capacity. limit=0 (default) means all matches. include_inactive defaults true, matching the IL2CPP instance browser. One class is rediscovered each game frame in rotation; legacy refresh_ms is accepted but ignored. Discovery preserves checkbox selections; select_new controls new objects. Returns acceptance; inspect render_list_tracked_classes for count_valid/pending/found/error and include_inactive_effective. IDs cannot be reassigned to a different class without untracking.", {
        "id": ID, "image": TEXT, "namespace": TEXT, "class": ID,
        "limit": {"type": "integer", "minimum": 0, "maximum": 2147483647},
        "include_inactive": {"type": "boolean", "default": True}, "select_new": {"type": "boolean"},
        "refresh_ms": {"type": "integer", "minimum": 1000, "maximum": 60000}}, ("id", "image", "class")),
    tool("render_list_tracked_classes", "Read class filters and count_valid/pending/found/error. found=null before a successful scan or on failure, not zero; include_inactive_effective reports legacy Unity scope fallback. Counts are matching instances, not guaranteed drawable objects.", readonly=True),
    tool("render_untrack_class", "Stop a class tracker and remove only automatic entries exclusively owned by it. Explicit/manual objects survive.", {"id": ID}, ("id",)),
    tool("render_refresh_class", "Request refresh on the next bound game frame; inspect class status for completion.", {"id": ID}, ("id",)),
    tool("render_refresh_cameras", "Queue camera enumeration on the game thread. Falls back to FindObjectsOfType(Camera) if get_allCameras is stripped. Read render_list_cameras for cached names/addresses."),
    tool("render_projection_result", "Fetch an asynchronous render_project result. Requests are bounded to the latest 16; pending queries time out after five seconds without a game frame.", {"request_id": {"type": "integer", "minimum": 1, "maximum": 2**63-1}}, ("request_id",), readonly=True),
    tool("overlay_set_panel", "Create/patch an ImGui panel in the Custom tab. New panels require id/title. Up to 8 panels. Existing settings are preserved for omitted fields; visible/order control layout. No script injection.", {"descriptor": PANEL}, ("descriptor",)),
    tool("overlay_set_widget", "Create/patch a widget: text/button/toggle/slider/select/input. New widgets require id/panel/type; panel must exist. Up to 64. Slider uses min/max/value; select uses options and a zero-based integer value. input uses Java EditText/system IME. Optional action uses an allowlisted native UI/render command and arguments with {value} (single token) or {value_hex} (UTF-8 text). action:null removes the binding. Saving never executes actions; only human interaction does. Poll overlay_ui_events for changes. Use debug_help for command syntax.", {"descriptor": WIDGET}, ("descriptor",)),
    tool("overlay_list_custom_ui", "Read shared custom panels and widgets, including current values; reflects both MCP and manual edits.", readonly=True),
    tool("overlay_remove_custom_ui", "Remove a custom widget or panel. Removing a panel also removes its widgets; built-in pages are unaffected.", {"kind": enum("panel", "widget"), "id": ID}, ("kind", "id")),
    tool("overlay_ui_events", "Read up to 128 recent manual widget interactions after a sequence number; does not consume events. Store latest_sequence for subsequent polling.", {"after_sequence": {"type": "integer", "minimum": 0, "maximum": 2**63-1}}, readonly=True),
    tool("overlay_call_logs", "Read the latest 256 native MCP/UI calls with status, duration and bounded 2-KiB argument/result previews. In-memory only and independent of Toast.", readonly=True),
    tool("overlay_clear_call_logs", "Clear in-memory call history only; does not change Toast, hooks, objects or UI."),
    tool("render_set_primitive", "Create/patch a drawing primitive by ID. New entries require type/points; default space=screen. text/circle require 1 point, line/rect 2, triangle 3, quad/bezier 4, polyline 2..64, polygon/mesh 3..64. Mesh uses zero-based triangle indices (3..384 entries); polygon is outline-only. Cubic bezier uses projected control points. foreground selects foreground/background; clip_rect is normalized top-left [x,y,w,h]. Each point is [x,y,z]. Screen coordinates are normalized top-left (x/y; z ignored, components -4..4). World coordinates use the active camera and require valid projection. radius/font_size/thickness are pixels. filled applies to circle/rect/triangle/quad/mesh; closed to polyline. Up to 128 entries and 512 total world vertices. Screen graphics work without IL2CPP. Existing object styles/labels are not overwritten.", {"descriptor": PRIMITIVE}, ("descriptor",)),
    tool("render_list_primitives", "List custom drawing descriptors, including IDs, geometry and styles. Visibility still depends on renderer/camera availability.", readonly=True),
    tool("render_remove_primitive", "Remove one custom primitive without affecting object rendering.", {"id": ID}, ("id",)),
    tool("render_clear_primitives", "Clear all custom drawing primitives; leaves object registry, hooks and UI unchanged."),
]
BY_NAME = {item["name"]: item for item in TOOLS}


def validate(value: Any, schema: dict, name: str) -> None:
    kind = schema["type"]
    kinds = kind if isinstance(kind, list) else [kind]
    actual = ("boolean" if isinstance(value, bool) else "integer" if isinstance(value, int)
              else "number" if isinstance(value, float) else "string" if isinstance(value, str)
              else "array" if isinstance(value, list) else "object" if isinstance(value, dict)
              else "null" if value is None else "invalid")
    if actual not in kinds and not (actual == "integer" and "number" in kinds):
        raise ValueError(f"{name}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{name}: choose from {schema['enum']}")
    if actual in {"integer", "number"}:
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError(f"{name}: number outside supported range")
    if actual == "string":
        if "\0" in value or len(value.encode("utf-8")) > schema.get("maxLength", 4096) or len(value) < schema.get("minLength", 0):
            raise ValueError(f"{name}: invalid string or UTF-8 byte length")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise ValueError(f"{name}: invalid string format")
    if actual == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 4096):
            raise ValueError(f"{name}: wrong array size")
        if "items" in schema:
            for index, item in enumerate(value):
                validate(item, schema["items"], f"{name}[{index}]")
    if actual == "object":
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise ValueError(f"{name}: unknown descriptor properties")
        for required in schema.get("required", []):
            if required not in value:
                raise ValueError(f"{name}: missing {required}")
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f"{name}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(item, schema["additionalProperties"], f"{name}.{key}")


def token(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    result = str(value)
    if not result or any(ch.isspace() for ch in result):
        raise ValueError("invalid command token")
    return result


def text(value: str) -> str:
    return value.encode("utf-8").hex() or "-"


def address(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("address must be a hexadecimal/decimal string or integer")
    try:
        parsed = int(value, 0) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError("invalid address") from exc
    if not 0 <= parsed <= 2**64-1:
        raise ValueError("address out of range")
    return hex(parsed)


def setting(fields: dict, key: str, value: Any) -> str:
    if key not in fields:
        raise ValueError("unknown setting")
    validate(value, fields[key], key)
    if key in {"color", "occluded_color"}:
        if len(value) != 8 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
            raise ValueError("color must contain exactly eight RRGGBBAA hex digits")
    return text(value) if key == "label" else token(value)


def encode(name: str, args: dict) -> str:
    spec = BY_NAME[name]["inputSchema"]
    if not isinstance(args, dict) or set(args) - set(spec["properties"]):
        raise ValueError("unknown render/UI arguments")
    for required in spec["required"]:
        if required not in args:
            raise ValueError(f"missing {required}")
    for key, value in args.items():
        validate(value, spec["properties"][key], key)
    descriptors = {"overlay_set_panel": "OVERLAY_PANEL_SET", "overlay_set_widget": "OVERLAY_WIDGET_SET",
                   "render_set_primitive": "RENDER_PRIMITIVE_SET"}
    if name in descriptors:
        data = args["descriptor"]
        action = data.get("action")
        if action and any(c in action["arguments"] for c in "\r\n"):
            raise ValueError("action arguments must be a single native command line")
        if name == "render_set_primitive" and "points" in data:
            count = len(data["points"])
            expected = {"text": 1, "circle": 1, "line": 2, "rect": 2, "triangle": 3, "quad": 4, "bezier": 4}.get(data.get("type"))
            if (expected is not None and count != expected) or (data.get("type") == "polyline" and count < 2):
                raise ValueError("wrong point count for primitive type")
            if data.get("type") in {"polygon", "mesh"} and count < 3:
                raise ValueError("polygon/mesh need at least 3 vertices")
            if data.get("type") == "mesh":
                indices = data.get("indices", [])
                if not indices or len(indices) % 3 or any(index >= count for index in indices):
                    raise ValueError("mesh needs valid zero-based triangle indices")
            if data.get("space") == "screen" and any(abs(n) > 4 for p in data["points"] for n in p):
                raise ValueError("normalized screen coordinates must be between -4 and 4")
        if data.get("type") == "polygon" and data.get("filled"):
            raise ValueError("polygon is outline-only; use indexed mesh for filled shapes")
        if data.get("type") not in {None, "mesh"} and "indices" in data:
            raise ValueError("indices apply only to mesh")
        if "clip_rect" in data:
            x, y, w, h = data["clip_rect"]
            if w <= 0 or h <= 0 or x+w > 1.0001 or y+h > 1.0001:
                raise ValueError("clip_rect must fit within normalized screen")
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 16384:
            raise ValueError("descriptor exceeds 16 KiB")
        return descriptors[name] + " " + text(payload)
    simple = {"overlay_status": "OVERLAY_STATUS", "overlay_reset": "OVERLAY_RESET",
              "render_status": "RENDER_STATUS", "render_list_objects": "RENDER_OBJECTS",
              "render_clear_objects": "RENDER_CLEAR", "render_list_cameras": "RENDER_CAMERAS",
              "render_unbind_update": "RENDER_UNBIND_UPDATE", "render_binding_status": "RENDER_BINDING_STATUS",
              "render_list_tracked_classes": "RENDER_TRACKED_CLASSES", "render_refresh_cameras": "RENDER_REFRESH_CAMERAS",
              "overlay_list_custom_ui": "OVERLAY_CUSTOM_LIST", "overlay_call_logs": "OVERLAY_LOG_LIST",
              "overlay_clear_call_logs": "OVERLAY_LOG_CLEAR", "render_list_primitives": "RENDER_PRIMITIVES",
              "render_clear_primitives": "RENDER_PRIMITIVES_CLEAR"}
    if name == "render_list_objects" and args:
        return f"RENDER_OBJECTS {args.get('offset', 0)} {args.get('limit', 256)}"
    if name in simple:
        return simple[name]
    if name in {"overlay_set", "render_set_style", "render_update_object"}:
        fields = UI_FIELDS if name == "overlay_set" else STYLE_FIELDS if name == "render_set_style" else OBJECT_FIELDS
        cmd = "OVERLAY_SET" if name == "overlay_set" else "RENDER_STYLE" if name == "render_set_style" else "RENDER_OBJECT_SET"
        parts = [cmd] + ([text(args["id"])] if name == "render_update_object" else [])
        return " ".join(parts + [args["key"], setting(fields, args["key"], args["value"])])
    if name == "overlay_set_window":
        parts = ["OVERLAY_WINDOW", *[args[k] for k in ("x", "y", "width", "height")]]
    elif name == "render_add_object":
        parts = ["RENDER_OBJECT_ADD", text(args["id"]), address(args.get("address", 0)), text(args.get("label", "")),
                 *args.get("position", [0, 0, 0]), args.get("height", 1.8), args.get("width", .6), args.get("depth", .6)]
    elif name == "render_set_object_position":
        parts = ["RENDER_OBJECT_POSITION", text(args["id"]), *args["position"]]
    elif name == "render_set_object_bones":
        parts = ["RENDER_OBJECT_BONES", text(args["id"]), *[v for segment in args["segments"] for point in segment for v in point]]
    elif name == "render_remove_object":
        parts = ["RENDER_OBJECT_REMOVE", text(args["id"]) ]
    elif name == "render_set_camera":
        ptr = address(args.get("address", 0))
        if args["mode"] == "address" and ptr == "0x0":
            raise ValueError("custom camera requires a nonzero address")
        parts = ["RENDER_CAMERA", args["mode"], ptr]
    elif name == "render_set_camera_matrix":
        viewport = args.get("viewport", [0, 0, 1, 1])
        if viewport[2] <= 0 or viewport[3] <= 0 or viewport[0]+viewport[2] > 1.0001 or viewport[1]+viewport[3] > 1.0001:
            raise ValueError("viewport must fit within the normalized surface")
        parts = ["RENDER_MATRIX", *args["matrix"], *args.get("camera_position", [0, 0, 0]), *viewport]
    elif name == "render_project":
        parts = ["RENDER_PROJECT", *args["position"], args["surface_width"], args["surface_height"]]
    elif name == "render_track_class":
        if not args["image"]:
            raise ValueError("tracked class requires an image")
        parts = ["RENDER_TRACK_CLASS", text(args["id"]), text(args["image"]), text(args.get("namespace", "")),
                 text(args["class"]), args.get("limit", 0), args.get("include_inactive", True),
                 args.get("select_new", True), args.get("refresh_ms", 2000), "replace"]
    elif name in {"render_untrack_class", "render_refresh_class", "render_remove_primitive"}:
        command = {"render_untrack_class": "RENDER_UNTRACK_CLASS", "render_refresh_class": "RENDER_REFRESH_CLASS",
                   "render_remove_primitive": "RENDER_PRIMITIVE_REMOVE"}[name]
        parts = [command, text(args["id"])]
    elif name == "render_projection_result":
        parts = ["RENDER_PROJECT_RESULT", args["request_id"]]
    elif name == "overlay_remove_custom_ui":
        parts = ["OVERLAY_CUSTOM_REMOVE", args["kind"], text(args["id"])]
    elif name == "overlay_ui_events":
        parts = ["OVERLAY_EVENTS", args.get("after_sequence", 0)]
    elif name in {"render_bind_update", "render_find_objects"}:
        parts = ["RENDER_BIND_UPDATE" if name == "render_bind_update" else "RENDER_FIND_OBJECTS",
                 text(args["image"]), text(args.get("namespace", "")), text(args["class"])]
        parts += [text(args["method"]), 0] if name == "render_bind_update" else [args.get("limit", 0), args.get("include_inactive", True)]
    else:
        raise ValueError(f"unimplemented tool {name}")
    return " ".join(token(part) for part in parts)


def features(name: str) -> tuple[str, ...]:
    if name.startswith("overlay_"):
        return ("overlay_ui",)
    if name in {"render_bind_update", "render_unbind_update"}:
        return ("rendering", "il2cpp_metadata", "il2cpp_invoke", "dobby")
    if name in {"render_find_objects", "render_track_class", "render_refresh_cameras"}:
        return ("rendering", "il2cpp_metadata", "il2cpp_invoke", "il2cpp_objects")
    return ("rendering",)


def native_features(command: str) -> tuple[str, ...]:
    special = {"RENDER_BIND_UPDATE": "render_bind_update", "RENDER_UNBIND_UPDATE": "render_unbind_update",
               "RENDER_FIND_OBJECTS": "render_find_objects", "RENDER_TRACK_CLASS": "render_track_class",
               "RENDER_REFRESH_CAMERAS": "render_refresh_cameras"}
    if command in special:
        return features(special[command])
    return ("overlay_ui",) if command.startswith("OVERLAY_") else ("rendering",)
