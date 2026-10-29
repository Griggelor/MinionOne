"""Internal ADR-022 outbox, no network sender, no successful-delivery assertion.

Do NOT expose to MCP or worker code. Only a trusted manager can call this.
Any uncertain external effect is quarantined, never automatically retried.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
import sqlite3


class OutboxRejected(ValueError):
    pass


class OutboxConflict(RuntimeError):
    pass


_JOB = re.compile(r'JOB-[A-Za-z0-9_-]{1,60}\Z', re.ASCII)
_KEY = re.compile(r'[A-Za-z0-9_.:-]{8,128}\Z', re.ASCII)
_DIGEST = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_READY = frozenset(('RESULT_READY', 'INTEGRATING'))


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _check(pattern, value):
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise OutboxRejected('invalid identifier or digest')


class OutboxStore:
    """Manager-private intent store; does not authenticate user or send messages."""

    def __init__(self, manager_database_path, *, max_entries_per_job=64):
        if type(max_entries_per_job) is not int or not 1 <= max_entries_per_job <= 256:
            raise OutboxRejected('invalid quota')
        self.path = Path(manager_database_path)  # trusted admin configuration
        if not self.path.is_file():
            raise OutboxRejected('manager database unavailable')
        self.max_entries_per_job = max_entries_per_job
        with self._open() as con:
            cols = {r[1] for r in con.execute('PRAGMA table_info(jobs)')}
            if not {'job_id', 'state', 'version'} <= cols:
                raise OutboxRejected('canonical job state owner missing')
            con.executescript('''
                CREATE TABLE IF NOT EXISTS outbox_intents(
                  job_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                  payload_sha256 TEXT NOT NULL, job_state_version INTEGER NOT NULL,
                  delivery_state TEXT NOT NULL CHECK(delivery_state IN
                      ('PREPARED','IN_FLIGHT','NEEDS_RECONCILIATION')),
                  outbox_version INTEGER NOT NULL CHECK(outbox_version>=0),
                  attempts INTEGER NOT NULL CHECK(attempts IN (0,1)),
                  dispatch_token_hash TEXT, receipt_json TEXT NOT NULL,
                  created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
                  PRIMARY KEY(job_id,operation_id),
                  FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );
            ''')

    @contextmanager
    def _open(self):
        con = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
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

    @staticmethod
    def _receipt(r):
        return {'job_id':r['job_id'], 'operation_id':r['operation_id'],
                'payload_sha256':r['payload_sha256'],
                'job_state_version':r['job_state_version'],
                'delivery_state':r['delivery_state'],
                'outbox_version':r['outbox_version'], 'attempt':r['attempts']}

    def prepare(self, job_id, operation_id, payload_sha256, *, expected_state_version):
        """Persist pre-send intent, or replay its original receipt."""
        _check(_JOB,job_id)
        _check(_KEY,operation_id)
        _check(_DIGEST,payload_sha256)
        if type(expected_state_version) is not int or expected_state_version < 0:
            raise OutboxRejected('invalid job version')
        with self._open() as con:
            con.execute('BEGIN IMMEDIATE')
            job=con.execute('SELECT state,version FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if job is None:
                raise OutboxRejected('job unavailable')
            prior=con.execute('SELECT * FROM outbox_intents WHERE job_id=? AND operation_id=?',
                              (job_id,operation_id)).fetchone()
            if prior is not None:
                if (prior['payload_sha256']!=payload_sha256 or
                        prior['job_state_version']!=expected_state_version):
                    raise OutboxConflict('operation key reused with different payload/version')
                return json.loads(prior['receipt_json'])
            if job['state'] not in _READY or job['version']!=expected_state_version:
                raise OutboxRejected('job not eligible')
            count=con.execute('SELECT count(*) FROM outbox_intents WHERE job_id=?',
                              (job_id,)).fetchone()[0]
            if count >= self.max_entries_per_job:
                raise OutboxRejected('quota exhausted')
            receipt={'job_id':job_id,'operation_id':operation_id,
                     'payload_sha256':payload_sha256,
                     'job_state_version':expected_state_version,
                     'delivery_state':'PREPARED','outbox_version':0,'attempt':0}
            now=_now()
            con.execute('''INSERT INTO outbox_intents
                (job_id,operation_id,payload_sha256,job_state_version,
                 delivery_state,outbox_version,attempts,dispatch_token_hash,
                 receipt_json,created_utc,updated_utc)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (job_id,operation_id,payload_sha256,expected_state_version,
                 'PREPARED',0,0,None,json.dumps(receipt,sort_keys=True),now,now))
            return receipt

    def status(self,job_id,operation_id):
        """Manager-internal diagnostic. External authorization is absent."""
        _check(_JOB,job_id)
        _check(_KEY,operation_id)
        with self._open() as con:
            row=con.execute('SELECT * FROM outbox_intents WHERE job_id=? AND operation_id=?',
                            (job_id,operation_id)).fetchone()
            if row is None:
                raise OutboxRejected('unknown delivery intent')
            return self._receipt(row)

    def list_entries(self,job_id):
        _check(_JOB,job_id)
        with self._open() as con:
            if con.execute('SELECT 1 FROM jobs WHERE job_id=?',(job_id,)).fetchone() is None:
                raise OutboxRejected('unknown job')
            rows=con.execute('''SELECT * FROM outbox_intents WHERE job_id=?
                ORDER BY operation_id LIMIT 257''',(job_id,)).fetchall()
            return [self._receipt(r) for r in rows]

    def claim(self,job_id,operation_id,*,expected_outbox_version):
        """Persist one-way IN_FLIGHT before any future external side effect."""
        _check(_JOB,job_id)
        _check(_KEY,operation_id)
        if type(expected_outbox_version) is not int or expected_outbox_version < 0:
            raise OutboxRejected('invalid outbox version')
        with self._open() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT * FROM outbox_intents WHERE job_id=? AND operation_id=?',
                            (job_id,operation_id)).fetchone()
            if (row is None or row['delivery_state']!='PREPARED' or
                    row['outbox_version']!=expected_outbox_version or row['attempts']!=0):
                raise OutboxConflict('already dispatched or unknown')
            job=con.execute('SELECT state,version FROM jobs WHERE job_id=?',(job_id,)).fetchone()
            if job is None or job['state'] not in _READY or job['version']!=row['job_state_version']:
                raise OutboxConflict('job changed before dispatch')
            token=secrets.token_hex(32)
            con.execute('''UPDATE outbox_intents SET delivery_state='IN_FLIGHT',
                outbox_version=outbox_version+1,attempts=1,
                dispatch_token_hash=?,updated_utc=?
                WHERE job_id=? AND operation_id=?''',
                (sha256(token.encode('ascii')).hexdigest(),_now(),job_id,operation_id))
            updated=con.execute('SELECT * FROM outbox_intents WHERE job_id=? AND operation_id=?',
                                (job_id,operation_id)).fetchone()
            return {**self._receipt(updated),'dispatch_token':token}

    def quarantine_in_flight(self):
        """Quarantine all unresolved sends on restart; no resend/DELIVERED API."""
        with self._open() as con:
            con.execute('BEGIN IMMEDIATE')
            cur=con.execute('''UPDATE outbox_intents
                SET delivery_state='NEEDS_RECONCILIATION',
                    outbox_version=outbox_version+1, updated_utc=?
                WHERE delivery_state='IN_FLIGHT' ''',(_now(),))
            return cur.rowcount
