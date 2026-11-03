"""Single bounded-text executor input envelope shared by seal and start.

No support for binary input, large projects or code execution is implied.
"""
from __future__ import annotations

from .admission import InputRejected

MAX_WORKER_DOCUMENTS = 4
MAX_WORKER_DOCUMENT_BYTES = 8192
MAX_WORKER_TEXT_BYTES = 4096


def bounded_worker_inputs(blobs: dict[str, bytes]) -> tuple[str, dict[str, str]]:
    """Validate verified snapshot bytes before ACCEPTED and again at START."""
    # Lazy import to keep admission and worker module layering acyclic.
    from .pilot_jobs import _name, _text

    if type(blobs) is not dict or type(blobs.get("TASK.md")) is not bytes:
        raise InputRejected("workspace task is missing or not a byte document")
    try:
        task = _text(blobs["TASK.md"].decode("utf-8"), MAX_WORKER_TEXT_BYTES)
    except UnicodeError as exc:
        raise InputRejected("workspace task must be UTF-8") from exc
    if not task.strip():
        raise InputRejected("workspace task must not be blank")
    others: dict[str, str] = {}
    for name, blob in sorted(blobs.items()):
        if name in ("TASK.md", "manifest.json"):
            continue
        canonical = _name(name)
        if canonical in others or canonical == "task.md":
            raise InputRejected("workspace document name collision")
        if type(blob) is not bytes:
            raise InputRejected("workspace document is not a byte document")
        try:
            others[canonical] = _text(blob.decode("utf-8"), MAX_WORKER_TEXT_BYTES)
        except UnicodeError as exc:
            raise InputRejected("binary workspace input not supported") from exc
    if len(others) > MAX_WORKER_DOCUMENTS:
        raise InputRejected("workspace supports at most four UTF-8 documents")
    if sum(len(text.encode("utf-8")) for text in others.values()) > MAX_WORKER_DOCUMENT_BYTES:
        raise InputRejected("workspace document quota exhausted")
    return task, others
