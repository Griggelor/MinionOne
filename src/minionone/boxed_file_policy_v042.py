"""Bounded synthetic v0.42 file-receipt policy; NEVER a worker tool.

Fixed developer-owned bytes and filenames only; not a generic file importer.
Requires separate target-host isolation approval before any real data.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

INPUT = b"V042_SYNTHETIC_SEALED_INPUT\n"
RESULT = b"V042_SYNTHETIC_RESULT_FROM_INPUT_V1\n"
MANAGER_CANARY = b"V042_MANAGER_IMMUTABLE_OUTPUT\n"
MAX_RESULT_BYTES = 4096


class SyntheticArtifactRejected(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _fixed_regular(path, *, max_bytes):
    """Reject links, reparse points, directories and oversize before reading.

    Only used AFTER the fixed child has exited; does not implement a
    race-free untrusted general-purpose Windows file broker.
    """
    item = Path(path)
    info = item.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes or
            info.st_nlink != 1 or
            getattr(info, "st_file_attributes", 0) & 0x400):
        raise SyntheticArtifactRejected("untrusted fixture is nonregular or oversized")
    with item.open("rb") as source:
        fd_stat = source.fileno()
        # Never read beyond the hard cap, even if the file changes.
        data = source.read(max_bytes + 1)
        actual = os.fstat(fd_stat)
        if (actual.st_dev != info.st_dev or actual.st_ino != info.st_ino or
                len(data) > max_bytes):
            raise SyntheticArtifactRejected("untrusted file changed during inspection")
    return data


def inspect_fixed_result(stage):
    """Accept one exact synthetic result and no auxiliary work artifacts."""
    if not isinstance(stage, Path):
        raise SyntheticArtifactRejected("trusted internal stage required")
    folder = stage / "work"
    if folder.is_symlink() or not folder.is_dir():
        raise SyntheticArtifactRejected("work zone invalid")
    if sorted(entry.name for entry in folder.iterdir()) != ["result.bin"]:
        raise SyntheticArtifactRejected("unexpected work zone contents")
    result = _fixed_regular(folder / "result.bin", max_bytes=MAX_RESULT_BYTES)
    if result != RESULT or sha(result) != sha(RESULT):
        raise SyntheticArtifactRejected("untrusted output content mismatch")
    if _fixed_regular(stage / "input" / "sealed.bin", max_bytes=MAX_RESULT_BYTES) != INPUT:
        raise SyntheticArtifactRejected("sealed input changed")
    if _fixed_regular(stage / "output" / "immutable.bin", max_bytes=MAX_RESULT_BYTES) != MANAGER_CANARY:
        raise SyntheticArtifactRejected("immutable output changed")
    return result
