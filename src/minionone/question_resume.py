"""Authenticated answers continue a verified, quiescent worker checkpoint.

Only explicit worker history is stored here. No parent chat is a worker input.
An accepted answer is fenced before dispatch; restart never repeats that dispatch.
"""
from dataclasses import asdict
from hashlib import sha256
import json
import secrets
import threading

from .admission import InputRejected, VersionConflict, _digest, _field, _OP
from .agent_loop import AgentLimits
from .lifecycle import Evidence


def install(con):
    for name,definition in (('attempt_no','INTEGER NOT NULL DEFAULT 1'),
                            ('active_ms','INTEGER NOT NULL DEFAULT 0'),
                            ('wall_limit_ms','INTEGER NOT NULL DEFAULT 0')):
        if name not in {r[1] for r in con.execute('PRAGMA table_info(pilot_runs)')}:
            con.execute('ALTER TABLE pilot_runs ADD COLUMN '+name+' '+definition)
    con.executescript('''
        CREATE TRIGGER IF NOT EXISTS pilot_wall_budget_sealed BEFORE UPDATE OF wall_limit_ms ON pilot_runs
            WHEN OLD.wall_limit_ms>0 AND NEW.wall_limit_ms!=OLD.wall_limit_ms
            BEGIN SELECT RAISE(ABORT,'sealed cumulative wall budget'); END;
        CREATE TABLE IF NOT EXISTS pilot_question_checkpoints(
            job_id TEXT NOT NULL REFERENCES jobs(job_id), attempt_no INTEGER NOT NULL,
            checkpoint_json TEXT NOT NULL, checkpoint_digest TEXT NOT NULL,
            question_digest TEXT NOT NULL, PRIMARY KEY(job_id,attempt_no));
        CREATE TABLE IF NOT EXISTS pilot_parent_answers(
            job_id TEXT NOT NULL REFERENCES jobs(job_id), attempt_no INTEGER NOT NULL,
            question_digest TEXT NOT NULL, checkpoint_digest TEXT NOT NULL,
            answer TEXT NOT NULL, PRIMARY KEY(job_id,attempt_no));
        CREATE TRIGGER IF NOT EXISTS question_checkpoint_no_update BEFORE UPDATE ON pilot_question_checkpoints
            BEGIN SELECT RAISE(ABORT,'immutable question checkpoint'); END;
        CREATE TRIGGER IF NOT EXISTS question_checkpoint_no_delete BEFORE DELETE ON pilot_question_checkpoints
            BEGIN SELECT RAISE(ABORT,'immutable question checkpoint'); END;
        CREATE TRIGGER IF NOT EXISTS parent_answer_no_update BEFORE UPDATE ON pilot_parent_answers
            BEGIN SELECT RAISE(ABORT,'immutable parent answer'); END;
        CREATE TRIGGER IF NOT EXISTS parent_answer_no_delete BEFORE DELETE ON pilot_parent_answers
            BEGIN SELECT RAISE(ABORT,'immutable parent answer'); END;
    ''')


