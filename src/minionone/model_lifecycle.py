"""Transactional model leases around the existing single worker, with real telemetry."""
import ctypes
from dataclasses import dataclass
import json
import multiprocessing
import os
import threading
import time
from .admission import _digest, InputRejected
from .model_runtime import ModelRuntime
from .worker_profiles import WorkerProviderError


@dataclass(frozen=True)
class MemoryReading:
    ram_mb:int
    vram_mb:int|None
    measured:float


def _measure_memory_local(gpu_index=0,need_gpu=False):
    if os.name=='nt':
        class Memory(ctypes.Structure):
            _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),
                      *[(n,ctypes.c_ulonglong) for n in ('total','available','page','freepage','virtual','freevirtual','extended')]]
        data=Memory();data.length=ctypes.sizeof(data)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
        ram=data.available//1048576
    else:ram=os.sysconf('SC_AVPHYS_PAGES')*os.sysconf('SC_PAGE_SIZE')//1048576
    vram=None
    if need_gpu:
        try:
            lib=ctypes.CDLL('nvml.dll' if os.name=='nt' else 'libnvidia-ml.so.1')
            if lib.nvmlInit_v2()!=0:raise ValueError()
            try:
                handle=ctypes.c_void_p()
                class Info(ctypes.Structure):_fields_=[('total',ctypes.c_ulonglong),('free',ctypes.c_ulonglong),('used',ctypes.c_ulonglong)]
                info=Info()
                if lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(gpu_index),ctypes.byref(handle))!=0:raise ValueError()
                if lib.nvmlDeviceGetMemoryInfo(handle,ctypes.byref(info))!=0:raise ValueError()
                vram=info.free//1048576
            finally:lib.nvmlShutdown()
        except (OSError,ValueError,AttributeError):raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED') from None
    return MemoryReading(ram,vram,time.monotonic())


def _memory_child(send,gpu_index,need_gpu):
    try:send.send(_measure_memory_local(gpu_index,need_gpu))
    except Exception:send.send(None)
    finally:send.close()


def measure_memory(gpu_index=0,need_gpu=False):
    # NVML initialization/query must not strand the manager on a driver stall.
    ctx=multiprocessing.get_context('spawn');receive,send=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_memory_child,args=(send,gpu_index,need_gpu),daemon=True)
    end=time.monotonic()+5
    try:
        process.start();send.close()
        if not receive.poll(max(0,end-time.monotonic())):raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
        value=receive.recv()
        if type(value) is not MemoryReading:raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
        return value
    finally:
        receive.close();send.close()
        if process.pid is not None:
            process.join(.1)
            if process.is_alive():process.terminate();process.join(1)
            if process.is_alive():process.kill();process.join(1)
            if process.is_alive():raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
            process.close()


