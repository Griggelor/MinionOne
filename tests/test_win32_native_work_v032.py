"""Win32 native Work broker unit feasibility (NOT target host G1b security).

Uses the actual NT handle API, real temp NTFS paths and no model fixture.
The full nine-case MCP/HTTP Work contract remains the product-path test.
"""
from contextlib import ExitStack
import os
import sys
from pathlib import Path
import tempfile
import unittest

from minionone.agent_loop import BrokerScopeDenied
from minionone.admission import InputRejected
from minionone.win32_native_work import _parts


class Win32NameContract(unittest.TestCase):
    def test_worker_components_never_contain_host_path_or_windows_special_names(self):
        for bad in ("../outside", "nested/../../secret", "C:/absolute",
                    "nested\\file", "/absolute", "work//file", "CON", "aux.txt",
                    "foo..txt", "dir.", "nested/" * 7 + "file"):
            with self.subTest(bad=bad), self.assertRaises(BrokerScopeDenied):
                _parts(bad)
        self.assertEqual(_parts("nested/file.txt"), ("nested", "file.txt"))


@unittest.skipUnless(sys.platform == "win32", "native Windows handles required")
class NativeNTFSFileOps(unittest.TestCase):
    def setUp(self):
        from minionone.win32_native_work import _WorkNative
        self.api = _WorkNative()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root_path = Path(temp.name)
        # The trusted admission manager, never the worker, creates the Work
        # root before the broker receives its confined handle.
        (self.root_path / "work").mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.api.root(self.root_path)
        self.stack.callback(self.api.close, self.root)
        self.volume = self.api.validate(self.root, directory=True)[0]
        self.work = self.api.directory(self.root, "work")
        self.assertIsNotNone(self.work)
        self.stack.callback(self.api.close, self.work)
        self.api.validate(self.work, directory=True, volume=self.volume)

    def test_create_read_no_replace_rename_delete_and_nested_dir(self):
        a = self.api
        a.create_file(self.work, "draft.txt", b"FIRST", self.volume)
        self.assertEqual(a.read_work(self.work, "draft.txt", self.volume), b"FIRST")
        existing = a.open_existing(self.work, "draft.txt", for_rename=True)
        try:
            a.rename_scoped(existing, self.work, "result.txt", replace=False)
        finally:
            a.close(existing)
        self.assertEqual(a.read_work(self.work, "result.txt", self.volume), b"FIRST")
        with self.assertRaises(BrokerScopeDenied):
            a.create_file(self.work, "result.txt", b"OVERWRITE", self.volume)
        a.create_file(self.work, "pending-file", b"SECOND", self.volume)
        pending = a.open_existing(self.work, "pending-file", for_rename=True)
        try:
            a.rename_scoped(pending, self.work, "result.txt", replace=True)
        finally:
            a.close(pending)
        self.assertEqual(a.read_work(self.work, "result.txt", self.volume), b"SECOND")
        handle = a.open_existing(self.work, "result.txt", for_rename=True)
        try:
            a.dispose(handle)
        finally:
            a.close(handle)
        self.assertIsNone(a.open_existing(self.work, "result.txt"))
        nested = a.directory(self.work, "nested", create=True)
        self.assertIsNotNone(nested)
        try:
            a.create_file(nested, "file.txt", b"NESTED", self.volume)
            self.assertEqual(a.read_work(nested, "file.txt", self.volume), b"NESTED")
            file = a.open_existing(nested, "file.txt", for_rename=True)
            try:
                a.dispose(file)
            finally:
                a.close(file)
        finally:
            a.close(nested)
        folder = a.directory(self.work, "nested", for_delete=True)
        try:
            a.dispose(folder)
        finally:
            a.close(folder)
        self.assertIsNone(a.directory(self.work, "nested"))

    def test_orphaned_windows_pending_file_blocks_new_mutations(self):
        from minionone.win32_native_work import _inventory
        from minionone.work_path_policy import WorkTransactionUncertain
        self.api.create_file(self.work, "result.txt", b"COMPLETE", self.volume)
        self.api.create_file(self.work, "pending-deadworker", b"PARTIAL", self.volume)
        with self.assertRaises(WorkTransactionUncertain) as observed:
            _inventory(self.api, self.work, self.volume)
        self.assertEqual(observed.exception.diagnostic.code, "PILOT_FILE_CHANGED")
        self.assertEqual(self.api.read_work(self.work, "result.txt", self.volume),
                         b"COMPLETE")

    def test_refuse_hardlink_alias_to_another_file(self):
        foreign = self.root_path / "foreign.txt"
        foreign.write_bytes(b"OUTSIDE SECRET")
        os.link(foreign, self.root_path / "work" / "alias.txt")
        with self.assertRaises(InputRejected):
            self.api.read_work(self.work, "alias.txt", self.volume)
        self.assertEqual(foreign.read_bytes(), b"OUTSIDE SECRET")


if __name__ == "__main__":
    unittest.main()