def limits(manager,job):
    from .pilot_jobs import PilotError
    with manager.owner._connection() as con:
        p=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(job,)).fetchone()
    remaining={'max_loops':50-p['turns'],'max_tool_calls':60-p['tools'],
               'max_tokens':150000-p['tokens'],'max_denials':3-p['denials'],
               'wall_seconds':(p['wall_limit_ms']-p['active_ms'])//1000}
    if any(v<=0 for v in remaining.values()):raise PilotError('PILOT_BUDGET_EXHAUSTED')
    return AgentLimits(**remaining,model_call_seconds=manager.config.model_call_seconds)


def work_digest(manager,job,broker):
    with manager.owner._connection() as con:
        row=con.execute('SELECT job_kind FROM jobs WHERE job_id=?',(job,)).fetchone()
        outputs=[tuple(r) for r in con.execute("SELECT name,digest FROM pilot_files WHERE job_id=? AND zone='output' ORDER BY name",(job,))]
        if row[0]!='WORKSPACE':
            work=[tuple(r) for r in con.execute("SELECT name,digest FROM pilot_files WHERE job_id=? AND zone='work' ORDER BY name",(job,))]
        else:work=None
    if work is None:
        work=[(name,sha256(blob).hexdigest()) for name,blob in broker.checkpoint_inventory()]
    return _digest([sorted(work),outputs])


def finished(manager,job,cap,run,provider,broker,elapsed_ms,*,job_log_digest=None):
    """Commit consumed active time and a question checkpoint before quiescence."""
    state=None;digest=None
    if run.status=='WAITING_FOR_PARENT':
        if not callable(getattr(provider,'export_worker_state',None)):
            raise InputRejected('worker adapter cannot checkpoint questions')
        state={'provider':provider.export_worker_state(),
               'work_digest':work_digest(manager,job,broker),'job_log_digest':job_log_digest}
    with manager.owner._connection() as con:
        con.execute('BEGIN IMMEDIATE');manager._current(con,job,cap)
        con.execute('UPDATE pilot_runs SET active_ms=active_ms+? WHERE job_id=?',(elapsed_ms,job))
        if state is not None:
            p=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(job,)).fetchone()
            if not p['question']:raise InputRejected('question checkpoint missing question')
            state.update({'attempt_no':p['attempt_no'],'turns':p['turns'],'tools':p['tools'],
                'tokens':p['tokens'],'denials':p['denials'],'active_ms':p['active_ms'],
                'worker_profile_sha256':p['worker_profile_sha256'],'wall_limit_ms':p['wall_limit_ms']})
            blob=json.dumps(state,sort_keys=True,separators=(',',':'),ensure_ascii=True)
            if len(blob)>300000:raise InputRejected('question checkpoint too large')
            digest=_digest(state)
            qdigest=_digest([job,p['attempt_no'],p['question'],digest])
            con.execute('INSERT INTO pilot_question_checkpoints VALUES(?,?,?,?,?)',
                (job,p['attempt_no'],blob,digest,qdigest))


def _load(con,job,attempt):
    row=con.execute('SELECT * FROM pilot_question_checkpoints WHERE job_id=? AND attempt_no=?',(job,attempt)).fetchone()
    if row is None:raise InputRejected('verified question checkpoint missing')
    state=json.loads(row['checkpoint_json'])
    if _digest(state)!=row['checkpoint_digest'] or state['attempt_no']!=attempt:
        raise InputRejected('question checkpoint changed')
    return row,state


def restore(manager,job,provider,broker):
    with manager.owner._connection() as con:
        p=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(job,)).fetchone()
        if p['attempt_no']==1:return
        answer=con.execute('SELECT * FROM pilot_parent_answers WHERE job_id=? AND attempt_no=?',(job,p['attempt_no'])).fetchone()
        checkpoint,state=_load(con,job,p['attempt_no']-1)
        if (answer is None or answer['checkpoint_digest']!=checkpoint['checkpoint_digest'] or
                answer['question_digest']!=checkpoint['question_digest'] or
                state['worker_profile_sha256']!=p['worker_profile_sha256']):
            raise InputRejected('answer checkpoint binding changed')
    if state['work_digest']!=work_digest(manager,job,broker):raise InputRejected('paused work changed')
    provider.restore_worker_state(state['provider'],answer['answer'],answer['question_digest'])


