"""A status response must use one SQLite snapshot, including timeout metadata."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from minionone.mcp_server import LocalMCP

TOKEN='synthetic-atomic-status-'+'x'*48

class AtomicStatusTests(unittest.TestCase):
    def test_actual_mcp_status_never_stitches_two_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            server=LocalMCP(('127.0.0.1',0),token=TOKEN,database_path=Path(tmp)/'state.sqlite')
            try:
                job=server.call_tool('minionone_admit',{'input_digest':'a'*64,'operation_id':'atomic-admit-0001'})['job_id']
                original=server.store.status
                def read_then_update(*args,**kwargs):
                    result=original(*args,**kwargs)
                    with closing(sqlite3.connect(server.store.database_path)) as con:
                        con.execute('UPDATE jobs SET version=version+1 WHERE job_id=?',(job,));con.commit()
                    return result
                def second_read(*_):
                    return {'job_id':job,'state':'ACCEPTED','version':1}
                with patch.object(server.store,'status',side_effect=read_then_update), \
                     patch.object(server.timeout_control,'status',side_effect=second_read) as separate:
                    result=server.call_tool('minionone_job_status',{'job_id':job})
                self.assertEqual(result['version'],0)
                self.assertNotIn('timeout',result)
                separate.assert_not_called()

            finally:
                server.server_close()
