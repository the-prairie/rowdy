# row(dy)

**See what your data does.**

Rowdy is a focused SQL investigation workbench: actual project files on the left,
records and evidence on the right. Click an ID, follow its representations, edit a
transformation against fixed inputs, verify independent expectations, then explicitly
apply the change to a real source file. No permanent dashboard, graph or chat rail.

## Run

Python 3.11+ and Git are sufficient for the non-sensitive local demo. No package install,
Rust, warehouse credentials, model provider, network download, or account is needed.

```sh
bash start-rowdy
```

The launcher copies two synthetic example projects into the local Rowdy home on first
launch. They are actual SQL files in real local Git repositories, not fixture-named UI
scratch. Existing example edits are not overwritten. `--no-browser --port 0` is supported.

```sh
# Open an independently registered project; read-only file access by default.
python3 -m rowdy --project /absolute/project --home /absolute/rowdy-state
# Explicitly allow reviewed, conflict-checked SQL file edits.
python3 -m rowdy --project /absolute/project --allow-writes
```

Open the served localhost URL, not `index.html`. Stop with Ctrl+C. Keep Rowdy state
outside source repositories. This version accepts synthetic and explicitly non-sensitive
snapshots; it is **not an approved environment for patient data or credentials**.

## The complete demonstration

1. Open **Web sessions**, run `session_summary`, and click **U-1042** in a user-ID cell.
2. Select **E-006 / Form submitted**. It arrived at 11:10 after occurring at 09:49.
3. Inspect the real matches in received, staged, unified and session representations.
   A shared-device ambiguity remains unassigned; delivery retries remain inspectable.
4. **Open transformation**, turn **Live** on, and change `schema_version = 1` to
   `schema_version IN (1, 2)`. Actual SQLite output changes 16→17; the watched session's
   completion count changes 0→1. The historical trace is not rewritten.
5. Open **Changes** and **Checks**. Expectations are independently registered, not
   generated from the candidate. **Verify** creates a separate scoped checkpoint.
6. **Review changes → Apply to source file** writes the actual SQL file and shows its
   Git diff. Undo is conflict-checked. No commit, push, dbt build or deployment happens.
7. Switch projects to **Order reconciliation**. Its different tables, IDs and grain work
   through the same engine using only another configuration profile.

The initial schema-v2 exclusion is an intentionally synthetic teaching example, not a
finding about any company's production data. The first expected inclusion is authored
in the example profile; it is not automatic product approval of a business rule.

## Execution and verification boundaries

| Capability | Current scope |
| --- | --- |
| Actual repository files, safe apply/undo, Git diff | Implemented and locally tested |
| SQL / CTE / expression preview | Real bounded deterministic SQLite; no GoogleSQL equivalence claim |
| Record trace | Configuration-driven lookups and scoped identity mappings; not arbitrary physical row lineage |
| Multiple profiles | Two unrelated registered examples; third profiles need their own tests |
| dbt Core | Explicit CLI adapter + artifact checks; intended-version/live-project tests still required |
| BigQuery | Explicit SDK plan/execute adapter; requires optional dependencies, approved profile and ADC; no live job verified here |
| Native Zed | GPUI integration source and stdio bridge; **native compilation and Mac runtime remain unverified** |
| AI | No bundled LLM or pretend AI animation |

The browser interface is the verified reference client. It is not represented as the
finished native zdbt product. See [architecture](docs/ARCHITECTURE.md),
[native integration](native/README.md), and [verification](docs/VERIFICATION.md).

## Register another project

A project contains `.rowdy/project.json`, a bounded JSON snapshot, and SQL files. The
profile declares models, keys/grain, independent expectations, optional downstream
observations, and typed trace relationships. See `rowdy/examples/orders` for the smallest
complete example. Identifier namespaces and mapping intervals are explicit. There is no
warehouse-wide search, implicit person merge, engine fallback, or network query on hover.

A real dbt project needs explicit package-aware `dbt_selector`, `dbt.binary`,
`dbt.profiles_dir`, `dbt.target` and `dbt.allowed_targets` configuration, plus `--allow-dbt`.
Compilation may contact the warehouse and execute trusted project macros. It is not
advertised as offline or harmless for untrusted repositories. BigQuery requires
`--allow-bigquery` and a reviewed `bigquery` policy (billing project, location,
allowed fully qualified relations, maximum bytes). Local replay does not automatically
translate Jinja/GoogleSQL or change your project's adapter. See [adapters](docs/ADAPTERS.md).

## Test and build

```sh
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --no-build-isolation . -w dist
# Optional UI tests on an ordinary browser:
python3 -m pip install 'playwright==1.57.0'
python3 -m playwright install chromium
python3 scripts/verify_browser.py --require-direct
```

UI verification tries direct navigation first. A declared managed-browser renderer/API
bridge is available only when navigation is explicitly blocked; it is never counted as
a direct-browser or native-app result. Tests and examples contain no company records.

## Public repository boundary

This public repository does **not** import private workflow research, operational source
records, company issue descriptions, the prior private audit archive, or its Git history.
This is a clean public-safe implementation of the selected interactions with explicit
project and execution boundaries. Earlier standalone feature demonstrations are not all
silently claimed to have been ported. See `docs/RELEASE_SCOPE.md` for retained and deferred
capabilities. No production access or company approval is implied by public availability.
