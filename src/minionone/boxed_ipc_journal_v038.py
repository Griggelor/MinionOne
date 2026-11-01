"""Durable manager-only receipt journal for future boxed IPC; no process launcher.

State is persisted *before* any transport side effect. An IN_FLIGHT
attempt is never automatically retried after a crash, even if the worker
has no recorded reply. Receipt observation is NOT an accepted result,
isolated-process attestation, artifact publication or parent ACK.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3

from .boxed_ipc_v038 import (
    FrameRejected, ProbeGrant, ProtocolUncertain, new_probe_grant,
    request_frame, validate_reply, _id, _JOB, _ATTEMPT, _OPERATION, _NONCE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS boxed_probe_receipts_v038 (
    operation_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    nonce TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK(state IN
      ('PREPARED','IN_FLIGHT','RESPONSE_OBSERVED','NEEDS_RECONCILIATION')),
    request_sha256 TEXT NOT NULL,
    response_sha256 TEXT,
    result_code TEXT,
    CHECK ((state='RESPONSE_OBSERVED' AND response_sha256 IS NOT NULL
            AND result_code IN ('PONG','DENIED'))
           OR (state!='RESPONSE_OBSERVED' AND
               response_sha256 IS NULL AND result_code IS NULL))
)
"""


@dataclass(frozen=True, slots=True)
class ReceiptState:
    state: str
    request_sha256: str
    response_sha256: str | None
    observed_code: str | None
    worker_execution_enabled: bool = False
    os_sandbox_attested: bool = False
    result_ready: bool = False
    automatic_replay_allowed: bool = False


class BoxedProbeJournal:
    """Manager-selected fixed-PING receipt registry. Manager DB path trusted."""

    def __init__(self, database_path):
        if type(database_path) is not str and not isinstance(database_path, Path):
            raise FrameRejected("trusted journal database path required")
        self._path = str(database_path)
        if not self._path:
            raise FrameRejected("missing journal database path")
        with self._connection() as con:
            con.execute(_SCHEMA)

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _identity(job_id, attempt_id, operation_id):
        _id(job_id, _JOB, "job")
        _id(attempt_id, _ATTEMPT, "attempt")
        _id(operation_id, _OPERATION, "operation")

    @staticmethod
    def _row(con, job_id, attempt_id, operation_id):
        row = con.execute(
            "SELECT job_id,attempt_id,nonce,state,request_sha256,"
            "response_sha256,result_code FROM boxed_probe_receipts_v038 "
            "WHERE operation_id=?", (operation_id,),
        ).fetchone()
        if row is None:
            raise ProtocolUncertain("unknown durable boxed operation")
        if row[0] != job_id or row[1] != attempt_id:
            raise ProtocolUncertain("operation identifier reused across job scopes")
        _id(row[2], _NONCE, "persisted nonce")
        return row

    @staticmethod
    def _snapshot(row):
        return ReceiptState(
            state=row[3], request_sha256=row[4],
            response_sha256=row[5], observed_code=row[6],
        )

    def prepare(self, job_id, attempt_id, operation_id):
        """Idempotent reservation only; does NOT send anything to a runner."""
        self._identity(job_id, attempt_id, operation_id)
        with self._connection() as con:
            found = con.execute(
                "SELECT job_id,attempt_id,nonce,state FROM boxed_probe_receipts_v038 "
                "WHERE operation_id=?", (operation_id,),
            ).fetchone()
            if found is not None:
                if found[0] != job_id or found[1] != attempt_id:
                    raise ProtocolUncertain("operation id conflicts with existing scope")
                if found[3] != "PREPARED":
                    raise ProtocolUncertain("existing or uncertain attempt cannot be relaunched")
                grant = ProbeGrant(job_id, attempt_id, operation_id, found[2])
            else:
                grant = new_probe_grant(job_id, attempt_id, operation_id)
                wire = request_frame(grant)
                con.execute(
                    "INSERT INTO boxed_probe_receipts_v038 "
                    "(operation_id,job_id,attempt_id,nonce,state,request_sha256) "
                    "VALUES (?,?,?,?,?,?)",
                    (operation_id, job_id, attempt_id, grant.nonce, "PREPARED",
                     hashlib.sha256(wire).hexdigest()),
                )
            return grant

    def before_dispatch(self, grant):
        """Durably fence an attempt before caller may initiate transport.

        The returned frame does NOT grant permission to run a process.
        Only the yet-unimplemented manager-owned launcher may use it.
        """
        if type(grant) is not ProbeGrant:
            raise FrameRejected("trusted probe grant required")
        wire = request_frame(grant)
        digest = hashlib.sha256(wire).hexdigest()
        with self._connection() as con:
            row = self._row(con, grant.job_id, grant.attempt_id, grant.operation_id)
            if (row[2] != grant.nonce or row[4] != digest or
                    row[3] != "PREPARED"):
                raise ProtocolUncertain("ambiguous or already dispatched boxed operation")
            con.execute(
                "UPDATE boxed_probe_receipts_v038 SET state='IN_FLIGHT' "
                "WHERE operation_id=? AND state='PREPARED'", (grant.operation_id,))
        return wire

    def observe_response(self, grant, wire):
        """Record an UNTRUSTED response; only exact re-observation idempotent.

        On malformed/conflicting bytes, persist NEEDS_RECONCILIATION before
        raising rather than accidentally rolling back the quarantine.
        """
        if type(grant) is not ProbeGrant or type(wire) is not bytes:
            raise FrameRejected("invalid receipt input")
        failure = None
        state = None
        with self._connection() as con:
            row = self._row(con, grant.job_id, grant.attempt_id, grant.operation_id)
            if row[2] != grant.nonce:
                raise ProtocolUncertain("receipt nonce does not match durable attempt")
            if row[3] not in ("IN_FLIGHT", "RESPONSE_OBSERVED"):
                raise ProtocolUncertain("receipt lacks in-flight authority")
            digest = hashlib.sha256(wire).hexdigest()
            if row[3] == "RESPONSE_OBSERVED":
                if row[5] == digest:
                    return self._snapshot(row)
                failure = "conflicting second IPC observation"
            else:
                try:
                    result = validate_reply(wire, grant)
                except FrameRejected:
                    failure = "invalid or wrong-attempt IPC response"
                else:
                    con.execute(
                        "UPDATE boxed_probe_receipts_v038 "
                        "SET state='RESPONSE_OBSERVED',response_sha256=?,result_code=? "
                        "WHERE operation_id=? AND state='IN_FLIGHT'",
                        (digest, result, grant.operation_id),
                    )
                    state = ReceiptState("RESPONSE_OBSERVED", row[4], digest, result)
            if failure is not None:
                con.execute(
                    "UPDATE boxed_probe_receipts_v038 "
                    "SET state='NEEDS_RECONCILIATION',response_sha256=NULL,"
                    "result_code=NULL WHERE operation_id=?",
                    (grant.operation_id,),
                )
        if failure is not None:
            raise ProtocolUncertain(failure)
        return state

    def status(self, job_id, attempt_id, operation_id):
        self._identity(job_id, attempt_id, operation_id)
        with self._connection() as con:
            return self._snapshot(
                self._row(con, job_id, attempt_id, operation_id))
