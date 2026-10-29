"""Manager-internal G2 attempt reservation and epoch fencing.

This is a *non-executing* security primitive. It never starts a worker,
performs I/O on job files, contacts providers, or establishes caller identity.
Only an independently authenticated manager may use it. An attested QUEUED
job with a matching version can reserve exactly one attempt; on uncertain
restart the reservation is permanently fenced until a future reviewed
reconciliation protocol exists. Neither a capability nor a worker result is
accepted as authority to complete a job.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from pathlib import Path
import re
import secrets
import sqlite3


class AttemptDenied(ValueError):
    """Invalid request or no confirmed eligibility."""


class AttemptConflict(RuntimeError):
    """Stale epoch, reused operation or preexisting attempt."""


_JOB = re.compile(r'JOB-[a-zA-Z0-9_-]{1,60}\Z', re.ASCII)
_KEY = re.compile(r'[A-Za-z0-9_.:-]{8,128}\Z', re.ASCII)
_TOKEN = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _valid(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


class AttemptFenceStore:
    """One-shot reservations in the existing canonical job SQLite database.

    Database path and this Python object MUST be manager-private. is_current
    attests *only* a particular reservation's fencing status; it does not
    authorize worker/file/provider access, which must be checked separately.
    """

    def __init__(self, manager_database_path, *, max_entries=2048):
        if type(max_entries) is not int or not 1 <= max_entries <= 100_000:
            raise AttemptDenied('invalid quota')
        self.path = Path(manager_database_path)  # trusted operator configuration
        if not self.path.is_file():
            raise AttemptDenied('canonical database unavailable')
        self.max_entries = max_entries
        with self._db() as con:
            cols = {r[1] for r in con.execute('PRAGMA table_info(jobs)')}
            if not {'job_id', 'state', 'version'} <= cols:
                raise AttemptDenied('canonical jobs table unavailable')
            con.executescript('''
                CREATE TABLE IF NOT EXISTS attempt_fences(
                    job_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL,
                    job_version INTEGER NOT NULL CHECK(job_version >= 0),
                    attempt_epoch INTEGER NOT NULL CHECK(attempt_epoch = 1),
                    status TEXT NOT NULL CHECK(status IN ('RESERVED','FENCED')),
                    capability_sha256 TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    updated_utc TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
            ''')

    @contextmanager
    def _db(self):
        con = sqlite3.connect(str(self.path), isolation_level=None, timeout=15.0)
        try:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            con.execute('PRAGMA busy_timeout=15000')
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA synchronous=FULL')
            yield con
            if con.in_transaction:
                con.commit()
        except BaseException:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _receipt(row):
        return {
            'job_id': row['job_id'],
            'attempt_epoch': row['attempt_epoch'],
            'state': row['status'],
            'job_version': row['job_version'],
        }

    def reserve(self, job_id, *, operation_id, expected_job_version):
        """Reserve a single future attempt, without dispatching or granting I/O.

        The first caller receives an ephemeral high-entropy capability.
        Replays NEVER reissue the capability. A different operation key
        or changed canonical job state/version fails closed.
        """
        if not _valid(job_id, _JOB) or not _valid(operation_id, _KEY):
            raise AttemptDenied('invalid job or operation identifier')
        if type(expected_job_version) is not int or expected_job_version < 0:
            raise AttemptDenied('invalid expected state version')
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            job = con.execute('SELECT state,version FROM jobs WHERE job_id=?',
                              (job_id,)).fetchone()
            if job is None:
                raise AttemptDenied('job unavailable')
            row = con.execute('SELECT * FROM attempt_fences WHERE job_id=?',
                              (job_id,)).fetchone()
            if row is not None:
                if (row['operation_id'] != operation_id or
                        row['job_version'] != expected_job_version or
                        job['state'] != 'QUEUED' or
                        job['version'] != row['job_version']):
                    raise AttemptConflict('reservation cannot be reused')
                return {**self._receipt(row), 'replayed': True}
            if job['state'] != 'QUEUED' or job['version'] != expected_job_version:
                raise AttemptDenied('no eligible queued manager job')
            if con.execute('SELECT count(*) FROM attempt_fences').fetchone()[0] >= self.max_entries:
                raise AttemptDenied('reservation quota exhausted')
            token = secrets.token_hex(32)
            now = _now()
            con.execute('''INSERT INTO attempt_fences
              (job_id,operation_id,job_version,attempt_epoch,status,
               capability_sha256,created_utc,updated_utc)
               VALUES (?,?,?,?,?,?,?,?)''',
                (job_id,operation_id,expected_job_version,1,'RESERVED',
                 sha256(token.encode('ascii')).hexdigest(),now,now))
            row = con.execute('SELECT * FROM attempt_fences WHERE job_id=?',
                              (job_id,)).fetchone()
            return {**self._receipt(row),'replayed':False,'capability':token}

    def status(self, job_id):
        """Manager-only metadata. Never returns a capability or its hash."""
        if not _valid(job_id, _JOB):
            raise AttemptDenied('invalid job id')
        with self._db() as con:
            row=con.execute('SELECT * FROM attempt_fences WHERE job_id=?',
                            (job_id,)).fetchone()
            return None if row is None else self._receipt(row)

    def is_current(self, job_id, attempt_epoch, capability):
        """Internal fence validation, NOT independent file/tool authorization."""
        if (not _valid(job_id,_JOB) or type(attempt_epoch) is not int or
                attempt_epoch < 1 or not _valid(capability,_TOKEN)):
            return False
        with self._db() as con:
            row=con.execute('''SELECT a.status,a.attempt_epoch,a.job_version,
                               a.capability_sha256,j.state,j.version
                               FROM attempt_fences a
                               JOIN jobs j ON j.job_id=a.job_id
                               WHERE a.job_id=?''',(job_id,)).fetchone()
            return bool(
                row is not None and row['status']=='RESERVED' and
                row['attempt_epoch']==attempt_epoch and
                row['state']=='QUEUED' and row['version']==row['job_version'] and
                hmac.compare_digest(
                    row['capability_sha256'],
                    sha256(capability.encode('ascii')).hexdigest()
                )
            )

    def revoke(self, job_id, *, expected_epoch):
        """Trusted manager fences an attempt; no automatic reissue."""
        if not _valid(job_id,_JOB) or type(expected_epoch) is not int or expected_epoch < 1:
            raise AttemptDenied('invalid revoke input')
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM attempt_fences WHERE job_id=?',
                            (job_id,)).fetchone()
            if row is None or row['attempt_epoch']!=expected_epoch:
                raise AttemptConflict('stale or unknown attempt')
            if row['status']=='RESERVED':
                con.execute('''UPDATE attempt_fences SET status='FENCED',updated_utc=?
                    WHERE job_id=? AND attempt_epoch=? AND status='RESERVED' ''',
                    (_now(),job_id,expected_epoch))
            return self._receipt(con.execute('SELECT * FROM attempt_fences WHERE job_id=?',
                                             (job_id,)).fetchone())

    def fence_unreconciled(self):
        """Quarantine unverified reservations after a crash or restart.

        This does NOT assert termination of an external worker or restore
        any resource residency; provider reconciliation remains a blocker.
        """
        with self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            cursor=con.execute("""UPDATE attempt_fences SET status='FENCED',
                updated_utc=? WHERE status='RESERVED'""", (_now(),))
            return cursor.rowcount
