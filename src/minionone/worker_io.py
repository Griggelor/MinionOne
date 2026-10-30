"""Exact, manager-owned external worker I/O transcript.

The operator-approved workspace mode captures raw UTF-8 JSON request/response
BODIES and exact broker tool replies; HTTP Authorization headers are never
captured. These contents are SENSITIVE, unlike redacted job_telemetry.

Each record is persisted and hash-linked BEFORE the worker can act on its
results. No worker capability can write to this journal or output/.
"""
from __future__ import annotations

from hashlib import sha256
import json

from .admission import InputRejected, StateIntegrityRejected, _utc

_MAX_FILE=960*1024  # Bounded by native output publisher's 1 MiB file quota.
_MAX_RECORD=256*1024
_MAX_RECORDS=256
_RESERVED=4096
_KINDS=frozenset(("MODEL_REQUEST","MODEL_RESPONSE","TOOL_RESPONSE","CAPTURE_ERROR"))
_GENESIS="0"*64


def install(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS workspace_worker_io (
        job_id TEXT NOT NULL REFERENCES jobs(job_id),
        seq INTEGER NOT NULL CHECK(seq BETWEEN 1 AND 256),
        utc TEXT NOT NULL,kind TEXT NOT NULL,
        body TEXT NOT NULL,previous_hash TEXT NOT NULL,entry_hash TEXT NOT NULL,
        PRIMARY KEY(job_id,seq)
    );
    CREATE TRIGGER IF NOT EXISTS worker_io_no_update BEFORE UPDATE ON workspace_worker_io
        BEGIN SELECT RAISE(ABORT,'immutable worker io transcript'); END;
    CREATE TRIGGER IF NOT EXISTS worker_io_no_delete BEFORE DELETE ON workspace_worker_io
        BEGIN SELECT RAISE(ABORT,'immutable worker io transcript'); END;
    """)


def _serialize(seq,utc,kind,body,previous):
    # Escape embedded line breaks: one immutable JSON object per JSONL row.
    return json.dumps({"seq":seq,"utc":utc,"kind":kind,"body_utf8":body,
                       "previous_hash":previous},
                      sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")


def append(con,job_id,kind,body):
    if (kind not in _KINDS or type(body) is not str or
            not con.in_transaction or len(body.encode("utf-8"))>_MAX_RECORD):
        raise InputRejected("worker IO log event invalid or too large")
    prev=con.execute("""SELECT seq,entry_hash FROM workspace_worker_io
        WHERE job_id=? ORDER BY seq DESC LIMIT 1""",(job_id,)).fetchone()
    number=1 if prev is None else prev["seq"]+1
    if number>_MAX_RECORDS:raise InputRejected("worker IO log entry limit")
    previous=_GENESIS if prev is None else prev["entry_hash"]
    utc=_utc()
    canonical=_serialize(number,utc,kind,body,previous)
    digest=sha256(bytes.fromhex(previous)+canonical).hexdigest()
    # Reserve room for terminal CAPTURE_ERROR if the next model response
    # exceeds the transcript quota; no silent partial capture.
    used=con.execute("""SELECT COALESCE(SUM(length(CAST(body AS BLOB))*6+512),0)
        FROM workspace_worker_io WHERE job_id=?""",(job_id,)).fetchone()[0]
    if used+len(canonical)+_RESERVED >_MAX_FILE:
        raise InputRejected("worker IO log byte budget exhausted; refusing further work")
    con.execute("""INSERT INTO workspace_worker_io
        (job_id,seq,utc,kind,body,previous_hash,entry_hash)
        VALUES(?,?,?,?,?,?,?)""",(job_id,number,utc,kind,body,previous,digest))
    return number


def verify(con,job_id):
    rows=con.execute("""SELECT * FROM workspace_worker_io WHERE job_id=?
        ORDER BY seq LIMIT ?""",(job_id,_MAX_RECORDS+1)).fetchall()
    if len(rows)>_MAX_RECORDS:
        raise StateIntegrityRejected("worker IO transcript too long")
    previous=_GENESIS
    output=[]
    for i,row in enumerate(rows,1):
        try:
            canonical=_serialize(row["seq"],row["utc"],row["kind"],row["body"],previous)
            valid=(row["seq"]==i and row["kind"] in _KINDS and
                   row["previous_hash"]==previous and
                   sha256(bytes.fromhex(previous)+canonical).hexdigest()==row["entry_hash"])
        except (TypeError,ValueError,OverflowError):
            valid=False
        if not valid:raise StateIntegrityRejected("worker IO transcript failed integrity verification")
        previous=row["entry_hash"]
        output.append(json.loads(canonical.decode("ascii"))|{"entry_hash":previous})
    return output


def jsonl(con,job_id):
    rows=verify(con,job_id)
    if not rows:
        return b''
    value=b''.join(json.dumps(row,sort_keys=True,separators=(",",":"),
                              ensure_ascii=True).encode("ascii")+b"\n" for row in rows)
    if len(value)>_MAX_FILE:raise InputRejected("worker IO transcript publication quota")
    return value


def page(con,job_id,after_seq=0,limit=10):
    if (type(after_seq) is not int or not 0<=after_seq<=_MAX_RECORDS or
            type(limit) is not int or not 1<=limit<=10):
        raise InputRejected("invalid worker IO pagination")
    rows=verify(con,job_id)
    # Bound response size independently of a potentially 256 KiB event.
    selected=[];bytes_used=0
    for row in rows[after_seq:]:
        size=len(json.dumps(row).encode("ascii"))
        if selected and (len(selected)>=limit or bytes_used+size>512*1024):
            break
        selected.append(row)
        bytes_used+=size
    return {"schema_version":1,"job_id":job_id,
            "sensitive":True,"content_policy":"EXACT_WORKER_IO_NO_AUTH_HEADERS",
            "integrity":"VERIFIED_HASH_CHAIN",
            "events":selected,"has_more":after_seq+len(selected)<len(rows),
            "next_after_seq":after_seq+len(selected),"total_events":len(rows)}
