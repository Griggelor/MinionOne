"""Durable automatic parent-return orchestration for a trusted host driver.

The host driver must authenticate/capture the original chat, restore its model
and resume that same chat. No native Unsloth wakeup endpoint is invented here.
Notifications carry job metadata only; workers never receive parent history.
"""
from dataclasses import dataclass
import threading

from .admission import InputRejected, VersionConflict, _digest
from .diagnostics import diagnostic,safe_tool_fault


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
            con.execute("""CREATE TABLE IF NOT EXISTS parent_return_history(
                job_id TEXT NOT NULL, result_digest TEXT NOT NULL, record_json TEXT NOT NULL,
                PRIMARY KEY(job_id,result_digest))""")
            con.execute("UPDATE parent_returns SET state='NEEDS_RECONCILIATION' WHERE state IN ('IN_FLIGHT','RESTORING')")
            if 'last_error_code' not in {r[1] for r in con.execute('PRAGMA table_info(parent_returns)')}:
                con.execute('ALTER TABLE parent_returns ADD COLUMN last_error_code TEXT')

    @staticmethod
    def _valid(state, principal, thread, model=None):
        return (type(state) is ParentState and state.principal==principal and state.thread_id==thread
                and type(state.model_id) is str and 1<=len(state.model_id)<=256
                and (model is None or state.model_id==model)
                and all(type(getattr(state,k)) is bool for k in
                        ('resident','busy','authenticated','can_restore','can_resume'))
                and state.authenticated and state.can_resume)

    def bind(self, job_id, *, refresh=False):
        with self.owner._connection() as con:
            row=con.execute('SELECT * FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            prior=con.execute('SELECT * FROM parent_bindings WHERE job_id=?',(job_id,)).fetchone()
            if prior is not None and not refresh:return
        state=self.driver.state(row['principal'],row['thread_id'])
        if not self._valid(state,row['principal'],row['thread_id'],prior['model_id'] if prior else None) or (not state.resident and not state.can_restore):
            raise InputRejected('original parent binding/restore capability unavailable')
        if callable(getattr(self.driver,'capture',None)):
            capture=(getattr(self.driver,'refresh_capture',self.driver.capture) if refresh else self.driver.capture)
            capture(job_id,row['principal'],row['thread_id'])
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT OR IGNORE INTO parent_bindings VALUES(?,?,?,?)',
                        (job_id,row['principal'],row['thread_id'],state.model_id))
        self.verified=True

    def enqueue(self, job_id):
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('''SELECT j.state,p.result_digest,p.question,p.error_code FROM jobs j
                JOIN pilot_runs p USING(job_id) JOIN parent_bindings b USING(job_id)
                WHERE j.job_id=?''',(job_id,)).fetchone()
            if row is None or row['state'] not in ('RESULT_READY','WAITING_FOR_PARENT','FAILED','CANCELLED'):
                raise InputRejected('verified result and original binding required')
            digest=(row['result_digest'] if row['state']=='RESULT_READY' else
                    _digest([job_id,row['state'],row['question'],row['error_code'],
                             con.execute('SELECT attempt_no FROM pilot_runs WHERE job_id=?',(job_id,)).fetchone()[0]]))
            if not digest:raise InputRejected('verified notification required')
            prior=con.execute('SELECT result_digest FROM parent_returns WHERE job_id=?',(job_id,)).fetchone()
            if prior is not None:
                if prior[0]!=digest:raise VersionConflict('return payload changed')
                return
            con.execute('INSERT INTO parent_returns(job_id,result_digest,state) VALUES(?,?,?)',
                        (job_id,digest,'PENDING'))
            table=con.execute("SELECT 1 FROM sqlite_master WHERE name='model_leases'").fetchone()
            if table:
                lease=con.execute('SELECT parent_unloaded FROM model_leases WHERE job_id=?',(job_id,)).fetchone()
                if lease and lease[0]:con.execute('UPDATE parent_returns SET restore_needed=1 WHERE job_id=?',(job_id,))

    def archive_for_resume(self,con,job):
        import json
        row=con.execute('SELECT * FROM parent_returns WHERE job_id=?',(job,)).fetchone()
        if row is None or row['state']!='DELIVERED':raise VersionConflict('question delivery not verified')
        con.execute('INSERT INTO parent_return_history VALUES(?,?,?)',
            (job,row['result_digest'],json.dumps(dict(row),sort_keys=True)))
        if callable(getattr(self.driver,'archive_for_resume',None)):
            self.driver.archive_for_resume(con,job)
        con.execute('DELETE FROM parent_returns WHERE job_id=? AND result_digest=?',(job,row['result_digest']))

    def status(self, job_id):
        with self.owner._connection() as con:
            row=con.execute('SELECT state,restore_needed,last_error_code FROM parent_returns WHERE job_id=?',(job_id,)).fetchone()
        return ({'state':row['state'],'restore_needed':bool(row['restore_needed']),
                 **({'error':diagnostic(row['last_error_code']).to_dict()} if row['last_error_code'] else {})}
                if row else {'state':'NOT_QUEUED','restore_needed':False})

    def require_delivery(self, job_id):
        result=self.status(job_id)
        if result['state']!='DELIVERED' and callable(getattr(self.driver,'reconcile',None)):
            self._reconcile(job_id)
            result=self.status(job_id)
        if result['state']!='DELIVERED':
            raise VersionConflict('original parent delivery/restore not yet verified')
        return result['restore_needed']

    def _set(self, job, state, expected, *, restore_needed=None,error_code=None,digest=None):
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            changed=con.execute('UPDATE parent_returns SET state=?,last_error_code=? WHERE job_id=? AND state=? AND (? IS NULL OR result_digest=?)',
                                (state,error_code,job,expected,digest,digest)).rowcount
            if changed and restore_needed is not None:
                con.execute('UPDATE parent_returns SET restore_needed=? WHERE job_id=?',
                            (int(restore_needed),job))
            return bool(changed)

    def _reconcile(self,job):
        if not callable(getattr(self.driver,'reconcile',None)):return
        with self.owner._connection() as con:
            row=con.execute('SELECT r.*,b.principal,b.thread_id FROM parent_returns r JOIN parent_bindings b USING(job_id) WHERE job_id=?',(job,)).fetchone()
        if row is None or row['state'] not in ('IN_FLIGHT','NEEDS_RECONCILIATION'):return
        update=lambda state,expected,**kw:self._set(job,state,expected,digest=row['result_digest'],**kw)
        operation='return-'+_digest([job,row['result_digest']])
        try:receipt=self.driver.reconcile(row['principal'],row['thread_id'],job,row['result_digest'],operation)
        except Exception:return
        if type(receipt) is ParentReceipt and receipt==ParentReceipt(job,row['thread_id'],row['result_digest'],operation):
            update('DELIVERED',row['state'])
        elif receipt is None and callable(getattr(self.driver,'delivery_uncertain',None)):
            try:uncertain=self.driver.delivery_uncertain(job)
            except Exception:return
            if not uncertain:update('WAITING_PARENT',row['state'])

    def tick(self):
        # No overlapping ticks, and never hold a SQLite write transaction
        # while the host is loading a model or accepting a notification.
        with self._tick_lock:
            if self._stop.is_set():return
            # Repair the commit/enqueue boundary without rerunning inference:
            # a result may have committed before a transient queue failure.
            with self.owner._connection() as con:
                missing=con.execute('''SELECT j.job_id FROM jobs j
                    JOIN parent_bindings b USING(job_id) LEFT JOIN parent_returns r USING(job_id)
                    WHERE j.state IN ('RESULT_READY','WAITING_FOR_PARENT','FAILED','CANCELLED') AND r.job_id IS NULL LIMIT 256''').fetchall()
            for item in missing:self.enqueue(item[0])
            if callable(getattr(self.driver,'reconcile',None)):
                with self.owner._connection() as con:
                    uncertain=con.execute("SELECT job_id FROM parent_returns WHERE state='NEEDS_RECONCILIATION' LIMIT 256").fetchall()
                for item in uncertain:self._reconcile(item[0])
            with self.owner._connection() as con:
                rows=con.execute('''SELECT r.*,b.principal,b.thread_id,b.model_id
                    FROM parent_returns r JOIN parent_bindings b USING(job_id)
                    JOIN jobs j USING(job_id) WHERE r.state IN ('PENDING','WAITING_PARENT')
                    AND j.state IN ('RESULT_READY','WAITING_FOR_PARENT','FAILED','CANCELLED') LIMIT 256''').fetchall()
            for row in rows:
                if self._stop.is_set():return
                job=row['job_id']; old=row['state']
                update=lambda state,expected,**kw:self._set(job,state,expected,digest=row['result_digest'],**kw)
                with self.owner._connection() as con:
                    if con.execute("SELECT 1 FROM sqlite_master WHERE name='model_leases'").fetchone():
                        lease=con.execute('SELECT state FROM model_leases WHERE job_id=?',(job,)).fetchone()
                        if lease and lease[0]!='RELEASED':
                            update('WAITING_PARENT',old);continue
                try: state=self.driver.state(row['principal'],row['thread_id'])
                except Exception as exc:
                    update('WAITING_PARENT',old,error_code=safe_tool_fault(exc).code);continue
                if not self._valid(state,row['principal'],row['thread_id'],row['model_id']) or state.busy:
                    update('WAITING_PARENT',old);continue
                self.verified=True
                if self._stop.is_set():return
                if not state.resident:
                    if not state.can_restore:
                        update('RESTORE_FAILED',old);continue
                    if not update('RESTORING',old,restore_needed=True):continue
                    try:
                        state=self.driver.restore(row['principal'],row['thread_id'],row['model_id'],
                                                  'restore-'+_digest([job,row['result_digest']]))
                    except Exception as exc:
                        update('RESTORE_FAILED','RESTORING',error_code=safe_tool_fault(exc).code);continue
                    if (not self._valid(state,row['principal'],row['thread_id'],row['model_id']) or
                            not state.resident):
                        update('RESTORE_FAILED','RESTORING');continue
                    update('WAITING_PARENT','RESTORING')
                    old='WAITING_PARENT'
                    if state.busy:continue
                if self._stop.is_set():return
                if not update('IN_FLIGHT',old):continue
                operation='return-'+_digest([job,row['result_digest']])
                try:
                    receipt=self.driver.deliver(row['principal'],row['thread_id'],
                        job,row['result_digest'],operation)
                    expected=ParentReceipt(job,row['thread_id'],row['result_digest'],operation)
                    if type(receipt) is not ParentReceipt or receipt!=expected:
                        raise ValueError('unverified return receipt')
                except Exception as exc:
                    uncertain=True
                    if callable(getattr(self.driver,'delivery_uncertain',None)):
                        try:uncertain=self.driver.delivery_uncertain(job)
                        except Exception:pass
                    update('NEEDS_RECONCILIATION' if uncertain else 'WAITING_PARENT','IN_FLIGHT',error_code=safe_tool_fault(exc).code);continue
                update('DELIVERED','IN_FLIGHT')

    def start(self):
        # Recover only delivery intent for already finished workers, never
        # inference or unknown prior sends. Bindings were captured pre-start.
        with self.owner._connection() as con:
            jobs=con.execute("SELECT job_id FROM jobs JOIN parent_bindings USING(job_id) WHERE state IN ('RESULT_READY','WAITING_FOR_PARENT','FAILED','CANCELLED')").fetchall()
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
