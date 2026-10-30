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

**v0.30 development status (overrides older notes below):** A native Windows handle-relative read-only input seal and manager-only output publisher are implemented and CI-tested, but have NOT passed independent multi-user or target-host G1b security approval. The direct live worker transcript in output/worker-io.jsonl contains exact model and tool text, including any reasoning fields returned by LM Studio; its confidentiality and target-host ACLs are NOT yet validated. State/logs/*.jsonl contains redacted manager-only metadata. Worker filesystem access, prompt-triggered execution and cloud export remain disabled. Only synthetic data is permitted.

HISTORICAL baseline note (pre-v0.25): The descriptor-relative read-only input seal is only available on POSIX hosts. It is **not** a production file broker. Windows sealing, Worker file access, cloud export and live provider switching remain disabled/unimplemented. SQLite-pinned input is immutable as manager state but source files may be changed by the authorized parent; `verify_workspace_input` detects changes before any future execution. Do not use confidential production files.
