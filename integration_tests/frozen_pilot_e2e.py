"""Exercise the actual frozen Windows launcher and network child process.

Uses a synthetic loopback model server, never actual target-model claims.
Fails on any unsuccessful stage; subprocess timeouts are failures, not passes.
"""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
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
    model=ThreadingHTTPServer(('127.0.0.1',0),Handler);model.daemon_threads=True;model.calls=0
    threading.Thread(target=model.serve_forever,daemon=True).start()
    process=None
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
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps({'passed':True,'scope':'frozen-executable + synthetic HTTP provider',
            'generation_requests':model.calls,'report':report,'export_verified':True,
            'credential_protection_verified':True,'duplicate_instance_rejected':True},indent=2)+'\n')
    finally:
        if process is not None:
            process.terminate()
            try:process.communicate(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=10)
        model.shutdown();model.server_close()
        temporary.cleanup()
    print('FROZEN_SYSTEM_TEST_PASS')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args();run(args.exe,args.report)
