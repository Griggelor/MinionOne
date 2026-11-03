"""Exercise the actual frozen Windows launcher and network child process.

Uses a synthetic loopback model server, never actual target-model claims.
Fails on any unsuccessful stage; subprocess timeouts are failures, not passes.
"""
import argparse
from contextlib import closing
from hashlib import sha256
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import zipfile

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send(self,data):
        body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):self.send({'data':[{'id':'frozen-synthetic','state':'loaded'}]})
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        tools=[m for m in data['messages'] if m.get('role')=='tool'];n=len(tools)
        self.server.calls+=1
        if n==0:name,args='read_file',{'path':'input/sample.txt'}
        elif n==1:
            text=json.loads(tools[0]['content'])['data']['content']
            name,args='create_work',{'name':'result.txt','content':text.upper()}
        elif n==2:name,args='publish_artifact',{'name':'result.txt'}
        else:
            self.send({'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'Published.'}}],
                       'usage':{'total_tokens':12}});return
        self.send({'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[
             {'id':'test-'+str(n),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}}],
             'usage':{'total_tokens':12}})

def run(executable,output):
    executable=Path(executable).resolve();output=Path(output).resolve()
    # Exercise the frozen subprocess-based memory sampler, not only its
    # mocked Python CLI entry point. No model/provider is touched here.
    read_only=subprocess.run(
        [str(executable),'resource-probe','--mode','os-shared'],
        capture_output=True,text=True,timeout=25,check=True)
    memory=json.loads(read_only.stdout)
    assert memory['probe']=='read_only_available_memory',memory
    assert memory['memory_accounting']=='os-shared',memory
    assert type(memory['ram_available_mb']) is int and memory['ram_available_mb']>0,memory
    assert memory['separate_vram_available_mb'] is None,memory
    assert memory['separate_vram_source'] is None,memory
    assert memory['resource_admission_granted'] is False,memory
    assert memory['memory_topology_attested'] is False,memory
    assert memory['provider_model_residency_attested'] is False,memory
    assert memory['release_gate']=='NO_GO',memory
    model=ThreadingHTTPServer(('127.0.0.1',0),Handler);model.daemon_threads=True;model.calls=0
    threading.Thread(target=model.serve_forever,daemon=True).start()
    process=None;cloud_wire=None
    temporary=tempfile.TemporaryDirectory(prefix='MinionOne Frozen Test ')
    try:
        tmp=temporary.name
        state=Path(tmp)/'state with spaces';state.mkdir()
        with closing(socket.socket()) as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        command=[str(executable),'--state',str(state),'serve','--model','frozen-synthetic',
                 '--provider-url',f'http://127.0.0.1:{model.server_port}','--port',str(port),
                 '--min-free-mb','1','--model-call-seconds','15']
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('frozen server exited during startup')
            if (state/'settings.json').exists():break
            time.sleep(.2)
        else:raise RuntimeError('frozen startup deadline elapsed')
        connection=subprocess.run([str(executable),'--state',str(state),'connection','--show-token'],
                                  capture_output=True,text=True,timeout=20,check=True)
        secret=json.loads(connection.stdout)['header_value']
        # Verify the protected credential does not simply store its plaintext.
        assert secret[7:].encode() not in (state/'credential.bin').read_bytes()
        with closing(http.client.HTTPConnection('127.0.0.1',port,timeout=5)) as client:
            client.request('POST','/mcp',json.dumps({'jsonrpc':'2.0','id':1,'method':'ping'}),
                headers={'Authorization':secret,'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
            response=client.getresponse();assert response.status==200;response.read()
        completed=subprocess.run([str(executable),'--state',str(state),'self-test','--timeout','90'],
                                  capture_output=True,text=True,timeout=120,check=True)
        report,_=json.JSONDecoder().raw_decode(completed.stdout)
        assert report['passed'] is True and model.calls==4,report
        result=Path(tmp)/'verified-result.zip'
        subprocess.run([str(executable),'--state',str(state),'export','--job',report['job_id'],'--out',str(result)],
                       capture_output=True,text=True,timeout=20,check=True)
        with zipfile.ZipFile(result) as archive:
            assert archive.testzip() is None
            assert archive.read('output/result.txt')==b'MINIONONE SYSTEMTEST OK'
        # A second server must fail BEFORE recovering/fencing this instance.
        duplicate=subprocess.run(command,capture_output=True,text=True,timeout=20)
        assert duplicate.returncode==2
        assert json.loads(duplicate.stderr)['error']['code']=='PILOT_INSTANCE_BUSY'
        # No real secrets or ephemeral private paths in the report.
        serialized=json.dumps(report)
        assert secret not in serialized and str(state) not in serialized
        process.terminate()
        process.communicate(timeout=10)
        process=None

        # Exercise the actual frozen native file backend too. The former
        # package test covered only the SQLite document pilot.
        workspace_state=Path(tmp)/'workspace state with spaces'
        workspace_state.mkdir()
        profiles=Path(tmp)/'trusted worker profiles.json'
        profiles.write_text(json.dumps({'profiles':[{
            'profile_id':'frozen-worker','provider':'lmstudio','model_id':'frozen-synthetic',
            'base_url':f'http://127.0.0.1:{model.server_port}',
            'timeout_seconds':15}]}),encoding='utf-8')
        command=[str(executable),'--state',str(workspace_state),'serve',
                 '--synthetic-workspace','--model','frozen-synthetic',
                 '--provider-url',f'http://127.0.0.1:{model.server_port}',
                 '--port',str(port),'--min-free-mb','1','--model-call-seconds','15',
                 '--worker-profiles',str(profiles)]
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('frozen workspace server exited during startup')
            if (workspace_state/'settings.json').exists():break
            time.sleep(.2)
        else:raise RuntimeError('frozen workspace startup deadline elapsed')
        workspace_run=subprocess.run([str(executable),'--state',str(workspace_state),
                    'workspace-self-test','--timeout','90'],
                    capture_output=True,text=True,timeout=120,check=True)
        workspace_report,_=json.JSONDecoder().raw_decode(workspace_run.stdout)
        assert workspace_report['passed'] is True,workspace_report
        assert workspace_report['worker_file_backend']=='native_win32_work_sqlite_output'
        assert workspace_report['release_gate']=='NO_GO'
        folder=workspace_state/'synthetic-project'/'.minionone'/'jobs'/workspace_report['job_id']
        assert (folder/'work'/'result.txt').read_bytes()==b'MINIONONE WORKSPACE OK'
        assert (folder/'output'/'result.txt').read_bytes()==b'MINIONONE WORKSPACE OK'
        assert (folder/'output'/'worker-io.jsonl').is_file()
        assert model.calls==8,model.calls
        connection=subprocess.run([str(executable),'--state',str(workspace_state),
                    'connection','--show-token'],capture_output=True,text=True,timeout=20,check=True)
        workspace_secret=json.loads(connection.stdout)['header_value']
        def rpc(name,args):
            with closing(http.client.HTTPConnection('127.0.0.1',port,timeout=15)) as client:
                client.request('POST','/mcp',json.dumps({'jsonrpc':'2.0','id':1,
                    'method':'tools/call','params':{'name':name,'arguments':args}}),
                    headers={'Authorization':workspace_secret,'Content-Type':'application/json',
                             'Accept':'application/json, text/event-stream'})
                response=client.getresponse();assert response.status==200
                result=json.loads(response.read())['result']
                assert not result.get('isError'),result
                return json.loads(result['content'][0]['text'])
        task='Read input/sample.txt and publish its uppercase content as result.txt.'
        profile_job=rpc('minionone_delegate',{'project_id':'demo','profile_id':'frozen-worker',
            'task':task,'files':{'sample.txt':'frozen universal ok'},'operation_id':'frozen-direct-operation-001'})
        def finish_profile(job,expected_calls,operation):
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                status=rpc('minionone_job_status',{'job_id':job['job_id']})
                if status['state'] in ('RESULT_READY','FAILED','NEEDS_RECONCILIATION'):break
                time.sleep(.1)
            assert status['state']=='RESULT_READY',status
            result=rpc('minionone_workspace_result',{'job_id':job['job_id']})
            assert result['files'][0]['text']=='FROZEN UNIVERSAL OK'
            assert rpc('minionone_accept_workspace_result',{'job_id':job['job_id'],
                'expected_version':result['version'],'result_digest':result['result_digest'],
                'operation_id':operation})['state']=='COMPLETED'
            assert model.calls==expected_calls,model.calls
        finish_profile(profile_job,12,'frozen-direct-ack-001')
        prepared=rpc('minionone_prepare_workspace',{'project_id':'demo','operation_id':'frozen-profile-prepare-001'})
        staged=workspace_state/'synthetic-project'/'.minionone'/'jobs'/prepared['job_id']/'input'
        inputs={'TASK.md':task.encode(),'sample.txt':b'frozen universal ok'}
        for name,body in inputs.items():(staged/name).write_bytes(body)
        (staged/'manifest.json').write_text(json.dumps({'files':{n:sha256(b).hexdigest() for n,b in inputs.items()}}))
        sealed=rpc('minionone_seal_workspace',{'job_id':prepared['job_id'],'expected_version':0,
            'operation_id':'frozen-profile-seal-001'})
        profile_file=rpc('minionone_start_workspace_job',{'job_id':prepared['job_id'],
            'expected_version':sealed['version'],'operation_id':'frozen-profile-start-001','profile_id':'frozen-worker'})
        finish_profile(profile_file,16,'frozen-profile-file-ack-001')
        assert secret not in json.dumps(workspace_report)
        assert str(workspace_state) not in json.dumps(workspace_report)
        process.terminate();process.communicate(timeout=10);process=None
        from native_fixture import NativeFixture,PARENT,WORKER,THREAD,question_worker_reply
        native=NativeFixture().start();native.capacity=6000;native.worker_reply=question_worker_reply
        try:
            native_state=Path(tmp)/'native lifecycle state';native_state.mkdir()
            native_profiles=Path(tmp)/'native worker profiles.json'
            native_profiles.write_text(json.dumps({'profiles':[{'profile_id':'managed-worker','provider':'lmstudio',
                'model_id':WORKER,'base_url':native.url,'auto_load':True,'memory_ram_mb':1,'inference_ram_mb':0,'inference_vram_mb':0,'load_timeout_seconds':15}]}))
            parent_config=Path(tmp)/'native parent.json'
            parent_config.write_text(json.dumps({'thread_id':THREAD,'model_id':PARENT,'base_url':native.url,
                'secret_env':'MINIONONE_FROZEN_PARENT_KEY','memory_ram_mb':1,'inference_ram_mb':0,'inference_vram_mb':0,'allow_parent_unload':True}))
            command=[str(executable),'--state',str(native_state),'serve','--synthetic-workspace',
                '--port',str(port),'--min-free-mb','1','--model-call-seconds','15',
                '--worker-profiles',str(native_profiles),'--parent-host',str(parent_config)]
            env=dict(os.environ,MINIONONE_FROZEN_PARENT_KEY='synthetic-key')
            process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('native frozen server exited during startup')
                if (native_state/'settings.json').exists():break
                time.sleep(.2)
            else:raise RuntimeError('native startup deadline')
            connection=subprocess.run([str(executable),'--state',str(native_state),'connection','--show-token'],
                capture_output=True,text=True,timeout=20,check=True)
            workspace_secret=json.loads(connection.stdout)['header_value']
            job=rpc('minionone_delegate',{'project_id':'demo','profile_id':'managed-worker',
                'task':'Publish the explicit synthetic task result.','operation_id':'frozen-native-operation-001'})
            native.end_turn()
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                status=rpc('minionone_job_status',{'job_id':job['job_id']})
                if status['state'] in ('FAILED','NEEDS_RECONCILIATION'):raise AssertionError(status)
                if status['state']=='WAITING_FOR_PARENT' and status['parent_return']['state']=='DELIVERED':break
                time.sleep(.2)
            else:raise RuntimeError('native worker question deadline')
            work_path=native_state/'synthetic-project'/'.minionone'/'jobs'/job['job_id']/'work'/'result.txt'
            paused_work_digest=sha256(work_path.read_bytes()).hexdigest()
            native.begin_parent_turn()
            answer_args={'job_id':job['job_id'],'expected_version':status['version'],
                'question_digest':status['workspace_job']['question_digest'],
                'answer':'Publish the existing result.','operation_id':'frozen-native-answer-001'}
            accepted=rpc('minionone_answer_question',answer_args)
            assert rpc('minionone_answer_question',answer_args)==accepted
            native.end_turn()
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                status=rpc('minionone_job_status',{'job_id':job['job_id']})
                if status['state'] in ('FAILED','NEEDS_RECONCILIATION'):raise AssertionError(status)
                if status['state']=='RESULT_READY' and status['parent_return']['state']=='DELIVERED':break
                time.sleep(.2)
            else:raise RuntimeError('native automatic return deadline')
            value=rpc('minionone_workspace_result',{'job_id':job['job_id']})
            assert value['files'][0]['text']=='NATIVE LIFECYCLE RESULT'
            assert rpc('minionone_accept_workspace_result',{'job_id':job['job_id'],'expected_version':value['version'],
                'result_digest':value['result_digest'],'operation_id':'frozen-native-ack-001'})['state']=='COMPLETED'
            assert native.events.count('parent-run')==2 and len(native.requests)==4
            assert status['workspace_job']['attempt_epoch']==2 and status['workspace_job']['tokens_used']==48
            assert sha256(work_path.read_bytes()).hexdigest()==paused_work_digest
            assert 'lm-load' in native.events and 'lm-unload' in native.events
            assert 'PRIVATE ORIGINAL FLASH HISTORY' not in json.dumps(native.requests)
            process.terminate();process.communicate(timeout=10);process=None
        finally:
            if process is not None:
                process.terminate();process.communicate(timeout=10);process=None
            native.shutdown();native.server_close()
        # Real frozen control-plane: pre-send gates and an ambiguous TLS failure.
        # Accept and close a loopback socket during TLS; never run a cloud model.
        class DropTLS(socketserver.BaseRequestHandler):
            def handle(self):
                self.server.connections+=1
                self.request.recv(4096)
        cloud_wire=socketserver.ThreadingTCPServer(('127.0.0.1',0),DropTLS)
        cloud_wire.daemon_threads=True;cloud_wire.connections=0
        threading.Thread(target=cloud_wire.serve_forever,daemon=True).start()
        cloud_state=Path(tmp)/'cloud gate state';cloud_state.mkdir()
        cloud_profiles=Path(tmp)/'cloud gate profiles.json'
        cloud_policy={'contract':'text_tokens_v1','data_class':'synthetic',
            'input_token_ceiling':262144,'input_price_microusd_per_million':1000000,
            'output_price_microusd_per_million':1000000,'max_total_cost_microusd':1000000}
        profiles=[{
            'profile_id':name,'provider':'cloud','model_id':'synthetic-model',
            'base_url':'https://synthetic.invalid/v1','secret_env':'MINIONONE_FROZEN_CLOUD_KEY',
            'cloud_approved':True,'cloud_policy':dict(cloud_policy,max_job_cost_microusd=limit)
        } for name,limit in (('private',1000000),('budget',1))]
        reserve=262144+2048
        profiles.append(dict(profiles[0],profile_id='uncertain',
            base_url=f'https://127.0.0.1:{cloud_wire.server_address[1]}/v1',
            cloud_policy=dict(cloud_policy,max_job_cost_microusd=reserve,max_total_cost_microusd=reserve)))
        cloud_profiles.write_text(json.dumps({'profiles':profiles}))
        command=[str(executable),'--state',str(cloud_state),'serve','--synthetic-workspace',
            '--port',str(port),'--min-free-mb','1','--worker-profiles',str(cloud_profiles)]
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env=dict(os.environ,MINIONONE_FROZEN_CLOUD_KEY='frozen-synthetic-cloud-key'))
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('cloud gate frozen server exited')
            if (cloud_state/'settings.json').exists():break
            time.sleep(.2)
        else:raise RuntimeError('cloud gate startup deadline')
        connection=subprocess.run([str(executable),'--state',str(cloud_state),'connection','--show-token'],
            capture_output=True,text=True,timeout=20,check=True)
        workspace_secret=json.loads(connection.stdout)['header_value']
        for name,task,code in (('private','Contact private@example.invalid.','CLOUD_EGRESS_BLOCKED'),
                               ('budget','Review the synthetic material.','CLOUD_BUDGET_EXHAUSTED')):
            job=rpc('minionone_delegate',{'project_id':'demo','profile_id':name,'allow_cloud':True,
                'task':task,'operation_id':'frozen-cloud-'+name})
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                blocked=rpc('minionone_job_status',{'job_id':job['job_id']})
                if blocked['state'] in ('FAILED','NEEDS_RECONCILIATION'):break
                time.sleep(.2)
            else:raise RuntimeError('cloud gate rejection deadline')
            assert blocked['state']=='FAILED' and blocked['workspace_job']['error']['code']==code,blocked
            assert blocked['workspace_job']['cloud_budget']['requests_reserved']==0
        job=rpc('minionone_delegate',{'project_id':'demo','profile_id':'uncertain','allow_cloud':True,
            'task':'Review the synthetic material.','operation_id':'frozen-cloud-uncertain'})
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            uncertain=rpc('minionone_job_status',{'job_id':job['job_id']})
            if uncertain['state'] in ('FAILED','NEEDS_RECONCILIATION'):break
            time.sleep(.2)
        else:raise RuntimeError('uncertain cloud transport deadline')
        assert uncertain['state']=='NEEDS_RECONCILIATION',uncertain
        assert uncertain['workspace_job']['cloud_budget']['reserved_upper_bound_microusd']==reserve
        assert cloud_wire.connections==1
        recovery=[str(executable),'--state',str(cloud_state),'resolve-cloud','--job',job['job_id'],
            '--confirm-provider-stopped','--confirm-work-inspected','--expected-version',str(uncertain['version'])]
        busy=subprocess.run(recovery,capture_output=True,text=True,timeout=20)
        assert busy.returncode!=0 and 'PILOT_INSTANCE_BUSY' in busy.stderr
        project=Path(json.loads((cloud_state/'settings.json').read_text())['workspace_projects']['demo'])
        folder=project/'.minionone'/'jobs'/job['job_id']
        before={p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        process.terminate();process.communicate(timeout=10);process=None
        resolved=subprocess.run(recovery,capture_output=True,text=True,timeout=30,check=True)
        receipt=json.loads(resolved.stdout)
        assert receipt['outcome']=='FAILED_NO_RETRY' and receipt['state']=='FAILED',receipt
        assert receipt['cloud_budget']['reserved_upper_bound_microusd']==reserve
        again=subprocess.run(recovery,capture_output=True,text=True,timeout=30,check=True)
        assert json.loads(again.stdout)==receipt
        assert before=={p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
            env=dict(os.environ,MINIONONE_FROZEN_CLOUD_KEY='frozen-synthetic-cloud-key'))
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('recovered frozen server exited')
            try:
                old=rpc('minionone_job_status',{'job_id':job['job_id']})
                break
            except (OSError,http.client.HTTPException):time.sleep(.2)
        else:raise RuntimeError('recovered frozen startup deadline')
        assert old['state']=='FAILED',old
        next_job=rpc('minionone_delegate',{'project_id':'demo','profile_id':'uncertain','allow_cloud':True,
            'task':'Review the synthetic material.','operation_id':'frozen-cloud-after-resolution'})
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            blocked=rpc('minionone_job_status',{'job_id':next_job['job_id']})
            if blocked['state'] in ('FAILED','NEEDS_RECONCILIATION'):break
            time.sleep(.2)
        else:raise RuntimeError('recovered cloud budget deadline')
        assert blocked['state']=='FAILED' and blocked['workspace_job']['error']['code']=='CLOUD_BUDGET_EXHAUSTED',blocked
        assert cloud_wire.connections==1  # Neither recovery nor restart repeats transport.
        process.terminate();process.communicate(timeout=10);process=None
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'passed':True,'scope':'frozen-executable + synthetic HTTP provider',
            'generation_requests':model.calls,'report':report,'export_verified':True,
            'credential_protection_verified':True,'duplicate_instance_rejected':True,
            'workspace_report':workspace_report,'native_work_verified':True,
            'profiled_handoffs_verified':True,'native_parent_return_verified':True,
            'automatic_model_lifecycle_verified':True,'question_resume_verified':True,
            'cumulative_worker_budget_verified':True,'cloud_egress_gate_verified':True,'cloud_budget_gate_verified':True,
            'cloud_operator_recovery_verified':True,'cloud_recovery_no_retry_verified':True,
            'cloud_recovery_budget_preserved':True,'cloud_uncertain_tls_connections':cloud_wire.connections,
            'cloud_scope':'pre-send rejection and loopback TLS disconnect; no real cloud billing or model','native_worker_requests':len(native.requests),
            'native_parent_runs':native.events.count('parent-run'),'native_scope':'synthetic HTTP host contract, not real Studio/GPU'},indent=2)+'\n')
    finally:
        if process is not None:
            process.terminate()
            try:process.communicate(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=10)
        if cloud_wire is not None:cloud_wire.shutdown();cloud_wire.server_close()
        model.shutdown();model.server_close()
        temporary.cleanup()
    print('FROZEN_SYSTEM_TEST_PASS')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args();run(args.exe,args.report)
