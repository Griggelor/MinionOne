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


class StateSchemaRejected(InputRejected):
    """Manager-owned database compatibility failure, not a client input error."""


class StateIntegrityRejected(InputRejected):
    """Detected inconsistent stored references; no automatic repair."""


class ControlJournalUnavailable(RuntimeError):
    """A rejected control could not be durably recorded. Never report success."""

    def __init__(self, message, *, storage_error=None):
        super().__init__(message)
        # Preserve only a catalog code, never the raw storage exception/cause.
        from .diagnostics import classify_storage_failure
        self.storage_code = (None if storage_error is None else
                             classify_storage_failure(storage_error).code)


_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z', re.ASCII)
_OP = re.compile(r'[A-Za-z0-9_.:-]{8,128}\Z', re.ASCII)
_SHA = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)


_JOB_TABLE = """CREATE TABLE IF NOT EXISTS {name}(
 job_id TEXT PRIMARY KEY, principal TEXT NOT NULL, thread_id TEXT NOT NULL,
 input_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
 ('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK','RUNNING','INTERRUPTED',
  'RESUMING','NEEDS_RECONCILIATION','CANCEL_REQUESTED','CANCELLED',
  'RESULT_READY','COMPLETED','FAILED','WAITING_FOR_PARENT')),
 version INTEGER NOT NULL CHECK(version>=0),
 job_kind TEXT NOT NULL DEFAULT 'NON_EXECUTING'
   CHECK(job_kind IN ('NON_EXECUTING','ISOLATED_TEST','WORKSPACE')),
 worker_started INTEGER NOT NULL DEFAULT 0 CHECK(worker_started IN (0,1))
   CHECK(worker_started=0 OR job_kind IN ('ISOLATED_TEST','WORKSPACE')),
 created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL)"""


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
            con.execute(_JOB_TABLE.format(name='jobs'))
            con.executescript('''
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
            # Additive metadata on the SAME canonical event timeline. These
            # rows describe rejected parent commands, never a second job state.
            con.execute('''CREATE TABLE IF NOT EXISTS control_errors(
                job_id TEXT NOT NULL, seq INTEGER NOT NULL,
                operation TEXT NOT NULL CHECK(operation IN ('ABORT','RESUME_REQUEST')),
                operation_digest TEXT NOT NULL CHECK(length(operation_digest)=64),
                request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
                state_version INTEGER NOT NULL CHECK(state_version>=0),
                attempt_epoch INTEGER CHECK(attempt_epoch>=1),
                error_code TEXT NOT NULL CHECK(error_code IN
                    ('STALE_VERSION','IDEMPOTENCY_CONFLICT','RESUME_NOT_READY')),
                diagnostic_json TEXT NOT NULL,
                PRIMARY KEY(job_id,operation,operation_digest),
                UNIQUE(job_id,seq),
                FOREIGN KEY(job_id,seq) REFERENCES admission_events(job_id,seq)
            )''')
            from .job_telemetry import install
            install(con)

    def _migrate_legacy_jobs(self, con):
        sqlrow = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()
        if sqlrow is None:
            raise StateSchemaRejected('canonical jobs schema unavailable')
        definition = sqlrow[0]
        normalized = ''.join(definition.split())
        if 'job_kind' in normalized:
            allowed_guard = ("CHECK(worker_started=0ORjob_kind='ISOLATED_TEST')" in normalized or
                "CHECK(worker_started=0ORjob_kindIN('ISOLATED_TEST','WORKSPACE'))" in normalized)
            if (not allowed_guard or "CHECK(worker_startedIN(0,1))" not in normalized or
                    not all(x in normalized for x in ("'RESULT_READY'","'COMPLETED'","'FAILED'"))):
                raise StateSchemaRejected('unsafe executable jobs schema')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise StateIntegrityRejected('broken canonical foreign keys')
            if "'WORKSPACE'" not in normalized:
                # Exact v0.25 constraint expansion for the first executable
                # workspace slice. The canonical row/ID and all child FKs
                # survive the single atomic table replacement.
                con.execute("PRAGMA foreign_keys=OFF")
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute(_JOB_TABLE.format(name="jobs__v026_rebuilt"))
                    con.execute("""INSERT INTO jobs__v026_rebuilt
                      (job_id,principal,thread_id,input_digest,state,version,
                       job_kind,worker_started,created_utc,updated_utc)
                      SELECT job_id,principal,thread_id,input_digest,state,version,
                             job_kind,worker_started,created_utc,updated_utc FROM jobs""")
                    con.execute("DROP TABLE jobs")
                    con.execute("ALTER TABLE jobs__v026_rebuilt RENAME TO jobs")
                    if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise StateIntegrityRejected('workspace migration foreign-key integrity failure')
                    con.commit()
                except BaseException:
                    if con.in_transaction: con.rollback()
                    raise
                finally:
                    con.execute("PRAGMA foreign_keys=ON")
            return
        # Support exactly the known historical 3-state and 6-state schemas.
        old_three="('ACCEPTED','CANCEL_REQUESTED','CANCELLED')"
        old_six=(
            "('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',"
            "'CANCEL_REQUESTED','CANCELLED')"
        )
        old_ten = ("('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',"
                   "'RUNNING','INTERRUPTED','RESUMING','NEEDS_RECONCILIATION',"
                   "'CANCEL_REQUESTED','CANCELLED')")
        if (not any(x in normalized for x in (old_three,old_six,old_ten)) or
                not all(term in normalized for term in (
                    'CHECK(worker_started=0)', 'PRIMARYKEY', 'input_digest',
                    'thread_id', 'created_utc', 'updated_utc'))):
            raise StateSchemaRejected('unexpected historical jobs schema')
        # Changing CHECK requires a table rebuild in SQLite. Disable FK
        # enforcement only for the duration of this manager-owned migration;
        # verify ALL existing references BEFORE committing. BEGIN IMMEDIATE
        # serializes this migration with concurrent SQLite writers.
        con.execute('PRAGMA foreign_keys=OFF')
        try:
            con.execute('BEGIN IMMEDIATE')
            con.execute(_JOB_TABLE.format(name='jobs__g2_preflight'))
            con.execute('''INSERT INTO jobs__g2_preflight
              (job_id,principal,thread_id,input_digest,state,version,
               worker_started,created_utc,updated_utc)
               SELECT job_id,principal,thread_id,input_digest,state,version,
                      worker_started,created_utc,updated_utc FROM jobs''')
            con.execute('DROP TABLE jobs')
            con.execute('ALTER TABLE jobs__g2_preflight RENAME TO jobs')
            if con.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise StateIntegrityRejected('legacy migration foreign-key integrity failure')
            con.commit()
        except BaseException:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.execute('PRAGMA foreign_keys=ON')

    @contextmanager
    def _connection(self, *, timeout_seconds=10.0):
        if type(timeout_seconds) not in (int,float) or not 0<timeout_seconds<=10:
            raise InputRejected('invalid bounded database timeout')
        con = sqlite3.connect(str(self.database_path), isolation_level=None, timeout=timeout_seconds)
        try:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA busy_timeout='+str(max(1,int(timeout_seconds*1000))))
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
            from .job_telemetry import append
            append(con,job_id,'AUDIT','JOB_ACCEPTED',{'state':'ACCEPTED','version':0})
            append(con,job_id,'JOB','JOB_ACCEPTED',{'state':'ACCEPTED','version':0})
            return receipt

    def status(self, parent_token, thread_id, job_id, *, include_timeout=False):
        principal = self._principal(parent_token, thread_id)
        with self._connection() as con:
            con.execute('BEGIN')
            row = self._owned_row(con, principal, thread_id, job_id)
            snapshot = self._snapshot(row)
            error = con.execute('''SELECT e.*,c.operation,c.state_version,
                c.attempt_epoch,c.diagnostic_json FROM admission_events e
                JOIN control_errors c ON c.job_id=e.job_id AND c.seq=e.seq
                WHERE e.job_id=? ORDER BY e.seq DESC LIMIT 1''', (job_id,)).fetchone()
            if error is not None:
                snapshot['latest_control_error'] = self._control_error_view(error)
            if include_timeout:
                from .timeout_control import TimeoutControlStore
                timeout = TimeoutControlStore.project(con,row)
                if timeout is not None:
                    snapshot['timeout'] = timeout
            if row['job_kind'] in ('ISOLATED_TEST','WORKSPACE'):
                from .pilot_jobs import PilotJobs
                snapshot['test_job' if row['job_kind']=='ISOLATED_TEST' else 'workspace_job'] = PilotJobs.project(con,row)
            return snapshot

    def _transition(self, con, row, new, evidence, operation_id, *, actor='MANAGER'):
        """Canonical owner of actual job transitions, within the caller's txn."""
        check_transition(row['state'],new,evidence)
        now=_utc()
        changed=con.execute('UPDATE jobs SET state=?,version=version+1,updated_utc=?, '
                            "worker_started=CASE WHEN ?='RUNNING' THEN 1 ELSE worker_started END "
                            'WHERE job_id=? AND version=?',
                            (new,now,new,row['job_id'],row['version']))
        if changed.rowcount != 1:
            raise VersionConflict('canonical transition lost CAS')
        seq=con.execute('SELECT COALESCE(MAX(seq),0)+1 FROM admission_events WHERE job_id=?',
                        (row['job_id'],)).fetchone()[0]
        con.execute('INSERT INTO admission_events VALUES (?,?,?,?,?,?)',
                    (row['job_id'],seq,new,operation_id,actor,now))
        from .job_telemetry import append
        append(con,row['job_id'],'AUDIT','JOB_TRANSITION',
               {'previous_state':row['state'],'state':new,'version':row['version']+1})
        append(con,row['job_id'],'JOB','JOB_TRANSITION',
               {'previous_state':row['state'],'state':new,'version':row['version']+1})
        return con.execute('SELECT * FROM jobs WHERE job_id=?',(row['job_id'],)).fetchone()

    def trace(self,parent_token,thread_id,job_id,*,after_seq=0,limit=100):
        principal=self._principal(parent_token,thread_id)
        with self._connection() as con:
            con.execute('BEGIN')
            self._owned_row(con,principal,thread_id,job_id)
            from .job_telemetry import read
            return read(con,job_id,after_seq=after_seq,limit=limit)

    def events(self, parent_token, thread_id, job_id):
        principal = self._principal(parent_token, thread_id)
        with self._connection() as con:
            con.execute('BEGIN')
            self._owned_row(con, principal, thread_id, job_id)
            rows = con.execute('''SELECT e.*,c.operation,c.state_version,
                c.attempt_epoch,c.diagnostic_json FROM admission_events e
                LEFT JOIN control_errors c ON c.job_id=e.job_id AND c.seq=e.seq
                WHERE e.job_id=? ORDER BY e.seq LIMIT 500''', (job_id,)).fetchall()
            return [self._control_error_view(r) if r['operation'] is not None else
                    {k:r[k] for k in ('seq','status','operation_id','actor','occurred_utc')}
                    for r in rows]

    @staticmethod
    def _control_error_view(row):
        return {**{k:row[k] for k in
                   ('seq','status','operation_id','actor','occurred_utc',
                    'operation','state_version','attempt_epoch')},
                'type':'CONTROL_REJECTED', 'schema_version':1,
                'phase':'PARENT_CONTROL', 'error':json.loads(row['diagnostic_json'])}

    @staticmethod
    def _control_error_types():
        # Late import avoids the timeout adapter's import of the canonical owner.
        from .timeout_control import TimeoutControlDenied
        return {'STALE_VERSION':VersionConflict,
                'IDEMPOTENCY_CONFLICT':IdempotencyConflict,
                'RESUME_NOT_READY':TimeoutControlDenied}

    def _raise_rejected_control(self, con, job_id, operation, operation_id,
                                expected_version):
        """Call AFTER checking an existing successful receipt for this command.

        A conflicting request must not poison the original accepted receipt.
        A rejected command, however, must never become a new action on replay.
        """
        prior = con.execute('''SELECT request_hash,error_code FROM control_errors
            WHERE job_id=? AND operation=? AND operation_digest=?''',
            (job_id,operation,_digest([job_id,operation,operation_id]))).fetchone()
        if prior is not None:
            if prior['request_hash'] != _digest([expected_version,operation]):
                raise IdempotencyConflict('rejected control key reused')
            raise self._control_error_types()[prior['error_code']](
                'previously rejected control; inspect job events')

    @contextmanager
    def _job_control(self, principal, thread_id, job_id, expected_version,
                     operation_id, operation):
        """One transaction for a control outcome and its canonical audit.

        Authorization precedes replay and journaling. Roll back all partial
        command writes to a savepoint before recording a classified rejection.
        Commit the error BEFORE raising it to the MCP boundary. SQLite failures
        roll back everything; no separate best-effort logger claims durability.
        """
        _field(_ID, job_id, 'job id')
        _field(_OP, operation_id, 'operation id')
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected('invalid expected version')
        if operation not in ('ABORT','RESUME_REQUEST'):
            raise InputRejected('invalid control operation')
        types = self._control_error_types()
        failure = None
        try:
            with self._connection() as con:
                con.execute('BEGIN IMMEDIATE')
                row = self._owned_row(con, principal, thread_id, job_id)
                con.execute('SAVEPOINT parent_control')
                try:
                    yield con, row
                except tuple(types.values()) as exc:
                    con.execute('ROLLBACK TO parent_control')
                    con.execute('RELEASE parent_control')
                    code = next(k for k,cls in types.items() if isinstance(exc,cls))
                    try:
                        self._record_control_error(con, row, operation, operation_id,
                                                   expected_version, code)
                    except sqlite3.Error as exc:
                        raise ControlJournalUnavailable('error journal write failed',storage_error=exc) from None
                    failure = types[code]('control rejected; inspect job events')
                else:
                    con.execute('RELEASE parent_control')
        except sqlite3.Error as exc:
            if failure is not None:
                raise ControlJournalUnavailable('error journal commit failed',storage_error=exc) from None
            raise
        if failure is not None:
            raise failure

    def _record_control_error(self, con, row, operation, operation_id,
                              expected_version, code):
        from .diagnostics import diagnostic
        operation_digest = _digest([row['job_id'],operation,operation_id])
        prior = con.execute('''SELECT 1 FROM control_errors
            WHERE job_id=? AND operation=? AND operation_digest=?''',
            (row['job_id'],operation,operation_digest)).fetchone()
        if prior is not None:
            return
        count = con.execute('SELECT count(*) FROM control_errors WHERE job_id=?',
                            (row['job_id'],)).fetchone()[0]
        if count >= 128:
            raise ControlJournalUnavailable('error journal capacity exhausted')
        # No attempt is invented for admitted/non-executing jobs. An existing
        # fence identifies the observed epoch; it does not prove worker liveness.
        epoch = None
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attempt_fences'").fetchone():
            fence = con.execute('SELECT attempt_epoch FROM attempt_fences WHERE job_id=?',
                                (row['job_id'],)).fetchone()
            if fence is not None:
                epoch = fence['attempt_epoch']
        seq = con.execute('SELECT COALESCE(MAX(seq),0)+1 FROM admission_events WHERE job_id=?',
                          (row['job_id'],)).fetchone()[0]
        con.execute('INSERT INTO admission_events VALUES (?,?,?,?,?,?)',
                    (row['job_id'],seq,row['state'],'error:'+operation_digest,
                     'AUTHENTICATED_PARENT_REQUEST_ERROR',_utc()))
        con.execute('INSERT INTO control_errors VALUES (?,?,?,?,?,?,?,?,?)',
                    (row['job_id'],seq,operation,operation_digest,
                     _digest([expected_version,operation]),row['version'],epoch,
                     code,json.dumps(diagnostic(code).to_dict(),sort_keys=True)))

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
        if type(expected_version) is not int or expected_version < 0:
            raise InputRejected('invalid expected version')
        digest = _digest([expected_version,'abort'])
        with self._job_control(principal,thread_id,job_id,expected_version,
                               operation_id,'ABORT') as (con,row):
            prior = con.execute('SELECT request_hash,receipt FROM controls WHERE job_id=? AND operation_id=?',
                                (job_id,operation_id)).fetchone()
            if prior:
                if prior['request_hash'] != digest:
                    raise IdempotencyConflict('control key reused with different request')
                return json.loads(prior['receipt'])
            self._raise_rejected_control(con,job_id,'ABORT',operation_id,expected_version)
            if (row['version'] != expected_version or
                    row['state'] not in ('ACCEPTED','VALIDATING','QUEUED','RESOURCE_CHECK',
                                         'INTERRUPTED') or
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
