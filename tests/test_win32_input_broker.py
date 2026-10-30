"""Native NTFS input broker adversarial evidence, no worker permissions."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from minionone.admission import InputRejected
from minionone.win32_input_broker import WindowsInputBroker

JOB="JOB-"+"a"*32

@unittest.skipUnless(sys.platform=="win32","native NTFS evidence only")
class NativeReadBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="minionone-win32-read-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/"project"
        self.job=self.root/".minionone"/"jobs"/JOB
        self.input=self.job/"input"
        self.input.mkdir(parents=True)
        (self.job/"work").mkdir()
        (self.job/"output").mkdir()
        (self.input/"TASK.md").write_bytes(b"synthetic task")
        (self.input/"sample.txt").write_bytes(b"abcde")

    def test_native_pinned_enumeration_and_content(self):
        with WindowsInputBroker(self.root,JOB) as broker:
            self.assertEqual(set(broker.list_input()),{"TASK.md","sample.txt"})
            self.assertEqual(broker.read_input("TASK.md"),b"synthetic task")
            self.assertEqual(broker.read_input("sample.txt"),b"abcde")
        print("G1B_NATIVE_READ_CONTENT_PASS")

    def test_invalid_names_and_scopes_denied(self):
        with WindowsInputBroker(self.root,JOB) as broker:
            for value in ("../work/fake","..",".","CON","NUL.txt","file:stream",
                          r"C:\Windows",r"\\host\share","output/file","work\\file",
                          "sample.txt.","", "invalid?.txt"):
                with self.subTest(value=value),self.assertRaises(InputRejected):
                    broker.read_input(value)
            with self.assertRaises(InputRejected):
                broker.read_input("nonexistent.txt")

    def test_hardlink_rejected(self):
        outside=self.root/"secret.txt"
        outside.write_bytes(b"outside")
        os.link(outside,self.input/"alias.txt")
        with WindowsInputBroker(self.root,JOB) as broker:
            self.assertIn("alias.txt",broker.list_input())
            with self.assertRaises(InputRejected):
                broker.read_input("alias.txt")
        self.assertEqual(outside.read_bytes(),b"outside")

    def test_file_symlink_rejected(self):
        outside=self.root/"outside.txt"
        outside.write_bytes(b"outside")
        try:
            os.symlink(outside,self.input/"alias.txt")
        except (OSError,NotImplementedError) as exc:
            self.skipTest("symlink not supported on runner: "+type(exc).__name__)
        with WindowsInputBroker(self.root,JOB) as broker:
            with self.assertRaises(InputRejected):
                broker.read_input("alias.txt")

    def test_directory_symlink_rejected(self):
        outside=self.root/"outside"
        outside.mkdir()
        (outside/"TASK.md").write_bytes(b"outside")
        self.input.rename(self.job/"input-old")
        try:
            os.symlink(outside,self.input,target_is_directory=True)
        except (OSError,NotImplementedError) as exc:
            self.skipTest("directory symlink unsupported: "+type(exc).__name__)
        with self.assertRaises(InputRejected):
            with WindowsInputBroker(self.root,JOB):
                pass

    def test_rename_blocked_while_directory_pinned(self):
        with WindowsInputBroker(self.root,JOB) as broker:
            with self.assertRaises(OSError):
                self.input.rename(self.job/"moved-input")
            self.assertEqual(broker.read_input("sample.txt"),b"abcde")

    def test_parent_substitution_and_untrusted_id_denied(self):
        with self.assertRaises(InputRejected):
            with WindowsInputBroker(self.root,"JOB-../escape"):
                pass
        with self.assertRaises(InputRejected):
            with WindowsInputBroker(self.root/"no-project",JOB):
                pass

    def test_quota_bounded_and_empty_files(self):
        (self.input/"empty.txt").write_bytes(b"")
        (self.input/"big.txt").write_bytes(b"X"*100)
        with WindowsInputBroker(self.root,JOB) as broker:
            self.assertEqual(broker.read_input("empty.txt",max_bytes=0),b"")
            with self.assertRaises(InputRejected):
                broker.read_input("big.txt",max_bytes=10)
            with self.assertRaises(InputRejected):
                broker.read_input("big.txt",max_bytes=-1)

class NonWindowsFailClosed(unittest.TestCase):
    @unittest.skipIf(sys.platform=="win32","non-Windows only")
    def test_win32_broker_does_not_work_on_posix(self):
        with self.assertRaises(InputRejected):
            with WindowsInputBroker("/tmp",JOB):
                pass

if __name__=="__main__":
    unittest.main(verbosity=2)
