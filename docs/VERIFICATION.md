# Rowdy 0.6 verification

## Actual local results

- 79 Python unit/integration tests passed on Python 3.13/Linux.
- 27 interactive workflows passed against the actual loopback service, SQLite queries,
  file writes/undo, Git diff and persistent state. No uncaught renderer exceptions.
- A wheel was built with setuptools without external package downloads.
- Two configuration profiles (web sessions and order items) execute and trace using the
  same engine. Web baseline 16 rows becomes 17; selected session completions 0 becomes 1.
- Five independently authored controls pass on the intended candidate. A second edit
  improves the selected event while breaking an existing control, which is reported failed.
- Quoted time functions, quoted aggregates in scalar scope, missing/unrelated dbt artifacts,
  changed semantic context and namespace collisions have explicit regression tests.
- The actual native stdio bridge process previews a registered dirty buffer through the
  authenticated service. This does not establish that the GPUI view compiles or runs.

An initial test exposed overrestrictive scalar-function authorization inside an existing
windowed source view. The fix preserves scalar restrictions on the submitted expression
while allowing approved source-view computation. The complete suite was rerun successfully.

## Browser boundary

This container's Chromium refuses ordinary navigation with ERR_BLOCKED_BY_ADMINISTRATOR.
The verifier attempts direct navigation first. Only for that restriction it mounts shipped
HTML/CSS/JavaScript and forwards requests to the real localhost API through Python. No
browser policy is changed. Browser-only preferences use a declared in-memory shim because
the test document has an opaque origin. Server-side drafts, receipts, actual file changes
and queries are real. Normal browser network delivery and persisted browser preferences
are NOT established by that fallback. `--require-direct` refuses fallback and is used in CI.

Screenshots and video show actual rendered states and executed actions. The walkthrough
adds captions and a pointer as presentation annotations; no result is fabricated. The
system FFmpeg binary was used for capture/conversion. No font files are distributed.

## Reproduce

```sh
python3 -m unittest discover -s tests -v
python3 -m pip wheel --no-deps --no-build-isolation . -w dist
python3 scripts/verify_browser.py --require-direct
python3 scripts/record_walkthrough.py --require-direct
```

Installed-package, clean-clone and remote CI results are reported in the delivery receipt
when observed. A workflow file alone does not count as a remote passing build.

## Not established

Native GPUI compilation/Mac packaging, real BigQuery jobs, intended-version dbt project
execution, optional dialect equivalence, an actual LLM session, enterprise security approval,
patient-data readiness, or measured user/team productivity. The cloud adapter tests use
injected objects or reject disabled/missing dependencies; none is counted as a cloud call.

The rebuilt wheel was installed in a fresh virtual environment and launched from a
separate temporary working directory with PYTHONPATH removed. Nine installed-product
checks passed, including packaged assets, both profiles, actual 16-to-17 evaluation,
independent verification, identity tracing and a real source-file Git diff.
