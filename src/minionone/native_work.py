"""POSIX-only, capability-scoped physical workspace Work broker.

This is an incremental native-filesystem backend, not a Windows G1b or
untrusted-host-ACL security attestation. No model-supplied host path is used.
Every operation opens the approved job tree using directory FDs and O_NOFOLLOW.
The legacy isolated SQLite-document pilot deliberately remains untouched.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import fcntl
import os
import re
import secrets
import stat

from .admission import InputRejected
from .agent_loop import BrokerReply, BrokerScopeDenied
from .pilot_jobs import DocumentBroker, _text, PilotError
from .workspace_sealing import _job_directory, _read_regular
from .work_path_policy import (ensure_clean_work_entry, ensure_worker_work_name,
                               artifact_target, ensure_unique_output_name)

_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
_MAX_FILES = 16
_MAX_FILE = 1_048_576
_MAX_TOTAL = 4_194_304


def _parts(value):
    if type(value) is not str or len(value) > 240:
        raise BrokerScopeDenied("invalid scoped work path")
    parts = value.split("/")
    if not 1 <= len(parts) <= 6:
        raise BrokerScopeDenied("invalid work directory depth")
    for part in parts:
        ensure_worker_work_name(part)
        if (not _COMPONENT.fullmatch(part) or part in (".", "..")
                or part.endswith(".") or ".." in part
                or part.split(".")[0].casefold() in _RESERVED):
            raise BrokerScopeDenied("unsafe work path component")
    return tuple(parts)


@contextmanager
def _parent(root, parts):
    """Never traverse a path controlled by the model without handle-relative NOFOLLOW."""
    fd = root
    opened = []
    try:
        volume = os.fstat(root).st_dev
        for name in parts[:-1]:
            fd = os.open(name, _FLAGS, dir_fd=fd)
            opened.append(fd)
            entry = os.fstat(fd)
            if not stat.S_ISDIR(entry.st_mode) or entry.st_dev != volume:
                raise BrokerScopeDenied("work directory escaped approved filesystem")
        yield fd, parts[-1]
    except (NotADirectoryError, FileNotFoundError, PermissionError) as exc:
        raise BrokerScopeDenied("work parent directory unavailable") from exc
    finally:
        for handle in reversed(opened):
            os.close(handle)


def _read(fd, name):
    try:
        handle = os.open(name, _READ_FLAGS, dir_fd=fd)
    except OSError as exc:
        raise BrokerScopeDenied("work file unavailable") from exc
    try:
        before = os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > _MAX_FILE or before.st_dev != os.fstat(fd).st_dev):
            raise BrokerScopeDenied("work file type, links or quota denied")
        blobs = []
        size = 0
        while size <= _MAX_FILE:
            data = os.read(handle, min(65536, _MAX_FILE + 1 - size))
            if not data:
                break
            blobs.append(data)
            size += len(data)
        after = os.fstat(handle)
        fingerprint = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if fingerprint(before) != fingerprint(after) or size > _MAX_FILE or size != after.st_size:
            raise BrokerScopeDenied("work file changed during read")
        return b"".join(blobs)
    finally:
        os.close(handle)


def _write_new(fd, name, content):
    handle = None
    try:
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                         getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=fd)
        with os.fdopen(handle, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(handle)
        info = os.fstat(handle)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                info.st_size != len(content) or info.st_dev != os.fstat(fd).st_dev):
            raise BrokerScopeDenied("new work file identity invalid")
        os.fsync(fd)
    except FileExistsError as exc:
        raise BrokerScopeDenied("work destination already exists") from exc
    finally:
        if handle is not None:
            os.close(handle)


def _inventory(fd, prefix="", depth=0, budget=None):
    if depth > 6:
        raise BrokerScopeDenied("work directory depth exceeded")
    if budget is None:
        budget = [0]  # Shared by all recursive calls: directories count too.
    result = []
    names = os.listdir(fd)
    if len({name.casefold() for name in names}) != len(names):
        raise BrokerScopeDenied("ambiguous work filenames")
    for name in sorted(names):
        budget[0] += 1
        if budget[0] > 64:
            raise BrokerScopeDenied('work total entry quota exceeded')
        ensure_clean_work_entry(name)
        _parts(name)
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_dev != os.fstat(fd).st_dev:
            raise BrokerScopeDenied("work foreign-volume entry denied")
        path = prefix + name
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, _FLAGS, dir_fd=fd)
            try:
                result.extend(_inventory(child, path + "/", depth + 1, budget))
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            result.append((path, _read(fd, name)))
        else:
            raise BrokerScopeDenied("work link or non-regular file denied")
        if len(result) > _MAX_FILES or sum(len(x[1]) for x in result) > _MAX_TOTAL:
            raise BrokerScopeDenied("work inventory quota exceeded")
    return result


class NativeWorkspaceBroker(DocumentBroker):
    """POSIX physical Work files. SQLite stores job state and immutable output snapshots."""

    def __init__(self, manager, job, cap, workspace_execution):
        super().__init__(manager, job, cap)
        self.execution = workspace_execution
        if os.name == "nt" or not hasattr(os, "O_NOFOLLOW"):
            raise InputRejected("native work broker not available on this platform")

    @contextmanager
    def _zone(self, area):
        with self.execution.workspace.store._connection() as con:
            row = con.execute("SELECT * FROM workspace_reservations WHERE job_id=? AND state='ADMITTED'",
                              (self.job,)).fetchone()
        if row is None:
            raise BrokerScopeDenied("workspace not admitted")
        with _job_directory(self.execution.workspace, row, area) as fd:
            yield fd

    def ensure_work_clean(self):
        """Check all physical Work entries before a result becomes publishable."""
        with self._zone('work') as root:
            fcntl.flock(root, fcntl.LOCK_EX)
            try:
                return _inventory(root)
            finally:
                fcntl.flock(root, fcntl.LOCK_UN)

    def _execute(self, name, args, *, timeout_seconds):
        if name in ("report_progress", "ask_question"):
            return super()._execute(name, args, timeout_seconds=timeout_seconds)
        if type(args) is not dict:
            raise BrokerScopeDenied("invalid worker arguments")
        required = {
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
        if name not in required or set(args) != required[name]:
            raise BrokerScopeDenied("unknown worker tool or fields")
        # All file authority derives from a live, manager-owned job capability.
        with self.m.owner._connection() as con:
            con.execute("BEGIN")
            self.m._current(con, self.job, self.cap)

        if name == "list_files":
            zone = args["zone"]
            if zone == "input":
                with self._zone("input") as root:
                    names = [n for n in os.listdir(root) if n not in ("manifest.json", "TASK.md")]
                    files = [{"path": "input/" + n,
                              "sha256": sha256(_read_regular(root, n, max_bytes=_MAX_FILE)).hexdigest()}
                             for n in sorted(names)]
            elif zone == "work":
                with self._zone("work") as root:
                    files = [{"path": "work/" + path, "sha256": sha256(data).hexdigest()}
                             for path, data in _inventory(root)]
            else:
                raise BrokerScopeDenied("zone denied")
            return BrokerReply("ok", "OK", {"files": files})

        if name == "read_file":
            raw = args["path"]
            if type(raw) is not str or "/" not in raw:
                raise BrokerScopeDenied("invalid read path")
            zone, relative = raw.split("/", 1)
            parts = _parts(relative)
            if zone == "output":
                if len(parts) != 1:
                    raise BrokerScopeDenied("output artifact name denied")
                with self.m.owner._connection() as con:
                    row = con.execute("SELECT content,digest FROM pilot_files WHERE job_id=? AND zone='output' AND name=?",
                                      (self.job, parts[0])).fetchone()
                if row is None:
                    raise BrokerScopeDenied("output artifact not published")
                content = row["content"]
                if sha256(content.encode("utf-8")).hexdigest() != row["digest"]:
                    raise PilotError("PILOT_FILE_CHANGED")
                return BrokerReply("ok", "OK", {"path": raw, "content": content, "sha256": row["digest"]})
            if zone not in ("input", "work"):
                raise BrokerScopeDenied("zone denied")
            if zone == "input" and len(parts) != 1:
                raise BrokerScopeDenied("nested input not supported")
            with self._zone(zone) as root:
                if zone == "work":
                    with _parent(root, parts) as (fd, basename):
                        data = _read(fd, basename)
                else:
                    data = _read_regular(root, parts[0], max_bytes=_MAX_FILE)
                    # Verify the exact admission snapshot, not just current readable bytes.
                    with self.m.owner._connection() as con:
                        original = con.execute("SELECT content FROM workspace_input_blobs WHERE job_id=? AND name=?",
                                               (self.job, parts[0])).fetchone()
                    if original is None or original["content"] != data:
                        raise PilotError("PILOT_FILE_CHANGED")
            try:
                content = data.decode("utf-8")
            except UnicodeError:
                raise BrokerScopeDenied("text worker cannot read binary file") from None
            return BrokerReply("ok", "OK", {"path": raw, "content": content, "sha256": sha256(data).hexdigest()})

        path = args.get("name", args.get("path", args.get("source")))
        parts = _parts(path)
        if name in ("create_work", "write_work", "publish_artifact") and name != "publish_artifact":
            try:
                content = _text(args["content"], 8192).encode("utf-8")
            except InputRejected as exc:
                raise BrokerScopeDenied("work document content denied") from exc
        with self._zone("work") as root:
            fcntl.flock(root, fcntl.LOCK_EX)
            try:
                verified_inventory = _inventory(root)
                if name in ("create_work", "write_work"):
                    with _parent(root, parts) as (fd, basename):
                        existing = None
                        if name == "write_work":
                            existing = _read(fd, basename)
                            if type(args["expected_sha256"]) is not str or sha256(existing).hexdigest() != args["expected_sha256"]:
                                raise BrokerScopeDenied("stale work version")
                        inventory = verified_inventory
                        current = sum(len(blob) for _, blob in inventory)
                        if len(inventory) >= _MAX_FILES and existing is None:
                            raise BrokerScopeDenied("work file-count quota exceeded")
                        if current - (len(existing) if existing is not None else 0) + len(content) > _MAX_TOTAL:
                            raise BrokerScopeDenied("work byte quota exceeded")
                        if name == "create_work":
                            if any(existing_path.casefold() == path.casefold()
                                   for existing_path, _ in inventory):
                                raise BrokerScopeDenied("ambiguous casefold work destination")
                            _write_new(fd, basename, content)
                        else:
                            temporary = "pending-" + secrets.token_hex(12)
                            _write_new(fd, temporary, content)
                            try:
                                # A mismatched old version must never be silently replaced.
                                if _read(fd, basename) != existing:
                                    raise BrokerScopeDenied("concurrent work modification")
                                os.replace(temporary, basename, src_dir_fd=fd, dst_dir_fd=fd)
                                os.fsync(fd)
                            finally:
                                try:
                                    os.unlink(temporary, dir_fd=fd)
                                except FileNotFoundError:
                                    pass
                    result = {"path": "work/" + path, "sha256": sha256(content).hexdigest()}
                elif name == "delete_work":
                    with _parent(root, parts) as (fd, basename):
                        _read(fd, basename)  # Reject links, devices, oversized or changing files.
                        os.unlink(basename, dir_fd=fd)
                        os.fsync(fd)
                    result = {"deleted": "work/" + path}
                elif name == "rename_work":
                    destination = _parts(args["destination"])
                    with _parent(root, parts) as (fd, basename):
                        original = _read(fd, basename)
                        with _parent(root, destination) as (target_fd, target):
                            # Atomic no-replace publication of the new work path.
                            # If killed before unlink, both names are hardlinks
                            # and the single-link read invariant fails closed.
                            try:
                                os.link(basename, target, src_dir_fd=fd,
                                        dst_dir_fd=target_fd, follow_symlinks=False)
                            except FileExistsError as exc:
                                raise BrokerScopeDenied("work rename destination already exists") from exc
                            os.fsync(target_fd)
                            os.unlink(basename, dir_fd=fd)
                            os.fsync(fd)
                            if _read(target_fd, target) != original:
                                raise BrokerScopeDenied("work rename source changed")
                    result = {"path": "work/" + args["destination"], "sha256": sha256(original).hexdigest()}
                elif name == "mkdir_work":
                    with _parent(root, parts) as (fd, basename):
                        try:
                            os.mkdir(basename, mode=0o700, dir_fd=fd)
                        except FileExistsError as exc:
                            raise BrokerScopeDenied("work directory already exists") from exc
                        os.fsync(fd)
                    result = {"path": "work/" + path}
                elif name == "rmdir_work":
                    with _parent(root, parts) as (fd, basename):
                        check = os.open(basename, _FLAGS, dir_fd=fd)
                        try:
                            if os.listdir(check):
                                raise BrokerScopeDenied("work directory not empty")
                        finally:
                            os.close(check)
                        os.rmdir(basename, dir_fd=fd)
                        os.fsync(fd)
                    result = {"removed": "work/" + path}
                elif name == "publish_artifact":
                    with _parent(root, parts) as (fd, basename):
                        content = _read(fd, basename)
                    target = parts[-1]  # Flat legacy output; nested Work source is still scoped.
                    artifact_target(target)
                    digest = sha256(content).hexdigest()
                    try:
                        text_content = content.decode("utf-8")
                    except UnicodeError:
                        raise BrokerScopeDenied("text worker cannot publish binary source") from None
                    with self.m.owner._connection() as con:
                        con.execute("BEGIN IMMEDIATE")
                        self.m._current(con, self.job, self.cap)
                        prior = [row[0] for row in con.execute(
                            "SELECT name FROM pilot_files WHERE job_id=? AND zone='output'",
                            (self.job,))]
                        ensure_unique_output_name(prior, target)
                        con.execute("INSERT INTO pilot_files VALUES(?,?,?,?,?)",
                                    (self.job, "output", target, text_content, digest))
                    result = {"path": "output/" + target, "sha256": digest}
                else:
                    raise BrokerScopeDenied("unsupported work operation")
            finally:
                fcntl.flock(root, fcntl.LOCK_UN)
        return BrokerReply("ok", "OK", result)
