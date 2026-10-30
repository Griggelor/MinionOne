"""Opt-in bounded SQLite-backed workspace worker dispatch (development only).

The selected parent project is never writable by the model. Only manager-pinned
UTF-8 documents cross into the existing document broker; the manager publishes
verified output after worker quiescence. This is NOT target-host G1a/G1b
attestation and MUST NOT be enabled in a production deployment.
"""
from __future__ import annotations

from hashlib import sha256
import json

from .admission import InputRejected, VersionConflict, IdempotencyConflict, PermissionDenied, _field, _OP
from .pilot_jobs import _name, _text
from .workspace_input_snapshot import SealedWorkspaceInput
from .workspace_publication import WorkspacePublisher
from .win32_output_publication import WindowsOutputPublisher
import os


class WorkspaceJobs:
    def __init__(self, workspace, pilot, token, thread):
        self.workspace = workspace
        self.pilot = pilot
        self.token = token
        self.thread = thread
        self.reader = SealedWorkspaceInput(workspace)
        self.publisher = (WindowsOutputPublisher if os.name == "nt" else WorkspacePublisher)(workspace)

    def start(self, token, thread, job_id, expected_version, operation_id, model_id):
        """Start on SAME sealed job ID, with per-job CAS and retry safety.

        The on-disk input is checked before QUEUED. Provider/model readiness is
        then checked by the bounded worker before any inference. The worker
        only gets a copy of the verified SQLite snapshot, not OS paths.
        """
        _field(_OP, operation_id, "operation")
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected("invalid workspace version")
        if type(model_id) is not str or model_id != self.pilot.config.model_id:
            raise InputRejected("unapproved workspace worker model")
        principal = self.workspace.store._principal(token, thread)
        # Idempotent replay must not run a second worker or reissue a capability.
        with self.workspace.store._connection() as con:
            row = self.workspace.store._owned_row(con,principal,thread,job_id)
            previous = self.pilot._receipt(con,job_id,"START",operation_id,[expected_version+1])
            if previous is not None:
                if row["job_kind"] != "WORKSPACE":
                    raise IdempotencyConflict("workspace operation kind conflict")
                return previous
            if row["job_kind"] != "NON_EXECUTING" or row["state"] != "ACCEPTED" or row["version"] != expected_version:
                raise VersionConflict("workspace not accepted at expected version")
        # Refuse a changed on-disk input even when its manifest was also rewritten.
        self.workspace.verify_input(token,thread,job_id)
        with self.workspace.store._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = self.workspace.store._owned_row(con,principal,thread,job_id)
            prior = self.pilot._receipt(con,job_id,"START",operation_id,[expected_version+1])
            if prior is not None:
                if row["job_kind"] != "WORKSPACE":
                    raise IdempotencyConflict("workspace operation kind conflict")
                return prior
            if row["job_kind"] != "NON_EXECUTING" or row["state"] != "ACCEPTED" or row["version"] != expected_version:
                raise VersionConflict("workspace admission changed during verification")
            snapshot, files = self.reader._load(con,token,thread,job_id)
            if snapshot["input_digest"] != row["input_digest"]:
                raise InputRejected("sealed workspace digest changed")
            # Reuse the existing bounded document tool protocol. Binary files,
            # large sources and ambiguous casefold names fail CLOSED.
            task = _text(files["TASK.md"].decode("utf-8"),4096)
            others = {}
            for name, blob in files.items():
                if name in ("TASK.md","manifest.json"):
                    continue
                canonical = _name(name)
                if canonical in others or canonical == "task.md":
                    raise InputRejected("workspace document name collision")
                try:
                    others[canonical] = _text(blob.decode("utf-8"),4096)
                except UnicodeError as exc:
                    raise InputRejected("binary workspace input not supported") from exc
            if not 1 <= len(others) <= 4:
                raise InputRejected("workspace requires one to four UTF-8 documents")
            if sum(len(x.encode("utf-8")) for x in others.values()) > 8192:
                raise InputRejected("workspace document quota exhausted")
            active = con.execute("SELECT 1 FROM pilot_runs WHERE active=1 OR provider_inflight=1").fetchone()
            if active is not None:
                raise InputRejected("another worker has exclusive provider lease")
            con.execute("UPDATE jobs SET job_kind='WORKSPACE' WHERE job_id=? AND version=?",
                        (job_id, expected_version))
            con.execute("INSERT INTO pilot_runs(job_id,task,model_id) VALUES(?,?,?)",
                        (job_id,task,model_id))
            for name,content in {"task.md":task,**others}.items():
                con.execute("INSERT INTO pilot_files VALUES(?,?,?,?,?)",
                            (job_id,"input",name,content,sha256(content.encode("utf-8")).hexdigest()))
            refreshed = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            row = self.workspace.store._transition(con,refreshed,"QUEUED",
                    __import__("minionone.lifecycle",fromlist=["Evidence"]).Evidence(),
                    "workspace-queued-"+sha256(operation_id.encode()).hexdigest()[:24])
            queued_version = row["version"]
        return self.pilot.start(token,thread,job_id,queued_version,operation_id,
                                kind="WORKSPACE")

    def result(self, token, thread, job_id):
        return self.pilot.result(token,thread,job_id,kind="WORKSPACE")

    def acknowledge(self, token, thread, job_id, expected_version, result_digest, operation_id):
        return self.pilot.acknowledge(token,thread,job_id,expected_version,
                                      result_digest,operation_id,kind="WORKSPACE")

    def cancel(self, token, thread, job_id, expected_version, operation_id):
        return self.pilot.cancel(token,thread,job_id,expected_version,
                                 operation_id,kind="WORKSPACE")

    def publish_finished(self, job_id, cap):
        """Executed only after AgentLoop returns AWAITING_REVIEW.

        Crash after a visible file but before RESULT_READY remains quarantined
        by pilot_run recovery; we never falsely assert COMPLETED.
        """
        self.workspace.verify_input(self.token,self.thread,job_id)
        with self.workspace.store._connection() as con:
            con.execute("BEGIN")
            self.pilot._current(con,job_id,cap)
            rows = con.execute("""SELECT name,content,digest FROM pilot_files
                WHERE job_id=? AND zone='output' ORDER BY name""",
                (job_id,)).fetchall()
            if not rows:
                raise InputRejected("workspace worker did not publish an artifact")
            outputs = [(r["name"], r["content"].encode("utf-8"), r["digest"]) for r in rows]
        for name,content,digest in outputs:
            if sha256(content).hexdigest()!=digest:
                raise InputRejected("workspace output digest invalid")
            operation = "ws-pub-"+sha256((job_id+"\0"+name).encode()).hexdigest()[:40]
            self.publisher.stage(self.token,self.thread,job_id,name,content,operation)
            self.publisher.publish(self.token,self.thread,job_id,name,operation)
