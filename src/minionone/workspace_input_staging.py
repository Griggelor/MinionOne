"""Manager-owned, create-only input staging for direct Flash MCP delegation.

Parent supplies bounded UTF-8 bytes and a configured project ID, NEVER a
host path. Native Windows writes stay relative to a verified held input
directory handle; POSIX uses pinned directory descriptors and O_EXCL.
An interrupted staging operation is NOT overwritten or auto-replayed.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .admission import InputRejected


def _check_payload(payload):
    if (type(payload) is not dict or not 2 <= len(payload) <= 6 or
            "TASK.md" not in payload or "manifest.json" not in payload):
        raise InputRejected("incomplete delegated workspace payload")
    if (any(type(n) is not str or type(b) is not bytes or
            not b or len(b) > 8192 for n,b in payload.items()) or
            sum(map(len,payload.values())) > 16384):
        raise InputRejected("delegated workspace exceeds pinned input quota")


def stage_sealed_input(workspace, project_id, job_id, payload):
    """Write only new manager-generated job input files; never overwrite/delete.

    A file already present is a conservative failure, including after partial
    crash. The caller must inspect/reset that job, never reuse a different task.
    """
    _check_payload(payload)
    project=workspace.projects.get(project_id)
    if project is None:
        raise InputRejected("project no longer approved")
    if os.name == "nt":
        _stage_windows(workspace,project,job_id,payload)
    else:
        _stage_posix(workspace,project,job_id,payload)


def _stage_posix(workspace,project,job_id,payload):
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    handles=[]
    try:
        root=os.open(project,flags)
        handles.append(root)
        s=os.fstat(root)
        if (s.st_dev,s.st_ino)!=workspace.project_identities[next(
                key for key,value in workspace.projects.items() if value==project)]:
            raise InputRejected("delegated project identity changed")
        current=root
        for part in (".minionone","jobs",job_id,"input"):
            current=os.open(part,flags,dir_fd=current)
            handles.append(current)
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                raise InputRejected("unsafe delegated input component")
        if os.listdir(current):
            raise InputRejected("delegated input is not empty")
        for name,content in sorted(payload.items()):
            handle=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|
                           os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=current)
            try:
                n=0
                while n<len(content):
                    written=os.write(handle,content[n:])
                    if written<=0:raise InputRejected("partial delegated input write")
                    n+=written
                os.fsync(handle)
                info=os.fstat(handle)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or
                        info.st_dev!=s.st_dev or info.st_size!=len(content)):
                    raise InputRejected("delegated input identity mismatch")
            finally:
                os.close(handle)
        os.fsync(current)
    finally:
        for handle in reversed(handles):
            os.close(handle)


def _stage_windows(workspace,project,job_id,payload):
    from .win32_input_broker import WindowsInputBroker
    from .win32_work_create_probe import (
        _NativeWriter,_FILE_STANDARD,_FileStandardInfo,_FILE_TAG,_FileTagInfo,
        _TAG_REPARSE,
    )
    st=Path(project).stat()
    if (st.st_dev,st.st_ino)!=workspace.project_identities[next(
            key for key,value in workspace.projects.items() if value==project)]:
        raise InputRejected("delegated project identity changed")
    with WindowsInputBroker(project,job_id) as broker:
        if broker.list_input():
            raise InputRejected("delegated input is not empty")
        writer=_NativeWriter()
        directory=broker._input
        volume=broker._api.identity(directory)[0]
        for name,data in sorted(payload.items()):
            handle=None
            try:
                handle=writer.create_new(directory,name)
                before=writer.validate(handle,directory=False,volume=volume,
                                       single_link=True)
                writer.write_exact_and_flush(handle,data)
                after=writer.validate(handle,directory=False,volume=volume,
                                      single_link=True)
                size=writer.query(handle,_FILE_STANDARD,_FileStandardInfo).EndOfFile
                tag=writer.query(handle,_FILE_TAG,_FileTagInfo)
                if (before!=after or size!=len(data) or
                        tag.FileAttributes&_TAG_REPARSE):
                    raise InputRejected("delegated Windows input identity mismatch")
            finally:
                if handle is not None:
                    writer.close(handle)
