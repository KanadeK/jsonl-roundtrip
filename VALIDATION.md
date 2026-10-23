# Validation scope for v0.1.0

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
