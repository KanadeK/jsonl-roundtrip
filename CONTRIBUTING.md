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
python -m coverage report
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

This repository currently has one maintainer. CI checks and builds artifacts;
it never commits code. Third-party pull requests are reviewed before any
maintainer decision to incorporate them. Preserve upstream license notices
when proposing third-party material. See [SECURITY.md](SECURITY.md) for
private vulnerability reports.
