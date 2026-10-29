"""Production-path restart recovery tests for the real local MCP listener.

Real SQLite files + synthetic unreachable external effects. No actual provider
or worker invocation. Every sqlite3 test connection is explicitly closed.
"""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from minionone.admission import AdmissionStore
from minionone.outbox import OutboxStore
from minionone.attempt_fence import AttemptFenceStore
from minionone.mcp_server import LocalMCP

TOKEN="recovery-internal-test-" + "z"*48
DIGEST="a"*64
NOW="2026-10-29T00:00:00Z"


class TestMCPRecoveryBeforeBind(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"canonical.sqlite"
        store=AdmissionStore(self.db,credentials={TOKEN:("operator",{"thread"})})
        self.job=store.admit(TOKEN,"thread",DIGEST,"admission-0001")["job_id"]
        OutboxStore(self.db)
        AttemptFenceStore(self.db)

    def seed_uncertain(self):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("""INSERT INTO outbox_intents
              (job_id,operation_id,payload_sha256,job_state_version,
               delivery_state,outbox_version,attempts,dispatch_token_hash,
               receipt_json,created_utc,updated_utc)
              VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
              (self.job,"notify-0001",DIGEST,0,"IN_FLIGHT",1,1,"b"*64,"{}",NOW,NOW))
            con.execute("""INSERT INTO attempt_fences
              (job_id,operation_id,job_version,attempt_epoch,status,
               capability_sha256,created_utc,updated_utc)
              VALUES (?,?,?,?,?,?,?,?)""",
              (self.job,"attempt-0001",0,1,"RESERVED","c"*64,NOW,NOW))
            con.commit()

    def listen(self):
        server=LocalMCP(("127.0.0.1",0),token=TOKEN,database_path=self.db)
        self.addCleanup(server.server_close)
        return server

    def test_restart_quarantines_both_effects_before_network_binding(self):
        self.seed_uncertain()
        s=self.listen()
        self.assertGreater(s.server_port,0)
        self.assertEqual(s.recovery_summary,
                         {"outbox_quarantined":1,"attempts_fenced":1})
        self.assertEqual(OutboxStore(self.db).status(self.job,"notify-0001")["delivery_state"],
                         "NEEDS_RECONCILIATION")
        self.assertEqual(AttemptFenceStore(self.db).status(self.job)["state"],"FENCED")
        self.assertFalse(hasattr(s,"dispatch_worker"))
        self.assertFalse(hasattr(s,"send_notification"))

    def test_second_restart_does_not_redeliver_or_reopen(self):
        self.seed_uncertain()
        a=self.listen()
        self.assertEqual(a.recovery_summary["outbox_quarantined"],1)
        b=self.listen()
        self.assertEqual(b.recovery_summary,{"outbox_quarantined":0,"attempts_fenced":0})
        self.assertEqual(OutboxStore(self.db).status(self.job,"notify-0001")["attempt"],1)
        self.assertEqual(AttemptFenceStore(self.db).status(self.job)["state"],"FENCED")

    def test_clean_new_server_has_zero_recovery_counts(self):
        s=self.listen()
        self.assertEqual(s.recovery_summary,{"outbox_quarantined":0,"attempts_fenced":0})

    def test_outbox_recovery_failure_prevents_listener_creation(self):
        with patch("minionone.mcp_server.OutboxStore.quarantine_in_flight",
                   side_effect=RuntimeError("synthetic DB read failure")):
            with self.assertRaises(RuntimeError):
                self.listen()

    def test_fence_recovery_failure_prevents_listener_creation(self):
        with patch("minionone.mcp_server.AttemptFenceStore.fence_unreconciled",
                   side_effect=RuntimeError("synthetic fencing write failure")):
            with self.assertRaises(RuntimeError):
                self.listen()


if __name__=="__main__":
    unittest.main()
