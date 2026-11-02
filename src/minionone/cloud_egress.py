"""Operator-declared data/billing policy and durable conservative cloud charges.

Every complete request is checked, including later tool reads and parent answers.
Detection is conservative rules plus operator-sensitive terms, not universal NER.
Reservations are upper bounds under the explicitly configured provider contract;
they are never refunded using untrusted usage or after an ambiguous network error.
"""
import json
import os
import re
import unicodedata

from .admission import InputRejected, _digest, _utc


_REQUIRED={'contract','data_class','max_job_cost_microusd','max_total_cost_microusd',
           'input_token_ceiling','input_price_microusd_per_million',
           'output_price_microusd_per_million'}
_OPTIONAL={'sensitive_terms','public_terms'}
_SECRET=re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b')
_PII=(
    re.compile(r'\b[^\s@<>"\\]+@[^\s@<>"\\]+\.[A-Za-z]{2,}\b'),
    re.compile(r'\b[A-Z]{2}\s*\d{2}(?:\s*[A-Z0-9]){11,30}\b'),
    re.compile(r'(?<!\w)(?:\d[ -]?){13,19}(?!\w)'),
    re.compile(r'(?<!\w)\+\d[\d ()-]{7,24}\d(?!\w)'),
    re.compile(r'\b(?:phone|telefon|tel|mobile)\s*[:=]\s*[\d ()+-]{7,26}',re.I),
    re.compile(r'\b[A-ZÄÖÜ][a-zäöüß]{1,35}\s+[A-ZÄÖÜ][a-zäöüß]{1,35}\b'),
    re.compile(r'\b[\wäöüß-]+(?:straße|strasse|str\.|weg|allee|street|road)\s+\d{1,5}\b',re.I),
    re.compile(r'\b\d{1,5}\s+(?:[A-Za-z-]+\s+){1,4}(?:Street|Road|Avenue|Lane)\b',re.I),
    re.compile(r'(?:[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]|/(?:home|Users)/)[^\s"<>]+',re.I),
)


def validate_policy(policy):
    if policy is None:return
    if (type(policy) is not dict or not _REQUIRED<=set(policy) or set(policy)-_REQUIRED-_OPTIONAL or
            policy['contract']!='text_tokens_v1' or
            policy['data_class'] not in ('public','synthetic','pseudonymized')):
        raise InputRejected('explicit cloud data and billing contract required')
    for key in _REQUIRED-{'contract','data_class'}:
        value=policy[key]
        ceiling=2_000_000 if key=='input_token_ceiling' else 1_000_000_000_000
        if type(value) is not int or not 1<=value<=ceiling:raise InputRejected('invalid cloud budget')
    if policy['max_job_cost_microusd']>policy['max_total_cost_microusd']:
        raise InputRejected('job cloud budget exceeds total budget')
    for key in _OPTIONAL:
        terms=policy.get(key,[])
        if (type(terms) is not list or len(terms)>256 or any(type(t) is not str or
                not 2<=len(t)<=256 or any(ord(c)<32 for c in t) for t in terms)):
            raise InputRejected('invalid operator cloud data rules')


def install(con):
    con.executescript('''CREATE TABLE IF NOT EXISTS cloud_request_reservations(
        job_id TEXT NOT NULL REFERENCES jobs(job_id), turn INTEGER NOT NULL,
        principal TEXT NOT NULL, request_digest TEXT NOT NULL, policy_digest TEXT NOT NULL,
        reserved_microusd INTEGER NOT NULL CHECK(reserved_microusd>0),
        state TEXT NOT NULL, created_utc TEXT NOT NULL, PRIMARY KEY(job_id,turn));
        CREATE TRIGGER IF NOT EXISTS cloud_charge_no_delete BEFORE DELETE ON cloud_request_reservations
            BEGIN SELECT RAISE(ABORT,'cloud reservations cannot be refunded'); END;
        CREATE TRIGGER IF NOT EXISTS cloud_charge_sealed BEFORE UPDATE OF
            job_id,turn,principal,request_digest,policy_digest,reserved_microusd,created_utc
            ON cloud_request_reservations
            BEGIN SELECT RAISE(ABORT,'immutable cloud reservation'); END;''')


def _strings(value,depth=0):
    if depth>8:raise InputRejected("cloud payload nesting exceeds the reviewed envelope")
    if type(value) is str:
        yield value
        if value.lstrip().startswith(('{','[')):
            try:decoded=json.loads(value)
            except (ValueError,TypeError):return
            yield from _strings(decoded,depth+1)
    elif type(value) is dict:
        for item in value.values():yield from _strings(item,depth+1)
    elif type(value) is list:
        for item in value:yield from _strings(item,depth+1)


