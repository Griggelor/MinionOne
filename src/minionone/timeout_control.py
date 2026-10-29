"""G2 manager-owned timeout and resume-intent journal (NO worker dispatcher).

Historical pilot does not start workers. A trusted future manager must attest
provider quiescence, seal checkpoint evidence, and fence capability *before*
calling record_timeout. The static pre-alpha MCP identity is not an actual
Unsloth original-chat attestation. A resume request never starts an attempt.
"""
from __future__ import annotations

import json
import re
from .admission import (
    AdmissionStore, InputRejected, PermissionDenied, IdempotencyConflict,
    VersionConflict, _field, _ID, _OP, _SHA, _digest, _utc
)
from .lifecycle import Evidence, check_transition


class TimeoutControlDenied(RuntimeError):
    """Uncertain or ineligible state cannot be resumed."""


class TimeoutControlStore:
    """Timeout metadata only; canonical state is stored in jobs, not here."""

    def __init__(self, admission: AdmissionStore):
        if type(admission) is not AdmissionStore:
            raise InputRejected("trusted canonical owner required")
        self.owner=admission
        with admission._connection() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS timeout_records(
                job_id TEXT PRIMARY KEY,
                timeout_operation_id TEXT NOT NULL,
                timeout_request_hash TEXT NOT NULL,
                attempt_epoch INTEGER NOT NULL CHECK(attempt_epoch>=1),
                reason_code TEXT NOT NULL CHECK(reason_code='TIMEOUT'),
                checkpoint_sha256 TEXT,
                worker_quiescent INTEGER NOT NULL CHECK(worker_quiescent IN (0,1)),
                resume_requested INTEGER NOT NULL DEFAULT 0
                    CHECK(resume_requested IN (0,1)),
                resume_operation_id TEXT,
                resume_request_hash TEXT,
                timeout_receipt_json TEXT NOT NULL,
                resume_receipt_json TEXT,
                created_utc TEXT NOT NULL,
                updated_utc TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            )""")

    @staticmethod
    def _view(job, record, fence_verified):
        # No raw checkpoint SHA, request key, resource handle or private data.
        return {
            "job_id":job["job_id"],"state":job["state"],
            "version":job["version"],"error_code":"TIMEOUT",
            "resume_available":bool(fence_verified and
                                    record["worker_quiescent"] and
                                    record["checkpoint_sha256"] is not None and
                                    job["state"]=="INTERRUPTED" and
                                    not record["resume_requested"]),
            "resume_requested":bool(record["resume_requested"]),
            "resume_started":False,
            "worker_running":False if record["worker_quiescent"] else None,
        }

    def status(self, parent_token, thread_id, job_id):
        principal=self.owner._principal(parent_token,thread_id)
        with self.owner._connection() as con:
            row=self.owner._owned_row(con,principal,thread_id,job_id)
            record=con.execute("SELECT * FROM timeout_records WHERE job_id=?",
                               (job_id,)).fetchone()
            if record is None:
                return None
            fence=con.execute(
                "SELECT status,attempt_epoch FROM attempt_fences WHERE job_id=?",
                (job_id,)).fetchone()
            verified=bool(fence is not None and fence["status"]=="FENCED" and
                          fence["attempt_epoch"]==record["attempt_epoch"])
            return self._view(row,record,verified)

    def record_timeout(self, job_id, *, expected_version, attempt_epoch,
                       worker_quiescent, checkpoint_sha256, operation_id):
        """Trusted MANAGER ONLY: fenced timeout, canonical state and audit CAS.

        If worker quiescence cannot be attested, enter reconciliation quarantine
        instead of pretending a resumable interrupted worker is safe.
        """
        _field(_ID,job_id,"job id")
        _field(_OP,operation_id,"timeout operation")
        if (type(expected_version) is not int or expected_version < 0 or
                type(attempt_epoch) is not int or attempt_epoch < 1 or
                type(worker_quiescent) is not bool):
            raise InputRejected("invalid timeout evidence")
        if checkpoint_sha256 is not None:
            _field(_SHA,checkpoint_sha256,"checkpoint sha256")
        request_hash=_digest([expected_version,attempt_epoch,
                              worker_quiescent,checkpoint_sha256])
        with self.owner._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            job=con.execute("SELECT * FROM jobs WHERE job_id=?",(job_id,)).fetchone()
            if job is None:
                raise VersionConflict("unknown canonical job")
            previous=con.execute("SELECT * FROM timeout_records WHERE job_id=?",
                                 (job_id,)).fetchone()
            if previous is not None:
                if (previous["timeout_operation_id"] != operation_id or
                        previous["timeout_request_hash"] != request_hash):
                    raise IdempotencyConflict("timeout operation key conflict")
                return json.loads(previous["timeout_receipt_json"])
            if job["state"] != "RUNNING" or job["version"] != expected_version:
                raise VersionConflict("job is not currently running at expected version")
            # A prior capability must already be fenced. A timeout signal
            # alone cannot assert provider termination.
            fence=con.execute("""SELECT status,attempt_epoch FROM attempt_fences
                                  WHERE job_id=?""",(job_id,)).fetchone()
            if (fence is None or fence["status"]!="FENCED" or
                    fence["attempt_epoch"]!=attempt_epoch):
                raise TimeoutControlDenied("attempt still active or unverified")
            target=("INTERRUPTED" if worker_quiescent else
                    "NEEDS_RECONCILIATION")
            evidence=(Evidence(attempt_no=attempt_epoch,worker_outcome="RUNNING",
                               error_code="TIMEOUT",worker_quiescent=True)
                      if worker_quiescent else
                      Evidence(attempt_no=attempt_epoch,
                               reconciliation_required=True,error_code="TIMEOUT"))
            check_transition("RUNNING",target,evidence)
            now=_utc()
            con.execute("UPDATE jobs SET state=?,version=?,updated_utc=? WHERE job_id=?",
                        (target,expected_version+1,now,job_id))
            seq=con.execute("""SELECT COALESCE(MAX(seq),0)+1 FROM admission_events
                               WHERE job_id=?""",(job_id,)).fetchone()[0]
            con.execute("INSERT INTO admission_events VALUES(?,?,?,?,?,?)",
                        (job_id,seq,target,operation_id,"MANAGER",now))
            updated=con.execute("SELECT * FROM jobs WHERE job_id=?",(job_id,)).fetchone()
            receipt={
                "job_id":job_id,"state":target,"version":updated["version"],
                "error_code":"TIMEOUT","resume_available":bool(
                    worker_quiescent and checkpoint_sha256 is not None),
                "resume_requested":False,"resume_started":False,
                "worker_running":False if worker_quiescent else None
            }
            con.execute("""INSERT INTO timeout_records(
                job_id,timeout_operation_id,timeout_request_hash,attempt_epoch,
                reason_code,checkpoint_sha256,worker_quiescent,resume_requested,
                timeout_receipt_json,created_utc,updated_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id,operation_id,request_hash,attempt_epoch,"TIMEOUT",
                 checkpoint_sha256,int(worker_quiescent),0,
                 json.dumps(receipt,sort_keys=True),now,now))
            return receipt

    def request_resume(self, parent_token, thread_id, job_id,
                       expected_version, operation_id):
        """Record authenticated parent intent only. NO attempt lease or launch.

        A separate future trusted manager must verify new epoch, checkpoint,
        provider quiescence and resource budget before RESUMING -> RUNNING.
        """
        principal=self.owner._principal(parent_token,thread_id)
        _field(_ID,job_id,"job id")
        _field(_OP,operation_id,"resume operation")
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected("invalid expected version")
        request_hash=_digest([expected_version,"REQUEST_RESUME"])
        with self.owner._connection() as con:
            con.execute("BEGIN IMMEDIATE")
            job=self.owner._owned_row(con,principal,thread_id,job_id)
            record=con.execute("SELECT * FROM timeout_records WHERE job_id=?",
                               (job_id,)).fetchone()
            if record is None:
                raise TimeoutControlDenied("job has no timeout")
            if record["resume_requested"]:
                if record["resume_operation_id"] == operation_id:
                    if record["resume_request_hash"]!=request_hash:
                        raise IdempotencyConflict("resume key reused")
                    return json.loads(record["resume_receipt_json"])
                raise VersionConflict("resume already requested")
            if job["version"]!=expected_version:
                raise VersionConflict("stale canonical job version")
            if (job["state"]!="INTERRUPTED" or not record["worker_quiescent"]
                    or record["checkpoint_sha256"] is None):
                raise TimeoutControlDenied("no verified resumable checkpoint")
            fence=con.execute("""SELECT status,attempt_epoch FROM attempt_fences
                                  WHERE job_id=?""",(job_id,)).fetchone()
            if (fence is None or fence["status"]!="FENCED" or
                    fence["attempt_epoch"]!=record["attempt_epoch"]):
                raise TimeoutControlDenied("previous attempt has not been fenced")
            now=_utc()
            con.execute("UPDATE jobs SET version=?,updated_utc=? WHERE job_id=?",
                        (expected_version+1,now,job_id))
            seq=con.execute("""SELECT COALESCE(MAX(seq),0)+1 FROM admission_events
                               WHERE job_id=?""",(job_id,)).fetchone()[0]
            # An intent event is not a RESUMING transition. Job state stays
            # INTERRUPTED until real new-epoch admission is implemented.
            con.execute("INSERT INTO admission_events VALUES(?,?,?,?,?,?)",
                        (job_id,seq,"INTERRUPTED",operation_id,
                         "AUTHENTICATED_PARENT",now))
            receipt={
                "job_id":job_id,"state":"INTERRUPTED",
                "version":expected_version+1,"error_code":"TIMEOUT",
                "resume_available":False,"resume_requested":True,
                "resume_started":False,"worker_running":False
            }
            con.execute("""UPDATE timeout_records
                           SET resume_requested=1,resume_operation_id=?,
                               resume_request_hash=?,resume_receipt_json=?,
                               updated_utc=? WHERE job_id=?""",
                        (operation_id,request_hash,json.dumps(receipt,sort_keys=True),
                         now,job_id))
            return receipt
