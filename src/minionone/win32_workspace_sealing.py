"""Native Windows manager-side input capture and admission log projection.

This is an admission-development seam, not a worker file broker or a release
security certification. Input handles stay open through the SQLite admission
transaction. Only already approved project IDs and manager-generated job IDs
are accepted. Target-host NTFS/ACL/AV/race tests remain mandatory.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import json
import sys

from .admission import InputRejected
from .workspace_sealing import (
    _MAX_FILES, _MAX_FILE_BYTES, _MAX_TOTAL_BYTES, _MAX_MANIFEST_BYTES,
    _MAX_TASK_BYTES, _SHA, _name, _pairs,
)
from .win32_input_broker import WindowsInputBroker
from .win32_output_publication import _output_directory


def _validate_payloads(files):
    """Use the same bounded manifest and task contract as the POSIX seal."""
    names = set(files)
    if not 2 <= len(names) <= _MAX_FILES + 1:
        raise InputRejected("Windows input file count outside admission quota")
    if "TASK.md" not in names or "manifest.json" not in names:
        raise InputRejected("TASK.md and manifest.json required")
    for name in names:
        _name(name)
    if len({name.casefold() for name in names}) != len(names):
        raise InputRejected("case-ambiguous Windows input names")
    manifest_bytes = files["manifest.json"]
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise InputRejected("Windows manifest quota exceeded")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"),
                              object_pairs_hook=_pairs)
    except (UnicodeError, ValueError) as exc:
        raise InputRejected("invalid Windows input manifest") from exc
    if (type(manifest) is not dict or set(manifest) != {"files"} or
            type(manifest["files"]) is not dict):
        raise InputRejected("Windows manifest must contain files map only")
    listed = manifest["files"]
    if (not 1 <= len(listed) <= _MAX_FILES or
            set(listed) | {"manifest.json"} != names):
        raise InputRejected("Windows manifest must declare all captured files")
    total = len(manifest_bytes)
    digests = {}
    for name, wanted in sorted(listed.items()):
        _name(name)
        if type(wanted) is not str or not _SHA.fullmatch(wanted):
            raise InputRejected("invalid Windows input SHA256")
        blob = files[name]
        if len(blob) > (_MAX_TASK_BYTES if name == "TASK.md" else _MAX_FILE_BYTES):
            raise InputRejected("Windows input file quota exceeded")
        total += len(blob)
        if total > _MAX_TOTAL_BYTES:
            raise InputRejected("Windows combined input quota exceeded")
        actual = hashlib.sha256(blob).hexdigest()
        if actual != wanted:
            raise InputRejected("Windows input differs from manifest SHA256")
        digests[name] = actual
    try:
        task = files["TASK.md"].decode("utf-8")
    except UnicodeError as exc:
        raise InputRejected("Windows TASK.md requires UTF-8") from exc
    if not task.strip() or "\x00" in task:
        raise InputRejected("Windows TASK.md requires nonempty task text")
    digest = hashlib.sha256(json.dumps({
        "files": digests,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return files, digests, digest


@contextmanager
def pinned_capture(workspace, row):
    """Yield verified bytes while denying writes/renames via held NT handles.

    All components and input files are opened relative to pinned validated
    handles with FILE_SHARE_READ only; individual file handles stay open
    until the caller finishes the admission DB transaction.
    """
    if sys.platform != "win32":
        raise InputRejected("native Windows admission capture requires Windows")
    project = workspace.projects.get(row["project_id"])
    if project is None:
        raise InputRejected("project no longer operator-approved")
    # Prevent a root directory substituted after configuration.
    from pathlib import Path
    st = Path(project).stat()
    if (st.st_dev, st.st_ino) != workspace.project_identities[row["project_id"]]:
        raise InputRejected("Windows project directory changed since configuration")
    with WindowsInputBroker(project, row["job_id"]) as broker:
        api = broker._api
        input_dir = broker._input
        volume = api.identity(input_dir)[0]
        names = broker.list_input()
        if not 2 <= len(names) <= _MAX_FILES + 1:
            raise InputRejected("Windows input file-count quota exceeded")
        if "manifest.json" not in names or "TASK.md" not in names:
            raise InputRejected("Windows manifest and TASK.md required")
        if len({name.casefold() for name in names}) != len(names):
            raise InputRejected("case-ambiguous Windows input names")
        with ExitStack() as pinned:
            blobs = {}
            handles = {}
            for name in sorted(names):
                _name(name)
                handle = api.child(input_dir, name, directory=False)
                pinned.callback(api.close, handle)
                api.validate(handle, directory=False, volume=volume,
                             single_link=True)
                quota = (_MAX_MANIFEST_BYTES if name == "manifest.json" else
                         _MAX_TASK_BYTES if name == "TASK.md" else _MAX_FILE_BYTES)
                blobs[name] = api.read_regular(handle, max_bytes=quota)
                handles[name] = (handle, api.identity(handle))
            # Catch arrivals or removals during enumeration. Existing handles
            # continue to pin file identity through the database transaction.
            if set(broker.list_input()) != set(names):
                raise InputRejected("Windows input directory mutated during capture")
            result = _validate_payloads(blobs)
            for handle, ident in handles.values():
                if api.validate(handle, directory=False, volume=volume,
                                single_link=True) != ident:
                    raise InputRejected("Windows pinned input identity changed")
            yield result


def project_admission_log(workspace, row, payload):
    """Native manager-only one-event admission log; never replaces an artifact.

    A pending file left by an interrupted seal is verified before reuse.
    Existing final logs must exactly match; tampering fails closed. This
    intentionally does not authorize mutable running-job log updates.
    """
    if sys.platform != "win32":
        raise InputRejected("Windows admission log requires native Windows")
    if type(payload) is not bytes or len(payload) > 65_536:
        raise InputRejected("Windows admission log size invalid")
    digest = hashlib.sha256(payload).hexdigest()
    pending = "pending-admission-" + row["job_id"][4:] + ".part"
    final_name = "job.log.jsonl"
    with _output_directory(workspace, row) as (api, directory, volume):
        finished = api.open_existing(directory, final_name)
        try:
            if finished is not None:
                api.read_verified(finished, digest, len(payload), volume)
                # A complete valid final is sufficient after an interrupted
                # database-to-NTFS boundary; no unsafe path cleanup is needed.
                return
        finally:
            if finished is not None:
                api.close(finished)
        partial = api.open_existing(directory, pending)
        if partial is None:
            partial = api.create_pending(directory, pending)
            try:
                api.validate(partial, directory=False, volume=volume,
                             single_link=True)
                api.write_exact_and_flush(partial, payload)
            finally:
                api.close(partial)
        else:
            try:
                api.read_verified(partial, digest, len(payload), volume)
            finally:
                api.close(partial)
        source = api.open_existing(directory, pending, for_rename=True)
        if source is None:
            raise InputRejected("Windows admission log pending file disappeared")
        try:
            api.read_verified(source, digest, len(payload), volume)
            api.rename_final_no_replace(source, directory, final_name)
        finally:
            api.close(source)
        verified = api.open_existing(directory, final_name)
        if verified is None:
            raise InputRejected("Windows admission log final absent")
        try:
            api.read_verified(verified, digest, len(payload), volume)
        finally:
            api.close(verified)
