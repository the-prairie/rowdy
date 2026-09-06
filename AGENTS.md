# Rowdy implementation contract

- Read README.md and docs/RELEASE_SCOPE.md before making completion claims.
- Keep one primary surface plus one evidence surface. Details replace, not stack, panels.
- Code is bound to registered actual files. All writes require explicit user action and
  source + semantic context preconditions. Never clean, commit, push or deploy unrelated work.
- A local run is not warehouse verification. A compile is not a build. A green process
  exit without matching invocation/resource evidence is unverified.
- Keep expected outcomes independent of the evaluated implementation. A successful selected
  record must not hide a failed control. Preserve useful no-change outcomes.
- Do not use patient records, private company research, secrets, or source exports in this
  public repo. This release is not approved for PHI. No real-data export to external agents.
- Continuous replay is opt-in, fixed-input and local. Never fall back to cloud execution.
- Namespaces and time-bound identity mappings are not universal person resolution.
- Native build and actual cloud execution need their own evidence. Test doubles and browser
  screenshots cannot substitute for them.
- Run unit and browser tests. Record direct vs bridged transport. Never change browser policy.
- Verification reports must list actual counts, environment and failures, not optimistic prose.
