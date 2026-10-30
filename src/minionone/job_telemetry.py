"""Durable, content-free per-job diagnostics and append-only audit hash chain.

The canonical jobs table and admission_events remain the state authorities.
Telemetry is a constrained observational projection only. All writes are
inside the owner's SQLite transaction; no secrets, task text, paths, provider
responses, prompts or reasoning are ever accepted as telemetry fields.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from .admission import _utc

_LEVELS=frozenset(("JOB","DEBUG","AUDIT"))
_EVENTS=frozenset((
    "JOB_ACCEPTED","JOB_TRANSITION","MODEL_REQUEST_STARTED",
    "MODEL_REQUEST_FINISHED","MODEL_REQUEST_FAILED",
    "WORKER_TOOL_COMPLETED","RESULT_DELIVERED",
))
_NAMES=frozenset(("list_files","read_file","create_work","publish_artifact",
                  "report_progress","ask_question","FORBIDDEN_TOOL"))
_STATES=frozenset(("ACCEPTED","VALIDATING","QUEUED","RESOURCE_CHECK",
                   "RUNNING","WAITING_FOR_PARENT","PAUSED","INTERRUPTED",
                   "RESUMING","CANCEL_REQUESTED","CANCELLED","RESULT_READY",
                   "INTEGRATING","COMPLETED","FAILED","RESTORATION_REQUIRED",
                   "NEEDS_RECONCILIATION"))
_CODES=re.compile(r"[A-Z0-9_]{1,64}\Z",re.ASCII)
_HASH=re.compile(r"[a-f0-9]{64}\Z",re.ASCII)
_KEYS=frozenset(("state","previous_state","version","tool","result_code",
                 "tokens","duration_ms","turn","attempt","error_code","result_digest"))
_GENESIS="0"*64
_MAX_EVENTS=2048


def _details(details):
    if type(details) is not dict or set(details)-_KEYS:
        raise ValueError("telemetry includes forbidden detail fields")
    clean={}
    for key,value in details.items():
        if key in ("state","previous_state"):
            good=type(value) is str and value in _STATES
        elif key=="tool":
            good=type(value) is str and value in _NAMES
        elif key in ("result_code","error_code"):
            good=type(value) is str and _CODES.fullmatch(value) is not None
        elif key=="result_digest":
            good=type(value) is str and _HASH.fullmatch(value) is not None
        else:
            good=type(value) is int and 0<=value<=4_000_000_000
        if not good:raise ValueError("invalid redacted telemetry field")
        clean[key]=value
    return clean


def install(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS job_telemetry (
            job_id TEXT NOT NULL REFERENCES jobs(job_id),
            seq INTEGER NOT NULL CHECK(seq BETWEEN 1 AND 2048),
            utc TEXT NOT NULL,
            level TEXT NOT NULL CHECK(level IN ('JOB','DEBUG','AUDIT')),
            event TEXT NOT NULL,
            details TEXT NOT NULL,
            previous_hash TEXT NOT NULL CHECK(length(previous_hash)=64),
            entry_hash TEXT NOT NULL CHECK(length(entry_hash)=64),
            PRIMARY KEY(job_id,seq)
        );
        CREATE TRIGGER IF NOT EXISTS telemetry_no_update
          BEFORE UPDATE ON job_telemetry
          BEGIN SELECT RAISE(ABORT,'immutable diagnostic journal'); END;
        CREATE TRIGGER IF NOT EXISTS telemetry_no_delete
          BEFORE DELETE ON job_telemetry
          BEGIN SELECT RAISE(ABORT,'immutable diagnostic journal'); END;
    """)


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=True,
                      separators=(",",":")).encode("ascii")


def append(con,job_id,level,event,details=None):
    """Fail-closed append in an already owned and started database transaction."""
    if (level not in _LEVELS or event not in _EVENTS or
            not con.in_transaction):
        raise ValueError("invalid telemetry transaction or category")
    clean=_details({} if details is None else details)
    previous=con.execute("""SELECT seq,entry_hash FROM job_telemetry
          WHERE job_id=? ORDER BY seq DESC LIMIT 1""",(job_id,)).fetchone()
    number=1 if previous is None else previous["seq"]+1
    if number>_MAX_EVENTS:
        raise ValueError("per-job telemetry quota exceeded; job fenced")
    prior=_GENESIS if previous is None else previous["entry_hash"]
    utc=_utc()
    payload={"seq":number,"utc":utc,"level":level,"event":event,"details":clean}
    digest=sha256(bytes.fromhex(prior)+_canonical(payload)).hexdigest()
    con.execute("""INSERT INTO job_telemetry
        (job_id,seq,utc,level,event,details,previous_hash,entry_hash)
        VALUES(?,?,?,?,?,?,?,?)""",
        (job_id,number,utc,level,event,_canonical(clean).decode("ascii"),prior,digest))
    return number


def read(con,job_id,*,after_seq=0,limit=100):
    """Verify entire bounded chain BEFORE showing even a truncated page."""
    if type(after_seq) is not int or not 0<=after_seq<=_MAX_EVENTS:
        raise ValueError("invalid telemetry cursor")
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("invalid telemetry page size")
    rows=con.execute("""SELECT * FROM job_telemetry WHERE job_id=?
              ORDER BY seq LIMIT ?""",(job_id,_MAX_EVENTS+1)).fetchall()
    if len(rows)>_MAX_EVENTS:
        raise ValueError("telemetry journal exceeds maximum")
    previous=_GENESIS
    records=[]
    for i,row in enumerate(rows,1):
        try:
            fields=json.loads(row["details"])
            clean=_details(fields)
            payload={"seq":row["seq"],"utc":row["utc"],"level":row["level"],
                     "event":row["event"],"details":clean}
            if (row["seq"]!=i or row["level"] not in _LEVELS or
                    row["event"] not in _EVENTS or
                    row["previous_hash"]!=previous or
                    sha256(bytes.fromhex(previous)+_canonical(payload)).hexdigest()
                    !=row["entry_hash"]):
                raise ValueError("invalid hash chain")
        except (ValueError,KeyError,TypeError) as exc:
            from .admission import StateIntegrityRejected
            raise StateIntegrityRejected("job diagnostic journal integrity failure") from None
        previous=row["entry_hash"]
        if i>after_seq:
            records.append({**payload,"entry_hash":previous})
    page=records[:limit]
    return {"schema_version":1,"job_id":job_id,
            "trace_id":sha256(job_id.encode("ascii")).hexdigest()[:24],
            "integrity":"VERIFIED_HASH_CHAIN",
            "events":page,"next_after_seq":page[-1]["seq"] if page else after_seq,
            "has_more":len(records)>limit,"total_events":len(rows)}