class ModelLifecycle:
    def __init__(self,owner,*,parent=None,measure=measure_memory,runtime=ModelRuntime,
                 ram_headroom_mb=1024,vram_headroom_mb=512,gpu_index=0,idle_seconds=180):
        if any(type(v) is not int or not 1<=v<=1048576 for v in (ram_headroom_mb,vram_headroom_mb)) or type(gpu_index) is not int or not 0<=gpu_index<=15 or type(idle_seconds) is not int or not 1<=idle_seconds<=900:
            raise InputRejected('invalid operator lifecycle policy')
        self.owner=owner;self.parent=parent;self.measure=measure;self.runtime=runtime
        self.ram_headroom=ram_headroom_mb;self.vram_headroom=vram_headroom_mb
        self.gpu_index=gpu_index;self.idle_seconds=idle_seconds;self.stop=threading.Event()
        with owner._connection() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS model_leases(
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id), state TEXT NOT NULL,
                profile_json TEXT NOT NULL, before_json TEXT NOT NULL, owned_instance TEXT,
                parent_unloaded INTEGER NOT NULL DEFAULT 0, phase TEXT NOT NULL);
                UPDATE model_leases SET state='NEEDS_RECONCILIATION'
                    WHERE state IN ('PREPARING','ACTIVE','RELEASING');''')
            if 'finish_outcome' not in {r[1] for r in con.execute('PRAGMA table_info(model_leases)')}:
                con.execute('ALTER TABLE model_leases ADD COLUMN finish_outcome TEXT')
    def blocked(self,con):
        return con.execute("SELECT 1 FROM model_leases WHERE state!='RELEASED'").fetchone() is not None
    def _set(self,job,*,state=None,phase=None,owned=None,parent_unloaded=None,finish_outcome=None):
        changes={k:v for k,v in {'state':state,'phase':phase,'owned_instance':owned,
            'parent_unloaded':parent_unloaded,'finish_outcome':finish_outcome}.items() if v is not None}
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            con.execute('UPDATE model_leases SET '+','.join(k+'=?' for k in changes)+' WHERE job_id=?',[*changes.values(),job])
    def fits(self,profile,*,already_loaded=False):
        s=self.measure(self.gpu_index,profile.memory_vram_mb>0)
        if (type(s) is not MemoryReading or type(s.ram_mb) is not int or s.ram_mb<0
                or time.monotonic()-s.measured>5 or s.measured>time.monotonic()):
            raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
        ram=profile.inference_ram_mb if already_loaded else profile.memory_ram_mb
        gpu=profile.inference_vram_mb if already_loaded else profile.memory_vram_mb
        if ram is None or gpu is None:raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
        return (s.ram_mb>=ram+self.ram_headroom and
                (profile.memory_vram_mb==0 or (type(s.vram_mb) is int and s.vram_mb>=gpu+self.vram_headroom)))
    def _idle(self,job,cancelled):
        if self.parent is None:return
        end=time.monotonic()+self.idle_seconds
        while True:
            if cancelled() or self.stop.is_set():raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
            state=self.parent.state(self.parent.principal,self.parent.thread_id)
            if cancelled() or self.stop.is_set():raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
            if not state.authenticated:raise WorkerProviderError('NATIVE_PARENT_UNAVAILABLE')
            if not state.busy:return
            self._set(job,phase='WAITING_PARENT_TURN_END')
            if cancelled() or self.stop.wait(.2):raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
            if time.monotonic()>=end:raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
    def prepare(self,job,profile,cancelled):
        if profile is None or profile.provider=='cloud' or not profile.auto_load:return
        from dataclasses import asdict
        if self.parent is not None and profile.endpoint==self.parent.profile.endpoint and profile.model_id==self.parent.profile.model_id:
            raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
        runtime=self.runtime(profile);before=runtime.snapshot()
        if before['loading']:raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
        parent_before=sorted(self.parent.runtime.snapshot()['loaded']) if self.parent else None
        with self.owner._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            if self.blocked(con):raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
            con.execute('INSERT INTO model_leases(job_id,state,profile_json,before_json,phase) VALUES(?,?,?,?,?)',
                (job,'PREPARING',json.dumps(asdict(profile),sort_keys=True),json.dumps({'worker':sorted(before['loaded']),
                    'parent':parent_before}),'PLANNING'))
        try:
            # Switching the active model on a shared host also waits for the parent turn.
            if not runtime.ready(before):self._idle(job,cancelled)
            if not self.fits(profile,already_loaded=runtime.present(before)):
                if (self.parent is None or not self.parent.allow_unload or
                        not self.parent.state(self.parent.principal,self.parent.thread_id).resident):
                    raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
                self._idle(job,cancelled)
                self._set(job,phase='PARENT_UNLOAD_IN_FLIGHT',parent_unloaded=1)
                self.parent.unload('parent-unload-'+_digest(job))
                self._set(job,phase='PARENT_UNLOADED')
                if not self.fits(profile,already_loaded=runtime.present(runtime.snapshot())):
                    raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
            if cancelled() or self.stop.is_set():raise WorkerProviderError('MODEL_LIFECYCLE_BLOCKED')
            if not runtime.ready(runtime.snapshot()):
                self._set(job,phase='WORKER_LOAD_IN_FLIGHT')
                instance=runtime.load('worker-load-'+_digest(job))
                if instance is not None:self._set(job,owned=instance)
            self._set(job,state='ACTIVE',phase='WORKER_READY')
            if not self.fits(profile,already_loaded=True):raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')
            if profile.provider=='lmstudio':
                from dataclasses import replace
                current=runtime.snapshot()
                matches=[i for i,key in current['instances'].items() if key==profile.model_id or i==profile.model_id]
                if len(matches)!=1:raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
                return replace(profile,model_id=matches[0])
            return profile
        except Exception:
            with self.owner._connection() as con:row=con.execute('SELECT phase FROM model_leases WHERE job_id=?',(job,)).fetchone()
            if row and row[0].endswith('IN_FLIGHT'):self._set(job,state='NEEDS_RECONCILIATION')
            raise
    def finish(self,job,outcome):
        from .worker_profiles import WorkerProfile
        with self.owner._connection() as con:row=con.execute('SELECT * FROM model_leases WHERE job_id=?',(job,)).fetchone()
        if row is None or row['state']=='RELEASED':return
        if outcome=='NEEDS_RECONCILIATION' or row['state']=='NEEDS_RECONCILIATION':
            self._set(job,state='NEEDS_RECONCILIATION');raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
        self._set(job,state='RELEASING',phase='WORKER_UNLOAD_IN_FLIGHT',finish_outcome=outcome)
        try:
            from .worker_selection import load as selection
            with self.owner._connection() as con:
                profile=selection(con,job)
            if profile!=WorkerProfile(**json.loads(row['profile_json'])):
                raise WorkerProviderError('MODEL_LIFECYCLE_UNCERTAIN')
            if row['owned_instance']:self.runtime(profile).unload(row['owned_instance'])
            self._set(job,phase='PARENT_RESTORE_IN_FLIGHT')
            if self.parent is not None:
                self.parent.restore(self.parent.principal,self.parent.thread_id,self.parent.profile.model_id,'parent-restore-'+_digest(job))
            self._set(job,state='RELEASED',phase='RESTORED')
        except Exception:
            self._set(job,state='NEEDS_RECONCILIATION');raise
    def status(self,job):
        with self.owner._connection() as con:row=con.execute('SELECT state,phase,parent_unloaded FROM model_leases WHERE job_id=?',(job,)).fetchone()
        return dict(row) if row else {'state':'NOT_REQUIRED','phase':'NONE','parent_unloaded':0}
    def reconcile(self,job):
        """Read-only proof after explicit operator intervention; never repeats an effect."""
        from .worker_profiles import WorkerProfile
        with self.owner._connection() as con:row=con.execute('SELECT * FROM model_leases WHERE job_id=?',(job,)).fetchone()
        if row is None or (row['state']!='NEEDS_RECONCILIATION' and row['phase']!='OPERATOR_RECONCILED'):raise InputRejected('no quarantined model lease')
        snapshot=self.runtime(WorkerProfile(**json.loads(row['profile_json']))).snapshot()
        before=json.loads(row['before_json'])
        expected=before['worker'] if type(before) is dict else before
        if snapshot['loading'] or snapshot['loaded']!=set(expected):
            raise InputRejected('original model inventory not restored')
        if self.parent is not None:
            if type(before) is not dict or before['parent']!=sorted(self.parent.runtime.snapshot()['loaded']):
                raise InputRejected('original parent inventory not restored')
            state=self.parent.state(self.parent.principal,self.parent.thread_id)
            if not state.resident or state.busy or not state.authenticated:raise InputRejected('original parent not restored and idle')
            self.parent.verify_restoration(job)
        self._set(job,state='RELEASED',phase='OPERATOR_RECONCILED')
