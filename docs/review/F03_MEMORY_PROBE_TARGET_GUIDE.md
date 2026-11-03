# F03 / G1c: read-only RAM/VRAM provenance probe (v0.49)

**Status: PRE-ALPHA / PRODUCT NO-GO.** This is **not** a GPU-placement or
Flash-residency attestation, and **never** grants a model-loading permission.

## Explicit operator accounting choices

Use the current v0.49 Windows test executable (once PR CI passes) or the
installed package CLI:

```powershell
.\app\MinionOne.exe resource-probe --mode os-shared
```

This reads **one** OS-available-physical-RAM pool through
`GlobalMemoryStatusEx` on Windows or `SC_AVPHYS_PAGES` on POSIX. For CPU or
**verified unified-memory** AMD APU use, GPU memory is not a second allocatable
pool. Select `os-shared` only after confirming the memory architecture and
calibrating the combined worker+KV+Flash budgets on the **actual** host.
The mode is operator-selected, not auto-detected or attested by MinionOne.

Only on a *discrete NVIDIA GPU* with valid NVML and a known index:

```powershell
.\app\MinionOne.exe resource-probe --mode nvidia-nvml --gpu-index 0
```

This keeps OS RAM and NVML-reported **dedicated** GPU free memory as separate
measurements. The tool does not prove the selected model is resident on that
GPU. Other/discrete GPU types (including AMD dGPUs) **are not enabled** by
this command: they remain unverified, fail closed and require a separate,
hardware-validated driver telemetry adapter.

## Evidence and limitation

The output contains only bounded numeric readings and provenance labels;
it has no secrets, paths, prompts, tokens, model names or chat history.
`resource_admission_granted=false`, `memory_topology_attested=false`,
`provider_model_residency_attested=false` and `release_gate=NO_GO` are
**intentional** in every successful probe. A fresh number is not proof of
shared UMA topology, live LM Studio/Unsloth placement, Flash non-eviction,
headroom calibration, backend driver correctness or uninterrupted available
capacity across processes. The existing ModelLifecycle still performs its
own just-in-time, separately fenced resource admission.

Reject stale, future, invalid or missing telemetry; do not automatically
switch to the other accounting mode on a failure. A process-isolated child
with a bounded timeout is used by the existing sampler. No model is loaded,
unloaded, probed through inference or contacted. This read-only probe
supports the hardware evidence to be gathered for [G1c #4](https://github.com/Griggelor/MinionOne/issues/4)
without claiming that G1c itself has passed.

### Technical basis

- [AMD Strix Halo GPU memory model](https://rocm.docs.amd.com/en/docs-7.2.0/how-to/system-optimization/strixhalo.html): GPUVM uses unified memory, not physically dedicated VRAM.
- [AMD SMI supported platforms](https://rocmdocs.amd.com/projects/amdsmi/en/latest/install/install.html): Linux ROCm platforms; **not** assumed available as a Windows AMD API.
- Normative project contract: `docs/OWNER_REQUIREMENTS_V046.md` and
  `system_test/NATIVE_RETURN_DE.md`.
