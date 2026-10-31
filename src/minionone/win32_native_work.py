"""Experimental Win32 native-handle Workspace Work CRUD broker.

Unlike the historical SQLite document pilot, this broker uses actual job work/
files. Names originate only from validated relative components. Every file or
directory is opened relative to an already pinned, verified native NT handle.
No shell, absolute worker path or arbitrary filesystem handle is exposed.

IMPORTANT: This is a synthetic-system-test implementation, NOT a G1b
target-host ACL / hostile concurrent writer / crash-idempotency attestation.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
from hashlib import sha256
from pathlib import Path
import os
import secrets
import stat
import sys
import threading

from .admission import InputRejected
from .agent_loop import BrokerReply, BrokerScopeDenied
from .pilot_jobs import DocumentBroker, _text, PilotError
from .win32_input_broker import (
    _UnicodeString, _ObjectAttributes, _IoStatusBlock, _component,
    _OBJ_CASE_INSENSITIVE, _SYNC, _OPEN_REPARSE, _NON_DIRECTORY, _DIRECTORY,
    _READ_ATTRIBUTES, _SYNCHRONIZE, _FILE_STANDARD, _FileStandardInfo,
)
from .win32_output_publication import (
    _PublisherNative, _FileRenameInfo, _DELETE, _FILE_READ_DATA,
    _FILE_CREATE, _FILE_OPEN, _NORMAL, _NOT_FOUND,
)
from .win32_work_create_probe import _FILE_WRITE_DATA
from .work_path_policy import (ensure_clean_work_entry, ensure_worker_work_name,
                               artifact_target, ensure_unique_output_name)
import re

_MAX_FILE = 1_048_576
_MAX_TOTAL = 4_194_304
_MAX_FILES = 16
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def _parts(path):
    if type(path) is not str or len(path) > 240:
        raise BrokerScopeDenied("invalid Work path")
    parts = path.split("/")
    if not 1 <= len(parts) <= 6:
        raise BrokerScopeDenied("invalid Work depth")
    for part in parts:
        ensure_worker_work_name(part)
        if (not _COMPONENT.fullmatch(part) or part in (".", "..")
                or part.endswith(".") or ".." in part
                or part.split(".")[0].casefold() in _RESERVED):
            raise BrokerScopeDenied("unsafe Work path component")
    return tuple(parts)

_SHARE_ALL = 0x1 | 0x2 | 0x4
_DISPOSITION_INFO = 13
_FILE_LIST_DIRECTORY = 0x0001
_FILE_ADD_FILE = 0x0002
_FILE_ADD_SUBDIRECTORY = 0x0004
_FILE_DELETE_CHILD = 0x0040
_REQUIRED = {
    "list_files": {"zone"},
    "read_file": {"path"},
    "create_work": {"name", "content"},
    "write_work": {"name", "content", "expected_sha256"},
    "delete_work": {"path"},
    "rename_work": {"source", "destination"},
    "mkdir_work": {"path"},
    "rmdir_work": {"path"},
    "publish_artifact": {"name"},
}


class _WorkNative(_PublisherNative):
    """NT handle helpers; no model-provided full paths or Windows path reopen."""

    def _open(self, parent, name, access, disposition=_FILE_OPEN, *, directory=False):
        _component(name)
        encoded = name.encode("utf-16-le")
        buffer = ctypes.create_unicode_buffer(name)
        uni = _UnicodeString(len(encoded), len(encoded) + 2,
                             ctypes.cast(buffer, wintypes.LPWSTR))
        obj = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent,
                                ctypes.pointer(uni), _OBJ_CASE_INSENSITIVE, None, None)
        ios = _IoStatusBlock()
        handle = wintypes.HANDLE()
        status = self.open_relative(ctypes.byref(handle), access,
                                    ctypes.byref(obj), ctypes.byref(ios), None,
                                    _NORMAL, _SHARE_ALL, disposition,
                                    _SYNC | _OPEN_REPARSE |
                                    (_DIRECTORY if directory else _NON_DIRECTORY),
                                    None, 0)
        if status != 0 or not handle.value:
            if (status & 0xffffffff) in _NOT_FOUND and disposition == _FILE_OPEN:
                return None
            raise BrokerScopeDenied("Windows scoped file operation denied")
        return handle.value

    def open_existing(self, parent, name, *, for_rename=False):
        access = _READ_ATTRIBUTES | _SYNCHRONIZE | _FILE_READ_DATA
        if for_rename:
            access |= _DELETE
        return self._open(parent, name, access)

    def create_pending(self, parent, name):
        access = (_READ_ATTRIBUTES | _SYNCHRONIZE | _FILE_READ_DATA |
                  _FILE_WRITE_DATA | _DELETE)
        return self._open(parent, name, access, _FILE_CREATE)

    def directory(self, parent, name, *, create=False, for_delete=False):
        # The *Work* directory handle must carry the directory rights needed
        # for handle-relative child creation and removal. Input and all
        # ancestors keep their read-only handles.
        access = (_READ_ATTRIBUTES | _SYNCHRONIZE | _FILE_LIST_DIRECTORY |
                  _FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY | _FILE_DELETE_CHILD)
        if for_delete:
            access |= _DELETE
        return self._open(parent, name, access,
                          _FILE_CREATE if create else _FILE_OPEN, directory=True)

    def rename_scoped(self, source, destination_directory, destination_name,
                      *, replace=False):
        _component(destination_name)
        encoded = destination_name.encode("utf-16-le")
        size = _FileRenameInfo.FileName.offset + len(encoded)
        data = ctypes.create_string_buffer(size)
        payload = ctypes.cast(data, ctypes.POINTER(_FileRenameInfo))
        payload.contents.ReplaceIfExists = 1 if replace else 0
        # Target directory is another held native handle in the SAME job Work
        # root, not a string supplied by the worker.
        payload.contents.RootDirectory = destination_directory
        payload.contents.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(data) + _FileRenameInfo.FileName.offset,
                       encoded, len(encoded))
        ios = _IoStatusBlock()
        status = self.rename(source, ctypes.byref(ios), data, size, 10)
        if status != 0:
            raise BrokerScopeDenied("Windows scoped no-clobber/replace rename denied")

    def dispose(self, source):
        flag = wintypes.BOOLEAN(1)
        ios = _IoStatusBlock()
        status = self.rename(source, ctypes.byref(ios), ctypes.byref(flag),
                             ctypes.sizeof(flag), _DISPOSITION_INFO)
        if status != 0:
            raise BrokerScopeDenied("Windows scoped deletion denied")

    def read_work(self, directory, name, volume):
        handle = self.open_existing(directory, name)
        if handle is None:
            raise BrokerScopeDenied("work file not found")
        try:
            self.validate(handle, directory=False, volume=volume, single_link=True)
            return self.read_regular(handle, max_bytes=_MAX_FILE)
        finally:
            self.close(handle)

    def create_file(self, directory, name, data, volume):
        handle = self.create_pending(directory, name)
        try:
            identity = self.validate(handle, directory=False, volume=volume,
                                     single_link=True)
            self.write_exact_and_flush(handle, data)
            after = self.validate(handle, directory=False, volume=volume,
                                  single_link=True)
            size = self.query(handle, _FILE_STANDARD, _FileStandardInfo).EndOfFile
            if identity != after or size != len(data):
                raise InputRejected("Windows new Work file changed or incomplete")
        finally:
            self.close(handle)
        if self.read_work(directory, name, volume) != data:
            raise InputRejected("Windows Work creation bytes differ")


@contextmanager
def _parent(api, root, parts, volume):
    """Open every intermediate Work component as a directory handle."""
    with ExitStack() as stack:
        parent = root
        for component in parts[:-1]:
            child = api.directory(parent, component)
            if child is None:
                raise BrokerScopeDenied("work parent directory missing")
            stack.callback(api.close, child)
            api.validate(child, directory=True, volume=volume)
            parent = child
        yield parent, parts[-1]


def _inventory(api, root, volume, prefix="", depth=0, budget=None):
    if depth > 6:
        raise BrokerScopeDenied("Windows Work nesting quota exceeded")
    if budget is None:
        budget = [0]  # Count empty dirs as well as real files across recursion.
    files = []
    names = api.list_names(root, max_names=32)
    if len({name.casefold() for name in names}) != len(names):
        raise BrokerScopeDenied("Windows Work casefold collision")
    for name in sorted(names):
        budget[0] += 1
        if budget[0] > 64:
            raise BrokerScopeDenied('Windows total Work entry quota exceeded')
        ensure_clean_work_entry(name)
        _parts(name)
        directory = None
        try:
            directory = api.directory(root, name)
        except BrokerScopeDenied:
            pass  # May be a regular file, which is checked below.
        if directory is not None:
            try:
                api.validate(directory, directory=True, volume=volume)
                files.extend(_inventory(api, directory, volume,
                                        prefix + name + "/", depth + 1, budget))
            finally:
                api.close(directory)
        else:
            files.append((prefix + name, api.read_work(root, name, volume)))
        if len(files) > _MAX_FILES or sum(len(data) for _, data in files) > _MAX_TOTAL:
            raise BrokerScopeDenied("Windows Work inventory quota exceeded")
    return files


class WindowsWorkspaceBroker(DocumentBroker):
    """Trusted manager-side operations on an approved, pinned NTFS job Work root."""

    _lock = threading.RLock()  # single process; multi-process/OS ACL gate remains open

    def __init__(self, manager, job, cap, execution):
        super().__init__(manager, job, cap)
        self.execution = execution
        if sys.platform != "win32":
            raise InputRejected("Windows Workspace broker requires native Win32")

    @contextmanager
    def _zone(self, area):
        with self.execution.workspace.store._connection() as db:
            row = db.execute("""SELECT * FROM workspace_reservations
                WHERE job_id=? AND state='ADMITTED'""", (self.job,)).fetchone()
        if row is None or area not in ("input", "work"):
            raise BrokerScopeDenied("unadmitted Workspace zone")
        workspace = self.execution.workspace
        project = workspace.projects.get(row["project_id"])
        if project is None:
            raise BrokerScopeDenied("project not configured")
        api = _WorkNative()
        with ExitStack() as stack:
            root = api.root(project)
            stack.callback(api.close, root)
            identity = api.validate(root, directory=True)
            stat_root = Path(project).stat()
            if ((stat_root.st_dev, stat_root.st_ino) !=
                    workspace.project_identities[row["project_id"]] or
                    int.from_bytes(identity[1], "little") != stat_root.st_ino):
                raise InputRejected("configured Windows project identity changed")
            parent = root
            for part in (".minionone", "jobs", row["job_id"], area):
                child = (api.directory(parent, part) if part == "work" else
                         api.child(parent, part, directory=True))
                if child is None:
                    raise BrokerScopeDenied("Windows job zone unavailable")
                stack.callback(api.close, child)
                api.validate(child, directory=True, volume=identity[0])
                parent = child
            yield api, parent, identity[0]

    def ensure_work_clean(self):
        """Validate the pinned real Work tree before terminal publication."""
        with self._lock, self._zone('work') as (api, root, volume):
            return _inventory(api, root, volume)

    def _execute(self, name, args, *, timeout_seconds):
        if name in ("report_progress", "ask_question"):
            return super()._execute(name, args, timeout_seconds=timeout_seconds)
        if type(args) is not dict or name not in _REQUIRED or set(args) != _REQUIRED[name]:
            raise BrokerScopeDenied("Windows worker operation or argument denied")
        with self.m.owner._connection() as db:
            db.execute("BEGIN")
            self.m._current(db, self.job, self.cap)

        if name == "list_files":
            zone = args["zone"]
            if zone not in ("input", "work"):
                raise BrokerScopeDenied("worker list zone denied")
            with self._zone(zone) as (api, directory, volume):
                if zone == "work":
                    files = _inventory(api, directory, volume)
                else:
                    files = []
                    for item in api.list_names(directory, max_names=18):
                        if item not in ("TASK.md", "manifest.json"):
                            files.append((item, api.read_work(directory, item, volume)))
            return BrokerReply("ok", "OK", {"files": [
                {"path": zone + "/" + path, "sha256": sha256(data).hexdigest()}
                for path, data in files]})

        if name == "read_file":
            raw = args["path"]
            if type(raw) is not str or "/" not in raw:
                raise BrokerScopeDenied("invalid worker read path")
            zone, path = raw.split("/", 1)
            parts = _parts(path)
            if zone == "output":
                if len(parts) != 1 or parts[0].casefold() in (
                        "worker-io.jsonl", "job.log.jsonl"):
                    raise BrokerScopeDenied("output metadata access denied")
                with self.m.owner._connection() as db:
                    row = db.execute("""SELECT content,digest FROM pilot_files
                        WHERE job_id=? AND zone='output' AND name=?""",
                        (self.job, parts[0])).fetchone()
                if row is None:
                    raise BrokerScopeDenied("output artifact not published")
                text = row["content"]
                data = text.encode("utf-8")
                if sha256(data).hexdigest() != row["digest"]:
                    raise PilotError("PILOT_FILE_CHANGED")
            elif zone in ("input", "work"):
                if zone == "input" and len(parts) != 1:
                    raise BrokerScopeDenied("nested input denied")
                with self._zone(zone) as (api, root, volume):
                    with _parent(api, root, parts, volume) as (fd, filename):
                        data = api.read_work(fd, filename, volume)
                if zone == "input":
                    with self.m.owner._connection() as db:
                        original = db.execute("""SELECT content FROM workspace_input_blobs
                            WHERE job_id=? AND name=?""",
                            (self.job, filename)).fetchone()
                    if original is None or original["content"] != data:
                        raise PilotError("PILOT_FILE_CHANGED")
            else:
                raise BrokerScopeDenied("read zone denied")
            try:
                content = data.decode("utf-8")
            except UnicodeError as exc:
                raise BrokerScopeDenied("text worker cannot read binary bytes") from exc
            return BrokerReply("ok", "OK", {
                "path": raw, "content": content, "sha256": sha256(data).hexdigest()})

        path = args.get("name", args.get("path", args.get("source")))
        parts = _parts(path)
        if name in ("create_work", "write_work"):
            try:
                content = _text(args["content"], 8192).encode("utf-8")
            except InputRejected as exc:
                raise BrokerScopeDenied("bounded worker text denied") from exc

        with self._lock, self._zone("work") as (api, root, volume):
            verified_inventory = _inventory(api, root, volume)
            if name in ("create_work", "write_work"):
                with _parent(api, root, parts, volume) as (fd, basename):
                    old = None
                    if name == "write_work":
                        old = api.read_work(fd, basename, volume)
                        if (type(args["expected_sha256"]) is not str or
                                sha256(old).hexdigest() != args["expected_sha256"]):
                            raise BrokerScopeDenied("stale Work overwrite digest")
                    contents = verified_inventory
                    size = sum(len(data) for _, data in contents)
                    if ((old is None and len(contents) >= _MAX_FILES) or
                            size - (len(old) if old is not None else 0) + len(content) > _MAX_TOTAL):
                        raise BrokerScopeDenied("Work quota exceeded")
                    if name == "create_work":
                        if any(name_.casefold() == path.casefold() for name_, _ in contents):
                            raise BrokerScopeDenied("Work filename casefold collision")
                        api.create_file(fd, basename, content, volume)
                    else:
                        temporary = "pending-" + secrets.token_hex(12) + ".tmp"
                        api.create_file(fd, temporary, content, volume)
                        if api.read_work(fd, basename, volume) != old:
                            raise BrokerScopeDenied("Work changed before replace")
                        handle = api.open_existing(fd, temporary, for_rename=True)
                        if handle is None:
                            raise BrokerScopeDenied("pending Work replace missing")
                        try:
                            api.rename_scoped(handle, fd, basename, replace=True)
                        finally:
                            api.close(handle)
                        if api.read_work(fd, basename, volume) != content:
                            raise InputRejected("Work replacement not verified")
                result = {"path": "work/" + path, "sha256": sha256(content).hexdigest()}
            elif name == "delete_work":
                with _parent(api, root, parts, volume) as (fd, basename):
                    api.read_work(fd, basename, volume)
                    handle = api.open_existing(fd, basename, for_rename=True)
                    if handle is None:
                        raise BrokerScopeDenied("Work deletion source absent")
                    try:
                        api.validate(handle, directory=False, volume=volume, single_link=True)
                        api.dispose(handle)
                    finally:
                        api.close(handle)
                result = {"deleted": "work/" + path}
            elif name == "rename_work":
                target_parts = _parts(args["destination"])
                with _parent(api, root, parts, volume) as (fd, basename):
                    old = api.read_work(fd, basename, volume)
                    with _parent(api, root, target_parts, volume) as (to_fd, target):
                        handle = api.open_existing(fd, basename, for_rename=True)
                        if handle is None:
                            raise BrokerScopeDenied("Work rename source missing")
                        try:
                            api.validate(handle, directory=False, volume=volume, single_link=True)
                            api.rename_scoped(handle, to_fd, target, replace=False)
                        finally:
                            api.close(handle)
                        if api.read_work(to_fd, target, volume) != old:
                            raise InputRejected("Work rename bytes mismatch")
                result = {"path": "work/" + args["destination"],
                          "sha256": sha256(old).hexdigest()}
            elif name == "mkdir_work":
                with _parent(api, root, parts, volume) as (fd, basename):
                    created = api.directory(fd, basename, create=True)
                    if created is None:
                        raise BrokerScopeDenied("new Work directory missing")
                    try:
                        api.validate(created, directory=True, volume=volume)
                    finally:
                        api.close(created)
                result = {"path": "work/" + path}
            elif name == "rmdir_work":
                with _parent(api, root, parts, volume) as (fd, basename):
                    handle = api.directory(fd, basename, for_delete=True)
                    if handle is None:
                        raise BrokerScopeDenied("Work directory not found")
                    try:
                        api.validate(handle, directory=True, volume=volume)
                        if api.list_names(handle, max_names=32):
                            raise BrokerScopeDenied("Work directory not empty")
                        api.dispose(handle)
                    finally:
                        api.close(handle)
                result = {"removed": "work/" + path}
            elif name == "publish_artifact":
                with _parent(api, root, parts, volume) as (fd, basename):
                    data = api.read_work(fd, basename, volume)
                target = parts[-1]
                artifact_target(target)
                try:
                    text_content = data.decode("utf-8")
                except UnicodeError as exc:
                    raise BrokerScopeDenied("text worker cannot publish binary source") from exc
                digest = sha256(data).hexdigest()
                with self.m.owner._connection() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.m._current(db, self.job, self.cap)
                    prior = [row[0] for row in db.execute("""SELECT name FROM pilot_files
                        WHERE job_id=? AND zone='output'""", (self.job,))]
                    ensure_unique_output_name(prior, target)
                    db.execute("INSERT INTO pilot_files VALUES (?,?,?,?,?)",
                               (self.job, "output", target, text_content, digest))
                result = {"path": "output/" + target, "sha256": digest}
            else:
                raise BrokerScopeDenied("unsupported Windows Work tool")
        return BrokerReply("ok", "OK", result)
