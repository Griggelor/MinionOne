# G1c resource eligibility — second-pass policy and security review

**PR:** #17, pure resource_policy.py with independent-role unit tests.
**Reviewer independence:** same development assistant acting as critical reviewer,
NOT a third-party auditor. **Verdict:** limited policy PASS,
actual target G1c/Beta NO-GO.

## Trusted boundary

The policy receives a frozen ResourceSnapshot, but this class alone cannot
attest that telemetry came from the actual host. A model can construct a
plausible snapshot if a future service wrongly exposes this interface.
It must remain manager-internal and use a measured host/protocol sampler,
which does not yet exist.

No side-effect methods exist: no model loading, unloading, shell, process
start, GPU invocation, browser/network, file access or worker execution.
The current MCP pilot does not export ResourceGate.

## Independent adversarial challenges

- Parent not resident, wrong parent identity, zero measured parent memory.
- Stale/future timestamps; exactly 5 seconds remains eligible.
- Unknown/unmeasured worker, missing or invalid calibrated estimate.
- Insufficient UMA headroom, boundary equality and one-byte deficit.
- Running worker count >= one, even with abundant memory.
- Contradictory same-pool totals, negative and oversized values.
- bool-as-int, malformed object/string IDs and model injection characters.
- Never raises malformed-telemetry TypeError into a fail-open caller.
- Data objects frozen; denial outputs code-only and no sensitive IDs.

## CI evidence

Green source commit e28ba1f2efeea34f7cc43f52d1752657872b9ee3:
https://github.com/Griggelor/MinionOne/actions/runs/40924809700
**103/103 tests on Windows (23.509s) and Ubuntu (12.797s).**
Real official SDK and Win32 feasibility runs at the same code revision passed.
A later documentation commit must pass its own checks before merge.

The test-only initial commit c6a13c20... was intentionally red:
https://github.com/Griggelor/MinionOne/actions/runs/40924723954

## Critical remaining gaps

- There is no real Rulith / Flint / LM Studio host telemetry sample,
  no UMA/KV/residency validation, no parent-eviction testing.
- The pure function is NOT a lease. Two concurrent managers can both see
  enough free memory and admit workers. Durable global CAS and real
  resource reservation are needed before executing a provider request.
- No live Unsloth parent identity, Windows broker or worker tool sandbox.
- No independent third-party security assessment, license or release signoff.

**Scope review recommendation:** merge only when exact-head CI passes.
G1c remains BLOCKED and community Beta NO-GO.
