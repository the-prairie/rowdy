# Native Rowdy integration

This increment implements one GPUI evidence pane in a pinned dbt-Zed checkout.
It is not a browser wrapper. Issue #2 remains open until launched/packaged Mac,
guarded source-change, keyboard/accessibility and user-acceptance gates pass.

## Scope

- Preview the exact active registered SQL buffer; optionally select a declared CTE.
- Click a registered, namespace-bearing ID cell to trace its original retained row.
- Inspect saved-source event/arrival chronology, duplicate representations, identity
  ambiguity, source coverage and stage presence; open a registered model's SQL.
- Watch a selected event while editing a supported local transformation.
- Enable debounced live preview only after an explicit successful preview establishes
  a fixed project/profile/input/Git binding. Input changes require explicit refresh.
- Inspect keyed differences, downstream observations and independent local checks.
- Pause on file switches, Rowdy-tab changes or dock deactivation; retain historical
  results. Returning to the editor restores focus without moving its selection.

Local synthetic/non-sensitive profiles only. The native v2 channel **refuses a
service configured with dbt or BigQuery enabled**. It exposes no compilation,
warehouse execution, export, apply, undo, merge, or deployment operation. This does
not sandbox arbitrary terminal commands or unrelated actions inherited from Zed.
Ordinary editor saves remain ordinary user-directed saves, not verified Rowdy apply.

## Build

1. Check out `arezki1990/dbt-zed` exactly at
   `3ee08f10debe9464b53b3e1241b56b6deb4e79fb` in a separate clean directory.
2. Run `python native/apply.py /path/to/dbt-zed --check`, then without `--check`.
   A second check validates the installed native modules. No fuzzy upstream edits.
3. Install this Rowdy package in your chosen Python environment and start its
   local service on the registered non-sensitive projects with no cloud flags.
4. Set `ROWDY_PYTHON` to that Python executable and `ROWDY_HOME` to the service home.
5. Compile the pinned host using its documented toolchain. At minimum run
   `cargo check --locked -p dbt_ui --lib`. A check is not an application build.
6. When an actual host executable is built, open the dbt results dock and select
   **Rowdy**. Use **Preview**, a result ID, **Open SQL**, **Live**, **Changes**, and
   **Verify locally**. The original trace always describes saved-source inputs.

Do not turn on inherited dbt auto-install/parse commands for this local-only trial.
The visual runner explicitly disables them; a regular host configuration must be
reviewed independently. Product naming/updater isolation and signed distribution
remain separate work; this is not an installable Rowdy release.

## Verification layers

- `python -m unittest discover -s tests -v`: includes v2 protocol tests against
  the actual authenticated loopback service, source files and SQLite, plus prior tests.
- Standalone `rustc --test native/rowdy_state.rs` and `rowdy_process.rs`:
  scheduling and process lifecycle, including late/cancelled responses.
- `Native GPUI compile gate`: actual macOS check of the pinned patched crate.
- `Native GPUI runtime evidence`: builds the actual upstream visual-test executable,
  instruments only control bounds and state observations, dispatches native pointer
  events, edits real Editor buffers, invokes the actual service, and captures its
  Metal-rendered surface. It must pass on a real macOS runner; there is no browser
  fallback and no change to OS privacy permissions. Test source lives in
  `visual_apply.py`, `rowdy_visual_smoke.rs`, and `scripts/verify_native_visual.py`.

Read the **observed** workflow outcome and artifact logs in PR #7. Instrumentation
compiling or a workflow existing is not evidence that the rendered walkthrough ran.

## Deliberate limitations

First 100 returned rows/events are displayed with explicit caps. No full native
virtualized grid, expression-watch control, arbitrary tracing, profile editor,
restart restore of the native pane, or guarded apply/undo is claimed here.
Context is checked at action start/completion; retained evidence is a historical
observation, not a continuously monitored warehouse or file-system freshness claim.
A full target-Mac app launch and signed packaging remain unverified until separately
observed. Cloud/IAM questions remain deferred.
