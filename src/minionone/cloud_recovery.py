"""Offline operator attestation for uncertain cloud WORKSPACE generations.

No retry, publication, model operation, refund or parent delivery happens here.
The CLI must hold PilotLock; remote quiescence is an independent operator fact,
not something a disconnected HTTP client can establish.
"""
from contextlib import ExitStack
from hashlib import sha256
import json
import os

from .admission import InputRejected, IdempotencyConflict, VersionConflict, _digest, _utc
from .lifecycle import Evidence


def install(con):
    con.executescript('''CREATE TABLE IF NOT EXISTS cloud_operator_resolutions(
        job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), request_hash TEXT NOT NULL,
        receipt_json TEXT NOT NULL, created_utc TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS cloud_resolution_no_update BEFORE UPDATE ON cloud_operator_resolutions
            BEGIN SELECT RAISE(ABORT,'immutable cloud operator receipt'); END;
        CREATE TRIGGER IF NOT EXISTS cloud_resolution_no_delete BEFORE DELETE ON cloud_operator_resolutions
            BEGIN SELECT RAISE(ABORT,'immutable cloud operator receipt'); END;''')


def _output_bytes(workspace, row, limits):
    """Read only through pinned native directories; never create/repair a file."""
    with ExitStack() as stack:
        if os.name == 'nt':
            from .win32_output_publication import (
                _output_directory, _FILE_OPEN, _FILE_READ_DATA, _READ_ATTRIBUTES, _SYNCHRONIZE)
            api, directory, volume = stack.enter_context(_output_directory(workspace, row))
            result = {}
            for name, limit in limits.items():
                handle = api._open(directory, name, _FILE_READ_DATA | _READ_ATTRIBUTES | _SYNCHRONIZE, _FILE_OPEN)
                if handle is None:raise InputRejected('recovery output unavailable')
                stack.callback(api.close, handle)
                api.validate(handle, directory=False, volume=volume, single_link=True)
                result[name] = api.read_regular(handle, max_bytes=limit)
            return result
        from .workspace_sealing import _job_directory, _read_regular
        directory = stack.enter_context(_job_directory(workspace, row, 'output'))
        return {name: _read_regular(directory, name, max_bytes=limit) for name, limit in limits.items()}


