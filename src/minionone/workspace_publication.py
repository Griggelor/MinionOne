"""Durable manager-only POSIX candidate publication; NOT worker or Windows access.

Pins trusted manager-supplied bytes in SQLite, then atomically links a verified
pending file into output/. An unfinished pending name is never a final result.
The job remains NO-GO for native Windows filesystem and worker execution.
"""
from __future__ import annotations

import hashlib
import os
import stat

from .admission import InputRejected, IdempotencyConflict, PermissionDenied
from .workspace_sealing import _job_directory, _name
from .workspace_preparation import _JOB, _OPERATION

_MAX_BYTES = 1_048_576
_MAX_TOTAL = 4_194_304
_MAX_FILES = 16


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _read_verified(fd, name, expected, size, *, allow_recovery_alias=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        handle = os.open(name, flags, dir_fd=fd)
    except OSError as exc:
        raise InputRejected("publication target not safely readable") from exc
    try:
        before = os.fstat(handle)
        valid_links = (1, 2) if allow_recovery_alias else (1,)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink not in valid_links
                or before.st_size != size):
            raise InputRejected("publication target type, links or length mismatch")
        chunks = []
        remaining = size + 1
        while remaining:
            blob = os.read(handle, min(65536, remaining))
            if not blob:
                break
            remaining -= len(blob)
            chunks.append(blob)
        after = os.fstat(handle)
        marker = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        content = b"".join(chunks)
        if marker(before) != marker(after) or len(content) != size or _digest(content) != expected:
            raise InputRejected("publication content integrity mismatch")
        return after
    finally:
        os.close(handle)


