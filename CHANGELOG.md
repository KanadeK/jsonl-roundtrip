# Changelog

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