def answer(manager,token,thread,job,version,question_digest,answer_text,operation,*,kind='WORKSPACE'):
    from .pilot_jobs import _text,PilotError
    principal=manager.owner._principal(token,thread);_field(_OP,operation,'operation')
    if type(version) is not int or version<0:raise InputRejected('invalid expected version')
    answer_text=_text(answer_text,4096)
    request=[version,question_digest,answer_text]
    with manager.owner._connection() as con:
        row=manager.owner._owned_row(con,principal,thread,job)
        prior=manager._receipt(con,job,'ANSWER',operation,request)
        if prior is not None:return prior
        if row['job_kind']!=kind or row['state']!='WAITING_FOR_PARENT' or row['version']!=version:
            raise VersionConflict('question no longer waiting at expected version')
    if manager.parent_return is not None:manager.parent_return.require_delivery(job)
    if manager.workspace_execution is not None and kind=='WORKSPACE':
        manager.workspace_execution.workspace.verify_input(token,thread,job)
    with manager.owner._connection() as con:
        p=con.execute('SELECT attempt_no FROM pilot_runs WHERE job_id=?',(job,)).fetchone()
        checkpoint,state=_load(con,job,p[0])
    verification_broker=manager.make_broker(job,'verification-only',kind=='WORKSPACE')
    if state['work_digest']!=work_digest(manager,job,verification_broker):
        raise InputRejected('paused work changed; restore or inspect it before answering')
    cap=secrets.token_hex(32)
    with manager.owner._connection() as con:
        con.execute('BEGIN IMMEDIATE');row=manager.owner._owned_row(con,principal,thread,job)
        prior=manager._receipt(con,job,'ANSWER',operation,request)
        if prior is not None:return prior
        if row['job_kind']!=kind or row['state']!='WAITING_FOR_PARENT' or row['version']!=version:
            raise VersionConflict('question changed during verification')
        p=con.execute('SELECT * FROM pilot_runs WHERE job_id=?',(job,)).fetchone()
        checkpoint,state=_load(con,job,p['attempt_no'])
        if (checkpoint['question_digest']!=question_digest or p['attempt_no']>=20 or
                any(state[k]!=p[k] for k in ('turns','tools','tokens','denials','active_ms','worker_profile_sha256','wall_limit_ms'))):
            raise VersionConflict('question checkpoint or budget changed')
        if p['turns']>=50 or p['tools']>=60 or p['tokens']>=150000 or p['denials']>=3 or p['active_ms']+1000>p['wall_limit_ms']:
            raise PilotError('PILOT_BUDGET_EXHAUSTED')
        if con.execute('SELECT 1 FROM pilot_runs WHERE active=1 OR provider_inflight=1').fetchone():raise PilotError('PILOT_JOB_BUSY')
        if manager.model_lifecycle is not None and manager.model_lifecycle.blocked(con):raise PilotError('MODEL_LIFECYCLE_BLOCKED')
        next_attempt=p['attempt_no']+1
        con.execute('INSERT INTO pilot_parent_answers VALUES(?,?,?,?,?)',
            (job,next_attempt,question_digest,checkpoint['checkpoint_digest'],answer_text))
        con.execute('UPDATE pilot_runs SET attempt_no=?,active=1,capability=?,question=NULL,error_code=NULL WHERE job_id=?',
            (next_attempt,sha256(cap.encode()).hexdigest(),job))
        row=manager.owner._transition(con,row,'RESUMING',
            Evidence(new_attempt=True,attempt_no=next_attempt,checkpoint_verified=True),
            'pilot-answer-'+_digest(operation),actor='AUTHENTICATED_PARENT')
        if manager.parent_return is not None:manager.parent_return.archive_for_resume(con,job)
        receipt=manager.owner._snapshot(row)
        manager._receipt(con,job,'ANSWER',operation,request,receipt)
    worker=threading.Thread(target=manager._run,args=(job,cap),daemon=True)
    with manager._lock:manager._threads.add(worker)
    try:
        if manager.parent_return is not None:manager.parent_return.bind(job,refresh=True)
        worker.start()
    except Exception:
        with manager._lock:manager._threads.discard(worker)
        manager._finish(job,'FAILED','PILOT_START_FAILED')
        raise PilotError('PILOT_START_FAILED') from None
    return receipt
