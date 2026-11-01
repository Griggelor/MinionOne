"""Bind operator-configured providers to sealed jobs, independent of handoff."""
from dataclasses import asdict
import json

from .admission import InputRejected, _digest
from .worker_profiles import WorkerProfile


def resolve(pilot, profile_id=None, model_id=None, allow_cloud=False):
    if type(allow_cloud) is not bool:
        raise InputRejected('invalid cloud permission')
    if profile_id is None:
        if pilot.config.model_id == 'profile-selection-required':
            raise InputRejected('select a configured worker profile')
        if allow_cloud or (model_id is not None and model_id != pilot.config.model_id):
            raise InputRejected('unapproved default worker')
        return None, pilot.config.model_id
    if type(profile_id) is not str or profile_id not in pilot.worker_profiles:
        raise InputRejected('unknown configured worker')
    profile = pilot.worker_profiles[profile_id]
    if model_id is not None and model_id != profile.model_id:
        raise InputRejected('worker/model mismatch')
    if profile.provider == 'cloud' and not allow_cloud:
        raise InputRejected('cloud requires explicit job approval')
    return profile, profile.model_id


def identity(input_digest, profile, allow_cloud):
    return (_digest([input_digest, asdict(profile), allow_cloud])
            if profile is not None else None)


def save(con, job, input_digest, profile, allow_cloud):
    if profile is None:
        return
    digest = identity(input_digest, profile, allow_cloud)
    con.execute('INSERT INTO pilot_worker_profiles VALUES(?,?,?)',
                (job, json.dumps(asdict(profile), sort_keys=True), int(allow_cloud)))
    con.execute('UPDATE pilot_runs SET worker_profile_sha256=? WHERE job_id=?',
                (digest, job))


def load(con, job):
    row = con.execute('''SELECT p.worker_profile_sha256,p.model_id,j.input_digest
        FROM pilot_runs p JOIN jobs j USING(job_id) WHERE j.job_id=?''', (job,)).fetchone()
    data = con.execute('SELECT * FROM pilot_worker_profiles WHERE job_id=?', (job,)).fetchone()
    if row is None:
        raise InputRejected('missing worker binding')
    if row['worker_profile_sha256'] is None and data is None:
        return None
    if data is None or row['worker_profile_sha256'] is None:
        raise InputRejected('missing worker snapshot')
    try:
        profile = WorkerProfile(**json.loads(data['profile_json']))
        approved = bool(data['allow_cloud'])
        if (identity(row['input_digest'], profile, approved) != row['worker_profile_sha256']
                or profile.model_id != row['model_id']
                or (profile.provider == 'cloud' and not approved)):
            raise ValueError('changed worker snapshot')
    except (ValueError, TypeError):
        raise InputRejected('changed worker snapshot') from None
    return profile
