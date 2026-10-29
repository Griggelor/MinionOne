# G1c: conservative model memory eligibility (pure policy)

**Status:** Internal *eligibility policy*, not a model scheduler.
The local MCP pilot has worker_execution_enabled=false. No model is
loaded, unloaded, started or killed by this component.

## Intended use

For parent-pinned Rulith + a single LM Studio/Flint worker, real target
telemetry must come from a trusted manager-side host sampler. The sampler
and verified LM Studio residency integration DO NOT yet exist.

ResourceGate requires trusted configuration of:
- Exact expected parent model ID.
- Exact permitted worker model ID and conservative memory estimate, in
  bytes. The estimate must include expected model residency and KV/context
  budget measured on actual hardware; no inferred/default estimate.
- Strict positive free-memory headroom, chosen by operator after measurement.
- Exactly one admitted prospective worker until target concurrency is proven.

Every invocation also requires a fresh immutable ResourceSnapshot with
same-pool measured total/available/parent memory, parent model identity,
parent residency, current running workers and a monotonic measurement time.
A snapshot may be at most five seconds old. Invalid/stale/contradictory
samples, parent missing or switched, insufficient memory and active-worker
count all yield a generic denial. A passing policy does NOT reserve memory.

## Threat-driven tests

- Positive synthetic example with explicit adequate headroom.
- Boundary at worker estimate + required headroom.
- Parent evicted, parent model swapped, zero parent residency memory.
- Stale, future-dated, inconsistent or malformed metrics.
- Boolean fields where integer values are required (no bool coercion).
- Too many workers, unknown worker model, missing calibration or dangerous
  configuration allowing unlimited concurrency.
- Model IDs containing control characters and invalid oversized estimates.
- Snapshot is immutable; no network, process, GPU or filesystem calls.

All numeric sample values in these tests are **synthetic contract data**.
They are NOT measurements of the user's Rulith/Flint hardware.

## CRITICAL release limitations

1. Real Windows/GPU/UMA telemetry and its provenance not verified; the
   function accepts snapshots constructed by the caller. It MUST be called
   only after a trusted sampler has verified measurement origin.
2. A positive result is **not** a resource lease, process authorization or
   provider admission. Two managers could both evaluate the same free bytes.
   Canonical SQLite admission/epoch fencing and single-process ownership
   must be proven in the future runtime.
3. Parent model residency and no-eviction must be checked with the exact
   running LM Studio/Unsloth provider versions and target hardware.
4. Actual Rulith memory use, Flint model residency, context allocations,
   GPU shared memory, shutdown, VRAM/UMA pressure and model-loading behavior
   must be measured on the target, not inferred from this policy.
5. ResourceGate is not wired into the MCP tool API or a real worker runner.

**G1c: BLOCKED (target-dependent). Beta: NO-GO.**
