# v0.39: Real Windows fixed-child AppContainer IPC probe (PRE-ALPHA)

## Scope

v0.38 provided a versioned 2048-byte frame contract and manager-only durable
no-replay receipts, **without an actual OS transport**. v0.39 joins that
contract to a **real separately launched Windows AppContainer child**, but
only for the fixed, trusted, developer-owned synthetic PROBE_PING helper.

This is NOT a general-purpose sandbox, worker API, shell, test runner,
host attestation, target-machine approval or production release.

## Concrete path

1. A fixed MSVC source test helper is compiled by Windows CI. The probe
   resolves only its expected source-checkout binary and copies it to a
   fresh disposable runnable directory. No model-controlled executable or
   command-line argument is accepted.
2. Manager creates a fresh AppContainer profile (zero capabilities), a
   two-pipe stdio channel, and a Job Object with kill-on-close,
   single-process and 128 MiB memory limits.
3. **Only the child pipe read and write handles** are explicitly passed
   using PROC_THREAD_ATTRIBUTE_HANDLE_LIST together with
   PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES. The other pipe ends are
   made noninheritable. There is no localhost listener and no inherited
   manager database handle or environment.
4. Manager starts child suspended, assigns it to the Job Object, verifies
   the actual child token's TokenIsAppContainer bit, and commits the
   v0.38 IN_FLIGHT dispatch fence **before ResumeThread**.
5. Manager writes one bounded PROBE_PING request to child stdin.
   Fixed native child returns a canonical, bounded PONG frame over stdout.
   Parent waits a maximum of four seconds, checks exit code, reads
   bounded reply bytes through actual OS pipes, validates the exact
   job/attempt/operation/nonce/hash fields and durably records
   RESPONSE_OBSERVED.
6. On timeout, process failure, invalid frame or crash, no result is
   accepted and IN_FLIGHT remains uncertain or invalid responses are
   quarantined by the existing journal. No automatic replay of a
   side-effect-ambiguous attempt occurs.

The helper does not receive access to manager state, project input,
work or output directories. stdout/stderr share the single bounded
reply pipe; any unexpected bytes invalidate the frame. No worker tools
are introduced. The v0.38 2 KiB limit remains unchanged.

## Native evidence

- Dedicated Windows CI compiles tests/native/boxed_ipc_child_v039.c with
  the installed MSVC x64 toolchain; no binary is committed.
- tests/test_win32_boxed_ipc_v039.py confirms child token, pre-resume
  Job attachment, exact real-pipe reply, durable observed state, and
  the false security/product status flags.
- Linux CI confirms no Windows native probe can be claimed on Linux.
- Existing v0.38 hostile-frame, crash-no-replay, and concurrent-dispatch
  tests run unchanged on both platforms.
- JSON proof artifact IPC_RESULT.json is produced with exact CI SHA.

**A test run is evidence only if the actual CI check passed at that commit.**

## Explicit limitations and next gates

This implementation uses *manager-created anonymous handles, not a
DACL-authenticated named pipe*. The handle list constrains inherited
handles to the exact spawned process. It does not prove general
arbitrary-code safety or that every alternative handle acquisition,
Windows network path, registry path, process-tree escape, target-host
access or external manipulation is blocked.

Next: hostile child/process-tree negative tests, no-network and
cross-job/credential denial, process kill at journal/pipe boundaries,
per-job scratch ACL, verified input import and output publication,
target-machine G1b security checks. A production-capable coding/test
tool requires a separate explicit authorization after those gates.

**worker_tool_exposed=false, boxed_execution_enabled=false,
os_sandbox_attested=false, product NO_GO.**
