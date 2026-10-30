"""Manager-only, authenticated reader for sealed workspace input snapshots.

Never opens a worker-controlled path and never authorizes worker execution.
Before a workspace is dispatched, the caller must separately re-verify the
native input seal and prove a current job/attempt/parent resource lease.
"""
from __future__ import annotations

import hashlib
import json

from .admission import InputRejected, PermissionDenied
from .workspace_preparation import _JOB
from .workspace_sealing import (
    _MAX_FILES, _MAX_FILE_BYTES, _MAX_MANIFEST_BYTES, _MAX_TASK_BYTES,
    _MAX_TOTAL_BYTES, _name, _pairs,
)


class SealedWorkspaceInput:
    """Read exact manager-admitted bytes, always checking the canonical digest.

    Requires parent credentials for EVERY operation. This is a manager API,
    not a worker capability or an MCP endpoint for raw document content.
    """

    def __init__(self, workspace):
        self.workspace = workspace

    def _load(self, con, token, thread, job_id):
        principal = self.workspace.store._principal(token, thread)
        if type(job_id) is not str or _JOB.fullmatch(job_id) is None:
            raise InputRejected("invalid workspace job ID")
        row = con.execute("""
            SELECT j.job_id,j.state,j.version,j.input_digest
            FROM jobs j JOIN workspace_reservations r USING(job_id)
            WHERE j.job_id=? AND j.principal=? AND j.thread_id=?
              AND r.principal=? AND r.thread_id=? AND r.state='ADMITTED'
        """, (job_id, principal, thread, principal, thread)).fetchone()
        if row is None:
            raise PermissionDenied("sealed workspace unavailable")
        saved = con.execute("""
            SELECT name,sha256,content FROM workspace_input_blobs
            WHERE job_id=? ORDER BY name
        """, (job_id,)).fetchall()
        if not 2 <= len(saved) <= _MAX_FILES + 1:
            raise InputRejected("sealed input snapshot incomplete")
        files = {}
        total = 0
        for entry in saved:
            name = _name(entry["name"])
            content = entry["content"]
            expected = entry["sha256"]
            if (type(content) is not bytes or
                    type(expected) is not str or len(expected) != 64 or
                    hashlib.sha256(content).hexdigest() != expected):
                raise InputRejected("sealed input snapshot integrity invalid")
            limit = (_MAX_MANIFEST_BYTES if name == "manifest.json" else
                     _MAX_TASK_BYTES if name == "TASK.md" else _MAX_FILE_BYTES)
            if len(content) > limit:
                raise InputRejected("sealed input snapshot exceeds quota")
            total += len(content)
            if total > _MAX_TOTAL_BYTES:
                raise InputRejected("sealed input total quota exceeded")
            files[name] = content
        if (len(files) != len(saved) or
                len({name.casefold() for name in files}) != len(files) or
                "manifest.json" not in files or "TASK.md" not in files):
            raise InputRejected("sealed input snapshot names invalid")
        try:
            manifest = json.loads(files["manifest.json"].decode("utf-8"),
                                  object_pairs_hook=_pairs)
            task = files["TASK.md"].decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise InputRejected("sealed input snapshot metadata invalid") from exc
        if (type(manifest) is not dict or set(manifest) != {"files"} or
                type(manifest["files"]) is not dict or
                set(manifest["files"]) != set(files) - {"manifest.json"} or
                not task.strip() or "\x00" in task):
            raise InputRejected("sealed input snapshot contract invalid")
        digests = {}
        for name, expected in manifest["files"].items():
            _name(name)
            if (type(expected) is not str or
                    expected != hashlib.sha256(files[name]).hexdigest()):
                raise InputRejected("sealed manifest differs from snapshot")
            digests[name] = expected
        digest = hashlib.sha256(json.dumps({
            "files": digests,
            "manifest_sha256": hashlib.sha256(files["manifest.json"]).hexdigest(),
        }, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        if digest != row["input_digest"]:
            raise InputRejected("canonical input digest differs from snapshot")
        return row, files

    def metadata(self, token, thread, job_id):
        """Only hashes/names; does not imply on-disk re-verification or a lease."""
        with self.workspace.store._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row, files = self._load(con, token, thread, job_id)
            return {
                "job_id": row["job_id"],
                "state": row["state"],
                "version": row["version"],
                "input_digest": row["input_digest"],
                "files": [
                    {"name": name, "size": len(content),
                     "sha256": hashlib.sha256(content).hexdigest()}
                    for name, content in sorted(files.items())
                    if name != "manifest.json"
                ],
                "source": "sealed_manager_sqlite_snapshot",
                "on_disk_integrity": "NOT_RECHECKED",
                "worker_execution_enabled": False,
            }

    def read_bytes(self, token, thread, job_id, name):
        """Internal manager-only access; raw contents are NEVER returned by MCP."""
        _name(name)
        if name == "manifest.json":
            raise InputRejected("manager manifest is not a worker input document")
        with self.workspace.store._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            _, files = self._load(con, token, thread, job_id)
            if name not in files:
                raise InputRejected("input document not in sealed snapshot")
            return files[name]
