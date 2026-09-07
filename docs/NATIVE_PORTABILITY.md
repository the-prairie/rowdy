# Native acceptance: macOS portability fixes

This records the two reproduced service failures addressed while resuming PR #7.
It is not a substitute for the native runtime workflow's observed result.

## 1. Optional SQLite extension-control API

The macOS Python 3.12 build used by Actions reports `extension-control: False`.
Its `sqlite3.Connection` does not expose `enable_load_extension`. Calling that
method unconditionally stopped every replay before evaluating SQL.

The connection factory now disables extension loading when the API exists. When
it is absent, extension loading was not built into this Python connection. An
available control that raises is **not** ignored: the connection closes and the
operation fails. The independent SQLite authorizer, function allowlist, query-only
setting and resource budgets remain unchanged.

`tests/test_sqlite_platform.py` executes actual SQLite using a connection subclass
that hides the optional API. It covers baseline/candidate outputs, the selected
record, a failing independent control, trace ambiguity, denied writes/metadata/
extension calls, quoted clock/aggregate restrictions, and control-failure cleanup.

## 2. Registered file identity across parent-directory aliases

After repairing SQLite startup, the actual Mac suite reached the native edit
endpoint. The platform can spell the same temporary path as `/var/...` or
`/private/var/...`. Preview resolution canonicalized it, while the edit endpoint
compared the original string against the canonical registered file. Legitimate
reviews were rejected before any write.

The edit endpoint now compares canonical existing resources while echoing the
caller's original path spelling for protocol correlation. This does **not** make
unregistered files acceptable: model membership is still resolved against the
project, and `Project.path` rejects symlinked components inside that project.

`tests/test_native_paths.py` reproduces the issue on other platforms using a
parent-directory alias. It exercises real review/apply/undo, rejects an unrelated
file with identical contents, and rejects a registered source replaced by an
external symlink. The original failing alias test passes only after the fix.

## Evidence and authority

The complete local Python suite after both fixes contains 161 tests. The new ten
regressions are part of that count, not additional independent whole-product
acceptance. The Mac source suite and native Metal workflow must run on the final
commit; their actual results belong in the PR record.

The duplicate `mac-service.yml` diagnostic imported nonexistent worker APIs and
was removed. `native-service.yml` remains the actual complete-source macOS gate.
The native visual workflow is triggered by service changes as well as native UI
changes. No failing assertion was removed or changed to skip, and no cloud flag,
SQL authority, expected business outcome or OS privacy setting was broadened.
