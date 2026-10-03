"""Offline source contracts for weak return identities; no native build/device I/O."""
from pathlib import Path
import re
import unittest

CPP = Path(__file__).resolve().parents[1] / "module/src/main/cpp"


class ReturnHandleSourceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.handles = (CPP / "runtime_return_handles.inc").read_text(encoding="utf-8")
        cls.inspector = (CPP / "runtime_inspector.inc").read_text(encoding="utf-8")

    def test_cache_is_bounded_and_keeps_weak_identity_not_address(self):
        self.assertIn("constexpr size_t Capacity=320", self.handles)
        self.assertIn("std::array<Entry,Capacity> entries", self.handles)
        entry = re.search(r"struct Entry \{([^}]+)\}", self.handles).group(1)
        self.assertIn("uint64_t id=0", entry)
        self.assertIn("Il2CppGCHandle weak=0", entry)
        self.assertNotIn("uint32_t weak", entry)
        self.assertNotIn("address", entry)
        self.assertNotIn("Il2CppObject", entry)
        remember = self.handles.split("std::string remember(", 1)[1].split("bool parseId(", 1)[0]
        self.assertIn("il2cpp_gchandle_new_weakref(object,false)", remember)
        self.assertNotIn("il2cpp_gchandle_new(", remember)

    def test_ids_never_wrap_or_reuse_expired_external_identity(self):
        remember = self.handles.split("std::string remember(", 1)[1].split("bool parseId(", 1)[0]
        self.assertIn("!nextId", remember)
        self.assertIn("nextId=id==UINT64_MAX?0:id+1", remember)
        self.assertIn("*slot={id,weak}", remember)
        self.assertNotIn("nextId=1", remember)
        self.assertIn("std::to_string(id)", remember)

    def test_eviction_frees_only_owned_weak_handle(self):
        self.assertIn("if(entry.id<slot->id) slot=&entry", self.handles)
        self.assertIn("expire(*slot)", self.handles)
        expire = self.handles.split("void expire(", 1)[1].split("std::string remember(", 1)[0]
        self.assertIn("il2cpp_gchandle_free(entry.weak)", expire)
        self.assertIn("entry={}", expire)
        self.assertNotIn("nextId", expire)

    def test_optional_gc_api_failure_does_not_fail_completed_call(self):
        available = self.handles.split("bool available()", 1)[1].split("void expire(", 1)[0]
        for api in ("il2cpp_gchandle_new_weakref", "il2cpp_gchandle_get_target",
                    "il2cpp_gchandle_free", "il2cpp_gchandle_new"):
            self.assertIn(api, available)
        remember = self.handles.split("std::string remember(", 1)[1].split("bool parseId(", 1)[0]
        self.assertIn("if(!object||!available()||!nextId) return {}", remember)
        self.assertIn("if(!weak) return {}", remember)
        self.assertNotIn("error=", remember)
        self.assertIn('RETURN_OBJECT_HANDLES_UNAVAILABLE', self.handles)

    def test_new_handle_is_only_attached_to_declared_managed_reference(self):
        self.assertIn("!il2cpp_type_is_byref(resultType)", self.inspector)
        self.assertIn("if(reference&&result&&il2cpp_gchandle_new&&il2cpp_gchandle_get_target&&il2cpp_gchandle_free)", self.inspector)
        self.assertIn('jsonString(resultHandle)', self.inspector)
        result_block = self.inspector.split('if(reference)kind="object";', 1)[1].split("return true;", 1)[0]
        self.assertIn("ReturnHandles::Pinned resultPinned", result_block)
        self.assertIn("resultPinned.strong=il2cpp_gchandle_new(result,true)", result_block)
        self.assertLess(result_block.index("if(auto* protectedResult=resultPinned.get())"), result_block.index("ReturnHandles::remember"))
        self.assertLess(result_block.index("ReturnHandles::remember"), result_block.index("formatInvokeResult"))
        self.assertLess(result_block.index("resultPinned.strong="), result_block.index("typeName(resultType)"))
        self.assertNotIn("error=", result_block)

    def test_members_snapshot_pins_target_until_inspection_returns(self):
        handler = self.inspector.split('if(command=="IL2CPP_RETURN_MEMBERS")', 1)[1].split('if(command=="IL2CPP_FIELD_GET"', 1)[0]
        self.assertIn("ReturnHandles::Pinned pinned", handler)
        self.assertIn("ReturnHandles::acquire(handleId,pinned,error)", handler)
        self.assertIn('return handleInspectorCommand("IL2CPP_OBJECT_MEMBERS"', handler)
        self.assertLess(handler.index("ReturnHandles::acquire"), handler.index('handleInspectorCommand("IL2CPP_OBJECT_MEMBERS"'))
        self.assertNotIn("openAddress", handler)
        self.assertNotIn("parseAddress(words[0]", handler)
        self.assertIn("il2cpp_gchandle_new(target,true)", self.handles)
        self.assertIn("~Pinned()", self.handles)
        self.assertIn("il2cpp_gchandle_free(strong)", self.handles)
        self.assertIn("Pinned(const Pinned&)=delete", self.handles)

    def test_expired_object_never_falls_back_to_stale_address(self):
        acquire = self.handles.split("bool acquire(", 1)[1]
        self.assertIn('if(!entry) { error="RETURN_OBJECT_EXPIRED"; return false; }', acquire)
        self.assertIn("il2cpp_gchandle_get_target(entry->weak)", acquire)
        self.assertIn('if(!target) { expire(*entry); error="RETURN_OBJECT_EXPIRED"; return false; }', acquire)
        self.assertLess(acquire.index("if(!target)"), acquire.index("il2cpp_gchandle_new(target,true)"))
        for forbidden in ("safeReadMemory", "validatedObjectClass", "reinterpret_cast", "parseAddress"):
            self.assertNotIn(forbidden, acquire)

    def test_decimal_identity_and_bounded_pagination_are_validated_first(self):
        self.assertIn("text.size()>20", self.handles)
        self.assertIn("c>='0'&&c<='9'", self.handles)
        self.assertIn("std::strtoull(text.c_str(),&end,10)", self.handles)
        handler = self.inspector.split('if(command=="IL2CPP_RETURN_MEMBERS")', 1)[1].split('if(command=="IL2CPP_FIELD_GET"', 1)[0]
        self.assertIn("words.size()!=3", handler)
        self.assertIn("parseSize(words[1],offset,10000)", handler)
        self.assertIn("parseSize(words[2],limit,256)||!limit", handler)
        self.assertLess(handler.index("EXPECTED_RETURN_HANDLE_OFFSET_LIMIT"), handler.index("ReturnHandles::acquire"))


if __name__ == "__main__":
    unittest.main()
