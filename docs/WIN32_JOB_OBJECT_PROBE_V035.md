# v0.35 – Win32 Job Object process lifecycle proof (NOT a sandbox)

**Status:** development / source-only Windows feasibility / product NO_GO.
**No new worker permission**. In particular `execute_shell`, `run_tests`,
user-supplied executable paths, arbitrary code, and scripted command lines
remain unreachable through MinionOne MCP, LM Studio, AgentLoop and
BoxedToolGateway.

## What is actually implemented

`src/minionone/win32_job_object_probe.py` is a trusted
**test-only** Win32 process-control probe, deliberately **not imported or
instantiated by any worker broker**.

- Three immutable probe identifiers (`exit`, `sleep`,
  `no-parent-env`), with fixed code compiled into the source. There is
  no function accepting model text, executable paths, scripts or arguments.
- `CreateJobObjectW`, `SetInformationJobObject` with kill-on-close,
  **256 MiB process memory** and **maximum two active processes**. No
  `BREAKAWAY_OK` flag.
- `CreateProcessW` with `CREATE_SUSPENDED`,
  `CREATE_NO_WINDOW`, `bInheritHandles=FALSE`, a minimal explicit
  Windows system environment, and Python isolated mode (`-I -S`).
- `AssignProcessToJobObject` **before** `ResumeThread`; creation or
  assignment failures return a stable exception without executing any
  unassigned process.
- `WaitForSingleObject` with a short, validated finite timeout and
  `TerminateJobObject` on timeout/failure; close all native handles.
  The Job Object is closed even during unexpected Python exceptions.
- Test evidence is bounded and contains fixed boolean flags:
  `os_sandbox_attested=false`, `worker_tool_exposed=false`;
  no model content, host paths, credentials or free-form child output.

## Execute the real tests

On Windows with installed source package:

```powershell
python -m unittest discover -s tests -p test_win32_job_object_probe_v035.py -v
python scripts/run_win32_job_probe.py --out evidence/win32-job-v035/REPORT.json
```

The dedicated GitHub Actions workflow
`.github/workflows/win32-job-object-v035.yml` executes these same
commands on `windows-latest`. Evidence records three native cases:
normal termination, stripped inherited environment and timeout
termination. A successful job **is not OS security attestation**.

## Important limitations

Job Objects **alone do not restrict host filesystem, registry, network,
credential APIs or access to other project directories**. The fixed
process currently runs with the CI/operator's ordinary Windows token.
Fixed test programs may only establish a *lifecycle property*, not
a protected execution environment. No untrusted executable or shell may
be run in this mode.

The next distinct implementation step is the genuine OS-boundary runner:

1. Create/provision a per-job AppContainer or LPAC identity with no
   network capability; use `STARTUPINFOEX` and
   `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`.
2. Provide narrow explicit DACL access to immutable imported input
   snapshots and a disposable scratch folder. **Never mount the canonical
   project or manager database into the runner.**
3. Verify independently that a separate untrusted process cannot open
   a foreign project, manager database, secret files, network socket
   or sibling worker area; no claiming isolation from policy flags alone.
4. Add a versioned bounded IPC protocol, operation leases, unambiguous
   crash receipts, full process-tree teardown, output import hashing,
   identity/ACL anti-race tests, and target-host gate.
5. Expose any fixed test execution *only after* these gates and an
   explicit owner-approved capability update. All customer release gates
   remain NO_GO.

## References

- [ADR-034 – Boxed Tool Runtime](ADR_034_BOXED_TOOL_EXECUTION.md)
- [Microsoft: Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Microsoft: AssignProcessToJobObject](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
- [Microsoft: Launch an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)
