"""Independent adversarial boundary tests; no production MCP or NTFS claim."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from minionone.admission import AdmissionStore, PermissionDenied, InputRejected, VersionConflict

TOKEN = 'c' * 48
THREAD = 'verified-thread'
DIGEST = 'f' * 64


class AdversarialAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'db.sqlite'
        self.s = AdmissionStore(self.db, credentials={TOKEN: ('alice', {THREAD})})

    def new_job(self):
        return self.s.admit(TOKEN, THREAD, DIGEST, 'initial-request-0001')

    def test_cross_thread_retry_never_returns_receipt(self):
        job = self.new_job()
        with self.assertRaises(PermissionDenied):
            self.s.admit(TOKEN, 'unverified-thread', DIGEST, 'initial-request-0001')
        with self.assertRaises(PermissionDenied):
            self.s.events(TOKEN, 'unverified-thread', job['job_id'])

    def test_concurrent_conflicting_abort_exactly_one_wins(self):
        job = self.new_job()
        def abort(i):
            try:
                self.s.request_abort(TOKEN, THREAD, job['job_id'], 0, f'abort-unique-{i:04d}')
                return 'success'
            except VersionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(abort, range(16)))
        self.assertEqual(results.count('success'), 1)
        self.assertEqual(results.count('conflict'), 15)
        self.assertEqual(self.s.status(TOKEN, THREAD, job['job_id'])['version'], 1)

    def test_terminal_state_never_reopens(self):
        job = self.new_job()
        self.s.request_abort(TOKEN, THREAD, job['job_id'], 0, 'abort-unique-0001')
        self.s.confirm_quiescent_cancel(job['job_id'], 1, 'manager-verify-0001')
        with self.assertRaises(VersionConflict):
            self.s.request_abort(TOKEN, THREAD, job['job_id'], 2, 'abort-unique-0002')
        self.assertEqual(self.s.status(TOKEN, THREAD, job['job_id'])['state'], 'CANCELLED')

    def test_boolean_is_not_state_version(self):
        job = self.new_job()
        with self.assertRaises(InputRejected):
            self.s.request_abort(TOKEN, THREAD, job['job_id'], False, 'abort-unique-0001')

    def test_malicious_id_and_operation_rejected(self):
        self.new_job()
        for jobid in ['../db.sqlite', 'JOB-good/../../host', 'JOB-good\\secret', 'JOB-bad\x00']:
            with self.subTest(jobid=jobid), self.assertRaises(InputRejected):
                self.s.status(TOKEN, THREAD, jobid)

    def test_cannot_mark_worker_started_in_database(self):
        job = self.new_job()
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite3.connect(self.db) as con:
                con.execute('UPDATE jobs SET worker_started=1 WHERE job_id=?', (job['job_id'],))

    def test_recovery_idempotence_never_mints_new_job(self):
        job = self.new_job()
        restored = AdmissionStore(self.db, credentials={TOKEN: ('alice', {THREAD})})
        self.assertEqual(restored.admit(TOKEN, THREAD, DIGEST, 'initial-request-0001'), job)
        self.assertEqual(restored.status(TOKEN, THREAD, job['job_id']), job)


if __name__ == '__main__':
    unittest.main()
