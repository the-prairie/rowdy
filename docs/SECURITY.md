# Non-sensitive evaluation boundary

This release is for synthetic or explicitly approved non-sensitive development snapshots.
It is not certified or approved for patient records, secrets or unrestricted source data.

The service binds loopback, validates Host/Origin and requires an owner-local token for
API calls. Its local descriptor is mode 0600; same-user malicious processes remain within
the host trust boundary. POSIX service ownership uses flock; Windows concurrency and
platform file ACLs require separate validation. No telemetry or automatic provider call.

Registered paths reject traversal and symlinks. SQL workers have read-only authorizers,
registered relations, canonical function policy, extension loading disabled, and bounded
execution/results. File writes require an explicit startup capability and source/context
checks. Preview is not verification, and verification is not deployment.

Rows and SQL may be retained in the owner-local SQLite store. This version does not provide
enterprise at-rest protection, automatic expiry, revocation across copies or safe patient-
record exports. Do not put sensitive data in it. Profile labels do not de-identify data.
No public issue, PR, screenshot, export or audit attachment should include such data.

Trusted Core macros and Google SDK credentials are separate capabilities; local live
replay cannot enable them. Never treat a target called development as independent proof
of authorized effective warehouse permissions.
