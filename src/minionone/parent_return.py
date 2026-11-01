"""Durable automatic parent-return orchestration for a trusted host driver.

The host driver must authenticate/capture the original chat, restore its model
and resume that same chat. No native Unsloth wakeup endpoint is invented here.
Notifications carry job metadata only; workers never receive parent history.
"""
from dataclasses import dataclass
import threading

from .admission import InputRejected, VersionConflict, _digest


@dataclass(frozen=True)
class ParentState:
    principal: str
    thread_id: str
    model_id: str
    resident: bool
    busy: bool
    authenticated: bool
    can_restore: bool
    can_resume: bool


@dataclass(frozen=True)
class ParentReceipt:
    job_id: str
    thread_id: str
    result_digest: str
    operation_id: str


class ParentReturnController:
    def __init__(self, owner, driver):
        if any(not callable(getattr(driver, method, None)) for method in ('state','restore','deliver')):
            raise InputRejected('trusted host driver requires state/restore/deliver')
        self.owner=owner; self.driver=driver; self.verified=False
        self._stop=threading.Event(); self._thread=None; self._tick_lock=threading.Lock()
        with owner._connection() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS parent_bindings(
                    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                    principal TEXT NOT NULL, thread_id TEXT NOT NULL, model_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS parent_returns(
                    job_id TEXT PRIMARY KEY REFERENCES parent_bindings(job_id),
                    result_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
                    ('PENDING','WAITING_PARENT','RESTORING','RESTORE_FAILED','IN_FLIGHT',
                     'DELIVERED','NEEDS_RECONCILIATION')),
                    restore_needed INTEGER NOT NULL DEFAULT 0 CHECK(restore_needed IN (0,1)));
            ''')
            con.execute("UPDATE parent_returns SET state='NEEDS_RECONCILIATION' WHERE state IN ('IN_FLIGHT','RESTORING')")

    @staticmethod
    def _valid(state, principal, thread, model=None):
        return (type(state) is ParentState and state.principal==principal and state.thread_id==thread
                and type(state.model_id) is str and 1<=len(state.model_id)<=256
                and (model is None or state.model_id==model)
                and all(type(getattr(state,k)) is bool for k in
                        ('resident','busy','authenticated','can_restore','can_resume'))
                and state.authenticated and state.can_resume)

    def bind(self, job_id):
        with self.owner._connection() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            prior=con.execute('SELECT * FROM parent_bindings WHERE job_id=?',(job_id,)).fetchone()
            if prior is not None:return
        state=self.driver.state(row['principal'],row['thread_id'])
        if not self._valid(state,row['principal'],row['thread_id']) or (not state.resident and not state.can_restore):
            raise InputRejected('original parent binding/restore capability unavailable')
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO parent_bindings VALUES(?,?,?,?)',
                        (job_id,row['principal'],row['thread_id'],state.model_id))
        self.verified=True

    def enqueue(self, job_id):
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('''SELECT j.state,p.result_digest FROM jobs j
                JOIN pilot_runs p USING(job_id) JOIN parent_bindings b USING(job_id)
                WHERE j.job_id=?''',(job_id,)).fetchone()
            if row is None or row['state']!='RESULT_READY' or not row['result_digest']:
                raise InputRejected('verified result and original binding required')
            prior=con.execute('SELECT result_digest FROM parent_returns WHERE job_id=?',(job_id,)).fetchone()
            if prior is not None:
                if prior[0]!=row['result_digest']:raise VersionConflict('return payload changed')
                return
            con.execute('INSERT INTO parent_returns(job_id,result_digest,state) VALUES(?,?,?)',
                        (job_id,row['result_digest'],'PENDING'))

    def status(self, job_id):
        with self.owner._connection() as con:
            row=con.execute('SELECT state,restore_needed FROM parent_returns WHERE job_id=?',(job_id,)).fetchone()
        return ({'state':row['state'],'restore_needed':bool(row['restore_needed'])}
                if row else {'state':'NOT_QUEUED','restore_needed':False})

    def require_delivery(self, job_id):
        result=self.status(job_id)
        if result['state']!='DELIVERED':
            raise VersionConflict('original parent delivery/restore not yet verified')
        return result['restore_needed']

    def _set(self, job, state, expected, *, restore_needed=None):
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            changed=con.execute('UPDATE parent_returns SET state=? WHERE job_id=? AND state=?',
                                (state,job,expected)).rowcount
            if changed and restore_needed is not None:
                con.execute('UPDATE parent_returns SET restore_needed=? WHERE job_id=?',
                            (int(restore_needed),job))
            return bool(changed)

    def tick(self):
        # No overlapping ticks, and never hold a SQLite write transaction
        # while the host is loading a model or accepting a notification.
        with self._tick_lock:
            # Repair the commit/enqueue boundary without rerunning inference:
            # a result may have committed before a transient queue failure.
            with self.owner._connection() as con:
                missing=con.execute('''SELECT j.job_id FROM jobs j
                    JOIN parent_bindings b USING(job_id) LEFT JOIN parent_returns r USING(job_id)
                    WHERE j.state='RESULT_READY' AND r.job_id IS NULL LIMIT 256''').fetchall()
            for item in missing:self.enqueue(item[0])
            with self.owner._connection() as con:
                rows=con.execute('''SELECT r.*,b.principal,b.thread_id,b.model_id
                    FROM parent_returns r JOIN parent_bindings b USING(job_id)
                    JOIN jobs j USING(job_id) WHERE r.state IN ('PENDING','WAITING_PARENT')
                    AND j.state='RESULT_READY' LIMIT 256''').fetchall()
            for row in rows:
                job=row['job_id']; old=row['state']
                try: state=self.driver.state(row['principal'],row['thread_id'])
                except Exception:
                    self._set(job,'WAITING_PARENT',old);continue
                if not self._valid(state,row['principal'],row['thread_id'],row['model_id']) or state.busy:
                    self._set(job,'WAITING_PARENT',old);continue
                self.verified=True
                if not state.resident:
                    if not state.can_restore:
                        self._set(job,'RESTORE_FAILED',old);continue
                    if not self._set(job,'RESTORING',old,restore_needed=True):continue
                    try:
                        state=self.driver.restore(row['principal'],row['thread_id'],row['model_id'],
                                                  'restore-'+_digest(job))
                    except Exception:
                        self._set(job,'RESTORE_FAILED','RESTORING');continue
                    if (not self._valid(state,row['principal'],row['thread_id'],row['model_id']) or
                            not state.resident):
                        self._set(job,'RESTORE_FAILED','RESTORING');continue
                    self._set(job,'WAITING_PARENT','RESTORING')
                    old='WAITING_PARENT'
                    if state.busy:continue
                if not self._set(job,'IN_FLIGHT',old):continue
                operation='return-'+_digest([job,row['result_digest']])
                try:
                    receipt=self.driver.deliver(row['principal'],row['thread_id'],
                        job,row['result_digest'],operation)
                    expected=ParentReceipt(job,row['thread_id'],row['result_digest'],operation)
                    if type(receipt) is not ParentReceipt or receipt!=expected:
                        raise ValueError('unverified return receipt')
                except Exception:
                    self._set(job,'NEEDS_RECONCILIATION','IN_FLIGHT');continue
                self._set(job,'DELIVERED','IN_FLIGHT')

    def start(self):
        # Recover only delivery intent for already finished workers, never
        # inference or unknown prior sends. Bindings were captured pre-start.
        with self.owner._connection() as con:
            jobs=con.execute("SELECT job_id FROM jobs JOIN parent_bindings USING(job_id) WHERE state='RESULT_READY'").fetchall()
        for job in jobs:self.enqueue(job[0])
        def run():
            while not self._stop.wait(.25):
                try:self.tick()
                except Exception:pass  # Durable pending intent remains inspectable.
        self._thread=threading.Thread(target=run,daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:self._thread.join(timeout=1)
