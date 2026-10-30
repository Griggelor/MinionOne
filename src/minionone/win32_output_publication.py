"""Native Windows manager-only output publication, backed by canonical SQLite.

NOT a worker tool or a Windows workspace seal. Callers must be trusted manager
code holding an already admitted job; synthetic Windows tests seed that admission
because the actual Windows seal remains blocked. A green runner is NOT G1b PASS.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import sys

from .admission import InputRejected, PermissionDenied
from .workspace_publication import WorkspacePublisher, _digest, _MAX_BYTES
from .workspace_preparation import _OPERATION
from .workspace_sealing import _name
from .win32_input_broker import (
    _Native, _UnicodeString, _ObjectAttributes, _IoStatusBlock,
    _OBJ_CASE_INSENSITIVE, _SYNC, _OPEN_REPARSE, _NON_DIRECTORY,
    _READ_ATTRIBUTES, _SYNCHRONIZE, _FILE_STANDARD, _FileStandardInfo,
    _component,
)
from .win32_work_create_probe import _NativeWriter, _FILE_WRITE_DATA

_FILE_READ_DATA = 0x0001
_DELETE = 0x00010000
_FILE_CREATE = 2
_FILE_OPEN = 1
_NORMAL = 0x80
_FILE_RENAME_INFO = 10  # NtSetInformationFile: FileRenameInformation
_NOT_FOUND = {0xC0000034, 0xC000003A}
_SHARE_READ = 1


class _FileRenameInfo(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BOOLEAN),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.DWORD),
        ("FileName", wintypes.WCHAR * 1),
    ]


class _PublisherNative(_NativeWriter):
    def __init__(self):
        super().__init__()
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        self.rename = ntdll.NtSetInformationFile
        self.rename.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IoStatusBlock),
                                ctypes.c_void_p, wintypes.ULONG, ctypes.c_int]
        self.rename.restype = ctypes.c_long

    def _open(self, parent, name, access, disposition):
        _component(name)
        encoded = name.encode("utf-16-le")
        buf = ctypes.create_unicode_buffer(name)
        uni = _UnicodeString(len(encoded), len(encoded)+2,
                             ctypes.cast(buf, wintypes.LPWSTR))
        obj = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent,
                                ctypes.pointer(uni), _OBJ_CASE_INSENSITIVE, None, None)
        ios = _IoStatusBlock()
        out = wintypes.HANDLE()
        code = self.open_relative(ctypes.byref(out), access,
                                  ctypes.byref(obj), ctypes.byref(ios), None,
                                  _NORMAL, _SHARE_READ, disposition,
                                  _SYNC | _OPEN_REPARSE | _NON_DIRECTORY, None, 0)
        if code != 0 or not out.value:
            if (code & 0xffffffff) in _NOT_FOUND and disposition == _FILE_OPEN:
                return None
            raise InputRejected("Windows manager output handle operation denied")
        return out.value

    def open_existing(self, parent, name, *, for_rename=False):
        rights = _READ_ATTRIBUTES | _SYNCHRONIZE | _FILE_READ_DATA
        if for_rename:
            rights |= _DELETE
        return self._open(parent, name, rights, _FILE_OPEN)

    def create_pending(self, parent, name):
        return self._open(parent, name, _READ_ATTRIBUTES | _SYNCHRONIZE |
                          _FILE_READ_DATA | _FILE_WRITE_DATA | _DELETE, _FILE_CREATE)

    def rename_final_no_replace(self, source, root, destination):
        _component(destination)
        encoded = destination.encode("utf-16-le")
        total = _FileRenameInfo.FileName.offset + len(encoded)
        buffer = ctypes.create_string_buffer(total)
        info = ctypes.cast(buffer, ctypes.POINTER(_FileRenameInfo))
        info.contents.ReplaceIfExists = 0
        # For a same-directory rename, Windows defines RootDirectory=NULL
        # and a single, validated final component. The source file handle was
        # opened relative to the pinned approved output handle; no host path
        # or new directory handle is reopened by the rename.
        info.contents.RootDirectory = None
        info.contents.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer)+_FileRenameInfo.FileName.offset,
                       encoded, len(encoded))
        ios = _IoStatusBlock()
        status = self.rename(source, ctypes.byref(ios), buffer, total,
                             _FILE_RENAME_INFO)
        if status != 0:
            raise InputRejected("native Windows no-replace output rename denied (ntstatus=0x%08x)" %
                                (status & 0xffffffff))

    def read_verified(self, handle, digest, size, volume):
        self.validate(handle, directory=False, volume=volume, single_link=True)
        value = self.read_regular(handle, max_bytes=size)
        if len(value) != size or _digest(value) != digest:
            raise InputRejected("Windows manager output content mismatch")
        return self.identity(handle)


@contextmanager
def _output_directory(workspace, row):
    if sys.platform != "win32":
        raise InputRejected("native Windows output publisher requires Windows")
    project = workspace.projects.get(row["project_id"])
    if project is None:
        raise InputRejected("Windows project no longer approved")
    api = _PublisherNative()
    with ExitStack() as stack:
        root = api.root(project)
        stack.callback(api.close, root)
        ident = api.validate(root, directory=True)
        try:
            expected = Path(project).stat()
        except OSError as exc:
            raise InputRejected("Windows project unavailable") from exc
        if int.from_bytes(ident[1], "little") != expected.st_ino:
            raise InputRejected("Windows project root identity changed")
        parent = root
        for part in (".minionone", "jobs", row["job_id"], "output"):
            child = api.child(parent, part, directory=True)
            stack.callback(api.close, child)
            api.validate(child, directory=True, volume=ident[0])
            parent = child
        yield api, parent, ident[0]


class WindowsOutputPublisher(WorkspacePublisher):
    """Durable trusted-manager publisher; deliberately NOT a worker capability.

    Stage uses v0.23's persistent publication table. publish() acquires a
    SQLite write transaction before touching NTFS, serializing independent
    manager processes. Crash after rename but before DB commit can be replayed.
    """

    def stage(self, token, thread, job_id, name, content, operation_id):
        _name(name)
        if name.startswith("pending-"):
            raise InputRejected("Windows output internal names reserved")
        return super().stage(token, thread, job_id, name, content, operation_id)

    def publish(self, token, thread, job_id, name, operation_id):
        row = self._row(token, thread, job_id)
        _name(name)
        if name == "job.log.jsonl" or name.startswith("pending-"):
            raise InputRejected("Windows output final name reserved")
        if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
            raise InputRejected("invalid publication operation ID")
        pending = "pending-" + hashlib.sha256(
            (job_id + "\x00" + name).encode("ascii")).hexdigest()[:40] + ".part"
        with self.workspace.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            entry = db.execute("""SELECT * FROM workspace_publications
                WHERE job_id=? AND name=? AND operation_id=?""",
                (job_id, name, operation_id)).fetchone()
            if entry is None:
                raise PermissionDenied("publication journal unavailable")
            data, digest = entry["content"], entry["sha256"]
            if (type(data) is not bytes or len(data) > _MAX_BYTES or
                    _digest(data) != digest):
                raise InputRejected("Windows publication journal corrupt")

            with _output_directory(self.workspace, row) as (api, directory, volume):
                finished = api.open_existing(directory, name)
                partial = api.open_existing(directory, pending)
                try:
                    if finished is not None:
                        if partial is not None:
                            raise InputRejected("ambiguous final and pending output names")
                        api.read_verified(finished, digest, len(data), volume)
                    else:
                        if entry["state"] == "PUBLISHED":
                            raise InputRejected("published Windows output disappeared")
                        if partial is None:
                            partial = api.create_pending(directory, pending)
                            try:
                                api.validate(partial, directory=False,
                                             volume=volume, single_link=True)
                                api.write_exact_and_flush(partial, data)
                            finally:
                                api.close(partial)
                                partial = None
                            self._fault("AFTER_PENDING_FSYNC")
                        else:
                            # A killed or failed writer may leave an incomplete
                            # pending file. Never repair by truncating or replacing.
                            api.read_verified(partial, digest, len(data), volume)

                        if partial is not None:
                            api.close(partial)
                            partial = None
                        source = api.open_existing(directory, pending, for_rename=True)
                        if source is None:
                            raise InputRejected("pending Windows output disappeared")
                        try:
                            api.read_verified(source, digest, len(data), volume)
                            api.rename_final_no_replace(source, directory, name)
                            self._fault("AFTER_ATOMIC_RENAME")
                        finally:
                            api.close(source)
                        target = api.open_existing(directory, name)
                        if target is None:
                            raise InputRejected("renamed Windows output unavailable")
                        try:
                            api.read_verified(target, digest, len(data), volume)
                        finally:
                            api.close(target)
                finally:
                    if finished is not None:
                        api.close(finished)
                    if partial is not None:
                        api.close(partial)
            self._fault("AFTER_VISIBLE_BEFORE_DB")
            result = db.execute("""UPDATE workspace_publications
                SET state='PUBLISHED' WHERE job_id=? AND name=? AND operation_id=?""",
                (job_id, name, operation_id))
            if result.rowcount != 1:
                raise InputRejected("Windows publication journal no longer available")
        return {"job_id":job_id, "name":name, "sha256":digest, "size":len(data),
                "state":"PUBLISHED", "worker_execution_enabled":False}
