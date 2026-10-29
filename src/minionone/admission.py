"""Experimental authenticated admission, transactional CAS and cancellation.

NOT a production MCP service: Windows file confinement, Unsloth thread-attestation,
provider integration, output publishing and delivery acknowledgement are absent.
The credentials map is trusted administrator configuration, never model output.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from .lifecycle import Evidence, check_transition


class InputRejected(ValueError):
    pass


class PermissionDenied(PermissionError):
    pass


class IdempotencyConflict(ValueError):
    pass


class VersionConflict(ValueError):
    pass


_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z', re.ASCII)
_OP = re.compile(r'[A-Za-z0-9_.:-]{8,128}\Z', re.ASCII)
_SHA = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def _field(pattern, value, name):
    if type(value) is not str or not pattern.fullmatch(value):
        raise InputRejected('invalid ' + name)


class AdmissionStore:
    """Internal admission/CAS store for jobs that NEVER launch workers.

    This class must not be bound directly to untrusted MCP tools. Authenticating
    the true parent and chat thread on the transport requires a separately
    demonstrated upstream integration. In this slice the mapping is preapproved.
    """

    def __init__(self, database_path, *, credentials, max_jobs_per_principal=256):
        if (type(max_jobs_per_principal) is not int or
                not 1 <= max_jobs_per_principal <= 4096):
            raise InputRejected('invalid admission quota')
        self.max_jobs_per_principal = max_jobs_per_principal
        if type(credentials) is not dict or not credentials:
            raise InputRejected('trusted credentials mapping required')
        clean = {}
        for token, identity in credentials.items():
            if type(token) is not str or not 32 <= len(token) <= 512:
                raise InputRejected('invalid configured credential')
            if not isinstance(identity, tuple) or len(identity) != 2:
                raise InputRejected('invalid configured identity')
            principal, threads = identity
            _field(_ID, principal, 'configured principal')
            if type(threads) not in (set, frozenset) or not threads:
                raise InputRejected('configured threads must be nonempty set')
            for thread in threads:
                _field(_ID, thread, 'configured thread')
            clean[token] = (principal, frozenset(threads))
        self._credentials = clean
        self.database_path = Path(database_path)  # trusted manager configuration only
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS jobs(
              job_id TEXT PRIMARY KEY, principal TEXT NOT NULL, thread_id TEXT NOT NULL,
              input_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
                ('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',
               'RUNNING','INTERRUPTED','RESUMING','NEEDS_RECONCILIATION',
               'CANCEL_REQUESTED','CANCELLED')),
              version INTEGER NOT NULL CHECK(version>=0),
              worker_started INTEGER NOT NULL DEFAULT 0 CHECK(worker_started=0),
              created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admissions(
              principal TEXT NOT NULL, operation_id TEXT NOT NULL,
              request_hash TEXT NOT NULL, job_id TEXT NOT NULL, receipt TEXT NOT NULL,
              PRIMARY KEY(principal,operation_id), FOREIGN KEY(job_id) REFERENCES jobs(job_id));
            CREATE TABLE IF NOT EXISTS controls(
              job_id TEXT NOT NULL, operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
              receipt TEXT NOT NULL, PRIMARY KEY(job_id,operation_id),
              FOREIGN KEY(job_id) REFERENCES jobs(job_id));
            CREATE TABLE IF NOT EXISTS admission_events(
              job_id TEXT NOT NULL, seq INTEGER NOT NULL, status TEXT NOT NULL,
              operation_id TEXT NOT NULL, actor TEXT NOT NULL, occurred_utc TEXT NOT NULL,
              PRIMARY KEY(job_id,seq), FOREIGN KEY(job_id) REFERENCES jobs(job_id));
            ''')
            # Legacy v0.10 databases had only ACCEPTED/CANCEL_REQUESTED/
            # CANCELLED in the canonical table's CHECK constraint. Migrate
            # that SAME table (never create a second state owner).
            self._migrate_legacy_jobs(con)
            con.execute('''CREATE TABLE IF NOT EXISTS manager_transitions(
                job_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                request_hash TEXT NOT NULL, receipt TEXT NOT NULL,
                PRIMARY KEY(job_id,operation_id),
                FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            )''')

    def _migrate_legacy_jobs(self, con):
        sqlrow = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if sqlrow is None:
            raise InputRejected('canonical jobs schema unavailable')
        definition = sqlrow[0]
        normalized = ''.join(definition.split())
        if (all(("'" + state + "'") in definition for state in (
                "VALIDATING","RESOURCE_CHECK","RUNNING","INTERRUPTED",
                "RESUMING","NEEDS_RECONCILIATION"))):
            if 'CHECK(worker_started=0)' not in normalized:
                raise InputRejected('worker execution must remain database-disabled')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise InputRejected('broken canonical foreign keys')
            return
        # Support exactly the known historical 3-state and 6-state schemas.
        old_three="('ACCEPTED','CANCEL_REQUESTED','CANCELLED')"
        old_six=(
            "('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',"
            "'CANCEL_REQUESTED','CANCELLED')"
        )
        if (not any(x in normalized for x in (old_three,old_six)) or
                not all(term in normalized for term in (
                    'CHECK(worker_started=0)', 'PRIMARYKEY', 'input_digest',
                    'thread_id', 'created_utc', 'updated_utc'))):
            raise InputRejected('unexpected historical jobs schema')
        # Changing CHECK requires a table rebuild in SQLite. Disable FK
        # enforcement only for the duration of this manager-owned migration;
        # verify ALL existing references BEFORE committing. BEGIN IMMEDIATE
        # serializes this migration with concurrent SQLite writers.
        con.execute('PRAGMA foreign_keys=OFF')
        try:
            con.execute('BEGIN IMMEDIATE')
            con.execute('''CREATE TABLE jobs__g2_preflight(
              job_id TEXT PRIMARY KEY, principal TEXT NOT NULL, thread_id TEXT NOT NULL,
              input_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
                ('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',
                 'RUNNING','INTERRUPTED','RESUMING','NEEDS_RECONCILIATION',
                 'CANCEL_REQUESTED','CANCELLED')),
              version INTEGER NOT NULL CHECK(version>=0),
              worker_started INTEGER NOT NULL DEFAULT 0 CHECK(worker_started=0),
              created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL)''')
            con.execute('''INSERT INTO jobs__g2_preflight
              (job_id,principal,thread_id,input_digest,state,version,
               worker_started,created_utc,updated_utc)
               SELECT job_id,principal,thread_id,input_digest,state,version,
                      worker_started,created_utc,updated_utc FROM jobs''')
            con.execute('DROP TABLE jobs')
            con.execute('ALTER TABLE jobs__g2_preflight RENAME TO jobs')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise InputRejected('legacy migration foreign-key integrity failure')
            con.commit()
        except BaseException:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.execute('PRAGMA foreign_keys=ON')

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(str(self.database_path), isolation_level=None, timeout=10.0)
        try:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA busy_timeout=10000')
            con.execute('PRAGMA foreign_keys=ON')
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

    def _principal(self, token, thread_id):
        if type(token) is not str or type(thread_id) is not str:
            raise PermissionDenied('unauthorized parent/thread')
        found = None
        for known, scope in self._credentials.items():
            if hmac.compare_digest(token, known):
                found = scope
        if found is None or thread_id not in found[1]:
            raise PermissionDenied('unauthorized parent/thread')
        return found[0]

    @staticmethod
    def _snapshot(row):
        return {k: row[k] for k in
                ('job_id','state','version','input_digest','created_utc','updated_utc')}

    def _owned_row(self, con, principal, thread_id, job_id):
        _field(_ID, job_id, 'job id')
        row = con.execute('SELECT * FROM jobs WHERE job_id=? AND principal=? AND thread_id=?',
                          (job_id, principal, thread_id)).fetchone()
        if row is None:
            raise PermissionDenied('job unavailable')
        return row

    def admit(self, parent_token, thread_id, input_digest, operation_id):
        principal = self._principal(parent_token, thread_id)
        _field(_ID, thread_id, 'thread')
        _field(_SHA, input_digest, 'input digest')
        _field(_OP, operation_id, 'operation id')
        request_hash = _digest([thread_id, input_digest])
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            prior = con.execute('SELECT request_hash,job_id,receipt FROM admissions WHERE principal=? AND operation_id=?',
                                (principal, operation_id)).fetchone()
            if prior:
                if prior['request_hash'] != request_hash:
                    raise IdempotencyConflict('admission key reused with different request')
                self._owned_row(con, principal, thread_id, prior['job_id'])
                return json.loads(prior['receipt'])
            active_count = con.execute(
                'SELECT COUNT(*) FROM jobs WHERE principal=?', (principal,)
            ).fetchone()[0]
            if active_count >= self.max_jobs_per_principal:
                raise InputRejected('admission quota exhausted')
            job_id = 'JOB-' + secrets.token_hex(16)
            now = _utc()
            con.execute('INSERT INTO jobs(job_id,principal,thread_id,input_digest,state,version,created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?)',
                        (job_id,principal,thread_id,input_digest,'ACCEPTED',0,now,now))
            receipt = self._snapshot(self._owned_row(con, principal, thread_id, job_id))
            con.execute('INSERT INTO admissions VALUES (?,?,?,?,?)',
                        (principal,operation_id,request_hash,job_id,json.dumps(receipt,sort_keys=True)))
            con.execute('INSERT INTO admission_events VALUES (?,?,?,?,?,?)',
                        (job_id,1,'ACCEPTED',operation_id,'MANAGER',now))
            return receipt

    def status(self, parent_token, thread_id, job_id):
        principal = self._principal(parent_token, thread_id)
        with self._connection() as con:
            return self._snapshot(self._owned_row(con, principal, thread_id, job_id))

    def events(self, parent_token, thread_id, job_id):
        principal = self._principal(parent_token, thread_id)
        with self._connection() as con:
            self._owned_row(con, principal, thread_id, job_id)
            return [dict(r) for r in con.execute('''SELECT seq,status,operation_id,actor,occurred_utc
              FROM admission_events WHERE job_id=? ORDER BY seq LIMIT 500''',(job_id,)).fetchall()]

    def advance_pre_dispatch(self, job_id, expected_version, operation_id, target_state):
        """Trusted manager ONLY: durable pre-execution stages on canonical jobs.

        Never bind this method to an untrusted MCP/worker tool. There is no
        worker launch, file authorization, model-provider or output operation.
        Worker_started remains CHECK(worker_started=0) in SQLite.
        """
        _field(_ID, job_id, 'job id')
        _field(_OP, operation_id, 'operation id')
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected('invalid expected version')
        if type(target_state) is not str or target_state not in (
                'VALIDATING', 'QUEUED', 'RESOURCE_CHECK'):
            raise InputRejected('invalid pre-dispatch state')
        request_hash = _digest([expected_version, target_state])
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM jobs WHERE job_id=?',
                              (job_id,)).fetchone()
            if row is None:
                raise VersionConflict('canonical job unavailable')
            prior = con.execute(
                'SELECT request_hash,receipt FROM manager_transitions '
                'WHERE job_id=? AND operation_id=?',
                (job_id,operation_id)).fetchone()
            if prior is not None:
                if prior['request_hash'] != request_hash:
                    raise IdempotencyConflict('manager transition key conflict')
                return json.loads(prior['receipt'])
            if (row['worker_started'] != 0 or row['version'] != expected_version
                    or row['state'] not in ('ACCEPTED','VALIDATING','QUEUED')):
                raise VersionConflict('stale or ineligible pre-dispatch state')
            check_transition(row['state'], target_state, Evidence())
            seq = con.execute(
                'SELECT COALESCE(MAX(seq),0)+1 FROM admission_events WHERE job_id=?',
                (job_id,)).fetchone()[0]
            now = _utc()
            con.execute(
                'UPDATE jobs SET state=?,version=?,updated_utc=? '
                'WHERE job_id=? AND version=?',
                (target_state,expected_version+1,now,job_id,expected_version))
            con.execute('INSERT INTO admission_events VALUES (?,?,?,?,?,?)',
                        (job_id,seq,target_state,operation_id,'MANAGER',now))
            receipt = self._snapshot(con.execute(
                'SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone())
            con.execute('INSERT INTO manager_transitions VALUES (?,?,?,?)',
                        (job_id,operation_id,request_hash,
                         json.dumps(receipt,sort_keys=True)))
            return receipt

    def request_abort(self, parent_token, thread_id, job_id, expected_version, operation_id):
        principal = self._principal(parent_token, thread_id)
        _field(_ID, job_id, 'job id')
        _field(_OP, operation_id, 'operation id')
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected('invalid expected version')
        digest = _digest([expected_version,'abort'])
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = self._owned_row(con, principal, thread_id, job_id)
            prior = con.execute('SELECT request_hash,receipt FROM controls WHERE job_id=? AND operation_id=?',
                                (job_id,operation_id)).fetchone()
            if prior:
                if prior['request_hash'] != digest:
                    raise IdempotencyConflict('control key reused with different request')
                return json.loads(prior['receipt'])
            if (row['version'] != expected_version or
                    row['state'] not in ('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK') or
                    row['worker_started'] != 0):
                raise VersionConflict('stale or ineligible state')
            check_transition(row['state'], 'CANCEL_REQUESTED', Evidence())
            now = _utc()
            con.execute('UPDATE jobs SET state=?,version=?,updated_utc=? WHERE job_id=?',
                        ('CANCEL_REQUESTED',expected_version+1,now,job_id))
            seq = con.execute(
                'SELECT COALESCE(MAX(seq),0)+1 FROM admission_events WHERE job_id=?',
                (job_id,)).fetchone()[0]
            con.execute('INSERT INTO admission_events VALUES (?,?,?,?,?,?)',
                        (job_id,seq,'CANCEL_REQUESTED',operation_id,'AUTHENTICATED_PARENT',now))
            receipt = self._snapshot(self._owned_row(con,principal,thread_id,job_id))
            con.execute('INSERT INTO controls VALUES (?,?,?,?)',
                        (job_id,operation_id,digest,json.dumps(receipt,sort_keys=True)))
            return receipt

    def confirm_quiescent_cancel(self, job_id, expected_version, operation_id):
        """Internal only; safe ONLY because this slice never starts a worker."""
        _field(_ID, job_id, 'job id')
        _field(_OP, operation_id, 'operation id')
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected('invalid expected version')
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if (row is None or row['version'] != expected_version
                    or row['state'] != 'CANCEL_REQUESTED' or row['worker_started'] != 0):
                raise VersionConflict('not verified quiescent')
            check_transition(row['state'], 'CANCELLED',
                             Evidence(worker_outcome='CANCELLED',worker_quiescent=True))
            now = _utc()
            con.execute('UPDATE jobs SET state=?,version=?,updated_utc=? WHERE job_id=?',
                        ('CANCELLED',expected_version+1,now,job_id))
            seq = con.execute(
                'SELECT COALESCE(MAX(seq),0)+1 FROM admission_events WHERE job_id=?',
                (job_id,)).fetchone()[0]
            con.execute('INSERT INTO admission_events VALUES (?,?,?,?,?,?)',
                        (job_id,seq,'CANCELLED',operation_id,'MANAGER',now))
            return self._snapshot(con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone())
