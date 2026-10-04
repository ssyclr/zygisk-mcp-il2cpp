"""Dependency-free encoder tests; these do not claim kernel ptrace validation."""
import json
import unittest
try:
    from . import paused_debug_tools as p
except ImportError:
    import paused_debug_tools as p


def decoded(name, args):
    command, data = p.encode(name, args).split(" ", 1)
    assert command == "DEBUGGER_CONTROL"
    return json.loads(bytes.fromhex(data))


class PausedDebuggerTools(unittest.TestCase):
    def test_status_is_readonly_and_has_no_arguments(self):
        self.assertEqual(decoded("debugger_status", {}), {"op": "status"})
        self.assertTrue(p.BY_NAME["debugger_status"]["annotations"]["readOnlyHint"])
        with self.assertRaises(ValueError): decoded("debugger_status", {"pid": 1})

    def test_no_arbitrary_pid_or_thread_boolean(self):
        for args in ({"pid": 1}, {"tid": True}, {"tid": -1}):
            with self.assertRaises(ValueError): decoded("debugger_registers", args)

    def test_pause_requires_explicit_confirmation_and_identity(self):
        data = {"op": "pause", "tid": 10, "expected_thread_start_time": "12345", "confirm": True}
        self.assertEqual(decoded("debugger_control", data)["expected_thread_start_time"], hex(12345))
        for key in ("tid", "expected_thread_start_time", "confirm"):
            wrong = dict(data); del wrong[key]
            with self.assertRaises(ValueError): decoded("debugger_control", wrong)
        with self.assertRaises(ValueError): decoded("debugger_control", {**data, "confirm": False})

    def test_lease_bounds(self):
        args = {"op": "pause", "tid": 10, "expected_thread_start_time": "123", "confirm": True}
        for value in (0, 999, 15001, True, 1000.1):
            with self.assertRaises(ValueError): decoded("debugger_control", {**args, "lease_ms": value})
        self.assertEqual(decoded("debugger_control", {**args, "lease_ms": 15000})["lease_ms"], 15000)

    def test_exact_register_values(self):
        args = {"op": "set_registers", "tid": 10, "stop_id": "1", "expected_pc": "0x1000", "confirm": True,
                "registers": {"x0": "18446744073709551615", "x30": "0xffffffffffffffff"}}
        result = decoded("debugger_control", args)
        self.assertEqual(result["registers"]["x0"], "0xffffffffffffffff")
        for bad in (18446744073709551615, True, "18446744073709551616", "-1", "+1", "0x", "1 2", "1e3"):
            with self.assertRaises(ValueError): decoded("debugger_control", {**args, "registers": {"x0": bad}})

    def test_only_supported_register_writes(self):
        args = {"op": "set_registers", "tid": 10, "stop_id": "1", "expected_pc": "0x1000", "confirm": True}
        for regs in ({}, {"x31": "0"}, {"pstate": 0}, {"v0": "0x"+"f"*33}, {"pc": "0x1001"}, {"sp": "0x1234"}):
            with self.assertRaises(ValueError): decoded("debugger_control", {**args, "registers": regs})

    def test_resume_does_not_require_confirmation(self):
        self.assertEqual(decoded("debugger_control", {"op": "resume", "tid": 1}), {"op": "resume", "tid": 1})
        self.assertEqual(decoded("debugger_control", {"op": "resume_all"}), {"op": "resume_all"})
        with self.assertRaises(ValueError): decoded("debugger_control", {"op": "resume_all", "tid": 1})

    def test_step_requires_fresh_stop_identity(self):
        for stop in (None, "0", 1, "18446744073709551616"):
            with self.assertRaises(ValueError): decoded("debugger_control", {"op": "step", "tid": 1, "stop_id": stop, "confirm": True})
        self.assertEqual(decoded("debugger_control", {"op": "step", "tid": 1, "stop_id": "2", "confirm": True})["stop_id"], "0x2")

    def test_reads_cannot_renew_lease(self):
        for name in ("debugger_registers", "debugger_backtrace"):
            with self.assertRaises(ValueError): decoded(name, {"tid": 1, "lease_ms": 10000})

    def test_no_unimplemented_operations(self):
        for op in ("step_over", "step_out", "breakpoint", "attach_pid", "kill", "signal"):
            with self.assertRaises(ValueError): decoded("debugger_control", {"op": op})

    def test_help_and_feature_mapping(self):
        self.assertEqual(decoded("debugger_control", {"op": "help"}), {"op": "help"})
        self.assertEqual(p.native_features("DEBUGGER_CONTROL"), ("breakpoint",))
        self.assertIsNone(p.native_features("MEMORY_READ"))

    def test_thread_and_backtrace_pagination(self):
        self.assertEqual(decoded("debugger_threads", {"offset": 64, "limit": 32})["offset"], 64)
        self.assertEqual(decoded("debugger_backtrace", {"tid": 1, "max_frames": 64})["max_frames"], 64)
        with self.assertRaises(ValueError): decoded("debugger_backtrace", {"tid": 1, "max_frames": 65})


if __name__ == "__main__":
    unittest.main()
