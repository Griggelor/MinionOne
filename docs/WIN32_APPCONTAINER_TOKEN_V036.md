# v0.36 – Real AppContainer child-token start (Windows-only feasibility)

**This is an actual Windows token/process probe; it is not a released worker
sandbox.** The native Windows host test must pass before any status is claimed.

The source-only function
`minionone.win32_appcontainer_probe.run_fixed_appcontainer_token_probe()`
accepts **no arguments**, does not accept worker/model command text and is
not exposed through MinionOne's MCP/AgentLoop/BoxedToolGateway tool surface.

## What the code performs

1. Provision a fresh per-run, per-user AppContainer profile using
   `CreateAppContainerProfile`; no capabilities (including network)
   are declared.
2. Build a `STARTUPINFOEX` attribute list with
   `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` and that AppContainer SID.
3. Start the fixed OS-owned `whoami.exe` diagnostic from the trusted
   Windows directory **suspended**, with no handle inheritance and a
   minimal explicitly constructed environment.
4. Attach it to a Windows Job Object with a single-process limit,
   128 MiB memory limit and kill-on-close, **before resume**.
5. Before any child instruction runs, query its **real child token** via
   `OpenProcessToken` and
   `GetTokenInformation(TokenIsAppContainer)`.
   Require that the kernel reports AppContainer.
6. Resume only when the token and Job Object checks have succeeded; wait
   for bounded completion, terminate on failure, close every handle and
   delete the transient profile.

There is no Shell, no external stdout or file capture, no worker-supplied
binary, no project-root mount or untrusted code execution.

```powershell
python -m unittest discover -s tests -p test_win32_appcontainer_probe_v036.py -v
```

GitHub Actions workflow: `.github/workflows/win32-appcontainer-v036.yml`,
with an evidence artifact whose `host_access_attested` is **false**.

## What this does NOT prove

An AppContainer token is a real security primitive, but this narrow probe
does **not** prove that arbitrary untrusted code cannot access sensitive
file locations, network, manager state or sibling jobs. In particular:

- no adversarial child attempts to open a secret file, make a network
  request, read another job, modify output, or write outside temporary work;
- no import/output IPC contract or per-job DACL/restricted scratch ACL;
- no process-tree escape and deliberate crash-site fault injection;
- no end-user Windows host/environment qualification or real Unsloth test.

Those are mandatory next tests. Until complete and verified,
`worker_tool_exposed=false`, `os_sandbox_attested=false` and release
**NO_GO** remain unchanged. Job Objects alone are resource fences, not
host-access isolation. The OS token check does not change the current worker
rights in any way.

Related: [v0.35 process lifecycle](WIN32_JOB_OBJECT_PROBE_V035.md);
[ADR-034 boxed tool architecture](ADR_034_BOXED_TOOL_EXECUTION.md);
[Microsoft AppContainer launch](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer).
