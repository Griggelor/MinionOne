"""Production control rejection journal: real MCP sockets, SQLite and restart.

Synthetic jobs only. No provider or worker executes. Failure injection is at
transaction boundaries, including real subprocess exits without clean shutdown.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from minionone.admission import (
    AdmissionStore, PermissionDenied, VersionConflict, IdempotencyConflict,
)
from minionone.diagnostics import safe_tool_fault
from minionone.attempt_fence import AttemptFenceStore
from minionone.mcp_server import LocalMCP
from minionone.timeout_control import TimeoutControlDenied

TOKEN = "synthetic-parent-" + "x" * 48
CANARY = "secret-operation-canary-0001"


class DurableControlErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "synthetic.sqlite"
        self.server = self.open_server()
        self.job = self.server.call_tool("minionone_admit", {
            "input_digest": "a" * 64, "operation_id": "admit-test-0001",
        })["job_id"]

    def open_server(self, principal="parent", thread="chat"):
        server = LocalMCP(("127.0.0.1", 0), token=TOKEN,
                          database_path=self.db, principal=principal, thread=thread)
        self.addCleanup(server.server_close)
        return server

    def args(self, operation=CANARY, version=0):
        return {"job_id": self.job, "expected_version": version,
                "operation_id": operation}

    def reject_resume(self, operation=CANARY, version=0, server=None):
        with self.assertRaises(TimeoutControlDenied):
            (server or self.server).call_tool("minionone_resume_request",
                                             self.args(operation, version))

    def errors(self, server=None):
        rows = (server or self.server).call_tool(
            "minionone_job_events", {"job_id": self.job})["events"]
        return [r for r in rows if r.get("type") == "CONTROL_REJECTED"]

    def rpc(self, server, tool, args, token=TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", "/mcp", json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }), headers={"Authorization": "Bearer " + token,
                     "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.shutdown)

    def test_wire_error_is_visible_after_actual_server_restart(self):
        self.serve(self.server)
        status, reply = self.rpc(self.server, "minionone_resume_request", self.args())
        self.assertEqual(status, 200)
        self.assertTrue(reply["result"]["isError"])
        fault = json.loads(reply["result"]["content"][0]["text"])["error"]
        self.assertEqual(fault["code"], "RESUME_NOT_READY")
        self.server.shutdown()
        self.server.server_close()
        restarted = self.open_server()
        self.serve(restarted)
        status, reply = self.rpc(restarted, "minionone_job_events", {"job_id": self.job})
        rows = json.loads(reply["result"]["content"][0]["text"])["events"]
        errors = [r for r in rows if r.get("type") == "CONTROL_REJECTED"]
        self.assertEqual(len(errors), 1)
        event = errors[0]
        self.assertEqual(event["error"], fault)
        self.assertEqual(event["status"], "ACCEPTED")
        self.assertEqual(event["state_version"], 0)
        self.assertIsNone(event["attempt_epoch"])
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["operation"], "RESUME_REQUEST")
        self.assertEqual(event["phase"], "PARENT_CONTROL")
        self.assertTrue(event["occurred_utc"].endswith("Z"))
        self.assertEqual(event["seq"], 2)

    def test_status_projects_historical_error_without_changing_job_state(self):
        self.reject_resume()
        self.server.call_tool("minionone_abort", self.args("valid-abort-0001"))
        status = self.server.call_tool("minionone_job_status", {"job_id": self.job})
        self.assertEqual((status["state"], status["version"]), ("CANCEL_REQUESTED", 1))
        error = status["latest_control_error"]
        self.assertEqual((error["status"], error["state_version"]), ("ACCEPTED", 0))
        self.assertEqual(error["error"]["code"], "RESUME_NOT_READY")
        self.assertFalse(self.server.call_tool("minionone_status", {})["worker_execution_enabled"])

    def test_replay_survives_restart_and_does_not_duplicate(self):
        self.reject_resume()
        first = self.errors()
        self.reject_resume(server=self.open_server())
        self.assertEqual(self.errors(), first)
        self.assertEqual(len(first), 1)

    def test_changed_payload_cannot_reuse_rejected_key(self):
        self.reject_resume()
        with self.assertRaises(IdempotencyConflict):
            self.server.call_tool("minionone_resume_request", self.args(version=1))
        self.assertEqual(len(self.errors()), 1)

    def test_failure_replay_keeps_original_class_after_job_changes(self):
        self.reject_resume()
        self.server.call_tool("minionone_abort", self.args("valid-abort-0001"))
        self.reject_resume()
        self.assertEqual(len(self.errors()), 1)

    def make_resumable(self):
        self.server.store.advance_pre_dispatch(self.job, 0, "queue-test-0001", "QUEUED")
        fences = AttemptFenceStore(self.db)
        fences.reserve(self.job, operation_id="attempt-test-0001", expected_job_version=1)
        fences.revoke(self.job, expected_epoch=1)
        # Only a synthetic prior worker state; production dispatch is disabled.
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE jobs SET state='RUNNING',version=2 WHERE job_id=?", (self.job,))
            con.commit()
        self.server.timeout_control.record_timeout(
            self.job, expected_version=2, attempt_epoch=1, worker_quiescent=True,
            checkpoint_sha256="c" * 64, operation_id="timeout-test-0001")

    def test_old_rejection_replays_after_another_resume_intent(self):
        self.reject_resume()
        self.make_resumable()
        self.server.call_tool("minionone_resume_request", self.args("new-resume-0001", 3))
        self.reject_resume()
        self.assertEqual(len(self.errors()), 1)
        status = self.server.call_tool("minionone_job_status", {"job_id": self.job})
        self.assertEqual((status["state"], status["version"]), ("INTERRUPTED", 4))
        self.assertFalse(status["timeout"]["resume_started"])

    def test_rejected_future_abort_never_becomes_a_success_on_replay(self):
        with self.assertRaises(VersionConflict):
            self.server.call_tool("minionone_abort", self.args(version=1))
        self.server.store.advance_pre_dispatch(self.job, 0, "queue-test-0001", "QUEUED")
        with self.assertRaises(VersionConflict):
            self.open_server().call_tool("minionone_abort", self.args(version=1))
        status = self.server.call_tool("minionone_job_status", {"job_id": self.job})
        self.assertEqual((status["state"], status["version"]), ("QUEUED", 1))
        self.assertEqual(len(self.errors()), 1)

    def test_rejected_resume_never_becomes_executable_when_checkpoint_arrives(self):
        self.reject_resume(version=3)
        self.make_resumable()
        self.reject_resume(version=3, server=self.open_server())
        status = self.server.call_tool("minionone_job_status", {"job_id": self.job})
        self.assertTrue(status["timeout"]["resume_available"])
        self.assertFalse(status["timeout"]["resume_requested"])
        self.assertEqual(status["version"], 3)
        self.assertEqual(len(self.errors()), 1)

    def test_attempt_metadata_is_observed_without_starting_or_unfencing(self):
        self.make_resumable()
        with self.assertRaises(VersionConflict):
            self.server.call_tool("minionone_resume_request", self.args(version=0))
        error = self.errors()[0]
        self.assertEqual((error["attempt_epoch"], error["state_version"]), (1, 3))
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT status FROM attempt_fences").fetchone()[0], "FENCED")
            self.assertEqual(con.execute("SELECT worker_started FROM jobs").fetchone()[0], 0)

    def test_accepted_receipt_still_replays_after_conflicting_payload(self):
        receipt = self.server.call_tool("minionone_abort", self.args())
        with self.assertRaises(IdempotencyConflict):
            self.server.call_tool("minionone_abort", self.args(version=1))
        self.assertEqual(self.server.call_tool("minionone_abort", self.args()), receipt)
        self.assertEqual(len(self.errors()), 1)

    def test_stale_abort_records_exact_observed_state_and_version(self):
        self.server.store.advance_pre_dispatch(self.job, 0, "queue-test-0001", "QUEUED")
        with self.assertRaises(VersionConflict):
            self.server.call_tool("minionone_abort", self.args())
        error = self.errors()[0]
        self.assertEqual((error["status"], error["state_version"]), ("QUEUED", 1))
        self.assertEqual(error["error"]["code"], "STALE_VERSION")

    def test_unowned_wrong_thread_and_wrong_token_cannot_inject_or_read(self):
        self.reject_resume()
        for principal, thread in (("outsider", "chat"), ("parent", "different")):
            with self.subTest(principal=principal, thread=thread):
                stranger = self.open_server(principal, thread)
                for tool, args in (("minionone_resume_request", self.args()),
                                   ("minionone_job_events", {"job_id": self.job}),
                                   ("minionone_job_status", {"job_id": self.job})):
                    with self.assertRaises(PermissionDenied):
                        stranger.call_tool(tool, args)
        self.serve(self.server)
        status, _ = self.rpc(self.server, "minionone_resume_request", self.args(), "wrong")
        self.assertEqual(status, 401)
        self.assertEqual(len(self.errors()), 1)

    def test_untrusted_exception_text_and_operation_key_are_not_persisted(self):
        with patch("minionone.admission.check_transition", side_effect=VersionConflict(CANARY)):
            with self.assertRaises(VersionConflict):
                self.server.call_tool("minionone_abort", self.args())
        self.assertEqual(len(self.errors()), 1)
        with self.server.store._connection() as con:
            dump = "\n".join(con.iterdump())
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn(CANARY, dump)
        self.assertNotIn(CANARY, json.dumps(self.errors()))

    def test_concurrent_same_error_has_one_committed_event(self):
        def reject(_):
            self.reject_resume()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(reject, range(16)))
        self.assertEqual(len(self.errors()), 1)
        self.assertEqual(self.server.store.status(TOKEN, "chat", self.job)["version"], 0)

    def test_partial_control_changes_roll_back_before_error_is_committed(self):
        original_connection = self.server.store._connection

        @contextmanager
        def injecting_connection():
            with original_connection() as con:
                def broken_transition(*_):
                    # Actual partial SQL write in the production transaction.
                    con.execute("UPDATE jobs SET version=999 WHERE job_id=?", (self.job,))
                    raise VersionConflict(CANARY)
                with patch("minionone.admission.check_transition", side_effect=broken_transition):
                    yield con

        with patch.object(self.server.store, "_connection", injecting_connection):
            with self.assertRaises(VersionConflict):
                self.server.call_tool("minionone_abort", self.args())
        self.assertEqual(len(self.errors()), 1)
        self.assertEqual(self.server.store.status(TOKEN, "chat", self.job)["version"], 0)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM controls").fetchone()[0], 0)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_journal_write_failure_is_visible_and_never_claims_success(self):
        # Trigger on the existing canonical timeline, rather than replacing a
        # Python helper. SQLite rejects the rejection-event write itself.
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TRIGGER reject_error BEFORE INSERT ON admission_events "
                        "WHEN NEW.actor='AUTHENTICATED_PARENT_REQUEST_ERROR' "
                        "BEGIN SELECT RAISE(ABORT, 'synthetic-disk-failure'); END")
            con.commit()
        try:
            self.server.call_tool("minionone_resume_request", self.args())
        except Exception as exc:
            self.assertEqual(safe_tool_fault(exc).code, "ERROR_JOURNAL_UNAVAILABLE")
        else:
            self.fail("failed error persistence returned success")
        self.assertEqual(self.errors(), [])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM admission_events").fetchone()[0], 1)

    def test_error_quota_is_durable_but_does_not_block_valid_abort(self):
        for i in range(128):
            self.reject_resume("rejection-%04d" % i)
        restarted = self.open_server()
        try:
            restarted.call_tool("minionone_resume_request", self.args("over-quota-0001"))
        except Exception as exc:
            self.assertEqual(safe_tool_fault(exc).code, "ERROR_JOURNAL_UNAVAILABLE")
        else:
            self.fail("unbounded journal accepted another event")
        self.assertEqual(len(self.errors()), 128)
        self.reject_resume("rejection-0000", server=restarted)
        self.assertEqual(restarted.call_tool("minionone_abort", self.args("abort-at-cap-0001"))["state"],
                         "CANCEL_REQUESTED")

    def test_commit_failure_rolls_back_timeline_and_metadata_together(self):
        # A deferred FK deliberately fails at COMMIT, after both error inserts.
        with closing(sqlite3.connect(self.db)) as con:
            con.executescript('''
                CREATE TABLE commit_parent(id INTEGER PRIMARY KEY);
                CREATE TABLE commit_child(id INTEGER REFERENCES commit_parent(id)
                    DEFERRABLE INITIALLY DEFERRED);
                CREATE TRIGGER fail_error_commit AFTER INSERT ON admission_events
                WHEN NEW.actor='AUTHENTICATED_PARENT_REQUEST_ERROR'
                BEGIN INSERT INTO commit_child VALUES(999); END;
            ''')
        try:
            self.server.call_tool("minionone_resume_request", self.args())
        except Exception as exc:
            self.assertEqual(safe_tool_fault(exc).code, "ERROR_JOURNAL_UNAVAILABLE")
        else:
            self.fail("failed commit returned success")
        self.assertEqual(self.errors(), [])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM admission_events").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM control_errors").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM commit_child").fetchone()[0], 0)

    def test_v015_database_gets_additive_journal_without_losing_existing_receipts(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("DROP TABLE control_errors")
            con.commit()
        migrated = self.open_server()
        self.reject_resume(server=migrated)
        self.assertEqual(len(self.errors(self.open_server())), 1)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM admissions").fetchone()[0], 1)
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_process_crash_before_and_after_journal_commit(self):
        script = '''
import os, sys
from minionone.admission import AdmissionStore
from minionone.mcp_server import LocalMCP
from minionone.timeout_control import TimeoutControlDenied
db, token, job, mode = sys.argv[1:]
s = LocalMCP(("127.0.0.1",0), token=token, database_path=db,
             principal="parent", thread="chat")
if mode == "before":
    original = AdmissionStore._record_control_error
    def crash(self, *args, **kwargs):
        original(self, *args, **kwargs)
        os._exit(23)
    AdmissionStore._record_control_error = crash
try:
    s.call_tool("minionone_resume_request", {
        "job_id":job,"expected_version":0,"operation_id":"crash-test-0001"})
except TimeoutControlDenied:
    os._exit(24)
os._exit(25)
'''
        for mode, expected_count, exit_code in (("before", 0, 23), ("after", 1, 24)):
            with self.subTest(mode=mode):
                result = subprocess.run([sys.executable, "-c", script, str(self.db),
                                         TOKEN, self.job, mode], capture_output=True,
                                        text=True, timeout=15)
                self.assertEqual(result.returncode, exit_code, result.stderr)
                restarted = self.open_server()
                self.assertEqual(len(self.errors(restarted)), expected_count)
                status = restarted.call_tool("minionone_job_status", {"job_id": self.job})
                self.assertEqual((status["state"], status["version"]), ("ACCEPTED", 0))


if __name__ == "__main__":
    unittest.main()
