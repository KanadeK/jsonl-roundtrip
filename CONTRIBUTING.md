# Contributing

Report a reproducible problem with the Python version, the command, and a
minimal **synthetic** input. Never attach production data, credentials or logs
containing private content. Discuss behavior changes in an issue first.

```sh
python -m venv .venv
# POSIX: source .venv/bin/activate
# PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m coverage run -m unittest discover -s tests -v
python -m coverage combine
python -m coverage report --fail-under=85
python -m build
python scripts/installed_smoke.py
```

Keep core behavior local, deterministic and free of runtime dependencies.
Add regression coverage for input handling, byte/data preservation and CLI
exit behavior. Document supported input formats and failure modes.

Keep input content out of diagnostics, manifests and shared test output. Add
corruption cases that recompute artifact hashes when testing index semantics;
otherwise a hash mismatch can hide the validation path being exercised. Test
closed-source mutation, preservation of CRLF/no-final-LF, and cleanup after failed
publication. Platform-limited tests must state why they skip.

`installed_smoke.py` creates a temporary venv using Python's bundled pip, installs
the built wheel with `--no-index --no-deps`, then checks the module and installed
entrypoint, quarantine/restore byte equality and uninstall from a clean working
directory. It needs a hard-link-capable temporary filesystem. Building from
source may download setuptools; the smoke installation and installed program
do not use the network. Keep exactly one current wheel in `dist/`.

The smoke workflow also checks exact/insufficient output budgets, failure cleanup,
all four export fixtures and the actual v0.1.0 bundle using the installed command.
Fixtures are included in the source distribution; Git text conversion must stay
disabled for them so CRLF and unterminated records remain unchanged.

To rerun the real exporters, use a separate Python 3.13 environment:

```sh
python -m pip install .
python -m pip install -r scripts/export-requirements.txt
python scripts/export_producer_smoke.py
```

This runs pinned DuckDB/pandas against original synthetic rows and checks the
committed bytes plus clean/damaged bundle-only recovery. These are optional
development dependencies, never runtime requirements. See
[fixture provenance](tests/fixtures/exports/README.md) before updating them.
The main tests need no exporter packages. Quota tests audit actual requested
file-write bytes across all output handles, including temporary manifest/output
files; do not replace those assertions with checks of final file size alone.

This repository currently has one maintainer. CI checks and builds artifacts;
it never commits code. Third-party pull requests are reviewed before any
maintainer decision to incorporate them. Preserve upstream license notices
when proposing third-party material. See [SECURITY.md](SECURITY.md) for
private vulnerability reports.
