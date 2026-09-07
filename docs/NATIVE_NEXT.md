# Native preview lifecycle — historical PR #6 increment

This document describes the narrower PR #6 increment, not the latest PR #7 scope.
The current trace/live/guarded-write integration is documented in
[`native/README.md`](../native/README.md). Keep these historical verification
boundaries distinct from later observed CI evidence.

## What this change does

The native view keeps code and one evidence surface. Preview and Verify submit an
explicit active SQL buffer. Cancel is visible only during a native request.

- Protocol `rowdy.native/1` correlates the request ID, session, generation, path,
  operation, exact SQL, SQL digest, project, model, context, receipt kind and version.
- The bridge uses an owner-only descriptor, disables environment proxies and rejects
  redirects. Duplicate JSON keys, non-finite values, malformed rows, false authority
  flags, empty passing checks and mismatched replies are rejected.
- Profile, inputs, source and Git context are checked again after execution. A changed
  context cannot become current native evidence; the service's original receipt remains
  a historical observation.
- Native child reads are bounded while streaming, not after buffering arbitrary output.
  Input/output pipes are handled concurrently. Timeout, cancellation, I/O failure and
  dropping the request owner kill and reap the direct bridge child. The deadline is 20 s.
- Active file or SQL edits invalidate the displayed receipt and cancel obsolete requests.
  Reply adoption rechecks the active buffer even if an editor notification was missed.
  Last evidence remains explicitly historical instead of being relabeled current.
- The patcher verifies both native source modules on repeat application. An older or
  partially applied integration is rejected; apply to a clean reviewed upstream checkout.

## What it does not establish

This is a prerequisite within issue #2, not closure of issue #2. Native record tracing,
continuous watches, diff/apply/undo controls, whole-application linking/launch, packaging,
accessibility and a user's Mac session remain separate work.

The existing gate runs `cargo check --locked -p dbt_ui --lib` on macOS. That is a Rust
crate check, not a built/installed application. The transport unit tests execute actual
child processes without a UI. No native screenshots are claimed for this change.

Cancellation stops the bridge process/wait. It does not claim that an already submitted
local service job has been cancelled; it may finish under the service's existing limits.
Only the direct configured Python bridge child is managed (not arbitrary process trees).
No warehouse, dbt or other subprocess action is reachable through this bridge.

Project context is rechecked at completion, not continuously polled afterward. Later
profile/input/Git changes still require another explicit evaluation. The banner therefore
says when context was checked; it does not imply continuous project surveillance.

## Reproduce

```
python -m unittest discover -s tests -v
python native/apply.py /path/to/pinned/upstream --check
python native/apply.py /path/to/pinned/upstream
cd /path/to/pinned/upstream
rustc --edition 2021 --test /path/to/rowdy/native/rowdy_process.rs -o /tmp/rowdy-transport-tests
/tmp/rowdy-transport-tests
cargo check --locked -p dbt_ui --lib
```

Use the Python interpreter in which this Rowdy code is installed as `ROWDY_PYTHON`.
Set `ROWDY_HOME` to the running service's state directory. Do not put credentials or
private project configuration in this public repository.

## Remaining external prerequisites

No cloud access is needed for native development. Later warehouse acceptance needs an
approved local Core project/environment plus a non-sensitive development dataset,
region, least-privilege identity and explicit query budget. Authenticate using the
approved local tooling, never by posting tokens or service-account keys in an issue.
The eventual Mac pilot additionally needs a target-machine launch and UI session.
