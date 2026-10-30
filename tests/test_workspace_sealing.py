"""Independent local POSIX seal/manifest/SQL-state/negative-path tests.

These do NOT attest native Windows confinement or executable workers.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest

from minionone.admission import AdmissionStore, InputRejected, IdempotencyConflict
from minionone.workspace_preparation import WorkspacePreparation

TOKEN='workspace-seal-token-'+('z'*48)

@unittest.skipIf(os.name=='nt','Win32 handle-relative file broker not yet verified; seal fail closed')
class POSIXWorkspaceSealTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.project=self.home/'project';self.project.mkdir()
        self.db=self.home/'manager.sqlite'
        self.store=AdmissionStore(self.db,credentials={TOKEN:('owner',{'thread'})})
        self.manager=WorkspacePreparation(self.store,{'demo':self.project})
        self.rec=self.manager.reserve(TOKEN,'thread','demo','prepare-seal-0001')
        self.input=Path(self.rec['input_dir'])
        self.task=b'Create a synthetic example summary.\n'
        self.sample=b'Synthetic input.\n'
        self.write_inputs()

    def write_inputs(self):
        (self.input/'TASK.md').write_bytes(self.task)
        (self.input/'sample.txt').write_bytes(self.sample)
        entries={'TASK.md':hashlib.sha256(self.task).hexdigest(),
                 'sample.txt':hashlib.sha256(self.sample).hexdigest()}
        (self.input/'manifest.json').write_text(json.dumps({'files':entries},sort_keys=True),encoding='utf-8')

    def seal(self,op='seal-00000001'):
        return self.manager.seal(TOKEN,'thread',self.rec['job_id'],0,op)

    def test_admission_same_job_id_canonical_and_pinned_bytes(self):
        receipt=self.seal()
        self.assertEqual(receipt['state'],'ACCEPTED')
        self.assertEqual(receipt['job_id'],self.rec['job_id'])
        self.assertFalse(receipt['worker_execution_enabled'])
        self.assertTrue(receipt['input_sealed'])
        self.assertEqual(self.store.status(TOKEN,'thread',receipt['job_id'])['input_digest'],receipt['input_digest'])
        self.assertEqual(self.manager.status(TOKEN,'thread',receipt['job_id'])['state'],'ACCEPTED')
        with sqlite3.connect(self.db) as con:
            rows=con.execute('SELECT name,content FROM workspace_input_blobs WHERE job_id=?',(receipt['job_id'],)).fetchall()
        self.assertEqual(dict(rows)['TASK.md'],self.task)
        self.assertEqual(self.manager.verify_input(TOKEN,'thread',receipt['job_id'])['input_integrity'],'VERIFIED')

    def test_seal_idempotent_through_restart(self):
        first=self.seal()
        manager=WorkspacePreparation(AdmissionStore(self.db,credentials={TOKEN:('owner',{'thread'})}),{'demo':self.project})
        second=manager.seal(TOKEN,'thread',self.rec['job_id'],0,'seal-00000001')
        self.assertEqual(first,second)
        with self.assertRaises(IdempotencyConflict):manager.seal(TOKEN,'thread',self.rec['job_id'],0,'seal-00000002')

    def test_mutated_manifest_hash_no_partial_canonical_admission(self):
        (self.input/'sample.txt').write_bytes(b'tampered')
        with self.assertRaises(InputRejected):self.seal()
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM jobs WHERE job_id=?',(self.rec['job_id'],)).fetchone()[0],0)
        self.assertEqual(self.manager.status(TOKEN,'thread',self.rec['job_id'])['state'],'PREPARING')

    def test_added_undeclared_input_rejected(self):
        (self.input/'unexpected.txt').write_bytes(b'a')
        with self.assertRaises(InputRejected):self.seal()

    def test_input_symlink_and_directory_denied(self):
        outside=self.home/'outside.txt';outside.write_text('outside')
        (self.input/'sample.txt').unlink()
        try:(self.input/'sample.txt').symlink_to(outside)
        except OSError:self.skipTest('symlinks unsupported')
        with self.assertRaises(InputRejected):self.seal()
        (self.input/'sample.txt').unlink()
        (self.input/'sample.txt').mkdir()
        with self.assertRaises(InputRejected):self.seal()

    def test_hardlink_denied(self):
        source=self.home/'source.txt';source.write_bytes(self.sample)
        (self.input/'sample.txt').unlink()
        os.link(source,self.input/'sample.txt')
        with self.assertRaises(InputRejected):self.seal()

    def test_oversized_and_empty_task_denied(self):
        self.task=b'A'*70000;self.write_inputs()
        with self.assertRaises(InputRejected):self.seal()
        self.task=b'  \n';self.write_inputs()
        with self.assertRaises(InputRejected):self.seal()

    def test_task_only_job_is_legal(self):
        (self.input/'sample.txt').unlink()
        (self.input/'manifest.json').write_text(json.dumps({'files':{
            'TASK.md':hashlib.sha256(self.task).hexdigest()}}),encoding='utf-8')
        self.assertEqual(self.seal()['state'],'ACCEPTED')

    def test_manifest_duplicate_key_denied(self):
        body=b'{"files":{"TASK.md":"abc","TASK.md":"def"}}'
        (self.input/'manifest.json').write_bytes(body)
        with self.assertRaises(InputRejected):self.seal()

    def test_tamper_after_seal_detected_without_rewriting_saved_blobs(self):
        self.seal();self.task=b'Different post-seal task.';self.write_inputs()
        with self.assertRaises(InputRejected):self.manager.verify_input(TOKEN,'thread',self.rec['job_id'])
        with sqlite3.connect(self.db) as con:
            saved=con.execute("SELECT content FROM workspace_input_blobs WHERE job_id=? AND name='TASK.md'",(self.rec['job_id'],)).fetchone()[0]
        self.assertNotEqual(saved,self.task)

    def test_output_log_is_redacted_and_reconstructable(self):
        self.seal()
        log=Path(self.rec['output_dir'])/'job.log.jsonl'
        old=log.read_bytes()
        self.assertNotIn(self.task,old);self.assertNotIn(self.sample,old)
        log.write_text('broken',encoding='utf-8')
        self.seal()
        self.assertEqual(old,log.read_bytes())
        self.assertEqual(json.loads(log.read_text())['status'],'ACCEPTED')

    def test_concurrent_seal_same_operation_single_canonical_job(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            replies=list(pool.map(lambda _:self.seal(),range(3)))
        self.assertEqual(len({r['input_digest'] for r in replies}),1)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM jobs WHERE job_id=?',(self.rec['job_id'],)).fetchone()[0],1)

    def test_project_directory_substitution_denied(self):
        original=self.home/'relocated'
        self.project.rename(original)
        self.project.mkdir()
        with self.assertRaises(InputRejected):self.seal()

    def test_upgrades_v019_workspace_state_schema_without_losing_job(self):
        # Emulate legacy v0.19 workspace state table prior to v0.20 constructor.
        with sqlite3.connect(self.db) as con:
            con.execute('DROP TABLE workspace_reservations')
            con.execute('''CREATE TABLE workspace_reservations (
                job_id TEXT PRIMARY KEY, principal TEXT NOT NULL,
                thread_id TEXT NOT NULL, project_id TEXT NOT NULL,
                operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('ALLOCATING','PREPARING')),
                created_utc TEXT NOT NULL, UNIQUE(principal,operation_id))''')
            con.execute('INSERT INTO workspace_reservations VALUES (?,?,?,?,?,?,?,?)',
                (self.rec['job_id'],'owner','thread','demo','prepare-seal-0001','hash','PREPARING','time'))
        restarted=WorkspacePreparation(self.store,{'demo':self.project})
        receipt=restarted.seal(TOKEN,'thread',self.rec['job_id'],0,'seal-00000001')
        self.assertEqual(receipt['state'],'ACCEPTED')


class WindowsGateBehaviorTests(unittest.TestCase):
    def test_no_general_worker_access_or_start_tool(self):
        with tempfile.TemporaryDirectory() as d:
            project=Path(d)/'project';project.mkdir()
            from minionone.mcp_server import LocalMCP
            srv=LocalMCP(('127.0.0.1',0),token=TOKEN,
                database_path=Path(d)/'db.sqlite',workspace_projects={'demo':project})
            try:
                self.assertIn('minionone_seal_workspace',srv.tool_map)
                self.assertNotIn('minionone_start_workspace_job',srv.tool_map)
                self.assertFalse(srv.call_tool('minionone_status',{})['host_filesystem_tools_enabled'])
            finally:srv.server_close()

if __name__=='__main__':unittest.main()
