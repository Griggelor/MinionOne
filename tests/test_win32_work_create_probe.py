"""Adversarial Windows CREATE_NEW work probe tests. NOT product acceptance."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

from minionone.admission import InputRejected
from minionone.win32_work_create_probe import WindowsWorkCreateProbe

JOB="JOB-"+"b"*32
OTHER="JOB-"+"c"*32

@unittest.skipUnless(sys.platform=="win32","native Windows NTFS evidence only")
class NativeWorkCreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="minionone-work-probe-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"project"
        self.job=self.root/".minionone"/"jobs"/JOB
        self.work=self.job/"work"
        self.work.mkdir(parents=True)
        (self.job/"input").mkdir()
        (self.job/"output").mkdir()
        self.other=self.root/".minionone"/"jobs"/OTHER/"work"
        self.other.mkdir(parents=True)

    def test_create_new_regular_and_digest(self):
        data=b"synthetic output \x00\xff"
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            result=broker.create_work("result.bin",data)
            self.assertEqual(result["sha256"],hashlib.sha256(data).hexdigest())
            self.assertFalse(result["published"])
            self.assertTrue(result["identity_verified_on_open_handle"])
        self.assertEqual((self.work/"result.bin").read_bytes(),data)
        print("G1B_CREATE_ONLY_WORK_PASS")

    def test_zero_and_multichunk_exact(self):
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            broker.create_work("empty.txt",b"")
            broker.create_work("large.bin",b"z"*130001)
        self.assertEqual((self.work/"empty.txt").read_bytes(),b"")
        self.assertEqual((self.work/"large.bin").stat().st_size,130001)

    def test_existing_file_never_overwritten(self):
        target=self.work/"exists.txt"
        target.write_bytes(b"original")
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            with self.assertRaises(InputRejected):
                broker.create_work("exists.txt",b"replacement")
            with self.assertRaises(InputRejected):
                broker.create_work("after_error.txt",b"blocked session")
        self.assertEqual(target.read_bytes(),b"original")
        self.assertFalse((self.work/"after_error.txt").exists())

    def test_invalid_destinations_rejected(self):
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            for name in ("../output/report.txt","..",".",r"..\input\TASK.md",
                         "input/TASK.md","output/report.txt","secret:stream",
                         r"C:\Windows\bad.txt",r"\\server\share","NUL.txt",
                         "COM1", "report.txt.","", "a?.txt"):
                with self.subTest(name=name),self.assertRaises(InputRejected):
                    broker.create_work(name,b"sentinel")
        self.assertEqual(list(self.work.iterdir()),[])

    def test_existing_hardlink_cannot_be_overwritten(self):
        outside=self.root/"outside.txt"
        outside.write_bytes(b"private")
        os.link(outside,self.work/"alias.txt")
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            with self.assertRaises(InputRejected):
                broker.create_work("alias.txt",b"overwritten")
        self.assertEqual(outside.read_bytes(),b"private")

    def test_existing_symlink_cannot_be_overwritten(self):
        outside=self.root/"outside.txt"
        outside.write_bytes(b"private")
        try:
            os.symlink(outside,self.work/"link.txt")
        except (OSError,NotImplementedError) as exc:
            self.skipTest("symlinks unsupported: "+type(exc).__name__)
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            with self.assertRaises(InputRejected):
                broker.create_work("link.txt",b"overwritten")
        self.assertEqual(outside.read_bytes(),b"private")

    def test_reparse_work_directory_denied(self):
        outside=self.root/"outside"
        outside.mkdir()
        self.work.rename(self.job/"work-old")
        try:
            os.symlink(outside,self.work,target_is_directory=True)
        except (OSError,NotImplementedError) as exc:
            self.skipTest("directory symlinks unsupported: "+type(exc).__name__)
        with self.assertRaises(InputRejected):
            with WindowsWorkCreateProbe(self.root,JOB):
                pass
        self.assertEqual(list(outside.iterdir()),[])

    def test_directory_handle_blocks_rename_while_open(self):
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            with self.assertRaises(OSError):
                self.work.rename(self.job/"work-renamed")
            broker.create_work("new.txt",b"inside pinned directory")
        self.assertTrue((self.work/"new.txt").exists())

    def test_cross_job_not_accessible(self):
        (self.other/"foreign.txt").write_bytes(b"foreign")
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            broker.create_work("local.txt",b"local")
            with self.assertRaises(InputRejected):
                broker.create_work("../"+OTHER+"/work/foreign.txt",b"bad")
        self.assertEqual((self.other/"foreign.txt").read_bytes(),b"foreign")

    def test_bad_job_id_or_root_denied(self):
        for value in ("../x","JOB-incorrect",OTHER+"/work"):
            with self.assertRaises(InputRejected):
                WindowsWorkCreateProbe(self.root,value)
        with self.assertRaises(InputRejected):
            with WindowsWorkCreateProbe(self.root/"missing",JOB):
                pass

    def test_quota_type_and_file_count(self):
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            for bad in (bytearray(b"x"),"text",None):
                with self.assertRaises(InputRejected):
                    broker.create_work("invalid.bin",bad)
            with self.assertRaises(InputRejected):
                broker.create_work("too-big.bin",b"x"*(1024*1024+1))
            for n in range(16):
                broker.create_work(f"part-{n}.txt",b"x")
            with self.assertRaises(InputRejected):
                broker.create_work("seventeenth.txt",b"x")
        self.assertEqual(len(list(self.work.iterdir())),16)

    def test_closed_broker_refuses_write(self):
        with WindowsWorkCreateProbe(self.root,JOB) as broker:
            broker.create_work("only.txt",b"one")
        with self.assertRaises(InputRejected):
            broker.create_work("after.txt",b"two")
        self.assertFalse((self.work/"after.txt").exists())

class OtherHostFailClosed(unittest.TestCase):
    @unittest.skipIf(sys.platform=="win32","POSIX safety check")
    def test_no_native_windows_probe_on_posix(self):
        with self.assertRaises(InputRejected):
            WindowsWorkCreateProbe("/tmp",JOB)

if __name__=="__main__":
    unittest.main(verbosity=2)