class WorkspacePublisher:
    """Trusted-manager Python helper; never exposed as an MCP tool."""

    def __init__(self, workspace, *, fault_hook=None):
        self.workspace = workspace
        self._fault_hook = fault_hook
        with workspace.store._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workspace_publications (
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                name TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                content BLOB NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('PENDING','PUBLISHED')),
                PRIMARY KEY(job_id,name), UNIQUE(job_id,operation_id))""")

    def _row(self, token, thread, job_id):
        if type(job_id) is not str or not _JOB.fullmatch(job_id):
            raise InputRejected("invalid workspace job ID")
        principal = self.workspace.store._principal(token, thread)
        with self.workspace.store._connection() as db:
            row = db.execute("""SELECT r.* FROM workspace_reservations r
                JOIN jobs j ON j.job_id=r.job_id
                WHERE r.job_id=? AND r.principal=? AND r.thread_id=?
                AND r.state='ADMITTED' AND j.principal=? AND j.thread_id=?""",
                (job_id, principal, thread, principal, thread)).fetchone()
        if row is None:
            raise PermissionDenied("workspace publication not permitted")
        return row

    def stage(self, token, thread, job_id, name, content, operation_id):
        """Durably pin manager-reviewed bytes BEFORE writing to output/."""
        self._row(token, thread, job_id)
        _name(name)
        if name == "job.log.jsonl" or name.startswith(".pending-"):
            raise InputRejected("manager-reserved output filename")
        if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
            raise InputRejected("invalid publication operation ID")
        if type(content) is not bytes or len(content) > _MAX_BYTES:
            raise InputRejected("invalid publication content or quota")
        digest = _digest(content)
        with self.workspace.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("""SELECT * FROM workspace_publications
                WHERE job_id=? AND (name=? OR operation_id=?)""",
                (job_id, name, operation_id)).fetchall()
            if old:
                if (len(old) != 1 or old[0]["name"] != name or
                        old[0]["operation_id"] != operation_id or
                        old[0]["sha256"] != digest or old[0]["content"] != content):
                    raise IdempotencyConflict("publication name or operation already reserved")
                state = old[0]["state"]
            else:
                quota = db.execute("""SELECT COUNT(*),COALESCE(SUM(length(content)),0)
                    FROM workspace_publications WHERE job_id=?""",
                    (job_id,)).fetchone()
                if quota[0] >= _MAX_FILES or quota[1] + len(content) > _MAX_TOTAL:
                    raise InputRejected("publication durable quota exhausted")
                db.execute("""INSERT INTO workspace_publications
                    (job_id,name,operation_id,sha256,content,state)
                    VALUES (?,?,?,?,?,'PENDING')""",
                    (job_id, name, operation_id, digest, content))
                state = "PENDING"
        return {"job_id":job_id,"name":name,"sha256":digest,"size":len(content),
                "state":state,"worker_execution_enabled":False}

    def _fault(self, point):
        if self._fault_hook is not None:
            self._fault_hook(point)

    def publish(self, token, thread, job_id, name, operation_id):
        """No-clobber atomic visibility with crash-replay and per-output lock."""
        row = self._row(token, thread, job_id)
        _name(name)
        if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
            raise InputRejected("invalid publication operation ID")
        with self.workspace.store._connection() as db:
            entry = db.execute("""SELECT * FROM workspace_publications
                WHERE job_id=? AND name=? AND operation_id=?""",
                (job_id, name, operation_id)).fetchone()
        if entry is None:
            raise PermissionDenied("publication receipt unavailable")
        data, digest = entry["content"], entry["sha256"]
        if _digest(data) != digest or len(data) > _MAX_BYTES:
            raise InputRejected("publication database content corrupt")
        pending = ".pending-" + hashlib.sha256(
            (job_id + "\x00" + name).encode("ascii")).hexdigest()[:40]
        with _job_directory(self.workspace, row, "output") as fd:
            import fcntl  # POSIX only; serializes cross-process writers on output handle
            fcntl.flock(fd, fcntl.LOCK_EX)
            final = None
            try:
                final = _read_verified(fd, name, digest, len(data),
                                       allow_recovery_alias=True)
            except InputRejected:
                try:
                    os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise
            if final is None and entry["state"] == "PUBLISHED":
                raise InputRejected("published artifact unexpectedly absent")
            if final is None:
                try:
                    handle = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                     os.O_NOFOLLOW, 0o600, dir_fd=fd)
                except FileExistsError:
                    _read_verified(fd, pending, digest, len(data))
                else:
                    try:
                        with os.fdopen(handle, "wb", closefd=False) as sink:
                            sink.write(data)
                            sink.flush()
                            os.fsync(handle)
                    finally:
                        os.close(handle)
                self._fault("AFTER_PENDING_FSYNC")
                _read_verified(fd, pending, digest, len(data))
                try:
                    os.link(pending, name, src_dir_fd=fd, dst_dir_fd=fd,
                            follow_symlinks=False)
                except FileExistsError as exc:
                    raise InputRejected("publication final name already occupied") from exc
                os.fsync(fd)
                self._fault("AFTER_ATOMIC_LINK")
                os.unlink(pending, dir_fd=fd)
                os.fsync(fd)
            else:
                try:
                    saved = os.stat(pending, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    if final.st_nlink != 1:
                        raise InputRejected("unaccounted published hardlink")
                else:
                    if ((saved.st_dev, saved.st_ino) != (final.st_dev, final.st_ino) or
                            not stat.S_ISREG(saved.st_mode) or saved.st_nlink != 2):
                        raise InputRejected("pending publication alias inconsistent")
                    os.unlink(pending, dir_fd=fd)
                    os.fsync(fd)
            _read_verified(fd, name, digest, len(data))
        self._fault("AFTER_VISIBLE_BEFORE_DB")
        with self.workspace.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE workspace_publications SET state='PUBLISHED'
                WHERE job_id=? AND name=? AND operation_id=?""",
                (job_id, name, operation_id))
            if changed.rowcount != 1:
                raise InputRejected("publication journal no longer available")
        return {"job_id":job_id,"name":name,"sha256":digest,"size":len(data),
                "state":"PUBLISHED","worker_execution_enabled":False}
