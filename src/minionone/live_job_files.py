"""Crash-observable manager-only live output appenders (POSIX/Win32).

Only the locally configured project path and canonical owned job ID are used.
No worker-controlled path or directory component is ever opened by this code.
Creates worker-io.jsonl once, keeps pinned native handles for the entire run,
flushes every record before the model or worker tool can advance.
A damaged/partial tail is never silently reused after restart.
"""
from __future__ import annotations

from contextlib import ExitStack
import json
import os
import stat

from .admission import InputRejected
from .worker_io import _MAX_FILE

_JOB_LIMIT=8*1024*1024


class LiveJobFiles:
    def __init__(self,manager,job_id):
        self.manager=manager
        self.job_id=job_id
        self._stack=None
        self._windows=False
        self._handles={}
        self._sizes={}
        self._native=None
        self._volume=None

    def __enter__(self):
        stack=ExitStack()
        try:
            self._stack=stack
            publisher=self.manager.publisher
            row=publisher._row(self.manager.token,self.manager.thread,self.job_id)
            if os.name=="nt":
                from .win32_output_publication import (
                    _output_directory,_FILE_OPEN,_FILE_CREATE,_FILE_READ_DATA,
                    _FILE_WRITE_DATA,_READ_ATTRIBUTES,_SYNCHRONIZE,
                    _FILE_STANDARD,_FileStandardInfo,
                )
                import ctypes
                from ctypes import wintypes
                api,directory,volume=stack.enter_context(
                    _output_directory(self.manager.workspace,row))
                self._native=api
                self._volume=volume
                self._windows=True
                kernel=ctypes.WinDLL("kernel32",use_last_error=True)
                seek=kernel.SetFilePointerEx
                seek.argtypes=[wintypes.HANDLE,ctypes.c_longlong,
                               ctypes.POINTER(ctypes.c_longlong),wintypes.DWORD]
                seek.restype=wintypes.BOOL
                for name,disposition in (
                        ("worker-io.jsonl",_FILE_CREATE),
                        ("job.log.jsonl",_FILE_OPEN)):
                    handle=api._open(directory,name,_FILE_READ_DATA|
                             _FILE_WRITE_DATA|_READ_ATTRIBUTES|_SYNCHRONIZE,
                             disposition)
                    if handle is None:
                        raise InputRejected("manager live output file unavailable")
                    stack.callback(api.close,handle)
                    api.validate(handle,directory=False,volume=volume,single_link=True)
                    size=api.query(handle,_FILE_STANDARD,_FileStandardInfo).EndOfFile
                    if not seek(handle,0,None,2):
                        raise InputRejected("Windows manager live output seek failed")
                    self._handles[name]=handle
                    self._sizes[name]=size
            else:
                from .workspace_sealing import _job_directory
                directory=stack.enter_context(
                    _job_directory(self.manager.workspace,row,"output"))
                directory_stat=os.fstat(directory)
                for name,flags in (
                        ("worker-io.jsonl",os.O_WRONLY|os.O_CREAT|os.O_EXCL),
                        ("job.log.jsonl",os.O_WRONLY|os.O_APPEND)):
                    handle=os.open(name,flags|os.O_NOFOLLOW|
                                   getattr(os,"O_CLOEXEC",0),0o600,dir_fd=directory)
                    stack.callback(os.close,handle)
                    info=os.fstat(handle)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
                            info.st_dev!=directory_stat.st_dev):
                        raise InputRejected("manager live output identity denied")
                    self._handles[name]=handle
                    self._sizes[name]=info.st_size
            if self._sizes["worker-io.jsonl"]!=0:
                raise InputRejected("new worker transcript not empty")
            if self._sizes["job.log.jsonl"]>_JOB_LIMIT:
                raise InputRejected("job log quota exhausted")
            self.job_event("WORKER_SESSION_OPENED",{"attempt":1})
            return self
        except BaseException:
            stack.close()
            self._stack=None
            raise

    def __exit__(self,*args):
        try:
            self._stack.close()
        finally:
            self._stack=None
            self._handles.clear()

    def _write(self,name,blob,limit):
        if self._stack is None or name not in self._handles:
            raise InputRejected("manager live output unavailable")
        if type(blob) is not bytes or not blob.endswith(b"\n") or b"\n" in blob[:-1]:
            raise InputRejected("invalid JSONL event")
        if self._sizes[name]+len(blob)>limit:
            raise InputRejected("manager live output limit exhausted")
        handle=self._handles[name]
        if self._windows:
            self._native.write_exact_and_flush(handle,blob)
        else:
            remaining=memoryview(blob)
            while remaining:
                written=os.write(handle,remaining)
                if written<=0:
                    raise InputRejected("incomplete manager JSONL write")
                remaining=remaining[written:]
            os.fsync(handle)
        self._sizes[name]+=len(blob)

    def worker_event(self,record):
        """Exact row as exposed by minionone_worker_io and final JSONL export."""
        line=json.dumps(record,sort_keys=True,ensure_ascii=True,
                        separators=(",",":")).encode("ascii")+b"\n"
        self._write("worker-io.jsonl",line,_MAX_FILE)

    def job_event(self,event,fields=None):
        # Closed schema, never write task text, path, prompt or model payload.
        allowed={"WORKER_SESSION_OPENED","MODEL_REQUEST_STARTED",
                 "MODEL_REQUEST_FINISHED","MODEL_REQUEST_FAILED",
                 "WORKER_TOOL_COMPLETED","JOB_OUTCOME","JOB_LOG_ERROR"}
        keys={"turn","duration_ms","tokens","attempt","tool","result_code",
              "error_code","outcome"}
        if (type(event) is not str or event not in allowed or
                type(fields) is not dict or set(fields)-keys or
                any(type(x) not in (str,int) or
                    (type(x) is str and (len(x)>64 or not x.replace("_","").isalnum()))
                    for x in fields.values())):
            raise InputRejected("invalid redacted manager job event")
        from .admission import _utc
        body={"utc":_utc(),"job_id":self.job_id,
              "level":"DEBUG" if event not in ("JOB_OUTCOME","JOB_LOG_ERROR") else "ERROR",
              "event":event,"fields":fields}
        line=json.dumps(body,sort_keys=True,ensure_ascii=True,
                        separators=(",",":")).encode("ascii")+b"\n"
        self._write("job.log.jsonl",line,_JOB_LIMIT)
