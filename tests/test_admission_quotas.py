"""Quota and replay gates challenge the real durable AdmissionStore."""
import tempfile
import unittest
from pathlib import Path
from minionone.admission import AdmissionStore, InputRejected

SECRET="operator-test-token-"+("Z"*48)
DIGEST="b"*64

class TestAdmissionQuota(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"manager.sqlite"
        self.store=AdmissionStore(self.path,credentials={SECRET:("principal",{"chat"})},
                                  max_jobs_per_principal=2)

    def admit(self,ident):
        return self.store.admit(SECRET,"chat",DIGEST,ident)

    def test_limit_is_transactional_and_replay_survives(self):
        first=self.admit("request-0001")
        second=self.admit("request-0002")
        self.assertNotEqual(first["job_id"],second["job_id"])
        with self.assertRaises(InputRejected):
            self.admit("request-0003")
        self.assertEqual(self.admit("request-0001"),first)
        restarted=AdmissionStore(self.path,credentials={SECRET:("principal",{"chat"})},
                                 max_jobs_per_principal=2)
        self.assertEqual(first,restarted.admit(SECRET,"chat",DIGEST,"request-0001"))
        with self.assertRaises(InputRejected):
            restarted.admit(SECRET,"chat",DIGEST,"request-0003")

    def test_configuration_rejects_bools_and_invalid_cap(self):
        for count in (0,True,-1,4097,"5"):
            with self.subTest(count=count):
                with self.assertRaises(InputRejected):
                    AdmissionStore(Path(self.temp.name)/"invalid.sqlite",
                        credentials={SECRET:("principal",{"chat"})},max_jobs_per_principal=count)

if __name__=="__main__":unittest.main()
