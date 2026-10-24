"""Offline source contracts for full-cache exports; no native build or device I/O.

These guard source-level invariants, not Android filesystem/kernel behavior.
"""
from pathlib import Path
import unittest

CPP = Path(__file__).resolve().parents[1] / "module/src/main/cpp"


class PointerExportSourceContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = (CPP / "runtime/runtime_bridge.cpp").read_text(encoding="utf-8")
        cls.memory_tu = (CPP / "runtime/runtime_bridge_memory.cpp").read_text(encoding="utf-8")
        cls.internal = (CPP / "runtime/runtime_bridge_internal.h").read_text(encoding="utf-8")
        cls.export = (CPP / "runtime/runtime_pointer_export.inc").read_text(encoding="utf-8")
        cls.chains = (CPP / "runtime/runtime_pointer_chains.inc").read_text(encoding="utf-8")
        cls.chain_ui = (CPP / "overlay/pointer_chains_menu.inc").read_text(encoding="utf-8")
        cls.menu = (CPP / "overlay/menu.cpp").read_text(encoding="utf-8")

    def test_full_candidate_export_reads_session_not_page_or_live_memory(self):
        export = self.export.split("bool candidates(", 1)[1]
        self.assertIn("findMemorySearchSession(sessionId)", export)
        self.assertIn("const size_t count=session->hits.size()", export)
        self.assertIn("for(size_t i=0;i<count;++i)", export)
        self.assertIn("const auto& hit=session->hits[i]", export)
        for forbidden in ("MEMORY_SEARCH_RESULTS", "safeReadMemory(", "parallelReadMemory(", "offset+limit", "slice."):
            self.assertNotIn(forbidden, export)

    def test_full_chain_export_uses_bounded_native_snapshot(self):
        self.assertIn("std::array<ScanSession,4> scanSessions", self.chains)
        self.assertIn("retainScan(std::move(result),body)", self.chains)
        export = self.chains.split("bool exportScan(", 1)[1].split("bool signedValue(", 1)[0]
        self.assertIn('selected->result["chains"].items', export)
        self.assertIn("for(size_t i=0;i<rows.size();++i)", export)
        self.assertIn("CHAIN_SCAN_SESSION_NOT_FOUND_OR_EXPIRED", export)
        for forbidden in ("safeReadMemory(", "scan(options", "slice.", "max_results"):
            self.assertNotIn(forbidden, export)

    def test_format_preserves_original_scan_and_separates_export_completeness(self):
        for token in ('"schema",string("zygisk.pointer-scan.v1")', '"complete",boolean(true)',
                      '"cached_count",number(count)', '"exported_count",number(count)',
                      '"scan",scan', '"all_cached_results_not_current_page"'):
            self.assertIn(token, self.export)
        for token in ('scan.members["stop_reasons"]', 'scan.members["options"]',
                      'scan.members["scan_complete"]', 'session.pointerScanMetadata='):
            self.assertIn(token, self.export)
        self.assertIn('result.members["options"]=options', self.chains)
        self.assertIn('result.members["budget_hint"]', self.chains)
        self.assertIn('member.first!="chains"', self.chains)

    def test_batches_keep_all_recipes_in_importable_bounded_groups(self):
        export = self.chains.split("bool exportScan(", 1)[1].split("bool signedValue(", 1)[0]
        self.assertIn("while(next<rows.size())", export)
        self.assertIn("recipes.items.size()<128", export)
        self.assertIn("recipeBytes+bytes>24*1024", export)
        self.assertIn('const auto& recipe=rows[next]["recipe"]', export)
        self.assertIn('recipes.items.push_back(recipe)', export)
        self.assertIn('batch.members["chains"]', export)
        self.assertIn('parsed["chains"].kind==Value::Array', self.chain_ui)

    def test_atomic_private_publication_never_overwrites_or_links_user_files(self):
        writer = self.export.split("bool writeDocument(", 1)[1].split("void capture(", 1)[0]
        self.assertIn("O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600", writer)
        self.assertGreaterEqual(writer.count("O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW"), 3)
        self.assertIn("if(fsync(output.fd)<0)", writer)
        self.assertIn("if(close(written)<0)", writer)
        self.assertIn("SYS_renameat2,dir.fd,temp.c_str(),dir.fd,file.c_str(),1U", writer)
        self.assertIn("POINTER_EXPORT_ATOMIC_PUBLISH_UNAVAILABLE", writer)
        self.assertLess(writer.index("fsync(output.fd)"), writer.index("syscall(SYS_renameat2"))
        self.assertNotIn("renameat(", writer)
        self.assertNotIn("linkat(", writer.replace("unlinkat(", "cleanup("))
        self.assertNotIn("unlinkat(dir.fd,file.c_str()", writer)

    def test_zero_results_are_exportable_and_success_is_receipt_only(self):
        export = self.chains.split("bool exportScan(", 1)[1].split("bool signedValue(", 1)[0]
        self.assertNotIn("rows.empty()", export)
        candidate = self.export.split("bool candidates(", 1)[1]
        self.assertNotIn("hits.empty()", candidate)
        for source in (export, candidate):
            receipt = source.split('body="', 1)[1].split(";", 1)[0]
            self.assertIn('success', receipt)
            self.assertIn('path', receipt)
            self.assertIn('count', receipt)
            for forbidden in ('results', 'chains', 'contents', 'recipes'):
                self.assertNotIn(forbidden, receipt)

    def test_native_wiring_and_both_manual_entry_points(self):
        # pointer_export.inc now compiles in the memory sibling TU; the shared
        # MemorySearchSession (with pointerScanMetadata) lives in the core header.
        self.assertIn('#include "runtime_pointer_export.inc"', self.memory_tu)
        self.assertIn("std::string pointerScanMetadata", self.internal)
        self.assertIn("PointerExport::capture(session, body, words", self.runtime)
        self.assertIn('command=="MEMORY_CHAIN_EXPORT"', self.chains)
        self.assertIn('chainExport.send("MEMORY_CHAIN_EXPORT"', self.chain_ui)
        self.assertNotIn('ExportButtons("chains-export",chainScan.body', self.chain_ui)
        self.assertIn('submit("MEMORY_CHAIN_EXPORT",displayed.pointerExportArgs)', self.menu)
        self.assertIn('"pointer.export.session"', self.menu)
        worker = self.menu.split("void* runJob(", 1)[1].split("void submit(", 1)[0]
        self.assertLess(worker.index("pointerExportArgs=OverlayProtocol::encode"), worker.index("result.raw = bounded"))


if __name__ == "__main__":
    unittest.main()
