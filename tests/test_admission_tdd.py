"""Independent positive, negative, adversarial and recovery tests for AdmissionStore."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.minionone.admission import (
    AdmissionStore, PermissionDenied, InputRejected,
    IdempotencyConflict, VersionConflict,
)

TOKEN_A = 'a' * 48
TOKEN_B = 'b' * 48
DIGEST = 'd' * 64


class TestAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'private' / 'jobs.sqlite'
        self.store = AdmissionStore(self.db, credentials={
            TOKEN_A: ('parent-a', {'chat-1'}),
            TOKEN_B: ('parent-b', {'chat-2'}),
        })

    def admit(self, op='request-unique-0001'):
        return self.store.admit(TOKEN_A, 'chat-1', DIGEST, op)

    def test_admit_and_owner_bound_reads(self):
        job = self.admit()
        self.assertTrue(job['job_id'].startswith('JOB-'))
        self.assertEqual(job['state'], 'ACCEPTED')
        self.assertEqual(job['version'], 0)
        self.assertEqual(self.store.status(TOKEN_A, 'chat-1', job['job_id']), job)
        for token, thread in [(TOKEN_B, 'chat-2'), (TOKEN_A, 'chat-2'), ('x'*48, 'chat-1')]:
            with self.subTest(token=token[:1], thread=thread):
                with self.assertRaises(PermissionDenied):
                    self.store.status(token, thread, job['job_id'])
        with self.assertRaises(PermissionDenied):
            self.store.admit(TOKEN_B, 'chat-1', DIGEST, 'request-other-0001')

    def test_idempotent_admit_and_content_collision(self):
        job = self.admit()
        self.assertEqual(job, self.admit())
        with self.assertRaises(IdempotencyConflict):
            self.store.admit(TOKEN_A, 'chat-1', 'a'*64, 'request-unique-0001')

    def test_invalid_inputs_rejected(self):
        for op in ['', 'abc', '../aaaaaaa', 'x'*129]:
            with self.subTest(op=op):
                with self.assertRaises(InputRejected):
                    self.store.admit(TOKEN_A, 'chat-1', DIGEST, op)
        for bad in ['xyz', 'D'*64, 'a'*65, None]:
            with self.subTest(bad=bad):
                with self.assertRaises(InputRejected):
                    self.store.admit(TOKEN_A, 'chat-1', bad, 'request-unique-0001')

    def test_no_client_provided_job_id(self):
        with self.assertRaises(TypeError):
            self.store.admit(TOKEN_A,'chat-1',DIGEST,'request-unique-0001',job_id='JOB-elected')

    def test_restart_and_replay(self):
        first = self.admit()
        again = AdmissionStore(self.db,credentials={TOKEN_A:('parent-a',{'chat-1'})})
        self.assertEqual(first, again.admit(TOKEN_A,'chat-1',DIGEST,'request-unique-0001'))
        self.assertEqual(first, again.status(TOKEN_A,'chat-1',first['job_id']))

    def test_concurrent_admission_deduplicates(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(lambda _: self.admit(), range(16)))
        self.assertEqual(len({x['job_id'] for x in jobs}), 1)

    def test_separate_parent_namespace(self):
        a = self.admit()
        b = self.store.admit(TOKEN_B,'chat-2',DIGEST,'request-unique-0001')
        self.assertNotEqual(a['job_id'], b['job_id'])

    def test_replay_after_abort_returns_original_admission_receipt(self):
        first = self.admit()
        self.store.request_abort(TOKEN_A,'chat-1',first['job_id'],0,'abort-unique-0001')
        self.assertEqual(self.admit(),first)

    def test_versioned_abort_and_no_terminal_rewrite(self):
        job = self.admit()
        aborted = self.store.request_abort(TOKEN_A,'chat-1',job['job_id'],0,'abort-unique-0001')
        self.assertEqual(aborted['state'],'CANCEL_REQUESTED')
        self.assertEqual(aborted['version'],1)
        self.assertEqual(aborted,self.store.request_abort(TOKEN_A,'chat-1',job['job_id'],0,'abort-unique-0001'))
        with self.assertRaises(VersionConflict):
            self.store.request_abort(TOKEN_A,'chat-1',job['job_id'],0,'abort-unique-0002')
        with self.assertRaises(IdempotencyConflict):
            self.store.request_abort(TOKEN_A,'chat-1',job['job_id'],123,'abort-unique-0001')
        final = self.store.confirm_quiescent_cancel(job['job_id'],1,'manager-verified-0001')
        self.assertEqual(final['state'],'CANCELLED')
        with self.assertRaises(VersionConflict):
            self.store.confirm_quiescent_cancel(job['job_id'],2,'manager-verified-0002')

    def test_auth_checked_before_idempotency_and_unknown_probe(self):
        job = self.admit()
        with self.assertRaises(PermissionDenied):
            self.store.request_abort(TOKEN_B,'chat-2',job['job_id'],0,'abort-unique-0001')
        with self.assertRaises(PermissionDenied):
            self.store.status(TOKEN_A,'chat-1','JOB-notreal')

    def test_transactional_journal_and_consistency(self):
        job = self.admit()
        receipt = self.store.request_abort(TOKEN_A,'chat-1',job['job_id'],0,'abort-unique-0001')
        self.assertEqual(len(self.store.events(TOKEN_A,'chat-1',job['job_id'])),2)
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute('select count(*) from jobs').fetchone()[0],1)
            self.assertEqual(con.execute('select count(*) from admissions').fetchone()[0],1)
        self.assertEqual(self.store.status(TOKEN_A,'chat-1',job['job_id']),receipt)

    def test_manager_cannot_claim_completed(self):
        self.admit()
        self.assertFalse(hasattr(self.store,'mark_completed'))
        self.assertFalse(hasattr(self.store,'publish_artifact'))


if __name__ == '__main__':
    unittest.main()
