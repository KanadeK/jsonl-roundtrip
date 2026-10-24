# Validation scope for v0.2.0

Local validation used Windows and Python 3.13.16. The following were actually run:

- **87 unittest methods: 85 passed, two platform skips** (POSIX FIFO and this
  Windows host's symlink-creation privilege). This includes 20 output-quota,
  11 export-format and three actual-old-bundle compatibility tests, alongside
  all 53 existing tests.
- Coverage.py measured **92% combined statement/branch coverage**, including
  CLI subprocesses. The CI floor remains 85%. Ruff lint and format checks passed.
- Independent output writers audited actual requested buffer bytes across the
  partitions, index, manifest temporary file and reconstruction temporary file.
  Exact total/one-byte-short, index amplification, oversized streaming, empty
  inputs, strict limits, cleanup and fault-injected overproduction were checked.
  A cast-buffer element-count defect found during review was fixed and retains
  a regression. No test allocates an actual 1 GiB output to check the default.
- **Actual DuckDB 2.0.0 and pandas 3.1.0** regenerated the original synthetic
  fixtures with matching bytes. Each produced three accepted records; each
  deliberately damaged variant produced three accepted/two rejected records.
  Bundle-only restoration matched all original bytes after deleting the inputs.
- Hand-authored Elasticsearch Bulk and MongoDB Extended JSON format fixtures
  were checked separately, including CRLF/unterminated variants and documented
  semantic boundaries. No Elasticsearch server or `mongoexport` was executed.
- The actual public v0.1.0 wheel created the frozen legacy bundle. v0.2.0 restored
  it, and the actual v0.1.0 CLI restored a v0.2.0-created bundle, byte for byte.
  Complete manifests matched; the format and aggregate-summary schemas stayed 1.
- A fresh environment installed the new wheel **offline with --no-index --no-deps**.
  Its installed entrypoint checked exact/insufficient split/restore quotas,
  source-free recovery, all four export fixtures, the legacy bundle, the README
  demonstration and uninstall.
- A separate fresh environment installed the **source distribution**, passed
  `pip check` and all 87 tests, then checked its version, exact/insufficient CLI
  quotas, source-free restoration and uninstall. Every fixture's packaged bytes
  matched the source tree. Source installation can download build tools.
- No third-party runtime dependencies or vendored implementations/data. The
  additional exporters are separately installed development tools; their own
  and transitive licenses remain with their original distributions.

CI is configured for Ubuntu/Python 3.11 and 3.13, Windows/Python 3.13 and
macOS/Python 3.13, plus an Ubuntu/Python 3.13 job actually running the two pinned
exporters. Consult the version's Actions run for remote platform results.

The quota bounds per-invocation logical file-content writes, not filesystem
allocation/metadata, existing files, other processes, runtime or memory. Tests
do not establish encryption, authenticity, power-loss durability, or database
semantic recovery. Reproduce using CONTRIBUTING.md and the fixture provenance.

## Historical v0.1.0 validation

Local validation used Windows and Python 3.13.16.

- 53 unittest methods: 51 passed, two platform-specific checks skipped (POSIX
  FIFO availability and this host's symbolic-link creation privilege).
- Coverage.py measured 91% combined statement/branch coverage, including CLI subprocesses.
- Ruff checks and formatting checks passed; wheel and source distribution built.
- Independent acceptance tests verified exact bytes, line offsets and hashes,
  adversarial metadata with recomputed index hashes, privacy-safe diagnostics,
  corrupt-bundle rejection and refusal to overwrite existing paths.
- Bundle-only reconstruction was checked after removing the synthetic original
  input, so restoration cannot depend on its continued availability.
- A 24 MiB oversized physical line with a 64-byte line policy was quarantined
  without loading it whole. Its tracemalloc regression check stayed below 8 MiB,
  and reconstruction matched the original. This is one tested workload, not a
  universal process-memory guarantee.
- A fresh environment installed the wheel with `--no-index --no-deps`, ran both
  module and installed entrypoints, expected rejection exits, successful check,
  byte-exact reconstruction, the README demo/compare flow and uninstall.
- No third-party runtime dependencies or vendored code. Developer/build tooling
  and the Python runtime are outside that runtime dependency statement.

CI covers Ubuntu/Python 3.11 and 3.13, Windows/Python 3.13 and macOS/Python 3.13.
Consult the actual Actions run for remote platform results. POSIX/link tests are
allowed to skip only for their explicitly checked platform conditions.

Tests do not establish authenticity, encryption, power-loss durability or
application-level recovery. See README.md for the supported filesystem and
stable-input requirements. Reproduce using CONTRIBUTING.md.
