"""Opt-in pre-admission job folder preparation (NOT a Windows worker file broker).

A prepared workspace is not an admitted job. Its input is mutable by the
trusted parent until a future seal/admission boundary. No worker can run here.
Project paths are trusted operator configuration, never MCP arguments.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat

from .admission import InputRejected, IdempotencyConflict, PermissionDenied

_PROJECT = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
_OPERATION = re.compile(r"[A-Za-z0-9_.:-]{8,128}\Z", re.ASCII)
_JOB = re.compile(r"JOB-[0-9a-f]{32}\Z", re.ASCII)


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _guard_directory(path: Path):
    """Fail-closed staging prefilter only; NOT race-safe Win32 confinement."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode) or path.is_symlink():
        raise InputRejected('unapproved workspace path type')
    if os.name == 'nt':
        attrs = path.stat().st_file_attributes
        if attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise InputRejected('workspace reparse point denied')
    return True


class WorkspacePreparation:
    """Prepares three visible job folders for an authorized configured project.

    Persistent reservations ensure retries preserve the same job_id. This is a
    manager-side staging utility; Windows worker security is explicitly absent.
    """
    def __init__(self, admission_store, projects, *, max_prepared_per_principal=32):
        if type(max_prepared_per_principal) is not int or not 1 <= max_prepared_per_principal <= 256:
            raise InputRejected('invalid workspace reservation quota')
        self.max_prepared_per_principal=max_prepared_per_principal
        if not isinstance(projects, dict) or not projects:
            raise InputRejected('trusted project mapping required')
        normalized = {}
        for key, value in projects.items():
            if type(key) is not str or not _PROJECT.fullmatch(key):
                raise InputRejected('invalid project identifier')
            if not isinstance(value, (str, os.PathLike)):
                raise InputRejected('invalid configured project directory')
            directory = Path(value)
            if not directory.is_absolute() or not _guard_directory(directory):
                raise InputRejected('configured project must be an existing real directory')
            normalized[key] = directory.absolute()
        self.projects = normalized
        self.project_identities = {key: (path.stat().st_dev, path.stat().st_ino)
                                   for key,path in normalized.items()}
        self.store = admission_store
        with self.store._connection() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS workspace_reservations (
                job_id TEXT PRIMARY KEY,
                principal TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('ALLOCATING','PREPARING','ADMITTED')),
                created_utc TEXT NOT NULL,
                UNIQUE(principal,operation_id))''')
            sql=con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='workspace_reservations'").fetchone()[0]
            normalized=''.join(sql.split())
            if "'ADMITTED'" not in normalized:
                if "CHECK(stateIN('ALLOCATING','PREPARING'))" not in normalized:
                    raise InputRejected('unknown workspace reservation schema; no unsafe migration')
                con.execute('BEGIN IMMEDIATE')
                con.execute('ALTER TABLE workspace_reservations RENAME TO workspace_reservations__v019')
                con.execute('''CREATE TABLE workspace_reservations (
                    job_id TEXT PRIMARY KEY, principal TEXT NOT NULL,
                    thread_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL, request_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ALLOCATING','PREPARING','ADMITTED')),
                    created_utc TEXT NOT NULL, UNIQUE(principal,operation_id))''')
                con.execute('''INSERT INTO workspace_reservations
                    SELECT * FROM workspace_reservations__v019''')
                con.execute('DROP TABLE workspace_reservations__v019')
                con.commit()
            con.execute('''CREATE TABLE IF NOT EXISTS workspace_seals (
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                operation_id TEXT NOT NULL, receipt TEXT NOT NULL)''')
            con.execute('''CREATE TABLE IF NOT EXISTS workspace_input_blobs (
                job_id TEXT NOT NULL REFERENCES jobs(job_id),
                name TEXT NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
                PRIMARY KEY(job_id,name))''')

    def _folder(self, row):
        project = self.projects.get(row['project_id'])
        if project is None:
            raise InputRejected('project no longer configured')
        if not _guard_directory(project):
            raise InputRejected('configured project directory unavailable')
        return project / '.minionone' / 'jobs' / row['job_id']

    def _create_directories(self, row):
        folder = self._folder(row)
        project = self.projects[row['project_id']]
        for path in (project / '.minionone', project / '.minionone' / 'jobs', folder,
                     folder / 'input', folder / 'work', folder / 'output'):
            if not _guard_directory(path):
                try:
                    path.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                if not _guard_directory(path):
                    raise InputRejected('workspace directory was not safely created')
        return folder

    def _view(self, row, folder):
        return {
            'job_id': row['job_id'], 'project_id': row['project_id'],
            'state': 'PREPARING', 'version': 0,
            'job_dir': str(folder),
            'input_dir': str(folder / 'input'),
            'work_dir': str(folder / 'work'),
            'output_dir': str(folder / 'output'),
            'next_action': 'PARENT_PREPARES_INPUT_THEN_SEAL_REQUIRED',
            'worker_execution_enabled': False,
            'input_sealed': False, 'file_broker_verified': False,
        }

    def reserve(self, token, thread, project_id, operation_id):
        principal = self.store._principal(token, thread)
        if type(project_id) is not str or project_id not in self.projects:
            raise InputRejected('unknown or unapproved project')
        if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):
            raise InputRejected('invalid operation id')
        request_hash = hashlib.sha256(json.dumps([thread, project_id],
                                separators=(',', ':')).encode()).hexdigest()
        with self.store._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('''SELECT * FROM workspace_reservations
                WHERE principal=? AND operation_id=?''', (principal, operation_id)).fetchone()
            if row:
                if row['request_hash'] != request_hash:
                    raise IdempotencyConflict('reservation key reused with different project')
                if row['thread_id'] != thread:
                    raise PermissionDenied('reservation unavailable')
            else:
                count=con.execute('SELECT COUNT(*) FROM workspace_reservations WHERE principal=?',
                                  (principal,)).fetchone()[0]
                if count >= self.max_prepared_per_principal:
                    raise InputRejected('workspace reservation quota exhausted')
                job_id = 'JOB-' + secrets.token_hex(16)
                con.execute('''INSERT INTO workspace_reservations
                    VALUES (?,?,?,?,?,?,?,?)''', (job_id,principal,thread,project_id,
                       operation_id,request_hash,'ALLOCATING',_timestamp()))
                row = con.execute('SELECT * FROM workspace_reservations WHERE job_id=?',
                                  (job_id,)).fetchone()
        folder = self._create_directories(row)
        with self.store._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute("UPDATE workspace_reservations SET state='PREPARING' "
                        "WHERE job_id=? AND state='ALLOCATING'", (row['job_id'],))
        if row['state']=='ADMITTED':
            return self.status(token,thread,row['job_id'])
        return self._view(row, folder)

    def status(self, token, thread, job_id):
        principal = self.store._principal(token, thread)
        if type(job_id) is not str or not _JOB.fullmatch(job_id):
            raise InputRejected('invalid job id')
        with self.store._connection() as con:
            row = con.execute('''SELECT * FROM workspace_reservations
                WHERE job_id=? AND principal=? AND thread_id=?''',
                (job_id,principal,thread)).fetchone()
        if row is None:
            raise PermissionDenied('reservation unavailable')
        if row['state'] == 'ADMITTED':
            snapshot=self.store.status(token,thread,job_id)
            return {**self._view(row,self._folder(row)),
                'state':snapshot['state'],'version':snapshot['version'],
                'input_digest':snapshot['input_digest'],'input_sealed':True,
                'next_action':'WAIT_FOR_VERIFIED_WORKER_FILE_BROKER'}
        if row['state'] != 'PREPARING':
            # A partially allocated job must never be reported as ready.
            raise InputRejected('workspace allocation incomplete')
        folder = self._folder(row)
        for name in ('input', 'work', 'output'):
            if not _guard_directory(folder / name):
                raise InputRejected('workspace directory unavailable')
        return self._view(row, folder)

    def seal(self,token,thread,job_id,expected_version,operation_id):
        from .workspace_sealing import seal
        return seal(self,token,thread,job_id,expected_version,operation_id)

    def verify_input(self,token,thread,job_id):
        from .workspace_sealing import verify
        return verify(self,token,thread,job_id)
