"""Negative POSIX file-boundary checks for the new Work broker.

These are additional low-level tests, not substitutes for the real MCP HTTP
worker integration cases or Windows G1b target-host security attestation.
"""
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipIf(os.name == "nt", "POSIX fd-relative broker only")
class NativeWorkBoundaries(unittest.TestCase):
    def setUp(self):
        from minionone.native_work import _FLAGS
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "work").mkdir()
        self.fd = os.open(str(self.root / "work"), _FLAGS)
        self.addCleanup(os.close, self.fd)

    def test_rejects_all_escape_and_windows_alias_path_components(self):
        from minionone.native_work import _parts
        from minionone.agent_loop import BrokerScopeDenied
        unsafe = ("../outside", "nested/../../outside", "nested//file",
                  "/absolute", "C:/file", "file\\name", ".hidden",
                  "foo..bar", "dir.", "CON", "aux.txt", "x/nul",
                  "a/" * 6 + "tail")
        for name in unsafe:
            with self.subTest(name=name), self.assertRaises(BrokerScopeDenied):
                _parts(name)
        self.assertEqual(_parts("nested/item.txt"), ("nested", "item.txt"))

    def test_symlink_is_never_a_readable_work_file(self):
        from minionone.native_work import _read, _inventory
        from minionone.agent_loop import BrokerScopeDenied
        (self.root / "secret.txt").write_bytes(b"CANARY")
        os.symlink(self.root / "secret.txt", self.root / "work" / "link.txt")
        with self.assertRaises(BrokerScopeDenied):
            _read(self.fd, "link.txt")
        with self.assertRaises(BrokerScopeDenied):
            _inventory(self.fd)
        self.assertEqual((self.root / "secret.txt").read_bytes(), b"CANARY")

    def test_external_hardlink_is_never_a_readable_work_file(self):
        from minionone.native_work import _read, _inventory
        from minionone.agent_loop import BrokerScopeDenied
        foreign = self.root / "foreign.txt"
        foreign.write_bytes(b"OUTSIDE")
        os.link(foreign, self.root / "work" / "alias.txt")
        with self.assertRaises(BrokerScopeDenied):
            _read(self.fd, "alias.txt")
        with self.assertRaises(BrokerScopeDenied):
            _inventory(self.fd)
        self.assertEqual(foreign.read_bytes(), b"OUTSIDE")

    def test_create_is_exclusive_and_never_truncates(self):
        from minionone.native_work import _write_new, _read
        from minionone.agent_loop import BrokerScopeDenied
        _write_new(self.fd, "result.txt", b"ORIGINAL")
        with self.assertRaises(BrokerScopeDenied):
            _write_new(self.fd, "result.txt", b"EVIL")
        self.assertEqual(_read(self.fd, "result.txt"), b"ORIGINAL")

    def test_orphaned_pending_work_file_quarantines_the_entire_inventory(self):
        from minionone.native_work import _inventory, _parts
        from minionone.work_path_policy import WorkTransactionUncertain
        from minionone.agent_loop import BrokerScopeDenied
        (self.root / "work" / "result.txt").write_bytes(b"COMPLETE")
        (self.root / "work" / "pending-deadworker").write_bytes(b"PARTIAL")
        with self.assertRaises(WorkTransactionUncertain) as observed:
            _inventory(self.fd)
        self.assertEqual(observed.exception.diagnostic.code, "PILOT_FILE_CHANGED")
        with self.assertRaises(BrokerScopeDenied):
            _parts("pending-deadworker")
        self.assertEqual((self.root / "work" / "result.txt").read_bytes(), b"COMPLETE")
        self.assertEqual((self.root / "work" / "pending-deadworker").read_bytes(), b"PARTIAL")

    def test_intermediate_symlink_directory_is_denied(self):
        from minionone.native_work import _parent
        from minionone.agent_loop import BrokerScopeDenied
        (self.root / "outside").mkdir()
        os.symlink(self.root / "outside", self.root / "work" / "nested")
        with self.assertRaises(BrokerScopeDenied):
            with _parent(self.fd, ("nested", "result.txt")):
                pass
        self.assertEqual(os.listdir(self.root / "outside"), [])


if __name__ == "__main__":
    unittest.main()
