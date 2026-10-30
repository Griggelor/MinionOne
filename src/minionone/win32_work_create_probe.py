"""Isolated Windows NTFS create-only work-file feasibility probe (NOT worker access).

The file remains visible and potentially partial on failure. No publication, cleanup,
crash recovery, ACL attestation, or MCP/worker entry point is authorized here.
A green Windows runner is only feasibility evidence, NEVER target-host G1b PASS.
"""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path
import sys

from .admission import InputRejected
from .win32_input_broker import (
    _Native, _component, _VALID_JOB, _UnicodeString, _ObjectAttributes,
    _IoStatusBlock, _OBJ_CASE_INSENSITIVE, _SYNC, _OPEN_REPARSE,
    _NON_DIRECTORY, _FILE_STANDARD, _FileStandardInfo, _READ_ATTRIBUTES,
    _SYNCHRONIZE, _FILE_OPEN, _FileTagInfo, _FILE_TAG, _TAG_REPARSE,
)

_FILE_CREATE=2                  # NtCreateFile: fail if the name already exists
_FILE_WRITE_DATA=0x0002
_FILE_ATTRIBUTE_NORMAL=0x80
_MAX_FILE_BYTES=1024*1024
_MAX_FILES=16
_MAX_SESSION_BYTES=4*1024*1024


class _NativeWriter(_Native):
    def __init__(self):
        super().__init__()
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        self.write=kernel.WriteFile
        self.write.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
        self.write.restype=wintypes.BOOL
        self.flush=kernel.FlushFileBuffers
        self.flush.argtypes=[wintypes.HANDLE]
        self.flush.restype=wintypes.BOOL

    def create_new(self,parent,name):
        _component(name)
        namebuf=ctypes.create_unicode_buffer(name)
        encoded=name.encode("utf-16-le")
        u=_UnicodeString(len(encoded),len(encoded)+2,
                         ctypes.cast(namebuf,wintypes.LPWSTR))
        attrs=_ObjectAttributes(ctypes.sizeof(_ObjectAttributes),parent,
                                ctypes.pointer(u),_OBJ_CASE_INSENSITIVE,None,None)
        ios=_IoStatusBlock()
        out=wintypes.HANDLE()
        # A held directory handle anchors this name to the approved work folder.
        # The API never accepts slash/colon/reparse syntax from the caller.
        status=self.open_relative(ctypes.byref(out),
                _FILE_WRITE_DATA|_READ_ATTRIBUTES|_SYNCHRONIZE,
                ctypes.byref(attrs),ctypes.byref(ios),None,
                _FILE_ATTRIBUTE_NORMAL,0,_FILE_CREATE,
                _SYNC|_OPEN_REPARSE|_NON_DIRECTORY,None,0)
        if status!=0 or not out.value:
            raise InputRejected("Windows create-only destination unavailable")
        return out.value

    def write_exact_and_flush(self,handle,data):
        offset=0
        while offset<len(data):
            chunk=data[offset:offset+65536]
            buf=ctypes.create_string_buffer(chunk)
            n=wintypes.DWORD()
            if not self.write(handle,buf,len(chunk),ctypes.byref(n),None):
                raise InputRejected("Windows work-file write failed; partial file quarantined")
            if n.value<=0 or n.value>len(chunk):
                raise InputRejected("Windows work-file short write; partial file quarantined")
            offset+=n.value
        if not self.flush(handle):
            raise InputRejected("Windows flush failed; work-file result not committed")


class WindowsWorkCreateProbe:
    """Manager-only feasibility API: no worker-facing handle or path is returned.

    On any failure, the created name may remain partially written. The caller MUST
    NOT reuse, publish, delete, or consider it a completed artifact. A separate
    crash-recovery and manager publication protocol is required before activation.
    """

    def __init__(self,project_dir,job_id):
        self.project=Path(project_dir)
        if sys.platform!="win32" or not self.project.is_absolute():
            raise InputRejected("native Windows absolute project root required")
        if type(job_id) is not str or not _VALID_JOB.fullmatch(job_id):
            raise InputRejected("invalid Windows job ID")
        self.job_id=job_id
        self._api=None
        self._stack=None
        self._work=None
        self._volume=None
        self._created=0
        self._bytes=0
        self._poisoned=False

    def __enter__(self):
        api=_NativeWriter()
        stack=ExitStack()
        try:
            root=api.root(self.project)
            stack.callback(api.close,root)
            root_id=api.validate(root,directory=True)
            try:
                stat=self.project.stat()
            except OSError as exc:
                raise InputRejected("configured Windows project unavailable") from exc
            if int.from_bytes(root_id[1],"little")!=stat.st_ino:
                raise InputRejected("Windows project root FileId changed")
            parent=root
            for part in (".minionone","jobs",self.job_id,"work"):
                child=api.child(parent,part,directory=True)
                stack.callback(api.close,child)
                api.validate(child,directory=True,volume=root_id[0])
                parent=child
            self._api,self._stack,self._work=api,stack,parent
            self._volume=root_id[0]
            return self
        except BaseException:
            stack.close()
            raise

    def __exit__(self,*exc):
        try:
            self._stack.close()
        finally:
            self._api=self._stack=self._work=self._volume=None

    def create_work(self,name,data):
        _component(name)
        if self._work is None or self._poisoned:
            raise InputRejected("Windows work probe unavailable or poisoned")
        if type(data) is not bytes or len(data)>_MAX_FILE_BYTES:
            raise InputRejected("invalid work content or per-file quota")
        if self._created>=_MAX_FILES or self._bytes+len(data)>_MAX_SESSION_BYTES:
            raise InputRejected("Windows work session quota exceeded")
        names=self._api.list_names(self._work,max_names=_MAX_FILES)
        if len(names)>=_MAX_FILES:
            raise InputRejected("Windows work directory file-count quota exceeded")
        handle=None
        try:
            handle=self._api.create_new(self._work,name)
            # Newly created files must remain ordinary, single-linked and
            # on the same volume. Never follow or reopen the supplied path.
            identity=self._api.validate(handle,directory=False,
                                        volume=self._volume,single_link=True)
            self._api.write_exact_and_flush(handle,data)
            after=self._api.validate(handle,directory=False,
                                     volume=self._volume,single_link=True)
            size=self._api.query(handle,_FILE_STANDARD,_FileStandardInfo).EndOfFile
            tag=self._api.query(handle,_FILE_TAG,_FileTagInfo)
            if (identity!=after or size!=len(data) or
                    tag.FileAttributes & _TAG_REPARSE):
                raise InputRejected("Windows work-file identity or size changed")
            self._created+=1
            self._bytes+=len(data)
            return {"name":name,"size":len(data),
                    "sha256":hashlib.sha256(data).hexdigest(),
                    "identity_verified_on_open_handle":True,
                    "published":False}
        except BaseException:
            # Do not confuse an IO error or partial write with committed output.
            # No cleanup is attempted: deleting via a path would be unsafe.
            self._poisoned=True
            raise
        finally:
            if handle is not None:
                self._api.close(handle)