def resolve(pilot, token, thread, job, *, confirm_provider_stopped=False,
            confirm_work_inspected=False, expected_version=None):
    if confirm_provider_stopped is not True or confirm_work_inspected is not True:
        raise InputRejected('independent provider stop and workspace inspection required')
    if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
        raise InputRejected('invalid expected version')
    if pilot.workspace_execution is None or any(t.is_alive() for t in pilot._threads):
        raise InputRejected('offline workspace verifier required')
    principal = pilot.owner._principal(token, thread)
    request_hash = _digest({'expected_version': expected_version, 'provider_stopped': True, 'work_inspected': True})
    manager = pilot.workspace_execution
    with pilot.owner._connection() as con:
        con.execute('BEGIN IMMEDIATE')
        row = pilot.owner._owned_row(con, principal, thread, job)
        if manager.token != token or manager.thread != thread:
            raise InputRejected('workspace verifier scope mismatch')
        prior = con.execute('SELECT * FROM cloud_operator_resolutions WHERE job_id=?', (job,)).fetchone()
        if prior:
            if prior['request_hash'] != request_hash:raise IdempotencyConflict('operator request changed')
            return json.loads(prior['receipt_json'])
        from .worker_selection import load
        profile = load(con, job)
        if row['job_kind'] != 'WORKSPACE' or profile is None or profile.provider != 'cloud':
            raise InputRejected('cloud workspace job required')
        if expected_version is not None and row['version'] != expected_version:
            raise VersionConflict('job version changed')
        # Only target-specific crash recovery. Never run global recover() here:
        # unrelated local-model leases require their own runtime verification.
        if row['state'] not in ('NEEDS_RECONCILIATION', 'RUNNING', 'RESUMING', 'CANCEL_REQUESTED', 'RESOURCE_CHECK'):
            raise VersionConflict('no uncertain cloud generation')
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_leases'").fetchone():
            if con.execute('SELECT 1 FROM model_leases WHERE job_id=?', (job,)).fetchone():
                raise InputRejected('model lease requires model reconciliation')
        run = con.execute('SELECT * FROM pilot_runs WHERE job_id=?', (job,)).fetchone()
        charges = [dict(r) for r in con.execute('SELECT * FROM cloud_request_reservations WHERE job_id=? ORDER BY turn', (job,))]
        if not charges or any(c['principal'] != principal or c['state'] not in
                ('IN_FLIGHT', 'UNKNOWN', 'RESPONSE_RECEIVED') or
                c['policy_digest'] != _digest(profile.cloud_policy) or c['turn'] > run['turns'] for c in charges):
            raise InputRejected('durable cloud reservation required')
        # Read verification only, including a complete hash-chained physical IO
        # mirror. Partial/tampered files and pending publications stay quarantined.
        manager.workspace.verify_input(token, thread, job)
        work = pilot.make_broker(job, 'offline-verification-only', True).checkpoint_inventory()
        from .worker_io import jsonl, _MAX_FILE
        from .live_job_files import _JOB_LIMIT
        expected_io = jsonl(con, job)
        requests = {_digest(r['body']) for r in con.execute(
            "SELECT body FROM workspace_worker_io WHERE job_id=? AND kind='MODEL_REQUEST'", (job,))}
        if any(c['request_digest'] not in requests for c in charges):
            raise InputRejected('cloud reservation request evidence missing')
        publications = con.execute('SELECT * FROM workspace_publications WHERE job_id=? ORDER BY name', (job,)).fetchall()
        if any(p['state'] != 'PUBLISHED' for p in publications):
            raise InputRejected('pending publication requires separate reconciliation')
        from .workspace_sealing import _name
        limits = {'worker-io.jsonl': _MAX_FILE, 'job.log.jsonl': _JOB_LIMIT}
        for p in publications:
            _name(p['name'])
            if p['name'] not in limits:limits[p['name']] = 1048576
        reservation = manager.publisher._row(token, thread, job)
        files = _output_bytes(manager.workspace, reservation, limits)
        if files['worker-io.jsonl'] != expected_io:
            raise InputRejected('physical worker IO differs from private journal')
        log = files['job.log.jsonl']
        try:
            if not log.endswith(b'\n') or any(type(json.loads(line)) is not dict for line in log.splitlines()):
                raise ValueError()
        except (ValueError, UnicodeError):raise InputRejected('incomplete manager log') from None
        for p in publications:
            if files[p['name']] != p['content'] or sha256(files[p['name']]).hexdigest() != p['sha256']:
                raise InputRejected('physical publication changed')
        source_state, source_version = row['state'], row['version']
        con.execute('''UPDATE pilot_runs SET active=0,capability=NULL,provider_inflight=0,
            result_digest=NULL,issued_digest=NULL,error_code='CLOUD_OPERATOR_RESOLVED' WHERE job_id=?''', (job,))
        if row['state'] != 'NEEDS_RECONCILIATION':
            row = pilot.owner._transition(con, row, 'NEEDS_RECONCILIATION',
                Evidence(reconciliation_required=True, error_code='CLOUD_OPERATOR_RESOLVED'),
                'cloud-operator-quarantine-' + job, actor='LOCAL_OPERATOR')
        row = pilot.owner._transition(con, row, 'FAILED', Evidence(reconciliation_verified=True,
            worker_outcome='FAILED', worker_quiescent=True, attempt_no=run['attempt_no'],
            error_code='CLOUD_OPERATOR_RESOLVED'), 'cloud-operator-resolved-' + job, actor='LOCAL_OPERATOR')
        from .cloud_egress import status
        receipt = {'job_id': job, 'state': 'FAILED', 'version': row['version'],
            'outcome': 'FAILED_NO_RETRY', 'source_state': source_state, 'source_version': source_version,
            'provider_stop_evidence': 'INDEPENDENT_OPERATOR_CONFIRMATION',
            'workspace_inspected': True, 'ledger_sha256': _digest(charges),
            'work_inventory_sha256': _digest([(name, sha256(data).hexdigest()) for name, data in work]),
            'output_sha256': {name: sha256(data).hexdigest() for name, data in files.items()},
            'cloud_budget': status(con, job)}
        con.execute('INSERT INTO cloud_operator_resolutions VALUES(?,?,?,?)',
            (job, request_hash, json.dumps(receipt, sort_keys=True), _utc()))
        return receipt
