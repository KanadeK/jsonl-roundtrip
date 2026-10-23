# jsonl-roundtrip

Keep the usable records in a damaged JSON Lines export **without losing the
original bytes**. Quarantine bad physical lines, record offsets and SHA-256
digests, and reconstruct the original file only after verifying the bundle.

[中文说明](README.zh-CN.md) · [MIT](LICENSE) · Python 3.11+ · No runtime dependencies

This is an offline CLI for data imports, logs and exported files. It never edits
the source, guesses missing JSON, or prints record contents. The accepted and
rejected artifacts still contain the original data: **offline is not encrypted**.

After splitting, the bundle alone is enough to reconstruct the original bytes;
restoration does not require keeping the original input file alongside it.

## Install

```sh
git clone https://github.com/KanadeK/jsonl-roundtrip.git
cd jsonl-roundtrip
python -m venv .venv
```

Activate the environment using the command for your shell:

```sh
# Linux/macOS
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Then install:

```sh
python -m pip install .
```

Alternatively, download the wheel from this repository's GitHub Release and use
`python -m pip install --no-index --no-deps path/to/jsonl_roundtrip-0.1.0-py3-none-any.whl`.
No PyPI publication is implied. Installing from source can download setuptools
as build tooling; the installed command has no network behavior.

Publishing complete bundles and restored files uses an exclusive hard link in
the destination filesystem. Use a filesystem supporting regular-file hard links,
such as NTFS, ext4 or APFS. FAT/exFAT and some mounted/network filesystems are not
supported for output. Unsupported publication fails without overwriting files.

## Try a complete, reversible workflow

```sh
python examples/make_demo.py demo.jsonl
jsonl-roundtrip check demo.jsonl --json
jsonl-roundtrip split demo.jsonl demo-bundle --json
jsonl-roundtrip restore demo-bundle restored.jsonl --json
python examples/compare.py demo.jsonl restored.jsonl
```

The generator creates five clearly synthetic physical records: three valid JSON
values, one malformed text record and one invalid UTF-8 record. It includes CRLF
and a final line without LF. `check` and `split` exit **1** because two records
are quarantined; this is an expected, complete outcome. `restore` exits **0** and
`compare.py` confirms equal bytes and SHA-256. In a shell using `set -e`, handle
exit 1 explicitly so the demonstration continues to restoration.

Use a new bundle directory and a new output file each time. The command refuses
existing paths, including empty directories and dangling symbolic links.
The restore destination must be **outside the bundle directory**.

## Commands and policy

```text
jsonl-roundtrip check INPUT [--max-line-bytes N] [--objects-only] [--json]
jsonl-roundtrip split INPUT OUTPUT_DIR [--max-line-bytes N] [--objects-only] [--json]
jsonl-roundtrip restore BUNDLE OUTPUT [--json]
```

`python -m jsonl_roundtrip` provides the same interface. Top-level `--help` and
`--version` do not need input files.

Input is split on the physical **LF byte only**. CRLF is preserved. A missing
final LF is allowed; a final LF does not create an additional empty record.
Unicode line separators inside JSON strings do not create physical records.
The source must be a stable, closed regular file; direct symbolic links and
nonregular source files are rejected. Stop its writer before checking or splitting.

Within the line limit, each record must be strict UTF-8 containing one JSON value.
Objects, arrays, strings, numbers, booleans and `null` are accepted. Numbers are
checked syntactically without conversion to Python integers or floats, so long
integer tokens and large exponents retain their exact spelling. No data is
reformatted or normalized.

Additional policies are explicit:

- BOM, blank/JSON-whitespace-only lines, invalid UTF-8 and malformed JSON are rejected.
- Literal `NaN`, `Infinity` and `-Infinity` are rejected.
- Duplicate object keys, including equal decoded escaped keys, are rejected.
  This is an extra strict policy, rather than a claim that every JSON consumer
  rejects duplicate keys. Unicode normalization is not applied to keys.
- Container nesting above **256** levels is quarantined. A lower interpreter
  recursion limit can also result in a `nesting_limit` rejection.
- `--objects-only` additionally quarantines every nonobject top-level value.
- `--max-line-bytes` defaults to **8 MiB**, accepts integers from 1 through
  **64 MiB**, and **includes the terminator**. Oversized physical lines are
  streamed directly into quarantine in 64 KiB chunks, without buffering the
  entire line. `line_too_long` takes precedence over its other possible errors.

Reason codes are `line_too_long`, `invalid_utf8`, `bom`, `blank_line`,
`invalid_json`, `duplicate_key`, `nonfinite_number`, `nesting_limit` and
`not_object`. Records are classified once; a record receives one reason.

| Exit | Meaning |
| --- | --- |
| `0` | No rejected records, or restoration completed successfully |
| `1` | Rejected records found; for `split`, a complete usable bundle was created |
| `2` | Usage/configuration, filesystem, changing-input, or corrupt/incomplete-bundle error |

`--json` prints only aggregate counts/digests. Operational errors are fixed,
content-free stderr messages, including with `--json`; they are not success
summaries. Neither record text, object keys nor source paths enter the report.
Unknown command-line arguments are handled by Python's argument parser; avoid
passing sensitive content as command-line options.

## Bundle format, version 1

A completed bundle has four fixed files:

| File | Contents |
| --- | --- |
| `accepted.jsonl` | Exact bytes of accepted physical records in original order |
| `rejected.bin` | Exact bytes of rejected physical records in original order |
| `index.jsonl` | One small JSON record per original physical record |
| `manifest.json` | Version, counts, policy, input digest and all three artifact sizes/digests |

Each index record has exactly these fields:

```json
{"line":1,"offset":0,"length":10,"stream":"accepted","stream_offset":0,"reason":null,"sha256":"<64 lowercase hex characters>"}
```

`line` starts at 1. Byte offsets start at 0. `length` includes any CR/LF terminator.
`stream` is `accepted` or `rejected`; accepted records have a null reason.
The example above illustrates the schema, not a real digest or a test result.

The manifest contains `format_version`, `total_lines`, `accepted_lines`,
`rejected_lines`, `input_bytes`, `input_sha256`, `reason_counts`, `policy` and
`artifacts`. `policy` contains `max_line_bytes` and `objects_only`. The `artifacts`
object maps the three fixed filenames to objects with `bytes` and `sha256`.
Source filenames, machine paths, timestamps and record contents are omitted.
The Python `partition` and `inspect` APIs return the seven aggregate manifest
fields through `reason_counts`; `restore` returns `format_version`, `bytes` and
`sha256`. Operational exceptions use `QuarantineError(ValueError)`.

Restore rejects unknown/duplicate metadata keys, unsupported versions, invalid
types and hashes, symlink/nonregular artifact files, gaps/overlaps/out-of-order
index entries, invalid physical line boundaries, per-record digest mismatches,
unindexed bytes and final input-digest mismatches. Manifest input is capped at
64 KiB, index records at 2 KiB, and metadata nesting at 16. Counts are nonnegative
signed 64-bit values. Paths in metadata can never select other files.

Restore verifies the byte reconstruction; it does not rerun the record classifier
or authenticate who created the bundle. Anyone able to replace the artifacts and
all digests can fabricate another consistent bundle. Keep bundles in trusted
storage and protect them with a separate authenticity mechanism if needed.

## Failure, resource and privacy boundaries

`split` creates its output directory exclusively with private permissions where
the OS supports them. Artifact handles are closed before a complete manifest is
published last. An incomplete directory without a valid manifest is not a
complete bundle; do not consume its accepted file as if the split succeeded.
Handled failures remove only files created by this invocation. If other software
adds files concurrently, cleanup may leave an incomplete directory behind.

`restore` writes to a private temporary file in the destination's parent, validates
everything, then exclusively publishes the final name. Handled validation failures
leave no partial final output. Exclusive hard-link publication is atomic at the
process/filesystem-operation level; this tool does **not** fsync directories or
promise power-loss durability. Abrupt termination can leave temporary files or an
incomplete bundle; before removing them, confirm the process has stopped and
inspect only the paths created by your invocation. After publication, an abrupt
termination can leave both the final file and its private temporary hard link.

Before/after identity/timestamp checks and a second source hash detect ordinary
concurrent changes. They are not a lock or a guarantee against a malicious local
process changing files during the operation. `check` and `split` read the source
twice. Restore reads artifacts to verify hashes and again to reconstruct them.

The index grows with the number of physical records. For many tiny or blank lines,
it can be **much larger than the input**. Splitting needs space for both partitions
(together equal to input size), the index and manifest. Restoration additionally
needs the original-size temporary output. There is no total line-count, output-size
or runtime quota beyond the format's count limits; use OS/job-runner quotas for
untrusted large workloads. Normal records can use memory proportional to the
configured line limit and decoded JSON; the streaming index does not retain the
whole file in memory.

Accepted bytes, rejected bytes and restored files remain **raw private data**.
Restrict access to the bundle as you would to the original. Hashes and offsets
can also reveal metadata. There is no upload, telemetry, secret detection,
encryption, schema validation or semantic session repair. One quarantined
physical line might contain several concatenated records or a referenced event;
the tool preserves it for review rather than claiming the remaining session is
application-correct.

## Alternatives and development

[jsonlines](https://github.com/wbolster/jsonlines) and
[ndjson](https://github.com/ndjson/ndjson.js) already support reading while
skipping invalid records. [jsonrepair](https://github.com/josdejong/jsonrepair)
repairs malformed JSON by inferring edits. Choose them for those workflows.
[jsonl-quarantine](https://github.com/KanadeK/jsonl-quarantine) provides a
source-referenced quarantine/accounting workflow whose verifier requires the
original input. This independently implemented tool uses its own package and
command name and adds verified reconstruction from the bundle alone.
This project's focus is **raw-byte partitions plus verified reversible evidence**;
it does not invent another JSON syntax or claim novel repair algorithms.

See [CONTRIBUTING.md](CONTRIBUTING.md) for actual checks and the fresh wheel-install
smoke test, [SECURITY.md](SECURITY.md) for private reporting, and
[CHANGELOG.md](CHANGELOG.md) for the version boundary. CI checks Python 3.11/3.13
and Windows/macOS/Linux; platform-dependent tests report skips explicitly.

Uninstall with `python -m pip uninstall jsonl-roundtrip`. Delete the virtual
environment and your synthetic `demo.jsonl`, `demo-bundle` and `restored.jsonl`
when no longer needed. The program does not create a persistent service or cache.
