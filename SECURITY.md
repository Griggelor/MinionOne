# Security Policy

**Status:** Public experimental pre-alpha. There is no supported production runtime, no guaranteed Windows confinement, and no security certification.

## Private vulnerability reports

Please use GitHub's **Report a vulnerability** / private security advisory functionality if enabled for this repository. If that option is unavailable, request a private channel from the maintainer **without sharing exploit details, secrets or user data publicly**.

Provide the affected revision, the violated trust boundary, a synthetic reproducer, expected/observed behavior and severity. No staffed security response SLA is currently promised.

## Security boundaries under development

- Worker denial of shell, arbitrary OS/filesystem access, network, Git, browser, code execution and uncontrolled MCP.
- Read-only sealed input, create-only worker work, output only through trusted manager publication.
- Scoped authentication, durable fencing, crash/retry/idempotency controls, audit projection with redaction.
- No automatic execution or integration of worker-created artifacts.

**Gate policy:** CI contract checks do not substitute for real target Windows NTFS, Unsloth MCP ownership or provider end-to-end security tests. A successful GitHub workflow is **not** an assurance certificate.

For planned independent security review and release gates, see [Issue #9](https://github.com/Griggelor/MinionOne/issues/9).


### v0.20 developmental boundary

The descriptor-relative read-only input seal is only available on POSIX hosts. It is **not** a production file broker. Windows sealing, Worker file access, cloud export and live provider switching remain disabled/unimplemented. SQLite-pinned input is immutable as manager state but source files may be changed by the authorized parent; `verify_workspace_input` detects changes before any future execution. Do not use confidential production files.
