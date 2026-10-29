"""G1b Win32 feasibility experiments ONLY; not a secure worker file broker.

Runs on Windows CI with fresh synthetic temporary files. No user data touched.
Windows native APIs used here are documented by Microsoft:
CreateFileW, GetFileInformationByHandleEx(FileIdInfo/FileStandardInfo/
FileAttributeTagInfo), CloseHandle. The probe is deliberately NOT exposed
as a MinionOne MCP tool, and it does not authorize any worker file operations.
"""
from __future__ import annotations
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "win32", "Win32 handle experiments require Windows")
class WindowsHandleFeasibility(unittest.TestCase):
    FILE_READ_ATTRIBUTES = 0x80
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_ALL = 0x7
    CREATE_NEW = 1
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_STANDARD_INFO_CLASS = 1
    FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    FILE_ID_INFO_CLASS = 18

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [("VolumeSerialNumber", ctypes.c_uint64),
                    ("FileId", ctypes.c_ubyte * 16)]

    class FILE_STANDARD_INFO(ctypes.Structure):
        _fields_ = [("AllocationSize", ctypes.c_int64),
                    ("EndOfFile", ctypes.c_int64),
                    ("NumberOfLinks", wintypes.DWORD),
                    ("DeletePending", wintypes.BOOLEAN),
                    ("Directory", wintypes.BOOLEAN)]

    class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD),
                    ("ReparseTag", wintypes.DWORD)]

    @classmethod
    def setUpClass(cls):
        if sys.platform != "win32":
            return
        cls.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        cls.create = cls.kernel.CreateFileW
        cls.create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                               wintypes.HANDLE]
        cls.create.restype = wintypes.HANDLE
        cls.info = cls.kernel.GetFileInformationByHandleEx
        cls.info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        cls.info.restype = wintypes.BOOL
        cls.close = cls.kernel.CloseHandle
        cls.close.argtypes = [wintypes.HANDLE]
        cls.close.restype = wintypes.BOOL

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="minionone-g1b-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.other = self.root / "not-authorized"
        self.work.mkdir()
        self.other.mkdir()

    @contextmanager
    def _handle(self, filename, *, create=False):
        disposition = self.CREATE_NEW if create else self.OPEN_EXISTING
        access = self.GENERIC_WRITE if create else self.FILE_READ_ATTRIBUTES
        flags = (self.FILE_ATTRIBUTE_NORMAL if create else
                 self.FILE_FLAG_OPEN_REPARSE_POINT | self.FILE_FLAG_BACKUP_SEMANTICS)
        h = self.create(str(filename), access, self.FILE_SHARE_ALL,
                        None, disposition, flags, None)
        invalid = ctypes.c_void_p(-1).value
        if h is None or h == invalid:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield h
        finally:
            if not self.close(h):
                raise ctypes.WinError(ctypes.get_last_error())

    def _query(self, handle, klass, structure):
        answer = structure()
        if not self.info(handle, klass, ctypes.byref(answer), ctypes.sizeof(answer)):
            raise ctypes.WinError(ctypes.get_last_error())
        return answer

    def test_win32_file_id_stable_for_reopened_regular_file(self):
        path = self.work / "hello.txt"
        path.write_bytes(b"synthetic")
        with self._handle(path) as a:
            first = self._query(a, self.FILE_ID_INFO_CLASS, self.FILE_ID_INFO)
            tag = self._query(a, self.FILE_ATTRIBUTE_TAG_INFO_CLASS,
                              self.FILE_ATTRIBUTE_TAG_INFO)
            self.assertFalse(tag.FileAttributes & self.FILE_ATTRIBUTE_REPARSE_POINT)
        with self._handle(path) as b:
            second = self._query(b, self.FILE_ID_INFO_CLASS, self.FILE_ID_INFO)
        self.assertEqual(first.VolumeSerialNumber, second.VolumeSerialNumber)
        self.assertEqual(bytes(first.FileId), bytes(second.FileId))
        self.assertNotEqual(bytes(first.FileId), bytes(16))

    def test_hardlink_alias_is_detectable_by_handle_identity_and_link_count(self):
        outside = self.other / "secret-synthetic.txt"
        outside.write_bytes(b"not a worker-scoped file")
        alias = self.work / "alias.txt"
        os.link(outside, alias)
        with self._handle(outside) as a, self._handle(alias) as b:
            a_id = self._query(a, self.FILE_ID_INFO_CLASS, self.FILE_ID_INFO)
            b_id = self._query(b, self.FILE_ID_INFO_CLASS, self.FILE_ID_INFO)
            links = self._query(b, self.FILE_STANDARD_INFO_CLASS,
                                self.FILE_STANDARD_INFO).NumberOfLinks
        self.assertEqual((a_id.VolumeSerialNumber, bytes(a_id.FileId)),
                         (b_id.VolumeSerialNumber, bytes(b_id.FileId)))
        self.assertGreaterEqual(links, 2)
        # Identity proves a hardlink can be 'inside' by spelling while
        # referring to an object with an outside name. MUST reject in broker.

    def test_create_new_cannot_replace_existing_file(self):
        file_path = self.work / "new-file.txt"
        with self._handle(file_path, create=True):
            pass
        self.assertTrue(file_path.exists())
        with self.assertRaises(OSError) as cm:
            with self._handle(file_path, create=True):
                pass
        self.assertEqual(getattr(cm.exception, "winerror", None), 80)
        self.assertEqual(file_path.stat().st_size, 0)

    def test_runner_volume_and_windows_build_reported(self):
        get_volume = self.kernel.GetVolumeInformationW
        get_volume.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
        ]
        get_volume.restype = wintypes.BOOL
        filesystem = ctypes.create_unicode_buffer(64)
        serial = wintypes.DWORD()
        flags = wintypes.DWORD()
        if not get_volume(str(self.root.anchor), None, 0, ctypes.byref(serial),
                          None, ctypes.byref(flags), filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        print("G1B_RUNNER_FACTS windows_build=%d fs=%s feature_flags=0x%X" %
              (sys.getwindowsversion().build, filesystem.value, flags.value))
        self.assertEqual(filesystem.value.upper(), "NTFS",
                         "G1b experiment cannot assume target filesystem")
        self.assertTrue(flags.value & 0x00400000,
                        "Volume must support hard links for adversarial suite")
        self.assertTrue(flags.value & 0x00000080,
                        "Volume must support reparse points for adversarial suite")

    def test_file_symlink_is_recognized_without_following(self):
        outside = self.other / "outside.txt"
        outside.write_bytes(b"synthetic")
        link = self.work / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("SYMLINK CAPABILITY UNAVAILABLE; G1b NOT PASSED: " +
                          type(exc).__name__)
        with self._handle(link) as h:
            info = self._query(h, self.FILE_ATTRIBUTE_TAG_INFO_CLASS,
                               self.FILE_ATTRIBUTE_TAG_INFO)
        self.assertTrue(info.FileAttributes & self.FILE_ATTRIBUTE_REPARSE_POINT)

    def test_directory_symlink_is_recognized_without_following(self):
        link = self.work / "linked-directory"
        try:
            os.symlink(self.other, link, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("DIRECTORY LINK CAPABILITY UNAVAILABLE; G1b NOT PASSED: " +
                          type(exc).__name__)
        with self._handle(link) as h:
            info = self._query(h, self.FILE_ATTRIBUTE_TAG_INFO_CLASS,
                               self.FILE_ATTRIBUTE_TAG_INFO)
        self.assertTrue(info.FileAttributes & self.FILE_ATTRIBUTE_REPARSE_POINT)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("BLOCKED: G1b Win32 feasibility requires Windows; no tests claimed.")
        raise SystemExit(2)
    unittest.main(verbosity=2)
