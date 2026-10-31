# v0.37 – Windows AppContainer: adversarial synthetic access tests

**Implementation class:** Windows CI feasibility, **not** a tested host sandbox
for untrusted worker code. No worker tool profile, command execution or shell
privilege is changed.

## Test scope and method

Unlike v0.36, which verifies `TokenIsAppContainer=1` but does not test
resource denial, this increment runs a **fixed developer-owned native C
test executable** both with the ordinary Windows CI user token and with a
fresh, empty-capabilities AppContainer token.

The ordinary-host **positive control must succeed first**. It checks that
the helper can actually:

- READ a synthetic `manager/manager.db` canary (no actual SQLite/secret data);
- READ a synthetic `sibling/other_job.txt` canary;
- open an existing synthetic immutable `published/immutable.bin` with
  `FILE_WRITE_DATA` access (does **not** actually modify it);
- connect to an actively listening loopback TCP socket.

Only then is the *identical* helper started with
`CreateProcessW(CREATE_SUSPENDED)`, an empty-capabilities
`STARTUPINFOEX` AppContainer SID, and a kill-on-close Job Object with
one process and a 128 MiB memory cap. The child token is independently
checked with `GetTokenInformation(TokenIsAppContainer)` before
`ResumeThread`.

The AppContainer result requires all four access attempts to be
**explicitly denied** (`ERROR_ACCESS_DENIED` / `ERROR_PRIVILEGE_NOT_HELD`
or `WSAEACCES`); missing files, absent network listeners, unexpected Win32
errors, unstarted processes and malformed arguments are **failures**, not
evidence of effective isolation.

The manager checks that all original synthetic canary SHA-256 hashes are
unchanged. The native probe returns a fixed exit-code bitmap rather than
opening a return-file path through the AppContainer. A separate fresh
AppContainer package SID is created and deleted for each run.

### Trusted build and execution

The fixed C helper source is
`tests/native/appcontainer_adversary_v037.c`; Windows CI builds it from
source with Visual C++ x64 and static runtime via
`scripts/build_win32_adversary_v037.py`. This is a **test-only binary**.
MinionOne does not download, install or expose the helper in any worker
tool. Its executable directory receives scoped RX permission for the
fresh package SID; synthetic manager, sibling and output directories
receive **no** AppContainer permission.

Run on a Windows machine with Visual Studio C++ Build Tools:

```powershell
python -m pip install -e .
python scripts/build_win32_adversary_v037.py --out evidence/v037/appcontainer_adversary_v037.exe
$env:MINIONONE_V037_NATIVE = "1"
python -m unittest discover -s tests -p test_win32_appcontainer_adversarial_v037.py -v
```

The dedicated workflow is
`.github/workflows/win32-appcontainer-adversarial-v037.yml` and emits a
small `SYNTHETIC_DENIAL_REPORT.json` report. The executable itself is
not uploaded as an artifact.

## What is still unproven

The positive/negative canaries exercise **one finite probe instance on the
GitHub-hosted Windows CI machine**. They do not prove the full arbitrary
code execution threat model, the operator's actual Windows installation,
post-crash replay correctness, inherited kernel handles, process-group
escape, privileged IPC, registry access, DNS/outbound internet, firewall
policy or controlled output import. Loopback denial is not proof that
every possible network path is blocked.

Before enabling Flint `run_tests`, there must still be an approved tool
protocol, signed/pinned executable selection, per-job security identity,
safe input/scratch/output ACL and data-flow, no credential inheritance,
no arbitrary network, full tree termination, adversarial race tests and
host-specific admission gates. Target release **NO_GO**.

See also: [ADR-034](ADR_034_BOXED_TOOL_EXECUTION.md),
[v0.35 Job Object](WIN32_JOB_OBJECT_PROBE_V035.md),
[v0.36 AppContainer token](WIN32_APPCONTAINER_TOKEN_V036.md).
