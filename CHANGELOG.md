# Changelog

## 0.2.0 — 2026-10-24

- Bound total output writes with `--max-output-bytes` and Python API equivalents.
  The default is 1 GiB per invocation; split counts both partitions, the index
  and manifest together, and restore counts its new reconstructed file.
- Check each write before sending bytes to disk, reject impossible budgets
  before output creation, and clean up owned files on quota exhaustion without
  removing foreign files. Quota errors use exit 2 and content-free diagnostics.
- Add exact-limit, one-byte-short, index amplification, streamed oversized-line,
  manifest, cast-buffer byte counting and restoration overproduction regressions.
- Add real DuckDB/pandas synthetic export reproduction and source-documented
  Elasticsearch Bulk/MongoDB Extended JSON fixtures. Test damaged variants and
  source-free byte recovery without claiming database semantic validation.
- Freeze a bundle produced by the actual v0.1.0 release and verify two-way
  compatibility. Bundle format 1 and aggregate summary schemas are unchanged.

Upgrade note: existing workflows/restorations larger than 1 GiB need an explicit
higher limit. The quota counts logical content bytes; filesystem allocation,
metadata, existing files, other processes and runtime are outside its scope.

## 0.1.0 — 2026-10-24

- Check and partition physical JSON Lines records with strict UTF-8/JSON validation,
  documented duplicate-key and nesting policies, and optional object-only filtering.
- Publish as jsonl-roundtrip with its own package/command name; reconstruct from
  the bundle alone without requiring the original input file.
- Preserve original bytes, CRLF/LF and unterminated final records in fixed artifacts.
- Stream oversized records and the per-record byte-offset/digest index.
- Verify artifact hashes, metadata schemas, line/stream sequencing, physical LF
  boundaries and final original hash before publishing a restored file.
- Refuse existing outputs and linked/nonregular bundle artifacts; publish complete
  manifests and restored files using exclusive same-filesystem hard links.
- Aggregate-only diagnostics, synthetic examples, adversarial tests and a fresh
  offline wheel-install/CLI/roundtrip/uninstall smoke workflow.

Current limits: Python 3.11+, hard-link-capable output filesystem, closed stable
regular inputs, maximum 64 MiB normal-record limit and 256 container nesting levels.
No inferred repair, encryption, authenticity guarantee or application-level recovery.
