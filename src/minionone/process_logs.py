"""Independent fsynced local manager process/error JSONL, available before SQLite.

Never copies caller JSON, credentials, prompts, paths, file content or raw
exception strings. Each event is a closed-schema record. Process and error
logs rotate without deleting prior segments; incomplete tails are retained
for operator diagnostics and never silently treated as valid JSON.
"""
from __future__ import annotations

from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import stat
import threading
import traceback

_SAFE=re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z",re.ASCII)
_EVENT=re.compile(r"[A-Z][A-Z0-9_]{1,63}\Z",re.ASCII)
_LIMIT=8*1024*1024


class ProcessLogs:
    def __init__(self,database_path):
        root=Path(database_path).parent/"logs"
        root.mkdir(mode=0o700,exist_ok=True)
        mode=root.lstat().st_mode
        if not stat.S_ISDIR(mode) or root.is_symlink():
            raise OSError("unsafe manager process log directory")
        if os.name=="nt" and root.stat().st_file_attributes & 0x400:
            raise OSError("reparse manager log directory denied")
        if os.name!="nt":
            os.chmod(root,0o700)
        self.root=root
        self._handles={}
        self._lock=threading.RLock()
        try:
            for name in ("minionone-process.jsonl","minionone-errors.jsonl"):
                self._handles[name]=self._open(name)
        except BaseException:
            self.close()
            raise

    def _open(self,name):
        flags=os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_EXCL
        path=self.root/name
        # Existing files must be ordinary single-link regular files.
        try:
            fd=os.open(path,flags|getattr(os,"O_NOFOLLOW",0)|
                       getattr(os,"O_BINARY",0),0o600)
        except FileExistsError:
            fd=os.open(path,os.O_WRONLY|os.O_APPEND|
                       getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_BINARY",0))
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            os.close(fd)
            raise OSError("unsafe manager log file")
        if os.name=="nt" and path.stat().st_file_attributes & 0x400:
            os.close(fd)
            raise OSError("manager log reparse file denied")
        if os.name!="nt":os.fchmod(fd,0o600)
        return fd

    def _rotate(self,name):
        old=self._handles.pop(name)
        os.fsync(old)
        os.close(old)
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        # Name cannot collide during same-PID sequential rotations.
        backup=name[:-6]+"."+stamp+"-"+str(os.getpid())+".jsonl"
        if (self.root/backup).exists():
            raise OSError("manager log rotation name exists")
        os.rename(self.root/name,self.root/backup)
        self._handles[name]=self._open(name)

    @staticmethod
    def _value(value):
        return type(value) is int and 0<=value<=4_000_000_000 or (
            type(value) is str and _SAFE.fullmatch(value) is not None)

    def _emit(self,file,event,fields):
        if (type(event) is not str or not _EVENT.fullmatch(event) or
                type(fields) is not dict or len(fields)>10 or
                any(type(k) is not str or not _SAFE.fullmatch(k) or
                    not self._value(v) for k,v in fields.items())):
            raise ValueError("unsafe process log metadata")
        payload={"utc":datetime.now(timezone.utc).isoformat(),
                 "pid":os.getpid(),"event":event,"fields":fields}
        blob=(json.dumps(payload,ensure_ascii=True,sort_keys=True,
                         separators=(",",":"))+"\n").encode("ascii")
        with self._lock:
            if file not in self._handles:raise OSError("process logger closed")
            if os.fstat(self._handles[file]).st_size+len(blob)>_LIMIT:
                self._rotate(file)
            fd=self._handles[file]
            offset=0
            while offset<len(blob):
                written=os.write(fd,blob[offset:])
                if written<=0:raise OSError("process JSONL short write")
                offset+=written
            os.fsync(fd)

    def event(self,event,**safe_fields):
        self._emit("minionone-process.jsonl",event,safe_fields)

    def failure(self,event,exc=None,**safe_fields):
        if exc is not None:
            safe_fields["exception_type"]=type(exc).__name__[:80]
        self._emit("minionone-errors.jsonl",event,safe_fields)
        self._emit("minionone-process.jsonl",event,safe_fields)
        if exc is not None:
            frames=traceback.extract_tb(exc.__traceback__)[-25:]
            # No absolute paths, source lines or exception __str__, which
            # could expose a token, path, model response or task content.
            safe=[{"file":Path(f.filename).name,"function":f.name,
                   "line":f.lineno} for f in frames]
            self._emit_stack("minionone-errors.jsonl",event,safe)

    def _emit_stack(self,file,event,frames):
        with self._lock:
            # Avoid passing arbitrary frame strings through ordinary event
            # fields. Keep only module basenames/functions validated above.
            scrubbed=[]
            for frame in frames:
                name=frame["file"] if _SAFE.fullmatch(frame["file"]) else "redacted"
                fn=frame["function"] if _SAFE.fullmatch(frame["function"]) else "redacted"
                scrubbed.append({"file":name,"function":fn,"line":int(frame["line"])})
            entry={"utc":datetime.now(timezone.utc).isoformat(),
                   "pid":os.getpid(),"event":event+"_STACK","frames":scrubbed}
            blob=(json.dumps(entry,sort_keys=True,separators=(",",":"))+"\n").encode("ascii")
            if os.fstat(self._handles[file]).st_size+len(blob)>_LIMIT:self._rotate(file)
            fd=self._handles[file];n=0
            while n<len(blob):
                written=os.write(fd,blob[n:])
                if written<=0:raise OSError("stack trace JSONL short write")
                n+=written
            os.fsync(fd)

    def close(self):
        with self._lock:
            for name,fd in list(self._handles.items()):
                try:os.fsync(fd)
                finally:os.close(fd)
                self._handles.pop(name,None)
