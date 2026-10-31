"""v0.31 TARGET-CONTRACT tests: intentionally RED until physical work/ exists.

REAL MCP HTTP / REAL LMStudioAgentProvider HTTP / REAL SQLite+native file
publisher, with ONLY the external inference model replaced by a deterministic
local HTTP server. Do not run in the green pilot suite or mark collector green
as customer-product passing. Only synthetic content is used.

Sources: ADR-020 §3, 23_TEST_PLAN_V06 T-112/T-121, SPEC_V031.
"""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tests"))
# Import the bridge module, NOT a unittest.TestCase class into this module's
# namespace. unittest discovery otherwise collects its unrelated dozen tests.
import test_full_bridge_http_e2e_ci as bridge


class NativeWorkV031Contract(unittest.TestCase):
    # Reuse the genuine transport fixture WITHOUT inheriting the existing
    # TestCase's dozen tests. These three tests exist precisely to expose the
    # gap hidden by the pilot document-store happy-path E2E checks.
    setUp=bridge.CompleteBridgeE2E.setUp
    rpc=bridge.CompleteBridgeE2E.rpc
    direct=bridge.CompleteBridgeE2E.direct
    staged=bridge.CompleteBridgeE2E.staged
    outcome=bridge.CompleteBridgeE2E.outcome

    def test_wf001_real_work_file_is_created_from_worker_tool_bytes(self):
        """WF-001: create_work must create a real disk file, not SQLite TEXT."""
        # Both customer-visible entry routes must result in the SAME real
        # filesystem semantics. Run the two routes even when the first
        # lacks a work file, then report both missing results together.
        cases=[
            ("DIRECT",lambda:self.direct("v031-work-real-0001"),
             b"SYNTHETIC BRIDGE CONTENT"),
            ("STAGED",self.staged,b"SYNTHETIC STAGED CONTENT"),
        ]
        absent=[]
        for route,start_job,expected in cases:
            # Never start two workers concurrently in the single-slot
            # product fixture; wait for the first terminal state before
            # preparing the second route.
            job=start_job()
            status=self.outcome(job)
            self.assertEqual(status["state"],"RESULT_READY",status)
            work=self.project/".minionone"/"jobs"/job["job_id"]/"work"/"result.txt"
            if not work.is_file():
                absent.append(route+": "+str(work))
            else:
                self.assertEqual(work.read_bytes(),expected)
        self.assertFalse(absent,
            "WF-001 TARGET_NOT_MET: no physical work file on route(s): "+
            "; ".join(absent))

    def test_wf002_published_result_is_a_snapshot_of_real_work_bytes(self):
        """WF-002: output must derive from physical work file and preserve it."""
        job=self.direct("v031-work-publish-0001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        folder=self.project/".minionone"/"jobs"/job["job_id"]
        work=folder/"work"/"result.txt"
        output=folder/"output"/"result.txt"
        self.assertTrue(output.is_file(),"synthetic provider must publish output")
        self.assertTrue(work.is_file(),
            "WF-002 TARGET_NOT_MET: no physical work file to substantiate publish")
        original=work.read_bytes()
        published=output.read_bytes()
        self.assertEqual(sha256(original).hexdigest(),
                         sha256(published).hexdigest(),
                         "the manager published bytes other than the pinned work source")
        self.assertEqual(work.read_bytes(),original,
                         "publication modified worker's original work file")

    def test_wf003_sqlite_is_not_the_primary_store_for_work_contents(self):
        """WF-003: job control metadata may be SQL; Work document bytes may not."""
        job=self.direct("v031-work-sqlite-0001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        # sqlite3.Connection.__exit__ commits; it does NOT close the handle.
        # Windows cannot remove the temp database until we explicitly close.
        with closing(sqlite3.connect(self.root/"jobs.sqlite")) as con:
            rows=con.execute(
                "SELECT name,content FROM pilot_files WHERE job_id=? AND zone='work'",
                (job["job_id"],)).fetchall()
        self.assertFalse(rows,
            "WF-003 TARGET_NOT_MET: Work content still persisted as "
            "pilot_files(zone='work', content=TEXT), not a real filesystem source")


    def test_wf017_work_file_can_be_overwritten_then_read_and_published(self):
        """WF-017: work/ is a real, editable filesystem, not create-only."""
        self.model.mode="work_edit"
        job=self.direct("v031-work-edit-001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]
        self.assertEqual((root/"work"/"result.txt").read_bytes(),b"REVISED")
        self.assertEqual((root/"output"/"result.txt").read_bytes(),b"REVISED")
        self.assertIn("write_work", (root/"output"/"worker-io.jsonl").read_text())

    def test_wf018_worker_can_delete_existing_work_file(self):
        """WF-018: delete_work physically deletes inside work/, not output."""
        self.model.mode="work_delete"
        job=self.direct("v031-work-delete-001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]
        self.assertFalse((root/"work"/"discard.txt").exists())
        self.assertEqual((root/"work"/"result.txt").read_bytes(),b"KEPT")
        self.assertEqual((root/"output"/"result.txt").read_bytes(),b"KEPT")

    def test_wf019_work_file_rename_preserves_bytes_and_removes_old_path(self):
        """WF-019: Worker can rename/move actual files inside its work dir."""
        self.model.mode="work_rename"
        job=self.direct("v031-work-rename-001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]
        self.assertFalse((root/"work"/"draft.txt").exists())
        self.assertEqual((root/"work"/"result.txt").read_bytes(),b"RENAMED CONTENT")
        self.assertEqual((root/"output"/"result.txt").read_bytes(),b"RENAMED CONTENT")

    def test_wf020_output_create_only_immutable_and_artifact_readable(self):
        """WF-020: Output snapshot cannot be changed after Work file is edited."""
        self.model.mode="output_immutable"
        job=self.direct("v031-output-once-001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]
        self.assertEqual((root/"work"/"result.txt").read_bytes(),b"SECOND")
        self.assertEqual((root/"output"/"result.txt").read_bytes(),b"FIRST")
        # The attempted second publish of the same name MUST be rejected;
        # worker must be able to read published artifact, but not manager logs.
        transcript=(root/"output"/"worker-io.jsonl").read_text(encoding="utf-8")
        self.assertGreaterEqual(transcript.count("SCOPE_DENIED"),2,
             "duplicate publication and attempted output deletion must both be denied")
        self.assertIn('output/result.txt',transcript)

    def test_wf021_input_is_readable_but_not_editable_by_worker(self):
        """WF-021: Input is a sealed read-only source, not a work alias."""
        self.model.mode="input_readonly"
        job=self.direct("v031-input-readonly-001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]
        inp=root/"input"/"sample.txt"
        self.assertEqual(inp.read_bytes(),b"synthetic bridge content")
        self.assertGreaterEqual((root/"output"/"worker-io.jsonl").read_text().count("SCOPE_DENIED"),2,
                                "both input overwrite and input deletion must be denied")
        self.assertEqual((root/"output"/"result.txt").read_bytes(),b"INPUT REMAINS SEALED")

    def test_wf022_worker_can_create_and_use_nested_work_directories(self):
        """WF-022: work/ allows subdirectories but never path escape."""
        self.model.mode="work_nested"
        job=self.direct("v031-work-nested-001")
        status=self.outcome(job)
        self.assertEqual(status["state"],"RESULT_READY",status)
        root=self.project/".minionone"/"jobs"/job["job_id"]
        # The nested directory is created, populated, used and then removed
        # by the worker; a completed full-CRUD job must leave no stale tree.
        self.assertFalse((root/"work"/"nested").exists(),
             "WF-022: worker must be able to delete nested files and directories")
        transcript=(root/"output"/"worker-io.jsonl").read_text()
        self.assertIn("nested/result.txt",transcript)
        self.assertIn("rmdir_work",transcript)


if __name__=="__main__":
    unittest.main()
