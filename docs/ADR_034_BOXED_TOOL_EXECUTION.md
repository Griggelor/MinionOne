# ADR-034: Boxed Worker Tool Runtime (v0.34 – security architecture, no shell grant)

**Decision status:** accepted as an incremental architecture; **OS process runner
NOT IMPLEMENTED and NO_GO for arbitrary code/test execution**.
Date: 2026-10-31. Owner asked for a boxed, narrowly encapsulated tool layer.
The existing manager-broker file tools remain active; new executable tools
must not be activated by a model prompt, merely changing an allowlist, or
publishing a green CI badge.

## Design boundaries

```text
Unsloth Flash (parent, untrusted task text / MCP tool caller)
    |
    v (authenticated, job-scoped, durable manager API)
MinionOne manager  [credential, SQLite control, job state, admission, logs]
    |
    +---- LM Studio/Flint model inference (untrusted text/tool proposals)
    |            |
    |            v
    +---- AgentLoop -> IMMUTABLE manager-selected grant -> scoped broker
    |                                                  |
    |                                    input/ RO, work/ CRUD, output/ publish
    |
    +---- FUTURE isolated tool runner (not implemented)
                 |
           [separate low-privilege OS identity / VM]
                 |
           [temporary scratch and explicitly imported data ONLY]
                 |
           [filtered, hashed outputs returned through manager]
```

The **external LM Studio process itself is not sandboxed by MinionOne** and
continues running under the operator's LM Studio account. Only worker
*tool requests* are intercepted by MinionOne. An OS-separated execution
runner can be built without relocating the model into the sandbox.

## Implemented in v0.34 (not an OS sandbox)

- `boxed_tool_policy.WorkerToolGrant`: immutable manager-selected
  `ISOLATED_TEST` (6 tools) and `WORKSPACE` (11 native file/progress tools).
- Provider advertisement derives from this same registry; `AgentLoop` checks
  the job-specific grant **again** before invoking any broker, counting
  forbidden attempts toward the three-denial limit.
- `BOXED_EXEC`, `run_tests`, `execute_shell`, arbitrary scripts/commands and
  all non-attested execution profiles are NOT implemented or advertised.
- MCP status explicitly reports `worker_tool_boundary=MANAGER_BROKER_FILES_ONLY`,
  `boxed_execution_enabled=false` and `os_sandbox_attested=false`.
- Separate tests use a deliberately permissive fake broker to verify that
  neither an injected tool name nor altered profile can cause execution.

No executable child process, host command, shell, container, dynamic plugin
loader, or unrestricted file permissions are introduced by this increment.

## Target runtime (requires future implementation + explicit separate owner decision)

**Protocol:** Trusted manager creates per-job/attempt immutable authority,
attaches an operation_id and expected input digest, and speaks a bounded
versioned length-delimited IPC protocol with a *separate* OS-confined runner.
Runner refuses unknown tools; manager independently validates operations,
bytes, hashes, output locations and receipts. External model strings NEVER
supply command line, executable name, host mount, token, domain or capability.

**Profiles:** `FILES_ONLY` remains default. A future `BOXED_TEST` profile
can run only operator-approved test programs and fixed argument templates,
from pinned digest-approved binaries, and only when OS confinement is
verified on that specific target host. `BOXED_BUILD` would require separate
approval and stronger isolation. Generic `execute_shell` is not part of the
default public tool surface.

**Isolation:**
- Windows candidate: AppContainer/LPAC or restricted token plus explicit
  NTFS DACL and per-job capability SID; Windows Job Object with memory,
  process and CPU/time budget, kill-on-close and no breakaway. Job Objects
  control resources but alone are **not** a filesystem/network sandbox.
- For high-risk arbitrary program execution, prefer a disposable VM or
  verified hardened container. Windows Sandbox offers VM isolation but
  is interactive/ephemeral, feature/edition dependent, and a mapped writable
  host folder or enabled network leaks authority; do not simply map the
  canonical project `work/` into it.
- Linux candidate: distinct uid/userns, mount namespace, read-only root,
  private tmpfs/cgroup, seccomp/landlock, no inherited FD except IPC;
  choose actual kernel mechanism only after supported-host checks.
- Network **off by default**, no parent credential or OS secret environment,
  no direct access to manager SQLite, LM Studio management endpoint, arbitrary
  `%USERPROFILE%`, input root, publisher files or host projects.
- Inputs imported as validated immutable snapshots; scratch is ephemeral;
  outputs undergo bounded hashing/type verification and manager-only artifact
  publication. stdout/stderr bounded and redacted separately from exact
  model-I/O logs. Do not execute artifacts during import or validation.
- Trust the OS boundary, not only process timeout, Python-level path checks,
  temp directories, string allowlists or prompt text.

**Execution and crash protocol:** One job-attempt lease and journaled
`PREPARED -> RUNNING -> VERIFIED -> ACKED` state; after an unknown
process exit/provider timeout the manager fences the attempt and records
`NEEDS_RECONCILIATION`, never restarts a side-effecting command blindly.
System restart replays durable *observations* only; no new runner execution
without evidence that old process group is quiescent. Test provider/service
termination and hostile concurrent write races separately.

## Release gates (must remain red until measured)

1. Supported Windows host/edition, AppContainer SID/DACL network and
   anti-breakaway tests with a **separate untrusted process**; prove it
   cannot open host secrets, network, other jobs, manager state or outputs.
2. Kill process/runner at each IPC/filesystem/journal commit boundary;
   no false result, silent partial artifact or duplicate side effect.
3. CPU/memory/PID/files/fd/time quotas, stdout/stderr limits, cancellation,
   shutdown, full process-tree termination and profile downgrade tests.
4. Authentic Flint/LM Studio and Flash/Unsloth same-chat return and human
   ACK, on actual target hardware, with only synthetic project data.
5. Explicit owner approval to activate any executable tool and
   verified package installation/rollback plan. **Until then: NO_GO.**

## Microsoft design references

- AppContainer isolation:
  https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation
- AppContainer process/token capabilities:
  https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer
- Job Objects:
  https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- Windows Sandbox configuration:
  https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file

This ADR is a security design and a fail-closed interim implementation,
**not** a statement that Windows AppContainer or a disposable VM is deployed.
