"""Run deliberately red v0.31 real-work target contracts with honest evidence.

Default/evidence mode exits 0 if the suite executed and evidence was written,
even if target assertions failed. The JSON still says CONTRACT_NOT_MET.
--strict fails when target assertions are red. Neither mode confers G1b/G3 GO.
"""
import argparse
from datetime import datetime,timezone
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE="product_acceptance/test_workfiles_v031_contract.py"
PATTERN=re.compile(r"test_wf(\d{3})_")
EXPECTED={"WF-001","WF-002","WF-003","WF-017","WF-018","WF-019","WF-020","WF-021","WF-022"}


def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main(argv=None):
    parser=argparse.ArgumentParser(description="v0.31 explicit RED work-folder contracts")
    parser.add_argument("--strict",action="store_true",
                        help="return nonzero if ANY nine executable native-work target assertions fail")
    parser.add_argument("--out",type=Path,default=ROOT/"evidence"/"v031-workfiles")
    args=parser.parse_args(argv)

    cases=json.loads((ROOT/"product_acceptance"/"WORKFILES_V031.json").read_text(encoding="utf-8"))
    assert cases["schema"]=="minionone.workfiles.v031"
    recorded={x["id"] for x in cases["cases"]}
    assert len(recorded)==22 and recorded=={"WF-"+f"{n:03d}" for n in range(1,23)}, \
        "v0.31 case registry must contain all 22 unique requirements"

    loader=unittest.TestLoader()
    suite=loader.discover(str(ROOT/"product_acceptance"),pattern="test_workfiles_v031_contract.py")
    discovered={}
    for test in flatten(suite):
        match=PATTERN.search(test.id())
        if not match:
            raise RuntimeError("unexpected contract name "+test.id())
        ident="WF-"+match.group(1)
        if ident in discovered:
            raise RuntimeError("duplicate contract test "+ident)
        discovered[ident]=test.id()
    if set(discovered)!=EXPECTED:
        raise RuntimeError("WF-001..003 and WF-017..022 must have nine concrete running tests")

    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    fail={}
    for test,reason in result.failures+result.errors:
        match=PATTERN.search(test.id())
        if match:
            fail["WF-"+match.group(1)]={"result":"FAIL_TARGET_CONTRACT",
                                        "diagnostic":reason[-1800:]}
    skipped={}
    for test,reason in result.skipped:
        match=PATTERN.search(test.id())
        if match:
            skipped["WF-"+match.group(1)]={"result":"SKIPPED","reason":reason}

    observed=[]
    for c in cases["cases"]:
        ident=c["id"]
        if ident in EXPECTED:
            status=fail.get(ident,skipped.get(ident,{"result":"PASS_SYNTH_HTTP_CONTRACT_NOT_TARGET_HOST"}))
            observed.append({**c,**status,"executed":True})
        else:
            observed.append({**c,"result":"NOT_EXECUTED_AS_NEW_V031_GATE",
                             "executed":False})

    red=sum(c["result"]=="FAIL_TARGET_CONTRACT" for c in observed)
    gaps=sum(c["result"] in ("FAIL_TARGET_CONTRACT","SKIPPED")
             for c in observed if c["id"] in EXPECTED)
    report={
        "schema":"minionone.workfiles.evidence.v031","schema_version":1,
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "commit":os.getenv("GITHUB_SHA","local-tree-unattested"),
        "environment":{"os":platform.platform(),
                       "python":platform.python_version(),
                       "filesystem":"CI temp filesystem; not Windows target-host attestation"},
        "execution":{"command":"python scripts/run_workfiles_v031.py",
                     "test_file":SOURCE,"executed":result.testsRun,
                     "failed":len(result.failures)+len(result.errors),
                     "skipped":len(result.skipped)},
        "contract_result":("CONTRACT_NOT_MET" if gaps else "SYNTH_HTTP_CONTRACT_MET_NOT_TARGET_APPROVED"),
        "customer_product_ready":False,
        "product_release":"NO_GO",
        "gate_caveat":"green evidence collection does NOT mean input/work/output filesystem permissions passed",
        "test_ids":sorted(discovered),
        "cases":observed}
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"WORKFILES_V031_EVIDENCE.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (args.out/"WORKFILES_V031_TEST_RUN.log").write_text(stream.getvalue(),encoding="utf-8")
    rows=["# MinionOne v0.31 – reale work/-Dateien: Test-First-Evidenz",
          "", "Ergebnis: **"+report["contract_result"]+"**",
          "Kunden-/Echtdatenfreigabe: **NO_GO**",
          "Commit: "+report["commit"],"OS: "+report["environment"]["os"],
          "", "| ID | Testergebnis |","|---|---|"]
    rows.extend("| "+c["id"]+" | "+c["result"]+" |" for c in observed)
    rows.extend(["","**Achtung:** Ein grüner CI-Collector bestätigt nur, dass diese",
                 "Evidenz erhoben wurde. Die positiven Work-Datei-Tests sind",
                 "bis zum nachgewiesenen nativen Work-Broker absichtlich rot.",
                 ""])
    (args.out/"WORKFILES_V031_EVIDENCE.md").write_text("\n".join(rows),encoding="utf-8")
    print(json.dumps({"contract_result":report["contract_result"],
                      "executed":result.testsRun,"failed":len(result.failures)+len(result.errors),
                      "product_release":"NO_GO","report":str(args.out/"WORKFILES_V031_EVIDENCE.json")},
                     sort_keys=True))
    if args.strict and (gaps or result.testsRun!=len(EXPECTED)):
        return 2
    return 0


if __name__=="__main__":
    raise SystemExit(main())
