# Native Rowdy integration

One GPUI evidence pane inside the pinned dbt-Zed workspace, not a browser wrapper.
The implementation covers the investigation loop and explicit guarded source
changes. A compiled/rendered visual-test workspace is not a packaged Rowdy product;
issue #2 and the later pilot gates retain their outstanding acceptance requirements.

## Working surfaces and operations

- Preview the exact active registered SQL buffer, or a declared CTE.
- Click a namespace-bearing typed ID cell to trace the actual retained result row.
- Inspect event/arrival chronology, duplicate delivery representations, ambiguous
  identity evidence, searched-source coverage and presence through modeled stages.
- Open the registered transformation without replacing the historical trace.
- Opt into 300 ms debounced live previews on a frozen source/profile/input/Git
  context. A changed context requires a deliberate new preview, not silent refresh.
- Inspect keyed differences, downstream observations and independent checks in
  the same evidence slot. A record improving cannot hide a failed control.
- Pause on file switches, dock deactivation or leaving the Rowdy tab. Preserve
  historical results and restore editor focus without moving the selection.
- After a current full-output verification, review the exact source diff, then
  explicitly apply it. Undo has its own review and confirmation.

## Two separate authority boundaries

`rowdy.native/2` is the read-only preview/verify/trace protocol. It refuses a
service with either the Core or BigQuery adapter enabled. Continuous preview is
local, fixed-input and opt-in. It never compiles Jinja or falls back to the cloud.

`rowdy.native-edit/1` is a separate reviewed file-change protocol. The service
must be started with explicitly authorized source writes. A preview cannot
approve a change. The independent verification must match the candidate, model,
project, session, source, definitions, Git context and snapshot. A five-minute
persisted review precedes mutation. A journal prevents repeated writes after a
lost response; an uncertain outcome stays uncertain until inspected.

The native client locks the shared buffer while applying, reloads the actual
written file and checks the resulting text before restoring its prior editing
capability. External changes and stale reviews fail rather than being overwritten.
Undo cannot discard later work. No commit, push, merge, build or deployment follows
an apply. Ordinary editor Save remains a different, user-directed operation.

Both channels are for registered synthetic/non-sensitive inputs in this scope.
They are not a sandbox around arbitrary terminals or inherited Zed actions. They
do not establish enterprise data handling, PHI approval or multi-tenant isolation.

## Pinned build and runtime verification

Use a separate checkout of `arezki1990/dbt-zed` at exactly
`3ee08f10debe9464b53b3e1241b56b6deb4e79fb`. The patcher checks unique upstream
anchors and validates every installed module when reapplied; it refuses partial
or mismatched installations. `rowdy_edit_hooks.py` installs the edit module into
the same native view rather than adding another panel.

The GitHub workflows perform these distinct checks:

| Workflow | Evidence |
| --- | --- |
| Verify Rowdy | Complete source tests, built package and direct-browser reference regression |
| macOS local service acceptance | Actual Mac Python/SQLite capabilities and complete source tests |
| Native GPUI compile gate | 10 standalone process tests, 16 lifecycle tests and pinned crate typecheck |
| Native GPUI runtime evidence | Linked visual-test executable, actual GPUI callbacks/buffers, service and Metal captures |

The runtime gate creates two disposable non-sensitive projects, explicitly turns
both cloud adapters off, disables inherited dbt auto-install/parse behavior, and
starts the authenticated local service. It dispatches real native pointer events,
edits the real Editor buffer, catches a deliberately failing control, reviews a
source change, applies it and undoes it. Source bytes, Git diff and buffer text
are checked independently. A successful run requires all nine native PNG captures
and a machine-readable `result.json`; it cannot substitute browser evidence.

Run the complete native test on a suitable Mac from this repository:

```sh
python3 -m pip install --no-deps --no-build-isolation -e .
python3 native/apply.py /path/to/pinned-dbt-zed --check
python3 native/apply.py /path/to/pinned-dbt-zed
python3 native/apply.py /path/to/pinned-dbt-zed --check
python3 native/visual_apply.py /path/to/pinned-dbt-zed
# In that pinned checkout, with its required Rust/Xcode toolchain:
cargo build --locked -p zed --bin zed_visual_test_runner \
  --features visual-tests,gpui_platform/runtime_shaders
# Back in this Rowdy checkout:
python3 scripts/verify_native_visual.py \
  --binary /path/to/pinned-dbt-zed/target/debug/zed_visual_test_runner \
  --out /path/to/separate-native-evidence
```

The visual instrumentation adds test-only control-bound/state access. It captures
the app's own Metal surface; it does not alter OS Screen Recording permissions or
provide an alternate execution engine. No native success is implied by a workflow
being present: inspect the actual result, commit and logs in PR #7.

## Remaining product acceptance

The display currently caps visible rows/events at 100. Native expression-watch
controls, a full virtualized grid, restart restoration of the native pane, and
arbitrary project discovery are not part of this increment. Context is checked
at action start/completion, not continuously monitored after a result appears.

A regular branded host must separately establish updater/data-directory isolation,
normal installation/launch, packaging, keyboard/accessibility coverage and an
actual user session. The visual-test executable is not that distributable app.
Warehouse/IAM and real-data questions remain deferred.

See [macOS portability fixes](../docs/NATIVE_PORTABILITY.md) for the reproduced
service failures and regression coverage that unblocked this verification work.
