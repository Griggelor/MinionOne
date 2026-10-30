"""Manager-owned, bounded POSIX and Windows input capture for staged jobs.

This is a DEVELOPMENT admission seam, not a verified worker broker or a
real target-host Windows security gate. Worker execution remains disabled.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
import stat

from .admission import InputRejected, IdempotencyConflict, VersionConflict

_NAME = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9._-]{0,119}\Z', re.ASCII)
_SHA = re.compile(r'[a-f0-9]{64}\Z', re.ASCII)
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{x}' for x in range(1,10)} | {f'LPT{x}' for x in range(1,10)}
_MAX_FILES = 16
_MAX_FILE_BYTES = 1_048_576
_MAX_TOTAL_BYTES = 4_194_304
_MAX_MANIFEST_BYTES = 32_768
_MAX_TASK_BYTES = 65_536


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')


def _name(name):
    if (type(name) is not str or not _NAME.fullmatch(name) or name.endswith('.') or
            name.split('.')[0].upper() in _RESERVED or ':' in name):
        raise InputRejected('invalid flat input filename')
    return name


def _pairs(pairs):
    out={}
    for k,v in pairs:
        if k in out:
            raise InputRejected('duplicate manifest key')
        out[k]=v
    return out


def _read_regular(fd, filename, *, max_bytes):
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os,'O_CLOEXEC',0) | getattr(os,'O_NONBLOCK',0)
    try:
        handle=os.open(filename,flags,dir_fd=fd)
    except OSError as exc:
        raise InputRejected('input file not safely readable') from exc
    try:
        before=os.fstat(handle)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > max_bytes):
            raise InputRejected('input file type, links or size rejected')
        chunks=[];size=0
        while True:
            blob=os.read(handle,min(65536,max_bytes-size+1))
            if not blob:break
            size+=len(blob)
            if size>max_bytes:raise InputRejected('input file exceeds bounded quota')
            chunks.append(blob)
        after=os.fstat(handle)
        fingerprint=lambda s: (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if fingerprint(before)!=fingerprint(after) or size!=after.st_size:
            raise InputRejected('input changed during capture')
        return b''.join(chunks)
    finally:
        os.close(handle)


@contextmanager
def _job_directory(workspace, row, area):
    if os.name=='nt' or not hasattr(os,'O_NOFOLLOW'):
        raise InputRejected('secure Windows workspace file broker not implemented')
    project=workspace.projects.get(row['project_id'])
    if project is None:raise InputRejected('project no longer approved')
    flags=os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | getattr(os,'O_CLOEXEC',0)
    handles=[]
    try:
        fd=os.open(str(project),flags);handles.append(fd)
        root=os.fstat(fd)
        if (root.st_dev,root.st_ino)!=workspace.project_identities[row['project_id']]:
            raise InputRejected('configured project identity changed')
        for component in ('.minionone','jobs',row['job_id'],area):
            fd=os.open(component,flags,dir_fd=fd)
            handles.append(fd)
            st=os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_dev!=root.st_dev:
                raise InputRejected('workspace directory identity/boundary invalid')
        yield fd
    except OSError as exc:
        raise InputRejected('workspace path not safely accessible') from exc
    finally:
        for handle in reversed(handles):os.close(handle)


def capture(workspace,row):
    with _job_directory(workspace,row,'input') as fd:
        names=os.listdir(fd)
        if not 2 <= len(names) <= _MAX_FILES+1:
            raise InputRejected('input must contain task, manifest and bounded files')
        if 'TASK.md' not in names or 'manifest.json' not in names:
            raise InputRejected('TASK.md and manifest.json required')
        for name in names:_name(name)
        if len({x.casefold() for x in names})!=len(names):
            raise InputRejected('ambiguous case-insensitive filenames')
        manifest_bytes=_read_regular(fd,'manifest.json',max_bytes=_MAX_MANIFEST_BYTES)
        try:
            manifest=json.loads(manifest_bytes.decode('utf-8'),object_pairs_hook=_pairs)
        except (UnicodeError,ValueError) as exc:
            raise InputRejected('invalid manifest document') from exc
        if type(manifest) is not dict or set(manifest)!={'files'} or type(manifest['files']) is not dict:
            raise InputRejected('manifest must contain only files map')
        listed=manifest['files']
        if (len(listed)<1 or len(listed)>_MAX_FILES or
                set(names)!=(set(listed)|{'manifest.json'})):
            raise InputRejected('manifest must cover every input file exactly once')
        data={'manifest.json':manifest_bytes};digests={}
        total=len(manifest_bytes)
        for name,expected in sorted(listed.items()):
            _name(name)
            if type(expected) is not str or not _SHA.fullmatch(expected):
                raise InputRejected('invalid input digest in manifest')
            blob=_read_regular(fd,name,max_bytes=(_MAX_TASK_BYTES if name=='TASK.md' else _MAX_FILE_BYTES))
            total+=len(blob)
            if total>_MAX_TOTAL_BYTES:raise InputRejected('total input quota exceeded')
            actual=hashlib.sha256(blob).hexdigest()
            if actual!=expected:raise InputRejected('manifest digest differs from file bytes')
            data[name]=blob;digests[name]=actual
        try:
            task=data['TASK.md'].decode('utf-8')
        except UnicodeError as exc:
            raise InputRejected('TASK.md must be UTF-8') from exc
        if not task.strip() or '\x00' in task:
            raise InputRejected('TASK.md must contain a nonempty text task')
        digest=hashlib.sha256(json.dumps({'files':digests,
                     'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest()},
                     sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
        return data,digests,digest


def _write_projection(workspace,row,events):
    """Rebuildable redacted canonical-event projection, manager writes only."""
    lines=[]
    for event in events:
        lines.append(json.dumps({'seq':event['seq'],'status':event['status'],
            'event':'JOB_STATE','actor':event['actor'],'attempt':None,
            'operation_id':event['operation_id'],'utc':event['occurred_utc'],
            'metadata':{}},sort_keys=True,separators=(',',':'))+'\n')
    payload=''.join(lines).encode('utf-8')
    if os.name == 'nt':
        # The native manager output route is append-free for the ACCEPTED
        # admission event. No Windows worker/running-job logging is enabled.
        if len(events) != 1 or events[0]['status'] != 'ACCEPTED':
            raise InputRejected('Windows admission projection supports only ACCEPTED')
        from .win32_workspace_sealing import project_admission_log
        return project_admission_log(workspace,row,payload)
    temp='.joblog-'+secrets.token_hex(12)+'.tmp'
    with _job_directory(workspace,row,'output') as fd:
        handle=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        try:
            with os.fdopen(handle,'wb',closefd=False) as file:
                file.write(payload);file.flush();os.fsync(handle)
            os.replace(temp,'job.log.jsonl',src_dir_fd=fd,dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(handle)
            try:os.unlink(temp,dir_fd=fd)
            except FileNotFoundError:pass



@contextmanager
def _capture_for_admission(workspace,row):
    if os.name == 'nt':
        from .win32_workspace_sealing import pinned_capture
        with pinned_capture(workspace,row) as snapshot:
            yield snapshot
    else:
        yield capture(workspace,row)


def seal(workspace,token,thread,job_id,expected_version,operation_id):
    principal=workspace.store._principal(token,thread)
    if type(expected_version) is not int or expected_version!=0:
        raise VersionConflict('staging version must be zero')
    from .workspace_preparation import _JOB, _OPERATION
    if type(job_id) is not str or not _JOB.fullmatch(job_id):raise InputRejected('invalid job id')
    if type(operation_id) is not str or not _OPERATION.fullmatch(operation_id):raise InputRejected('invalid operation id')
    with workspace.store._connection() as con:
        row=con.execute('''SELECT * FROM workspace_reservations
           WHERE job_id=? AND principal=? AND thread_id=?''',(job_id,principal,thread)).fetchone()
    if row is None:raise InputRejected('workspace unavailable')
    if row['state']=='ADMITTED':
        with workspace.store._connection() as con:
            old=con.execute('SELECT * FROM workspace_seals WHERE job_id=?',(job_id,)).fetchone()
            if old is None or old['operation_id']!=operation_id:
                raise IdempotencyConflict('already sealed with another operation')
            receipt=json.loads(old['receipt'])
        _write_projection(workspace,row,workspace.store.events(token,thread,job_id))
        return receipt
    if row['state']!='PREPARING':raise InputRejected('workspace not prepared')
    with _capture_for_admission(workspace,row) as snapshot:
        return _commit_captured(workspace,token,thread,job_id,operation_id,
                                principal,row,snapshot)


def _commit_captured(workspace,token,thread,job_id,operation_id,principal,row,snapshot):
    data,digests,input_digest=snapshot
    with workspace.store._connection() as con:
        con.execute('BEGIN IMMEDIATE')
        fresh=con.execute('''SELECT * FROM workspace_reservations
                     WHERE job_id=? AND principal=? AND thread_id=?''',(job_id,principal,thread)).fetchone()
        if fresh is None:raise InputRejected('workspace unavailable')
        if fresh['state']=='ADMITTED':
            old=con.execute('SELECT * FROM workspace_seals WHERE job_id=?',(job_id,)).fetchone()
            if old is None or old['operation_id']!=operation_id:
                raise IdempotencyConflict('already sealed with another operation')
            receipt=json.loads(old['receipt'])
        else:
            if fresh['state']!='PREPARING':
                raise VersionConflict('reservation changed during input capture')
            active=con.execute('SELECT COUNT(*) FROM jobs WHERE principal=?',(principal,)).fetchone()[0]
            if active >= workspace.store.max_jobs_per_principal:
                raise InputRejected('admission job quota reached')
            now=_now()
            con.execute('''INSERT INTO jobs(job_id,principal,thread_id,input_digest,state,version,
                      created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?)''',
                      (job_id,principal,thread,input_digest,'ACCEPTED',1,now,now))
            for name,blob in sorted(data.items()):
                con.execute('''INSERT INTO workspace_input_blobs(job_id,name,sha256,content)
                           VALUES(?,?,?,?)''',(job_id,name,hashlib.sha256(blob).hexdigest(),blob))
            receipt={'job_id':job_id,'state':'ACCEPTED','version':1,
                     'input_digest':input_digest,'input_sealed':True,
                     'worker_execution_enabled':False,'file_broker_verified':False,
                     'next_action':'WAIT_FOR_VERIFIED_WORKER_FILE_BROKER'}
            con.execute('''INSERT INTO workspace_seals(job_id,operation_id,receipt)
                        VALUES(?,?,?)''',(job_id,operation_id,json.dumps(receipt,sort_keys=True)))
            con.execute("UPDATE workspace_reservations SET state='ADMITTED' WHERE job_id=?",(job_id,))
            con.execute('''INSERT INTO admission_events
                        (job_id,seq,status,operation_id,actor,occurred_utc)
                        VALUES (?,1,'ACCEPTED',?,'MANAGER',?)''',(job_id,operation_id,now))
    _write_projection(workspace,row,workspace.store.events(token,thread,job_id))
    return receipt

def verify(workspace,token,thread,job_id):
    principal=workspace.store._principal(token,thread)
    from .workspace_preparation import _JOB
    if type(job_id) is not str or not _JOB.fullmatch(job_id):raise InputRejected('invalid job id')
    with workspace.store._connection() as con:
        row=con.execute('''SELECT * FROM workspace_reservations
             WHERE job_id=? AND principal=? AND thread_id=? AND state='ADMITTED' ''',
             (job_id,principal,thread)).fetchone()
        if row is None:raise InputRejected('workspace not sealed')
        sealed=con.execute('SELECT input_digest FROM jobs WHERE job_id=?',(job_id,)).fetchone()
        snapshots=con.execute('SELECT name,sha256,content FROM workspace_input_blobs WHERE job_id=?',
                              (job_id,)).fetchall()
    if sealed is None or not snapshots:raise InputRejected('trusted input snapshot missing')
    with _capture_for_admission(workspace,row) as snapshot:
        data,digests,digest=snapshot
        if digest!=sealed['input_digest'] or len(data)!=len(snapshots):
            raise InputRejected('sealed inputs were modified')
        for saved in snapshots:
            if (saved['name'] not in data or
                hashlib.sha256(saved['content']).hexdigest()!=saved['sha256'] or
                data[saved['name']]!=saved['content']):
                raise InputRejected('sealed input snapshot mismatch')
        return {'job_id':job_id,'state':'ACCEPTED','input_integrity':'VERIFIED',
                'input_digest':digest,'worker_execution_enabled':False}
