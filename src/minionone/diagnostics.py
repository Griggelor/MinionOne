"""Fixed, secret-free diagnostics for local provider and MCP pilot failures.

No exception message, URL, HTTP response body or supplied model text is
ever reflected in these public results. Distinguish *retryable* from
*automatically retryable*: ambiguous effects are NEVER auto-replayed.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
import errno
import socket
import sqlite3


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    action: str
    retryable: bool

    def to_dict(self):
        return {"code":self.code,"message":self.message,
                "action":self.action,"retryable":self.retryable}


_CATALOG = {
    "PROVIDER_AUTH_FAILED": ("LM Studio rejected the API credentials.",
                             "Check the configured LM Studio API key/password and server authentication settings.", False),
    "PROVIDER_CONNECTION_REFUSED": ("Cannot connect to the local LM Studio service.",
                                    "Start LM Studio, enable its local API server and verify the configured port.", True),
    "PROVIDER_CONNECTION_FAILED": ("Connection to the local model service failed.",
                                   "Check the local service, port, firewall and connectivity.", True),
    "PROVIDER_TIMEOUT": ("The local model API request timed out.",
                         "Check model loading or server responsiveness; verify any previous operation before retrying.", True),
    "PROVIDER_ENDPOINT_NOT_FOUND": ("The local model API endpoint was not found.",
                                    "Check the server API endpoint and selected model compatibility.", False),
    "PROVIDER_REQUEST_REJECTED": ("The local model API rejected the request.",
                                  "Review the selected model, parameters and supported API format.", False),
    "PROVIDER_RATE_LIMITED": ("The local model API is currently limiting requests.",
                              "Reduce request frequency and retry after checking job state.", True),
    "PROVIDER_SERVER_ERROR": ("The local model service reported an internal error.",
                              "Check the LM Studio server health and logs; verify earlier effects before retrying.", True),
    "PROVIDER_HTTP_ERROR": ("The local model service returned an unexpected HTTP status.",
                            "Inspect local API configuration and provider health.", False),
    "PROVIDER_RESPONSE_INVALID": ("The local model service returned an invalid or unsupported response.",
                                  "Check the model API format, compatible model and response size.", False),
    "MCP_AUTH_FAILED": ("MinionOne authentication failed.",
                        "Check the MCP bearer token configured in the client and server.", False),
    "MCP_REQUEST_DENIED": ("The MCP request was refused by a local security rule.",
                           "Check the configured local MCP address, host and origin settings.", False),
    "INVALID_INPUT": ("The request contains invalid or unsupported parameters.",
                      "Review required fields, values and allowed operations.", False),
    "ACCESS_DENIED": ("Access to this job or operation was denied.",
                      "Use the correctly authorized parent session and job.", False),
    "STALE_VERSION": ("The job changed since the request was prepared.",
                      "Read the latest job status before choosing the next action.", True),
    "IDEMPOTENCY_CONFLICT": ("This operation identifier was already used for different parameters.",
                             "Read job status and use a new identifier only for a genuinely new operation.", False),
    "RESUME_NOT_READY": ("This job cannot be resumed safely yet.",
                         "Check the timeout status, checkpoint and reconciliation requirements.", False),
    "ERROR_JOURNAL_UNAVAILABLE": ("The rejected request could not be recorded in the job error journal.",
                                  "Check local storage and journal capacity; read job status before choosing a new operation. Do not blindly replay.", False),
    "DB_BUSY": ("The local state database is busy or locked.",
                "Resolve the conflicting database connection or transaction and wait for active writes to finish. Read job status and reconcile any previous operation before choosing a retry; no automatic retry is performed.", True),
    "DB_CORRUPT": ("The local state file is damaged or is not a SQLite database.",
                   "Stop MinionOne, preserve the database and recovery files, and restore a verified backup. Do not delete or recreate the state automatically.", False),
    "DB_UNAVAILABLE": ("The local state database cannot be opened.",
                        "Check that the configured database location and its parent directory are available and accessible. Preserve existing state before making changes.", False),
    "DB_SCHEMA_ERROR": ("The local state schema is incompatible or unsafe for this MinionOne version.",
                         "Stop MinionOne and check database/version compatibility. Use the documented backup and migration procedure; do not recreate state automatically.", False),
    "DB_INTEGRITY_ERROR": ("The local state violates a database integrity rule.",
                            "Stop the affected operation, preserve the database and inspect redacted manager diagnostics or a verified backup. Do not bypass integrity checks or blindly retry.", False),
    "DB_RESOURCE_EXHAUSTED": ("The local database could not allocate required memory.",
                              "Reduce local resource pressure and inspect manager health before choosing another operation. Do not unload the parent model automatically.", False),
    "DB_ERROR": ("The local state database reported an unclassified error.",
                  "Preserve local state and inspect redacted manager diagnostics and database/version compatibility. Do not infer the cause from raw error text or blindly retry.", False),
    "STORAGE_FULL": ("Local storage or its configured capacity is exhausted.",
                      "Free space or review the local storage quota without deleting the job database or recovery files. Check job state before choosing another operation.", False),
    "STORAGE_READ_ONLY": ("A required local resource is read-only.",
                           "Check the read-only setting and intended write permissions for local state. Preserve the database and recovery files before changing storage configuration.", False),
    "STORAGE_ACCESS_DENIED": ("Access to required local state or resources was denied.",
                               "Check the operator account and intended filesystem permissions. Do not weaken worker isolation or expose credentials to resolve this error.", False),
    "STORAGE_IO_ERROR": ("An input/output operation on local state failed.",
                          "Stop affected writes, preserve state and check local storage health. Reconcile the operation before attempting another write.", False),
    "STORAGE_UNAVAILABLE": ("A required local state location is missing or incompatible.",
                             "Check that the configured parent location exists and the state path has the expected file or directory type. Do not delete existing state automatically.", False),
    "CONFIGURATION_INVALID": ("MinionOne could not start with the supplied configuration.",
                               "Check the local MCP configuration and supported parameter values without sharing credentials or private paths.", False),
    "MCP_ADDRESS_IN_USE": ("The configured local MCP address is already in use.",
                           "Check for another MinionOne instance or deliberately select an unused local port. Do not terminate unrelated processes automatically.", False),
    "INTERNAL_ERROR": ("MinionOne encountered an internal error.",
                       "Check the local manager's redacted diagnostics and job state; do not blindly repeat operations.", False),
}


def diagnostic(code: str) -> Diagnostic:
    if type(code) is not str:
        code='INTERNAL_ERROR'
    message,action,retryable=_CATALOG.get(code,_CATALOG["INTERNAL_ERROR"])
    if code not in _CATALOG:
        code="INTERNAL_ERROR"
    return Diagnostic(code,message,action,retryable)


_SQLITE_DIAGNOSTICS = {
    sqlite3.SQLITE_BUSY:'DB_BUSY', sqlite3.SQLITE_LOCKED:'DB_BUSY',
    sqlite3.SQLITE_CORRUPT:'DB_CORRUPT', sqlite3.SQLITE_NOTADB:'DB_CORRUPT',
    sqlite3.SQLITE_CANTOPEN:'DB_UNAVAILABLE', sqlite3.SQLITE_FULL:'STORAGE_FULL',
    sqlite3.SQLITE_READONLY:'STORAGE_READ_ONLY', sqlite3.SQLITE_IOERR:'STORAGE_IO_ERROR',
    sqlite3.SQLITE_PERM:'STORAGE_ACCESS_DENIED', sqlite3.SQLITE_AUTH:'STORAGE_ACCESS_DENIED',
    sqlite3.SQLITE_SCHEMA:'DB_SCHEMA_ERROR', sqlite3.SQLITE_CONSTRAINT:'DB_INTEGRITY_ERROR',
    sqlite3.SQLITE_NOMEM:'DB_RESOURCE_EXHAUSTED',
}
_STORAGE_CODES = frozenset(_SQLITE_DIAGNOSTICS.values()) | {
    'DB_ERROR', 'STORAGE_UNAVAILABLE',
}


def classify_storage_failure(exc) -> Diagnostic:
    """Numeric result codes and known exception types only; never text guessing.

    Python 3.11+ exposes sqlite_errorcode for exceptions from SQLite. Extended
    result codes retain the primary category in their low byte. A hand-built
    exception without a usable code is DB_ERROR, not a guessed corruption/full.
    """
    if isinstance(exc, sqlite3.Error):
        value = getattr(exc, 'sqlite_errorcode', None)
        if type(value) is int and 0 < value <= 0x7fffffff:
            return diagnostic(_SQLITE_DIAGNOSTICS.get(value & 0xff, 'DB_ERROR'))
        return diagnostic('DB_ERROR')
    if isinstance(exc, OSError):
        value = exc.errno
        if type(value) is int:
            if value in (errno.ENOSPC, getattr(errno, 'EDQUOT', errno.ENOSPC)):
                return diagnostic('STORAGE_FULL')
            if value in (errno.EACCES, errno.EPERM):
                return diagnostic('STORAGE_ACCESS_DENIED')
            if value == errno.EROFS:
                return diagnostic('STORAGE_READ_ONLY')
            if value == errno.EIO:
                return diagnostic('STORAGE_IO_ERROR')
            if value in (errno.ENOENT, errno.ENOTDIR, errno.EISDIR, errno.EEXIST):
                return diagnostic('STORAGE_UNAVAILABLE')
        if isinstance(exc, PermissionError):
            return diagnostic('STORAGE_ACCESS_DENIED')
        if isinstance(exc, (FileNotFoundError, NotADirectoryError, IsADirectoryError)):
            return diagnostic('STORAGE_UNAVAILABLE')
    return diagnostic('INTERNAL_ERROR')


def classify_provider_failure(exc) -> Diagnostic:
    """Classify only known HTTP/transport types, never untrusted messages.

    HTTPError subclasses URLError, so numeric status mapping MUST precede
    examination of .reason. Credentials, URL, response body are discarded.
    """
    if isinstance(exc,HTTPError):
        status=exc.code
        if status in (401,403): code="PROVIDER_AUTH_FAILED"
        elif status in (408,504): code="PROVIDER_TIMEOUT"
        elif status==404: code="PROVIDER_ENDPOINT_NOT_FOUND"
        elif status in (400,422): code="PROVIDER_REQUEST_REJECTED"
        elif status==429: code="PROVIDER_RATE_LIMITED"
        elif type(status) is int and 500<=status<=599: code="PROVIDER_SERVER_ERROR"
        else: code="PROVIDER_HTTP_ERROR"
        return diagnostic(code)
    reason=exc.reason if isinstance(exc,URLError) else exc
    if isinstance(reason,(TimeoutError,socket.timeout)):
        return diagnostic("PROVIDER_TIMEOUT")
    if isinstance(reason,ConnectionRefusedError):
        return diagnostic("PROVIDER_CONNECTION_REFUSED")
    if isinstance(reason,(URLError,OSError,ConnectionError)):
        return diagnostic("PROVIDER_CONNECTION_FAILED")
    return diagnostic("PROVIDER_HTTP_ERROR")


def safe_tool_fault(exc) -> Diagnostic:
    """Map known manager exception *types* to safe stable public codes."""
    from .admission import (
        PermissionDenied, InputRejected, VersionConflict, IdempotencyConflict,
        ControlJournalUnavailable, StateSchemaRejected, StateIntegrityRejected,
    )
    from .timeout_control import TimeoutControlDenied
    if isinstance(exc,StateSchemaRejected): return diagnostic('DB_SCHEMA_ERROR')
    if isinstance(exc,StateIntegrityRejected): return diagnostic('DB_INTEGRITY_ERROR')
    if isinstance(exc,PermissionDenied): return diagnostic("ACCESS_DENIED")
    if isinstance(exc,InputRejected): return diagnostic("INVALID_INPUT")
    if isinstance(exc,VersionConflict): return diagnostic("STALE_VERSION")
    if isinstance(exc,IdempotencyConflict): return diagnostic("IDEMPOTENCY_CONFLICT")
    if isinstance(exc,TimeoutControlDenied): return diagnostic("RESUME_NOT_READY")
    if isinstance(exc,ControlJournalUnavailable):
        fault = diagnostic('ERROR_JOURNAL_UNAVAILABLE')
        if type(exc.storage_code) is str and exc.storage_code in _STORAGE_CODES:
            return Diagnostic(fault.code,fault.message,
                              fault.action+' '+diagnostic(exc.storage_code).action,False)
        return fault
    if isinstance(exc,(sqlite3.Error,OSError)):
        return classify_storage_failure(exc)
    attached = getattr(exc,'diagnostic',None)
    if type(attached) is Diagnostic:
        # Even a Diagnostic instance may contain fabricated message/action text.
        # Rebuild from the fixed catalog rather than trusting attached content.
        return diagnostic(attached.code)
    return diagnostic("INTERNAL_ERROR")
