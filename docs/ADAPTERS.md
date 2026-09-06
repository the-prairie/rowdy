# Explicit adapters and integration gates

Continuous local replay calls neither dbt nor Google. There is no engine fallback.
The shipped example profiles are synthetic, non-sensitive, and use SQLite semantics.

## dbt Core

The CLI adapter is opt-in (`--allow-dbt`) and executes only the configured absolute
Core 1.x executable against the trusted registered project. Add a reviewed block:

```json
{
  "dbt": {
    "binary": "/absolute/venv/bin/dbt",
    "profiles_dir": "/absolute/approved/profiles",
    "target": "development",
    "allowed_targets": ["development"]
  }
}
```

Register a package-aware `dbt_selector` on a model. Compilation runs on saved disk files;
this release does NOT stage arbitrary unsaved dbt buffers into a compilation sandbox.
Local scratch previews and actual Core compilation therefore have different scope.
Compilation may query metadata and execute trusted project macros. Do not enable an
untrusted repo, assume compilation is offline, or infer effective IAM from a target name.

Each invocation uses isolated target/log directories. Test resources are resolved explicitly;
`run_results.json` and `manifest.json` must share invocation identity and exactly match the
expected resources. Empty, stale, duplicate, unrelated, missing or skipped results do not
become a passing claim. Tests with actual intended Core/adapter versions are still required.
The returned compilation is not deployment or consumer proof.

## BigQuery

Install optional dependencies in an approved environment:

```sh
python3 -m pip install '.[bigquery]'
python3 -m rowdy --project /approved/project --allow-bigquery
```

Example policy shape (replace with approved values, never credentials):

```json
{
  "bigquery": {
    "billing_project": "approved-development-project",
    "location": "US",
    "allowed_relations": ["approved-development-project.replay.events"],
    "maximum_bytes_billed": 100000000
  }
}
```

ADC stays with the Google SDK. A conservative GoogleSQL plan denies scripts, DDL/DML,
unknown functions and unregistered or incompletely qualified relations. The warehouse
must report SELECT. Dry run returns a short-lived, one-use plan bound to SQL/context;
execution rechecks context and applies the byte budget. A dry-run receipt is not execution.
Results are capped, partial scope is explicit, and cancellation is not reported as proof.

This adapter's live integration has NOT been verified without a real approved project.
Its parser profile is deliberately narrow. It does not implement full organizational
query authorization, cumulative/reservation budgets, retention, general UDF support or
production dataset isolation. Before enabling live data, independently verify least-
privilege IAM, principal, allowed sources/functions, cost mode, storage and expiry, exports,
error logs and cancellation. A SELECT may still call a function with external behavior;
unknown functions remain denied. UI status is not a substitute for permission enforcement.

## Native bridge

`python -m rowdy.bridge` reads its session token from a mode-0600 runtime descriptor,
accepts a known file and buffer on standard input, and calls the same local preview or
verification route. It cannot apply files, change expectations or enable a warehouse.
The protocol subprocess is tested. The accompanying GPUI view is an uncompiled spike,
not a full port. See `native/README.md` for exact build and remaining verification gates.

## Authoritative references

- https://docs.getdbt.com/reference/commands/compile
- https://docs.getdbt.com/reference/artifacts/run-results-json
- https://cloud.google.com/bigquery/docs/running-queries
- https://cloud.google.com/bigquery/docs/best-practices-costs
- https://www.sqlite.org/c3ref/set_authorizer.html
