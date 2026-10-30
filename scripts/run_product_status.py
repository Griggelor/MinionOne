"""Evidence-aware product acceptance status: green pilot != product readiness."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import re
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]


def _all_tests(suite):
    for obj in suite:
        if isinstance(obj,unittest.TestSuite):
            yield from _all_tests(obj)
        else:
            yield obj


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--strict',action='store_true',help='exit nonzero while any product gate remains open')
    parser.add_argument('--out',default='evidence/product-status')
    args=parser.parse_args()
    out=ROOT/args.out
    out.mkdir(parents=True,exist_ok=True)
    spec=json.loads((ROOT/'product_acceptance/TARGET_CASES.json').read_text(encoding='utf-8'))
    cases=spec['cases']
    targets={case['id'] for case in cases}
    if len(cases)!=182 or targets != {'T-'+f'{n:03d}' for n in range(1,183)}:
        raise RuntimeError('target catalog incomplete or duplicated')
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_workspace_preparation.py')
    tested={re.search(r'test_t(\d{3})_',x.id()).group(1) for x in _all_tests(suite) if re.search(r'test_t(\d{3})_',x.id())}
    if tested != {f'{n:03d}' for n in range(170,183)}:
        raise RuntimeError('mapped executable tests missing')
    output=io.StringIO()
    result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite)
    failed={}
    for test,_ in result.failures+result.errors:
        match=re.search(r'test_t(\d{3})_',test.id())
        if match:failed['T-'+match.group(1)]='FAIL_LOCAL'
    skipped={}
    for test,_ in result.skipped:
        match=re.search(r'test_t(\d{3})_',test.id())
        if match:skipped['T-'+match.group(1)]='SKIPPED_LOCAL'
    for case in cases:
        if int(case['id'].split('-')[1])>=170:
            case['verification']=failed.get(case['id'],skipped.get(case['id'],'PASS_LOCAL_ONLY'))
            case['evidence']='tests/test_workspace_preparation.py (local Linux MCP/SQLite execution)'
    # Independent new security/integrity tests are not target-hardware acceptance.
    seal_suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_workspace_sealing.py')
    seal_output=io.StringIO()
    seal_result=unittest.TextTestRunner(stream=seal_output,verbosity=2).run(seal_suite)
    (out/'LOCAL_SEAL_SECURITY_TESTS.log').write_text(seal_output.getvalue(),encoding='utf-8')
    publish_suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_workspace_publication.py')
    publish_output=io.StringIO()
    publish_result=unittest.TextTestRunner(stream=publish_output,verbosity=2).run(publish_suite)
    (out/'LOCAL_PUBLICATION_SECURITY_TESTS.log').write_text(publish_output.getvalue(),encoding='utf-8')
    native_suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_win32_output_publication.py')
    native_output=io.StringIO()
    native_result=unittest.TextTestRunner(stream=native_output,verbosity=2).run(native_suite)
    (out/'WINDOWS_MANAGER_OUTPUT_TESTS.log').write_text(native_output.getvalue(),encoding='utf-8')
    winseal_suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_win32_workspace_sealing.py')
    winseal_output=io.StringIO()
    winseal_result=unittest.TextTestRunner(stream=winseal_output,verbosity=2).run(winseal_suite)
    (out/'WIN32_INPUT_SEAL_ADMISSION_TESTS.log').write_text(winseal_output.getvalue(),encoding='utf-8')
    # Run RED target contract tests separately. These tests must not be hidden
    # by the green pilot suite or marked successful without execution evidence.
    target_suite=unittest.defaultTestLoader.discover(str(ROOT/'product_acceptance'),
                                                     pattern='test_target_product_e2e.py')
    target_output=io.StringIO()
    target_result=unittest.TextTestRunner(stream=target_output,verbosity=2).run(target_suite)
    target_failed={}
    for test,_ in target_result.failures+target_result.errors:
        m=re.search(r'test_t(\d{3})_',test.id())
        if m:target_failed['T-'+m.group(1)]='FAIL_TARGET_CONTRACT'
    target_skipped={}
    for test,_ in target_result.skipped:
        m=re.search(r'test_t(\d{3})_',test.id())
        if m:target_skipped['T-'+m.group(1)]='SKIPPED_TARGET_CONTRACT'
    expected_target={'T-082','T-102','T-163'}
    if {('T-'+m.group(1)) for t in _all_tests(unittest.defaultTestLoader.discover(
          str(ROOT/'product_acceptance'),pattern='test_target_product_e2e.py'))
          if (m:=re.search(r'test_t(\d{3})_',t.id()))} != expected_target:
        raise RuntimeError('target contract test mapping incomplete')
    for case in cases:
        if case['id'] in expected_target:
            case['verification']=target_failed.get(case['id'],target_skipped.get(case['id'],
                                 'PASS_LOCAL_CONTRACT_NOT_TARGET_E2E'))
            case['evidence']='product_acceptance/test_target_product_e2e.py (local actual MCP, no Windows attestation)'
    counts={}
    for case in cases:counts[case['verification']]=counts.get(case['verification'],0)+1
    product_pass=all(c['verification']=='PASS_TARGET_E2E' for c in cases)
    report={'schema_version':1,'generated_utc':datetime.now(timezone.utc).isoformat(),
      'version':'0.25.0.dev0 (native Windows seal + output proofs; worker BLOCKED)',
      'baseline':'v0.24.0.dev0 (GitHub main baseline, PR #39)',
      'environment':{'os':platform.system(),'python':platform.python_version()},
      'execution':{'command':'PYTHONPATH=src python scripts/run_product_status.py',
        'tests_run':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
        'failed':len(result.failures)+len(result.errors),'skipped':len(result.skipped),
        'type':'local MCP HTTP / SQLite; not target Windows/Flint/Unsloth'},
      'target_contract_execution':{'tests_run':target_result.testsRun,'passed':target_result.testsRun-len(target_result.failures)-len(target_result.errors)-len(target_result.skipped),
                                   'failed':len(target_result.failures)+len(target_result.errors),
                                   'skipped':len(target_result.skipped)},
      'seal_security_execution':{'tests_run':seal_result.testsRun,'passed':seal_result.testsRun-len(seal_result.failures)-len(seal_result.errors)-len(seal_result.skipped),
                                   'failed':len(seal_result.failures)+len(seal_result.errors),
                                   'skipped':len(seal_result.skipped),
                                   'type':'local POSIX descriptor-relative; Windows not verified'},
      'publication_security_execution':{'tests_run':publish_result.testsRun,
            'passed':publish_result.testsRun-len(publish_result.failures)-len(publish_result.errors)-len(publish_result.skipped),
            'failed':len(publish_result.failures)+len(publish_result.errors),
            'skipped':len(publish_result.skipped),
            'type':'manager-only POSIX staged output publication; Windows worker not enabled'},
      'native_windows_publisher_execution':{'tests_run':native_result.testsRun,
         'passed':native_result.testsRun-len(native_result.failures)-len(native_result.errors)-len(native_result.skipped),
         'failed':len(native_result.failures)+len(native_result.errors),
         'skipped':len(native_result.skipped),
         'type':'native Windows manager-only output proof; synthetic ADMITTED rows, NOT E2E'},
      'native_win32_input_seal_execution':{'tests_run':winseal_result.testsRun,
         'passed':winseal_result.testsRun-len(winseal_result.failures)-len(winseal_result.errors)-len(winseal_result.skipped),
         'failed':len(winseal_result.failures)+len(winseal_result.errors),
         'skipped':len(winseal_result.skipped),
         'type':'native Windows pinned input, admission, MCP contract; not target host G1b'},
      'catalog':{'cases_total':len(cases),'counts':counts,'known_baseline_test_ids':169,
                 'historical_undefined_test_ids':0},
      'release_gate':{'product_ready':product_pass,'reason':('ALL_TARGET_GATES_VERIFIED' if product_pass else
          'REQUIRED_PRODUCT_E2E_EVIDENCE_MISSING')},'cases':cases}
    (out/'PRODUCT_STATUS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (out/'LOCAL_TEST_RUN.log').write_text(output.getvalue(),encoding='utf-8')
    (out/'TARGET_CONTRACT_RED_RUN.log').write_text(target_output.getvalue(),encoding='utf-8')
    md=['# MinionOne product acceptance status','',f"Generated: {report['generated_utc']}", '',
      f"**Product release ready: {'YES' if product_pass else 'NO'}**",'',
      f"Target catalog: {len(cases)} IDs (169 source-defined original cases + 13 new cases).",
      f"Original 13 workspace tests: **{report['execution']['passed']} passed, {report['execution']['failed']} failed, {report['execution']['skipped']} skipped**.",
      f"New local input-seal/security tests: **{report['seal_security_execution']['passed']} passed, {report['seal_security_execution']['failed']} failed, {report['seal_security_execution']['skipped']} skipped**.",
      f"Target MCP contract (local only): **{report['target_contract_execution']['passed']} passed, {report['target_contract_execution']['failed']} failed**.",
      f"New manager publication tests: **{report['publication_security_execution']['passed']} passed, {report['publication_security_execution']['failed']} failed, {report['publication_security_execution']['skipped']} skipped** (NOT target product approval).",
      f"Native Windows manager-only publisher tests: **{report['native_windows_publisher_execution']['passed']} passed, {report['native_windows_publisher_execution']['failed']} failed, {report['native_windows_publisher_execution']['skipped']} skipped** (NOT target-host acceptance).",
      f"Native Windows seal/admission: **{report['native_win32_input_seal_execution']['passed']} passed, {report['native_win32_input_seal_execution']['failed']} failed, {report['native_win32_input_seal_execution']['skipped']} skipped** (runner evidence only).",
      '', '| Verification | Cases |', '|---|---:|']
    for status,n in sorted(counts.items()):md.append(f'| `{status}` | {n} |')
    md+=['','**Crucial:** `PASS_LOCAL_ONLY` does NOT count as production/target acceptance.',
      '`NOT_VERIFIED` means no target-product evidence linked.',
      '`FAIL_TARGET_CONTRACT` records an actual failing desired-product test at the production MCP boundary.',
      'An older passing pilot suite does not close these target gates.','',
      '## Open product milestones','',
      '- Win32 manager seal implemented; target-host G1b and immutable Worker read broker still BLOCKED.',
      '- Admission and selected model bound to the same durable job_id.',
      '- Secure native Windows NTFS handle-based broker; adversarial host tests.',
      '- Real Flint tool execution through an activated Windows broker, verified result publication and parent ACK.',
      '- Parent Unsloth ownership, polling/wakeup verification, ACK and crash/restart E2E.',
      '', 'Exact per-case details: `PRODUCT_STATUS.json`.','']
    (out/'PRODUCT_STATUS.md').write_text('\n'.join(md),encoding='utf-8')
    print(f"PRODUCT_READY={product_pass} | CASES={len(cases)} | {counts} | WORKSPACE_LOCAL={report['execution']['passed']}/{result.testsRun} | SEAL_LOCAL={report['seal_security_execution']['passed']}/{seal_result.testsRun}")
    if (result.failures or result.errors or seal_result.failures or seal_result.errors or
            publish_result.failures or publish_result.errors or
            native_result.failures or native_result.errors or
            winseal_result.failures or winseal_result.errors):return 1
    # Target failures are expected during development, but strict mode fails.
    if args.strict and not product_pass:return 2
    return 0

if __name__=='__main__':sys.exit(main())
