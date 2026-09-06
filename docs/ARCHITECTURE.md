# Architecture

Rowdy 0.6 is a repository-backed local reference client plus an experimental native
integration, not a completed native editor distribution.

```
Browser reference / experimental GPUI bridge
                 |
       Authenticated loopback service
                 |
     Registered Project + frozen context
       /         |                 \
Local evaluator  Trace lookups    Explicit optional adapters
SQLite worker   scoped identities  dbt Core / BigQuery SDK
                 |
        Immutable receipts + drafts
```

## Authority and identity

`project.py` binds explicitly registered relative model paths to disk content. The context
digest includes the complete profile (keys, grain, expectations, trace relationships),
all registered source hashes, snapshot content and actual Git HEAD. UI arrangements are
not semantic context. Run receipts retain submitted code, inputs and outcome; historical
observations are never relabeled after a new edit. In-flight requests carry a session,
code revision and monotonically increasing request generation. Cancellation is not success.

`Project.apply` requires an explicit write capability and both current context and disk
hashes. It checks registered paths and rejects symlinks; it writes one file atomically
and returns actual `git diff`. It does not commit, push, merge or deploy. Another local
program can still race an OS file replacement in the narrow check/replace interval; this
is not a kernel compare-and-swap primitive. Native editor file locks/dirty-buffer integration
and target-platform tests remain a pilot gate.

## Execution

`engine.py` constructs in-memory tables from a bounded non-sensitive snapshot and SQL views
from actual model files. A candidate replaces only its selected view. It does not filter
inputs to the watched user before joins, ranks or aggregation. Registered downstream
observations run against baseline and candidate. Independently authored expected results
are evaluated separately from the output under inspection.

SQLite's authorizer is the read/mutation/function boundary. Tokenization is used only for
supported CTE/expression navigation, not as a security claim. SQL functions are checked
by SQLite's canonical name, so quoted spelling cannot enable clock functions. Scalar
watches cannot introduce aggregates/windows, although their input views can contain an
approved deduplication window. Output, statement, operation time and concurrency are bounded.

`trace.py` queries registered stages using bound values, selected namespace and half-open
UTC event window. It retains arrival timestamps independently. Identity bridges have
explicit intervals; overlapping assignments remain unresolved. Duplicate deliveries are
representations of one registered event key. Aggregates remain distinct-grain related
observations. It is not physical query lineage or a universal identity resolution service.
Profile authors must test collisions and sufficiency for their source domain.

## Interface

The reference client has one editor and one evidence slot. Data, trace, changes and checks
are views of that evidence, not competing panels. An explicit selection opens deeper detail.
Results never steal focus when a background request completes. The current editor is a
small dependency-free textarea/highlight layer, not feature parity with Zed or VS Code.

## Public boundary

The public repository uses generic synthetic web and order examples. It does not contain
private workflow recordings, operational issue text, healthcare records or private history.
Only synthetic or explicitly non-sensitive profiles are accepted by this release. Merely
naming a profile `non_sensitive` is not proof of de-identification; the owner must approve
it. Automatic retention expiry, OS-backed protected storage, enterprise authorization and
agent export grants are not established here. Do not load patient or secret data.
