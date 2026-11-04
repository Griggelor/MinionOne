"""Synthetic AMD UMA memory admission and native lifecycle regression (NOT host proof)."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from minionone.admission import InputRejected
from minionone.control_http import ControlHTTP
from minionone.mcp_server import LocalMCP
from minionone.model_lifecycle import MemoryReading, ModelLifecycle, memory_budget_fits
from minionone.pilot_cli import PilotClient
from minionone.unsloth_parent import NativeParentConfig, UnslothParent
from minionone.worker_profiles import WorkerProfile, WorkerProviderError
import importlib.util

_fixture_path=Path(__file__).resolve().parents[1]/'integration_tests/native_fixture.py'
_fixture_spec=importlib.util.spec_from_file_location('uma_native_fixture',_fixture_path)
_fixture=importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture)
NativeFixture,THREAD,PARENT,WORKER=(_fixture.NativeFixture,_fixture.THREAD,
                                   _fixture.PARENT,_fixture.WORKER)

URL = 'http://127.0.0.1:1234'


def worker(**kw):
    args = dict(profile_id='amd-395', provider='lmstudio',
                model_id='synthetic-worker', base_url=URL, auto_load=True,
                memory_accounting='shared_uma', memory_ram_mb=14000,
                memory_vram_mb=0, inference_ram_mb=1500, inference_vram_mb=0)
    args.update(kw)
    return WorkerProfile(**args)


class SharedUmaBudgetTests(unittest.TestCase):
    def test_worker_profile_roundtrip_catalog_and_legacy_default(self):
        p = worker()
        self.assertEqual(p.catalog()['memory_accounting'], 'shared_uma')
        self.assertEqual(WorkerProfile(**asdict(p)), p)
        old = asdict(p)
        old.pop('memory_accounting')
        self.assertEqual(WorkerProfile(**old).memory_accounting, 'separate')

    def test_unsafe_shared_uma_budgets_are_rejected(self):
        bad = [{'memory_vram_mb':1}, {'inference_vram_mb':1},
               {'inference_vram_mb':None}, {'inference_ram_mb':None},
               {'memory_ram_mb':0}, {'inference_ram_mb':15000},
               {'memory_accounting':'auto'}, {'memory_accounting':'amd-smi'},
               {'memory_accounting':True}]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(InputRejected):
                worker(**values)
        with self.assertRaises(InputRejected):
            worker(provider='cloud')

    def test_worker_shared_pool_without_nvml_or_double_counting(self):
        calls = []
        lease = object.__new__(ModelLifecycle)
        lease.gpu_index = 0
        lease.ram_headroom = 1024
        lease.vram_headroom = 512
        def measure(index, need_gpu):
            calls.append((index, need_gpu))
            if need_gpu: raise AssertionError('must not call NVML')
            return MemoryReading(18096, None, time.monotonic())
        lease.measure = measure
        self.assertTrue(lease.fits(worker()))
        self.assertTrue(lease.fits(worker(), already_loaded=True))
        self.assertEqual(calls, [(0,False), (0,False)])

    def test_boundary_and_incremental_kv_share_the_same_pool(self):
        p = worker()
        now = 100.0
        self.assertTrue(memory_budget_fits(p, MemoryReading(18096,None,99.9), now=now))
        self.assertFalse(memory_budget_fits(p, MemoryReading(18095,None,99.9), now=now))
        self.assertTrue(memory_budget_fits(p, MemoryReading(5596,None,99.9),
                                           already_loaded=True, now=now))
        self.assertFalse(memory_budget_fits(p, MemoryReading(5595,None,99.9),
                                            already_loaded=True, now=now))
        self.assertFalse(memory_budget_fits(p, MemoryReading(18096,None,99.9),
                                            now=now, ram_headroom_mb=5000))

    def test_untrusted_or_stale_readings_never_become_uma_capacity(self):
        for sample in [MemoryReading(25000,1,100.0),
                       MemoryReading(25000,16000,100.0),
                       MemoryReading(25000,None,90.0),
                       MemoryReading(25000,None,101.0),
                       MemoryReading(True,None,100.0),
                       MemoryReading(-1,None,100.0),
                       MemoryReading(25000,None,True), None]:
            with self.subTest(sample=sample), self.assertRaises(WorkerProviderError):
                memory_budget_fits(worker(),sample,now=100.5)

    def test_separate_nvidia_memory_path_is_preserved(self):
        p = worker(memory_accounting='separate',memory_vram_mb=6000,
                   inference_vram_mb=500)
        self.assertFalse(memory_budget_fits(p,MemoryReading(25000,5999,99.9),now=100))
        self.assertTrue(memory_budget_fits(p,MemoryReading(25000,6512,99.9),now=100))
        self.assertTrue(memory_budget_fits(p,MemoryReading(6000,1012,99.9),
                                           now=100,already_loaded=True))
        self.assertFalse(memory_budget_fits(p,MemoryReading(6000,1011,99.9),
                                            now=100,already_loaded=True))
        self.assertFalse(memory_budget_fits(p,MemoryReading(25000,None,99.9),now=100))
        lease = object.__new__(ModelLifecycle)
        lease.gpu_index=0;lease.ram_headroom=1024;lease.vram_headroom=512
        calls=[]
        def measure(idx,gpu):
            calls.append(gpu)
            return MemoryReading(25000,6512,time.monotonic())
        lease.measure=measure
        self.assertTrue(lease.fits(p))
        self.assertEqual(calls,[True])

    def test_original_flash_uses_exact_same_uma_budget_policy(self):
        cfg=NativeParentConfig(THREAD,PARENT,URL,'TEST_SECRET',
                               memory_ram_mb=10000,inference_ram_mb=500,
                               inference_vram_mb=0,memory_accounting='shared_uma')
        self.assertEqual(cfg.profile().memory_accounting,'shared_uma')
        with self.assertRaises(InputRejected):
            replace(cfg,memory_vram_mb=100)
        old=replace(cfg,memory_accounting='separate')
        self.assertEqual(old.profile().memory_accounting,'separate')
        parent=object.__new__(UnslothParent)
        parent.profile=cfg.profile()
        calls=[]
        def measure(idx,gpu):
            calls.append(gpu)
            return MemoryReading(4596,None,time.monotonic())
        parent.measure=measure
        parent._budget(loaded=True)
        self.assertEqual(calls,[False])
        parent.measure=lambda *_:MemoryReading(4595,None,time.monotonic())
        with self.assertRaises(WorkerProviderError):
            parent._budget(loaded=True)


class SharedUmaNativeMcpTests(unittest.TestCase):
    """Actual MCP/SQLite/HTTP/job/return boundaries; deterministic model fixture."""
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        project=root/'project'
        project.mkdir()
        host=NativeFixture().start()
        host.capacity=18000
        self.host=host
        self.addCleanup(host.server_close)
        self.addCleanup(host.shutdown)
        env=patch.dict(os.environ,{'MINIONONE_NATIVE_TEST_KEY':'synthetic-key'})
        env.start()
        self.addCleanup(env.stop)
        parent=NativeParentConfig(THREAD,PARENT,host.url,
             'MINIONONE_NATIVE_TEST_KEY',memory_ram_mb=13000,
             inference_ram_mb=500,inference_vram_mb=0,
             memory_accounting='shared_uma',allow_parent_unload=True)
        w=WorkerProfile('amd-worker','unsloth',WORKER,host.url,auto_load=True,
             memory_ram_mb=13500,inference_ram_mb=500,inference_vram_mb=0,
             memory_accounting='shared_uma',
             secret_env='MINIONONE_NATIVE_TEST_KEY',load_timeout_seconds=15)
        token='synthetic-uma-token-'+'z'*40
        m=LocalMCP(('127.0.0.1',0),token=token,database_path=root/'jobs.sqlite',
                   parent_config=parent,worker_profiles={'amd-worker':w},
                   workspace_projects={'demo':project},enable_workspace_jobs=True,
                   min_free_mb=1)
        self.manager=m
        self.addCleanup(m.server_close)
        self.addCleanup(m.shutdown)
        # Direct trusted synthetic loopback transport saves test-only process
        # startup latency. The actual production control transport is untouched.
        def fast_control(control,method,path,payload=None,*,timeout=5,missing=False):
            raw=None if payload is None else json.dumps(payload).encode()
            req=Request(control.root+path,data=raw,
                headers={'Authorization':'Bearer synthetic-key',
                         'Content-Type':'application/json'},method=method)
            try:response=build_opener(ProxyHandler({})).open(req,timeout=timeout)
            except HTTPError as exc:response=exc
            with response:
                data=json.loads(response.read() or b'{}')
                status=response.status
            if status==404 and missing:return None
            if not 200<=status<300:
                raise WorkerProviderError('PROVIDER_REQUEST_REJECTED')
            return data
        patched=patch.object(ControlHTTP,'request',fast_control)
        patched.start()
        self.addCleanup(patched.stop)
        self.gpu_requests=[]
        def measure(idx,need_gpu):
            self.gpu_requests.append(need_gpu)
            if need_gpu:raise AssertionError('NVML requested in UMA mode')
            return host.memory()
        m.model_lifecycle.measure=measure
        m.native_parent.measure=measure
        threading.Thread(target=m.serve_forever,daemon=True).start()
        self.client=PilotClient(m.server_port,token)

    def submit(self,op):
        return self.client.tool('minionone_delegate',
            {'project_id':'demo','task':'Publish synthetic fixture result.',
             'profile_id':'amd-worker','operation_id':op})

    def wait_state(self,job,states):
        end=time.monotonic()+25
        while time.monotonic()<end:
            status=self.client.tool('minionone_job_status',{'job_id':job['job_id']})
            if status['state'] in states:return status
            time.sleep(.04)
        self.fail('synthetic native job did not settle: '+str(status))

    def test_ordered_parent_unload_worker_load_restore_and_same_chat(self):
        job=self.submit('uma-synthetic-positive-001')
        time.sleep(.2)
        self.assertNotIn('unload:'+PARENT,self.host.events)
        self.host.end_turn()
        status=self.wait_state(job,('RESULT_READY','FAILED','NEEDS_RECONCILIATION'))
        self.assertEqual(status['state'],'RESULT_READY',status)
        end=time.monotonic()+25
        while time.monotonic()<end:
            receipt=self.manager.parent_return.status(job['job_id'])
            if receipt['state']=='DELIVERED':break
            time.sleep(.05)
        self.assertEqual(receipt['state'],'DELIVERED',receipt)
        self.assertEqual(self.host.events.count('unload:'+PARENT),1)
        self.assertLess(self.host.events.index('unload:'+WORKER),
                        self.host.events.index('load:'+PARENT))
        self.assertLess(self.host.events.index('load:'+PARENT),
                        self.host.events.index('parent-run'))
        self.assertTrue(self.gpu_requests)
        self.assertTrue(all(flag is False for flag in self.gpu_requests))
        self.assertNotIn('PRIVATE ORIGINAL FLASH HISTORY',str(self.host.requests))
        result=self.client.tool('minionone_workspace_result',{'job_id':job['job_id']})
        ack=self.client.tool('minionone_accept_workspace_result',
            {'job_id':job['job_id'],'expected_version':result['version'],
             'result_digest':result['result_digest'],
             'operation_id':'uma-synthetic-parent-ack-001'})
        self.assertEqual(ack['state'],'COMPLETED')

    def test_insufficient_shared_memory_without_unload_permission_denies_worker(self):
        self.manager.native_parent.allow_unload=False
        job=self.submit('uma-synthetic-deny-001')
        self.host.end_turn()
        status=self.wait_state(job,('FAILED','RESULT_READY','NEEDS_RECONCILIATION'))
        self.assertEqual(status['state'],'FAILED',status)
        self.assertNotIn('unload:'+PARENT,self.host.events)
        self.assertFalse(self.host.requests)
        self.assertTrue(all(flag is False for flag in self.gpu_requests))


if __name__=='__main__':
    unittest.main()