class CloudEgressGuard:
    def __init__(self,manager,job,cap,profile):
        self.manager=manager;self.job=job;self.cap=cap;self.profile=profile
        self.policy=json.loads(json.dumps(profile.cloud_policy))
        validate_policy(self.policy)

    def validate(self,body):
        from .worker_profiles import WorkerProviderError
        if self.policy is None:raise WorkerProviderError('CLOUD_POLICY_REQUIRED')
        try:
            payload=json.loads(body)
            max_output=payload[self.profile.token_parameter]
            if (payload['model']!=self.profile.model_id or payload.get('stream') is not False or
                    type(payload.get('messages')) is not list or
                    type(max_output) is not int or not 1<=max_output<=min(4096,self.profile.max_output_tokens) or
                    any(t.get('type')!='function' for t in payload.get('tools',[]))):
                raise ValueError()
            # The operator attests this ceiling against the selected provider's
            # text-token billing/context contract. Additional fees are unsupported.
            if len(body)+8192>self.policy['input_token_ceiling']:
                raise WorkerProviderError('CLOUD_INPUT_LIMIT')
            strings=list(_strings(payload))
            text=unicodedata.normalize('NFKC','\n'.join(strings))
            text=''.join(c for c in text if unicodedata.category(c)!='Cf')
            secrets=list(self.manager.owner._credentials)
            secrets.extend(v for k,v in os.environ.items() if
                any(part in k.upper() for part in ('KEY','TOKEN','SECRET','PASSWORD')) and len(v)>=8)
            if _SECRET.search(text) or any(secret in text for secret in secrets):
                raise WorkerProviderError('CLOUD_EGRESS_BLOCKED')
            folded=text.casefold()
            if any(unicodedata.normalize('NFKC',t).casefold() in folded for t in self.policy.get('sensitive_terms',[])):
                raise WorkerProviderError('CLOUD_EGRESS_BLOCKED')
            from .worker_contract import SYSTEM_POLICY,WORKSPACE_SYSTEM_POLICY
            checked={**payload,'messages':[m for m in payload['messages'] if not
                (m.get('role')=='system' and m.get('content') in (SYSTEM_POLICY,WORKSPACE_SYSTEM_POLICY))]}
            text=unicodedata.normalize('NFKC','\n'.join(_strings(checked)))
            text=''.join(c for c in text if unicodedata.category(c)!='Cf')
            # Exceptions are operator-owned exact public phrases, never worker settings.
            for term in self.policy.get('public_terms',[]):text=text.replace(unicodedata.normalize('NFKC',term),' ')
            metadata_text=re.sub(r'[_./\\]+',' ',text)
            if any(rule.search(text) or rule.search(metadata_text) for rule in _PII):raise WorkerProviderError('CLOUD_EGRESS_BLOCKED')
        except WorkerProviderError:raise
        except (TypeError,ValueError,KeyError,AttributeError,UnicodeError):
            raise WorkerProviderError('CLOUD_EGRESS_BLOCKED') from None
        policy=self.policy
        reserve=(policy['input_token_ceiling']*policy['input_price_microusd_per_million']+
                 max_output*policy['output_price_microusd_per_million']+999999)//1000000
        return max(1,reserve)

    def request(self,body,send):
        from .worker_profiles import WorkerProviderError
        reserve=self.validate(body)
        with self.manager.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE');row=self.manager._current(con,self.job,self.cap)
            p=con.execute('SELECT turns FROM pilot_runs WHERE job_id=?',(self.job,)).fetchone()
            # Per-job and per-principal totals include unknown/failed requests,
            # all attempts, model selections and manager restarts.
            job_spent=con.execute('SELECT COALESCE(SUM(reserved_microusd),0) FROM cloud_request_reservations WHERE job_id=?',(self.job,)).fetchone()[0]
            total=con.execute('SELECT COALESCE(SUM(reserved_microusd),0) FROM cloud_request_reservations WHERE principal=?',(row['principal'],)).fetchone()[0]
            if (job_spent+reserve>self.policy['max_job_cost_microusd'] or
                    total+reserve>self.policy['max_total_cost_microusd']):
                raise WorkerProviderError('CLOUD_BUDGET_EXHAUSTED')
            if con.execute('SELECT 1 FROM cloud_request_reservations WHERE job_id=? AND turn=?',(self.job,p[0])).fetchone():
                raise WorkerProviderError('CLOUD_REQUEST_ALREADY_RESERVED')
            con.execute('INSERT INTO cloud_request_reservations VALUES(?,?,?,?,?,?,?,?)',
                (self.job,p[0],row['principal'],_digest(body.decode('ascii')),_digest(self.policy),reserve,'IN_FLIGHT',_utc()))
            turn=p[0]
        try:result=send()
        except Exception:
            with self.manager.owner._connection() as con:
                con.execute("UPDATE cloud_request_reservations SET state='UNKNOWN' WHERE job_id=? AND turn=?",(self.job,turn))
            raise
        with self.manager.owner._connection() as con:
            con.execute("UPDATE cloud_request_reservations SET state='RESPONSE_RECEIVED' WHERE job_id=? AND turn=?",(self.job,turn))
        return result


def status(con,job):
    principal=con.execute('SELECT principal FROM jobs WHERE job_id=?',(job,)).fetchone()
    global_total=con.execute('SELECT COALESCE(SUM(reserved_microusd),0) FROM cloud_request_reservations WHERE principal=?',(principal[0],)).fetchone()[0]
    total=con.execute('SELECT COALESCE(SUM(reserved_microusd),0),COUNT(*) FROM cloud_request_reservations WHERE job_id=?',(job,)).fetchone()
    return {'currency':'USD','reserved_upper_bound_microusd':total[0],
            'requests_reserved':total[1],'principal_reserved_upper_bound_microusd':global_total,
            'refund_on_uncertain_response':False}
