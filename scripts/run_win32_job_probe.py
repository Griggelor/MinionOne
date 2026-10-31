"""Emit bounded, redacted native Windows Job Object lifecycle probe evidence."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from minionone.win32_job_object_probe import run_fixed_job_probe


def main(argv=None):
    p = argparse.ArgumentParser(description="Win32 lifecycle probe; no worker execution")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    if sys.platform != "win32":
        p.error("native Windows required")
    scenarios = (
        ("exit", 4000, False, 0),
        ("no-parent-env", 4000, False, 0),
        ("sleep", 100, True, None),
    )
    results = []
    for mode, deadline, timed_out, code in scenarios:
        proof = run_fixed_job_probe(mode, timeout_ms=deadline)
        if (proof.timed_out != timed_out or proof.exit_code != code or
                proof.job_attached_before_resume is not True or
                proof.worker_tool_exposed is not False or
                proof.os_sandbox_attested is not False):
            raise RuntimeError("native lifecycle contract not met")
        results.append(asdict(proof))
    evidence = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "test_kind": "WINDOWS_JOB_OBJECT_PROCESS_LIFECYCLE_ONLY",
        "product_release": "NO_GO",
        "runner_attested": False,
        "appcontainer_attested": False,
        "worker_execution_enabled": False,
        "cases": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True),
                        encoding="utf-8")
    print("WIN32_JOB_OBJECT_LIFECYCLE_TESTED_ONLY; CASES=3; "
          "OS_SANDBOX=NOT_ATTESTED; PRODUCT_GO=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
