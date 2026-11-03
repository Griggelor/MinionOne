"""Read-only resource telemetry provenance probe; never authorizes model loading.

An operator explicitly selects how memory is accounted for. This does not
identify the physical GPU or certify an Unsloth/LM Studio process's placement.
"""
from __future__ import annotations

import os
import time

from .admission import InputRejected
from .model_lifecycle import MemoryReading, measure_memory
from .worker_profiles import WorkerProviderError

_MODES = frozenset(('os-shared', 'nvidia-nvml'))


def probe_resources(mode: str, gpu_index: int = 0, *, measure=measure_memory,
                    clock=time.monotonic) -> dict:
    """Sample a single bounded fresh reading; no model or provider side effects.

    os-shared accounts for CPU / measured UMA memory as *one* OS RAM pool.
    nvidia-nvml adds a *separate* discrete GPU pool, never treated as UMA.
    """
    if (type(mode) is not str or mode not in _MODES or
            type(gpu_index) is not int or not 0 <= gpu_index <= 15 or
            (mode == 'os-shared' and gpu_index != 0)):
        raise InputRejected('invalid resource probe mode or GPU index')

    need_gpu = mode == 'nvidia-nvml'
    sample = measure(gpu_index, need_gpu)
    now = clock()
    if (type(sample) is not MemoryReading or
            type(sample.ram_mb) is not int or sample.ram_mb < 0 or
            type(sample.measured) is not float or
            type(now) is not float or not 0 <= now - sample.measured <= 5 or
            (need_gpu and (type(sample.vram_mb) is not int or sample.vram_mb < 0)) or
            (not need_gpu and sample.vram_mb is not None)):
        raise WorkerProviderError('PILOT_RESOURCE_UNVERIFIED')

    return {
        'schema_version': 1,
        'probe': 'read_only_available_memory',
        'memory_accounting': mode,
        'mode_selected_by_operator': True,
        'memory_topology_attested': False,
        'provider_model_residency_attested': False,
        'resource_admission_granted': False,
        'release_gate': 'NO_GO',
        'ram_available_mb': sample.ram_mb,
        'ram_source': ('windows_GlobalMemoryStatusEx' if os.name == 'nt'
                       else 'posix_SC_AVPHYS_PAGES'),
        'separate_vram_available_mb': sample.vram_mb,
        'separate_vram_source': 'nvidia_nvml' if need_gpu else None,
        'sample_age_ms': int((now - sample.measured) * 1000),
    }
