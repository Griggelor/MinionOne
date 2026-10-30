"""Storage diagnostics at real startup/MCP boundaries; synthetic local state.

Some taxonomy cases inject numeric exceptions; separate tests cause genuine
SQLite lock, read-only, size-limit, corruption and cannot-open failures. A
SQLite size cap is not a physical-disk-full or Windows ACL test.
"""
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
import errno
import http.client
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from minionone import mcp_cli
from minionone.admission import AdmissionStore, PermissionDenied
from minionone.diagnostics import Diagnostic, diagnostic, safe_tool_fault
from minionone.mcp_server import LocalMCP

TOKEN = "synthetic-storage-token-" + "x" * 48
CANARY = "secret-password=/private/synthetic/storage-canary"


def sqlite_fault(code, message=CANARY):
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = code
    return exc


class StorageTaxonomyTests(unittest.TestCase):
    def test_primary_sqlite_codes_have_distinct_safe_actions(self):
        cases = [(sqlite3.SQLITE_BUSY, "DB_BUSY", True),
                 (sqlite3.SQLITE_LOCKED, "DB_BUSY", True),
                 (sqlite3.SQLITE_CORRUPT, "DB_CORRUPT", False),
                 (sqlite3.SQLITE_NOTADB, "DB_CORRUPT", False),
                 (sqlite3.SQLITE_CANTOPEN, "DB_UNAVAILABLE", False),
                 (sqlite3.SQLITE_FULL, "STORAGE_FULL", False),
                 (sqlite3.SQLITE_READONLY, "STORAGE_READ_ONLY", False),
                 (sqlite3.SQLITE_IOERR, "STORAGE_IO_ERROR", False),
                 (sqlite3.SQLITE_PERM, "STORAGE_ACCESS_DENIED", False),
                 (sqlite3.SQLITE_AUTH, "STORAGE_ACCESS_DENIED", False),
                 (sqlite3.SQLITE_SCHEMA, "DB_SCHEMA_ERROR", False),
                 (sqlite3.SQLITE_CONSTRAINT, "DB_INTEGRITY_ERROR", False),
                 (sqlite3.SQLITE_NOMEM, "DB_RESOURCE_EXHAUSTED", False)]
        for number, code, retryable in cases:
            with self.subTest(number=number):
                fault = safe_tool_fault(sqlite_fault(number))
                self.assertEqual(fault.code, code)
                self.assertIs(fault.retryable, retryable)
                self.assertEqual(set(fault.to_dict()), {"code", "message", "action", "retryable"})
                self.assertGreater(len(fault.action), 15)
                self.assertNotIn(CANARY, json.dumps(fault.to_dict()))

    def test_extended_codes_use_the_primary_low_byte(self):
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_FULL,
                     sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_IOERR):
            with self.subTest(code=code):
                self.assertEqual(safe_tool_fault(sqlite_fault(code | (7 << 8))),
                                 safe_tool_fault(sqlite_fault(code)))
                self.assertNotEqual(safe_tool_fault(sqlite_fault(code)).code, "INTERNAL_ERROR")

    def test_missing_malformed_codes_and_error_words_do_not_guess_causes(self):
        for code in (None, True, "13", -1, 1 << 40):
            exc = sqlite_fault(code, "disk full locked corrupt permission " + CANARY)
            with self.subTest(code=code):
                self.assertEqual(safe_tool_fault(exc).code, "DB_ERROR")
        plain = sqlite3.OperationalError("database is locked " + CANARY)
        self.assertEqual(safe_tool_fault(plain).code, "DB_ERROR")

    def test_os_errno_and_known_types_do_not_echo_paths(self):
        cases = [(OSError(errno.ENOSPC, CANARY, CANARY), "STORAGE_FULL"),
                 (OSError(errno.EACCES, CANARY, CANARY), "STORAGE_ACCESS_DENIED"),
                 (OSError(errno.EPERM, CANARY, CANARY), "STORAGE_ACCESS_DENIED"),
                 (OSError(errno.EROFS, CANARY, CANARY), "STORAGE_READ_ONLY"),
                 (OSError(errno.EIO, CANARY, CANARY), "STORAGE_IO_ERROR"),
                 (FileNotFoundError(CANARY), "STORAGE_UNAVAILABLE"),
                 (NotADirectoryError(CANARY), "STORAGE_UNAVAILABLE"),
                 (PermissionError(CANARY), "STORAGE_ACCESS_DENIED"),
                 (OSError("disk full permission " + CANARY), "INTERNAL_ERROR")]
        if hasattr(errno, "EDQUOT"):
            cases.append((OSError(errno.EDQUOT, CANARY), "STORAGE_FULL"))
        for exc, code in cases:
            with self.subTest(code=code, exc=type(exc).__name__):
                fault = safe_tool_fault(exc)
                self.assertEqual(fault.code, code)
                self.assertNotIn(CANARY, json.dumps(fault.to_dict()))
        # This application's job authorization error subclasses PermissionError.
        self.assertEqual(safe_tool_fault(PermissionDenied(CANARY)).code, "ACCESS_DENIED")

    def test_attached_diagnostics_cannot_spoof_storage_cause_or_leak_text(self):
        exc = sqlite_fault(sqlite3.SQLITE_FULL)
        exc.diagnostic = Diagnostic("PROVIDER_AUTH_FAILED", CANARY, CANARY, True)
        self.assertEqual(safe_tool_fault(exc).code, "STORAGE_FULL")
        other = RuntimeError(CANARY)
        other.diagnostic = Diagnostic("PROVIDER_AUTH_FAILED", CANARY, CANARY, True)
        self.assertEqual(safe_tool_fault(other), diagnostic("PROVIDER_AUTH_FAILED"))

    def test_malformed_attached_catalog_key_stays_a_safe_internal_error(self):
        for code in ([], {}, None, True, "unrecognized-" + CANARY):
            with self.subTest(code=type(code).__name__):
                exc = RuntimeError(CANARY)
                exc.diagnostic = Diagnostic(code, CANARY, CANARY, True)
                fault = safe_tool_fault(exc)
                self.assertEqual(fault.code, "INTERNAL_ERROR")
                self.assertNotIn(CANARY, json.dumps(fault.to_dict()))

    def test_real_sqlite_readonly_and_size_cap(self):
        with closing(sqlite3.connect(":memory:")) as con:
            con.execute("CREATE TABLE t(value BLOB)")
            con.execute("PRAGMA query_only=ON")
            with self.assertRaises(sqlite3.Error) as readonly:
                con.execute("INSERT INTO t VALUES(1)")
            self.assertEqual(safe_tool_fault(readonly.exception).code, "STORAGE_READ_ONLY")
        with closing(sqlite3.connect(":memory:")) as con:
            con.execute("PRAGMA page_size=512")
            con.execute("CREATE TABLE t(value BLOB)")
            con.execute("PRAGMA max_page_count=2")
            with self.assertRaises(sqlite3.Error) as full:
                con.execute("INSERT INTO t VALUES(zeroblob(100000))")
            self.assertEqual(safe_tool_fault(full.exception).code, "STORAGE_FULL")


class StartupStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.db = self.home / "synthetic.sqlite"

    def run_startup(self, args=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"MINIONONE_MCP_TOKEN": TOKEN}), \
                patch("minionone.mcp_cli.Path.home", return_value=self.home), \
                redirect_stdout(out), redirect_stderr(err):
            result = mcp_cli.main(args if args is not None else ["--db", str(self.db)])
        self.assertEqual(result, 2)
        self.assertNotIn("MinionOne PILOT:", out.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())
        self.assertNotIn(CANARY, err.getvalue())
        self.assertNotIn(str(self.db), err.getvalue())
        value = json.loads(err.getvalue())
        self.assertIs(value["ok"], False)
        self.assertIs(value["started"], False)
        return value["error"]

    def test_real_corrupt_database_is_preserved_and_listener_never_binds(self):
        data = (CANARY * 128).encode()
        self.db.write_bytes(data)
        with patch("minionone.mcp_server.ThreadingHTTPServer.__init__") as bind:
            fault = self.run_startup()
        self.assertEqual(fault["code"], "DB_CORRUPT")
        self.assertIn("backup", fault["action"].lower())
        bind.assert_not_called()
        self.assertEqual(self.db.read_bytes(), data)

    def test_real_cannot_open_directory_is_typed_and_not_deleted(self):
        self.db.mkdir()
        self.assertEqual(self.run_startup()["code"], "DB_UNAVAILABLE")
        self.assertTrue(self.db.is_dir())

    def test_default_state_directory_failure_is_inside_safe_startup_boundary(self):
        blocked = self.home / ".minionone"
        blocked.write_text(CANARY)
        self.assertEqual(self.run_startup([])["code"], "STORAGE_UNAVAILABLE")
        self.assertEqual(blocked.read_text(), CANARY)

    def test_explicit_db_does_not_require_or_create_default_home_state(self):
        with patch.dict(os.environ, {"MINIONONE_MCP_TOKEN": TOKEN}), \
                patch("minionone.mcp_cli.Path.home", side_effect=AssertionError("unused home")), \
                patch("minionone.mcp_cli.LocalMCP") as constructor, \
                redirect_stdout(io.StringIO()):
            constructor.return_value.server_port = 18765
            self.assertEqual(mcp_cli.main(["--db", str(self.db), "--port", "18765"]), 0)
        constructor.return_value.serve_forever.assert_called_once()
        constructor.return_value.server_close.assert_called_once()

    def test_unexpected_existing_schema_is_not_reported_as_client_input(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("CREATE TABLE jobs(job_id TEXT PRIMARY KEY)")
            con.commit()
        with patch("minionone.mcp_server.ThreadingHTTPServer.__init__") as bind:
            fault = self.run_startup()
        self.assertEqual(fault["code"], "DB_SCHEMA_ERROR")
        bind.assert_not_called()

    def test_foreign_key_damage_is_distinct_from_physical_corruption(self):
        AdmissionStore(self.db, credentials={TOKEN:("parent", {"chat"})})
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("INSERT INTO admission_events VALUES('JOB-missing',1,'ACCEPTED','op','MANAGER','t')")
            con.commit()
        self.assertEqual(self.run_startup()["code"], "DB_INTEGRITY_ERROR")

    def test_known_startup_os_faults_and_unexpected_errors_are_redacted(self):
        cases = [(PermissionError(errno.EACCES, CANARY, CANARY), "STORAGE_ACCESS_DENIED"),
                 (OSError(errno.ENOSPC, CANARY, CANARY), "STORAGE_FULL"),
                 (OSError(errno.EADDRINUSE, CANARY), "MCP_ADDRESS_IN_USE"),
                 (ValueError(CANARY), "CONFIGURATION_INVALID"),
                 (RuntimeError(CANARY), "INTERNAL_ERROR")]
        for exc, code in cases:
            with self.subTest(code=code), patch("minionone.mcp_cli.LocalMCP", side_effect=exc):
                self.assertEqual(self.run_startup()["code"], code)


class MCPStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "synthetic.sqlite"
        self.server = LocalMCP(("127.0.0.1", 0), token=TOKEN, database_path=self.db,
                               principal="parent", thread="chat")
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.job = self.server.call_tool("minionone_admit", self.admit_args("admit-original-0001"))["job_id"]

    def admit_args(self, operation):
        return {"input_digest":"a" * 64, "operation_id":operation}

    def rpc(self, name, args):
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)) as conn:
            conn.request("POST", "/mcp", json.dumps({"jsonrpc":"2.0", "id":1,
                         "method":"tools/call", "params":{"name":name,"arguments":args}}),
                         headers={"Authorization":"Bearer " + TOKEN, "Content-Type":"application/json",
                                  "Accept":"application/json, text/event-stream"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            result = json.loads(response.read())["result"]
        value = json.loads(result["content"][0]["text"])
        self.assertNotIn(CANARY, json.dumps(value))
        self.assertNotIn(TOKEN, json.dumps(value))
        self.assertNotIn(str(self.db), json.dumps(value))
        return result, value

    def test_real_writer_lock_reports_busy_without_retry_or_partial_admission(self):
        original = self.server.store._connection

        @contextmanager
        def short_lock_wait():
            with original() as con:
                # Only reduce SQLite's wait budget; actual SQL and failure stay real.
                con.execute("PRAGMA busy_timeout=25")
                yield con

        with closing(sqlite3.connect(self.db, isolation_level=None)) as locker:
            locker.execute("BEGIN IMMEDIATE")
            with patch.object(self.server.store, "_connection", short_lock_wait):
                result, value = self.rpc("minionone_admit", self.admit_args("admit-after-lock-0001"))
            self.assertTrue(result["isError"])
            self.assertEqual(value["error"]["code"], "DB_BUSY")
            self.assertTrue(value["error"]["retryable"])
            self.assertIn("retry", value["error"]["action"].lower())
            locker.rollback()
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM jobs").fetchone()[0], 1)
        # Deliberate client decision after releasing the writer, not an auto retry.
        _, admitted = self.rpc("minionone_admit", self.admit_args("admit-after-lock-0001"))
        _, replayed = self.rpc("minionone_admit", self.admit_args("admit-after-lock-0001"))
        self.assertEqual(replayed, admitted)

    def test_numeric_storage_failures_cross_real_http_boundary(self):
        for code, expected in ((sqlite3.SQLITE_FULL,"STORAGE_FULL"),
                               (sqlite3.SQLITE_READONLY,"STORAGE_READ_ONLY"),
                               (sqlite3.SQLITE_CORRUPT,"DB_CORRUPT")):
            with self.subTest(code=code), patch.object(self.server.store, "admit", side_effect=sqlite_fault(code)):
                result, value = self.rpc("minionone_admit", self.admit_args("injected-0001"))
                self.assertTrue(result["isError"])
                self.assertEqual(value["error"]["code"], expected)
                self.assertFalse(value["error"]["retryable"])

    def test_journal_failure_keeps_primary_code_but_gives_specific_storage_action(self):
        with patch.object(self.server.store, "_record_control_error", side_effect=sqlite_fault(sqlite3.SQLITE_FULL)):
            result, value = self.rpc("minionone_resume_request", {
                "job_id":self.job,"expected_version":0,"operation_id":"resume-no-checkpoint-0001"})
        self.assertTrue(result["isError"])
        self.assertEqual(value["error"]["code"], "ERROR_JOURNAL_UNAVAILABLE")
        self.assertIn("space", value["error"]["action"].lower())
        events = self.server.call_tool("minionone_job_events", {"job_id":self.job})["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(self.server.call_tool("minionone_job_status", {"job_id":self.job})["version"], 0)

    def test_corrupt_runtime_file_reports_error_instead_of_successful_status(self):
        # Replace only this synthetic file and its WAL while no connection is open.
        with self.server.store._connection() as con:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.write_bytes((CANARY * 128).encode())
        result, value = self.rpc("minionone_job_status", {"job_id":self.job})
        self.assertTrue(result["isError"])
        self.assertEqual(value["error"]["code"], "DB_CORRUPT")


if __name__ == "__main__":
    unittest.main()
