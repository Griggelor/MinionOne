"""Fixed, secret-free diagnostics for local provider and MCP pilot failures.

No exception message, URL, HTTP response body or supplied model text is
ever reflected in these public results. Distinguish *retryable* from
*automatically retryable*: ambiguous effects are NEVER auto-replayed.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
import socket


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
    "INTERNAL_ERROR": ("MinionOne encountered an internal error.",
                       "Check the local manager's redacted diagnostics and job state; do not blindly repeat operations.", False),
}


def diagnostic(code: str) -> Diagnostic:
    message,action,retryable=_CATALOG.get(code,_CATALOG["INTERNAL_ERROR"])
    if code not in _CATALOG:
        code="INTERNAL_ERROR"
    return Diagnostic(code,message,action,retryable)


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
    )
    from .timeout_control import TimeoutControlDenied
    if hasattr(exc,"diagnostic") and type(exc.diagnostic) is Diagnostic:
        return exc.diagnostic
    if isinstance(exc,PermissionDenied): return diagnostic("ACCESS_DENIED")
    if isinstance(exc,InputRejected): return diagnostic("INVALID_INPUT")
    if isinstance(exc,VersionConflict): return diagnostic("STALE_VERSION")
    if isinstance(exc,IdempotencyConflict): return diagnostic("IDEMPOTENCY_CONFLICT")
    if isinstance(exc,TimeoutControlDenied): return diagnostic("RESUME_NOT_READY")
    return diagnostic("INTERNAL_ERROR")
