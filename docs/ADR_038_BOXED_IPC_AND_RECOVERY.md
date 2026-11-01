# ADR-038: bounded boxed-runner IPC and durable attempt journal (v0.38)

**Status:** manager-only protocol foundation implemented. **No OS child IPC
transport, untrusted code execution, worker-facing command, AppContainer
data bridge, or product/target-host attestation has been delivered.**

## Rationale

v0.34 enforces immutable tool grants in the manager. v0.35–v0.37 proved
native Windows process lifecycle, an actual AppContainer child token and
three synthetic out-of-scope filesystem access denials. Loopback failed
to connect but timed out; network-denial attestation remained **false**.

The next missing foundation is a protocol and crash boundary that cannot
turn an untrusted model result or uncertain child process into accepted
output or duplicate execution.

## v0.38 code

- `src/minionone/boxed_ipc_v038.py`: schema version 1, big-endian
  4-byte length header, **2048-byte absolute JSON body maximum**, canonical
  ASCII JSON and exact top-level keys. No duplicate JSON keys, trailing
  bytes, multiple frames, nonfinite data or unbounded stream allocation.
- Only `PROBE_PING` is supported. There is **no** `execute_shell`,
  `run_tests`, system path, model prompt, arbitrary tool name, user code,
  binary, stdout, stderr or file content in either message.
- Manager-generated immutable `ProbeGrant` binds a lower-case hexadecimal
  `JOB-...`, `ATT-...`, `OP-...` and cryptographically generated
  per-attempt nonce. A peer-supplied response is rejected unless all
  identities match the current trusted grant and the fixed observation
  digest is exact. This **does not prove** which OS process produced it.
- `FrameReader` validates the length *before* accepting an entire message;
  a truncated transport raises uncertainty rather than producing an empty
  success or retry authorization.
- `src/minionone/boxed_ipc_journal_v038.py`: SQLite WAL with
  `synchronous=FULL`, a unique operation ID and nonce, persisted request
  digest, exact repeat observation digest, and manager-owned states:

```text
PREPARED
    | before_dispatch() commits *before* future OS transport
    v
IN_FLIGHT  -- valid exact response --> RESPONSE_OBSERVED
    |                                (NOT result ready or attested)
    |
    +---- bad/wrong/forged/contradictory --> NEEDS_RECONCILIATION
    |
    +---- process failure / restart --> IN_FLIGHT, no automatic replay

RESPONSE_OBSERVED -- identical repeat --> same observation
                  -- conflicting repeat --> NEEDS_RECONCILIATION
```

A subsequent `prepare` cannot launch an operation already IN_FLIGHT or
RESPONSE_OBSERVED. No endpoint in v0.38 can promote any state to
`RESULT_READY`, publish files, acknowledge parent delivery, or attest
the Windows security boundary. The model does not select database paths,
capability grants, nonce or target executable.

## Verification

```powershell
python -m unittest discover -s tests -p test_boxed_ipc_v038.py -v
```

Dedicated `.github/workflows/boxed-ipc-v038.yml` executes the contract
on both `windows-latest` and `ubuntu-24.04` and explicitly requires
the `run_tests` / `execute_shell` worker tool requests remain denied.

Test cases include malformed/oversized/truncated/multiple frames,
noncanonical and duplicate-key JSON, wrong job/attempt/op/nonce,
unknown capability, wrong payload hashes, re-instantiating the journal,
conflicting operation id, last-state persistence, double dispatch
and contradictory responses. These are **synthetic protocol tests**,
not evidence of a functioning cross-process transport.

## Remaining security gates – not implemented

1. **OS-verified channel identity** and per-job, per-attempt Windows
   AppContainer SID/DACL for the named-pipe (or another audited local
   transport). No unauthenticated localhost service, shared token or
   inherited manager handles. The child must *not* receive the manager
   SQLite database or the full OS environment.
2. Bound `PROBE_PING` to the exact child process created **suspended**,
   given an AppContainer token and Job Object **before** resume; implement
   process/IPC timeouts and cancellation without deadlocks or leaks.
3. Atomic launch/crash receipts and process-tree quiescence proof
   after interruption. An `IN_FLIGHT` entry is uncertain, not safe
   to relaunch just because no result arrived.
4. Per-job immutable input import, scratch isolation, output digest
   verification and manager-only artifact publication, with cross-job
   races and host-credential/registry/network denial tests.
5. Actual supported Windows target-machine G1b gate, real Flint/LM Studio,
   real Flash/Unsloth same-chat handoff and authorized parent ACK.

Until completion of the above, `worker_tool_boundary` remains
`MANAGER_BROKER_FILES_ONLY`, `boxed_execution_enabled=false`,
`os_sandbox_attested=false` and product remains **PRE-ALPHA / NO_GO**.

See [ADR-034](ADR_034_BOXED_TOOL_EXECUTION.md),
[v0.37 Windows native denial evidence](WIN32_APPCONTAINER_ACCESS_DENIAL_V037.md).
