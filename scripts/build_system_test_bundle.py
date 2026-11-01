"""Create a self-contained Windows app or offline Python system-test ZIP."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
from email.parser import BytesParser

def build(wheel,output,app=None,evidence=None):
    root=Path(__file__).resolve().parents[1]
    wheel=Path(wheel).resolve();output=Path(output).resolve()
    match=re.fullmatch(r'minionone-([0-9][A-Za-z0-9_.]*)-py3-none-any.whl',wheel.name)
    if match is None:raise ValueError('unexpected wheel name')
    version=match[1]
    with zipfile.ZipFile(wheel) as archive:
        metadata=f'minionone-{version}.dist-info/METADATA'
        if metadata not in archive.namelist():raise ValueError('wheel metadata missing')
        package=BytesParser().parsebytes(archive.read(metadata))
        if package.get('Name')!='minionone' or package.get('Version')!=version:raise ValueError('wheel version mismatch')
    with tempfile.TemporaryDirectory(prefix='minionone-bundle-') as tmp:
        stage=Path(tmp)/('MinionOne_SystemTest_'+version);stage.mkdir()
        (stage/'packages').mkdir();shutil.copyfile(wheel,stage/'packages'/wheel.name)
        for name in ('README_DE.md','README_LEGACY_DE.md','bootstrap.py'):
            shutil.copyfile(root/'system_test'/name,stage/name)
        manifest={'schema_version':1,'version':version,'mode':'windows-native' if app else 'python-offline',
                  'wheel':{'filename':wheel.name,'sha256':sha256(wheel.read_bytes()).hexdigest()},
                  'default_test':'synthetic_sealed_workspace',
                  'source_commit':os.environ.get('GITHUB_SHA','local-unattested'),
                  'worker_file_backend':'native_win32_work_sqlite_output' if app else 'native_platform_work_sqlite_output',
                  'legacy_document_pilot_available':True,
                  'host_filesystem_tools_enabled':False,
                  'boxed_execution_enabled':False,'product_release':'NO_GO'}
        (stage/'bundle.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        if app:
            app=Path(app).resolve()
            if not (app/'MinionOne.exe').is_file():raise ValueError('native app missing')
            shutil.copytree(app,stage/'app')
        if evidence:
            proof=json.loads(Path(evidence).read_text())
            if proof.get('passed') is not True:raise ValueError('failed executable test cannot be packaged as passed')
            shutil.copyfile(evidence,stage/'CI_FROZEN_TEST.json')
        # Dedicated state avoids silently reusing the older SQLite pilot DB.
        workspace_state=['--state','"%LOCALAPPDATA%\\MinionOne\\WorkspaceSystemTest"']
        commands={'1_START.cmd':workspace_state+['serve','--synthetic-workspace'],
                  '2_UNSLOTH.cmd':workspace_state+['connection','--show-token'],
                  '3_SYSTEMTEST.cmd':workspace_state+['workspace-self-test'],
                  '4_FACTS.cmd':['facts'],
                  'LEGACY_1_START.cmd':['serve'],
                  'LEGACY_2_UNSLOTH.cmd':['connection','--show-token'],
                  'LEGACY_3_SYSTEMTEST.cmd':['self-test']}
        for name,args in commands.items():
            invoke='"%~dp0app\\MinionOne.exe"' if app else 'py -3.13 "%~dp0bootstrap.py"'
            data='@echo off\r\n'+invoke+' '+' '.join(args)+' %*\r\nset "minionone_exitcode=%ERRORLEVEL%"\r\n'
            if name not in ('1_START.cmd','LEGACY_1_START.cmd'):data+='echo.\r\npause\r\n'
            data+='exit /b %minionone_exitcode%\r\n';(stage/name).write_bytes(data.encode('ascii'))
        files=sorted(p for p in stage.rglob('*') if p.is_file())
        (stage/'SHA256SUMS.txt').write_text(''.join(sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(stage).as_posix()+'\n' for p in files))
        output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for file in sorted(stage.rglob('*')):
                if file.is_file():archive.write(file,file.relative_to(stage.parent))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():raise ValueError('bundle CRC failure')
    return {'filename':output.name,'sha256':sha256(output.read_bytes()).hexdigest(),'bytes':output.stat().st_size,'version':version,'native':bool(app)}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--wheel',required=True);parser.add_argument('--output',required=True);parser.add_argument('--app');parser.add_argument('--evidence')
    args=parser.parse_args();print(json.dumps(build(args.wheel,args.output,args.app,args.evidence),indent=2))
