"""Independent byte-quota acceptance: size real outputs and observe real writes.

No production sizing helper or patched default is used to establish the limits.
Fault injection exercises the output boundary even if an earlier validator were
to miss a malformed source/index; it does not claim those inputs normally pass.
"""

import hashlib
import inspect as python_inspect
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from jsonl_roundtrip import core

SUMMARY_FIELDS = {
    "format_version",
    "total_lines",
    "accepted_lines",
    "rejected_lines",
    "input_bytes",
    "input_sha256",
    "reason_counts",
}
BUNDLE_FILES = {"accepted.jsonl", "rejected.bin", "index.jsonl", "manifest.json"}
QUOTA_MESSAGE = "Total output byte quota exceeded"


class _IntSubclass(int):
    pass


class _ObservedWriter:
    def __init__(self, handle, audit, path):
        self._handle = handle
        self._audit = audit
        self._path = path

    def write(self, data):
        length = memoryview(data).nbytes
        self._audit.test.assertLessEqual(
            self._audit.bytes + length,
            self._audit.limit,
            "The underlying writer received bytes exceeding the invocation quota",
        )
        written = self._handle.write(data)
        self._audit.bytes += written
        self._audit.events.append((self._path, length, written))
        return written

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *args):
        return self._handle.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._handle, name)


class _WriteAudit:
    """Observe actual fd-backed output writes, including temporary metadata."""

    def __init__(self, test, limit):
        self.test = test
        self.limit = limit
        self.bytes = 0
        self.events = []
        self.opened = []
        self.paths = {}
        self.stack = ExitStack()

    def __enter__(self):
        original_open = os.open
        original_fdopen = os.fdopen

        def open_fd(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            self.paths[fd] = Path(path)
            return fd

        def fdopen(fd, *args, **kwargs):
            handle = original_fdopen(fd, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if "w" not in mode:
                return handle
            path = self.paths.get(fd)
            self.test.assertIsNotNone(path, "Unidentified output descriptor")
            self.opened.append(path)
            return _ObservedWriter(handle, self, path)

        self.stack.enter_context(patch.object(core.os, "open", side_effect=open_fd))
        self.stack.enter_context(patch.object(core.os, "fdopen", side_effect=fdopen))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


class OutputQuotaCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.jsonl"
        self.bundle = self.root / "bundle"
        self.output = self.root / "restored.jsonl"

    def quota_error(self, operation):
        with self.assertRaises(core.OutputQuotaExceeded) as caught:
            operation()
        self.assertIsInstance(caught.exception, core.QuarantineError)
        self.assertEqual(str(caught.exception), QUOTA_MESSAGE)

    def probe(self, raw, **options):
        self.source.write_bytes(raw)
        baseline = self.root / "baseline"
        summary = core.partition(self.source, baseline, **options)
        self.assertEqual({p.name for p in baseline.iterdir()}, BUNDLE_FILES)
        sizes = {p.name: p.stat().st_size for p in baseline.iterdir()}
        return summary, sizes

    def cli(self, *args):
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        return subprocess.run(
            [sys.executable, "-m", "jsonl_roundtrip", *map(str, args)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )

    def assert_no_output(self):
        self.assertFalse(os.path.lexists(self.output))
        self.assertFalse(list(self.root.glob(".jsonl-roundtrip-*.tmp")))


class OutputQuotaBoundaryTests(OutputQuotaCase):
    def test_default_and_keyword_contract_without_giant_allocations(self):
        self.assertEqual(core.DEFAULT_MAX_OUTPUT_BYTES, 1 << 30)
        for operation in (core.partition, core.restore):
            parameter = python_inspect.signature(operation).parameters["max_output_bytes"]
            self.assertEqual(parameter.default, 1 << 30)
            self.assertIs(parameter.kind, python_inspect.Parameter.KEYWORD_ONLY)
        self.assertNotIn("max_output_bytes", python_inspect.signature(core.inspect).parameters)
        self.source.write_bytes(b"{}\n")
        before = set(self.root.iterdir())
        self.assertEqual(core.inspect(self.source)["input_bytes"], 3)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_strict_limit_types_are_rejected_before_any_input_or_output_io(self):
        invalid = (False, True, None, 0, -1, 1.0, "1", Decimal(1), _IntSubclass(1), 2**63)
        for value in invalid:
            with self.subTest(value=repr(value)):
                with (
                    patch.object(core, "_regular_reader", side_effect=AssertionError("Input I/O")),
                    patch.object(Path, "mkdir", side_effect=AssertionError("Output mkdir")),
                ):
                    with self.assertRaises(core.QuarantineError):
                        core.partition(self.source, self.bundle, max_output_bytes=value)
                with (
                    patch.object(Path, "lstat", side_effect=AssertionError("Input I/O")),
                    patch.object(
                        core.tempfile, "mkstemp", side_effect=AssertionError("Output temp")
                    ),
                ):
                    with self.assertRaises(core.QuarantineError):
                        core.restore(self.bundle, self.output, max_output_bytes=value)

    def test_maximum_supported_positive_integer_is_accepted(self):
        self.source.write_bytes(b"{}\n")
        summary = core.partition(self.source, self.bundle, max_output_bytes=2**63 - 1)
        result = core.restore(self.bundle, self.output, max_output_bytes=2**63 - 1)
        self.assertEqual(summary["input_bytes"], result["bytes"])
        self.assertEqual(self.output.read_bytes(), b"{}\n")

    def test_split_exact_actual_four_file_size_is_inclusive(self):
        raw = b'{"kept":true}\r\nPRIVATE_REJECT_SENTINEL\nnull'
        original_summary, sizes = self.probe(raw)
        limit = sum(sizes.values())
        with _WriteAudit(self, limit) as audit:
            summary = core.partition(self.source, self.bundle, max_output_bytes=limit)
        self.assertEqual(summary, original_summary)
        self.assertEqual(set(summary), SUMMARY_FIELDS)
        self.assertEqual(sum(p.stat().st_size for p in self.bundle.iterdir()), limit)
        self.assertEqual(audit.bytes, limit, "Temporary manifest is counted once, not per name")
        self.assertEqual(
            {p.name for p, _, _ in audit.events},
            BUNDLE_FILES - {"manifest.json"} | {".manifest.tmp"},
        )
        manifest = json.loads((self.bundle / "manifest.json").read_bytes())
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(set(manifest), SUMMARY_FIELDS | {"policy", "artifacts"})
        self.assertEqual(set(manifest["policy"]), {"max_line_bytes", "objects_only"})
        self.assertEqual(self.source.read_bytes(), raw)

    def test_split_one_less_than_actual_size_fails_at_manifest_without_overwrite(self):
        raw = b"{}\nbroken\n"
        _, sizes = self.probe(raw)
        limit = sum(sizes.values()) - 1
        marker = self.root / "unrelated.txt"
        marker.write_bytes(b"KEEP FOREIGN FILE")
        with _WriteAudit(self, limit) as audit:
            self.quota_error(
                lambda: core.partition(self.source, self.bundle, max_output_bytes=limit)
            )
        self.assertEqual(audit.bytes, sum(sizes[name] for name in BUNDLE_FILES - {"manifest.json"}))
        self.assertNotIn(".manifest.tmp", {path.name for path, _, _ in audit.events})
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(marker.read_bytes(), b"KEEP FOREIGN FILE")

    def test_input_size_over_limit_fails_before_bundle_mkdir(self):
        self.source.write_bytes(b"{}\n")
        with patch.object(Path, "mkdir", side_effect=AssertionError("mkdir happened")):
            self.quota_error(lambda: core.partition(self.source, self.bundle, max_output_bytes=2))
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.source.read_bytes(), b"{}\n")

    def test_index_amplification_is_included_even_when_source_size_fits(self):
        raw = b"0\n" * 12
        self.source.write_bytes(raw)
        with _WriteAudit(self, len(raw)) as audit:
            self.quota_error(
                lambda: core.partition(self.source, self.bundle, max_output_bytes=len(raw))
            )
        self.assertGreater(audit.bytes, 0)
        self.assertNotIn(".manifest.tmp", {path.name for path in audit.opened})
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.source.read_bytes(), raw)

    def test_empty_split_still_counts_final_metadata(self):
        summary, sizes = self.probe(b"")
        self.assertEqual(summary["input_bytes"], 0)
        self.assertGreater(sizes["manifest.json"], 0)
        exact = sum(sizes.values())
        with _WriteAudit(self, exact) as audit:
            core.partition(self.source, self.bundle, max_output_bytes=exact)
        self.assertEqual(audit.bytes, exact)
        second = self.root / "too-small"
        with _WriteAudit(self, exact - 1) as audit:
            self.quota_error(
                lambda: core.partition(self.source, second, max_output_bytes=exact - 1)
            )
        self.assertEqual(audit.bytes, 0)
        self.assertFalse(second.exists())

    def test_restore_exact_input_size_ignores_existing_bundle_bytes(self):
        raw = b'{"a":1}\r\nPRIVATE_REJECT_SENTINEL\nnull'
        self.source.write_bytes(raw)
        core.partition(self.source, self.bundle)
        self.assertGreater(sum(p.stat().st_size for p in self.bundle.iterdir()), len(raw))
        marker = self.root / "foreign-large-file"
        marker.write_bytes(b"foreign" * 1000)
        with _WriteAudit(self, len(raw)) as audit:
            summary = core.restore(self.bundle, self.output, max_output_bytes=len(raw))
        self.assertEqual(self.output.read_bytes(), raw)
        self.assertEqual(summary["bytes"], len(raw))
        self.assertEqual(audit.bytes, len(raw), "Hard-link publication must not count bytes twice")
        self.assertEqual(marker.read_bytes(), b"foreign" * 1000)

    def test_restore_over_declared_quota_refuses_before_temp_creation(self):
        raw = b"{}\nnull\n"
        self.source.write_bytes(raw)
        core.partition(self.source, self.bundle)
        before = {p.name: p.read_bytes() for p in self.bundle.iterdir()}
        with patch.object(core.tempfile, "mkstemp", side_effect=AssertionError("temp created")):
            self.quota_error(
                lambda: core.restore(self.bundle, self.output, max_output_bytes=len(raw) - 1)
            )
        self.assert_no_output()
        self.assertEqual({p.name: p.read_bytes() for p in self.bundle.iterdir()}, before)

    def test_empty_restore_uses_zero_data_bytes_under_positive_quota(self):
        self.source.write_bytes(b"")
        core.partition(self.source, self.bundle)
        with _WriteAudit(self, 1) as audit:
            result = core.restore(self.bundle, self.output, max_output_bytes=1)
        self.assertEqual(result["bytes"], 0)
        self.assertEqual(self.output.read_bytes(), b"")
        self.assertEqual(audit.bytes, 0)


class OutputQuotaDefenseTests(OutputQuotaCase):
    def test_split_all_data_paths_reserve_before_delegating_to_underlying_writers(self):
        # Simulate a producer supplying more data after the initial source-size
        # preflight. The original file remains unchanged; quota must stop writes
        # before later source-integrity validation becomes relevant.
        cases = (
            ("accepted", b'"' + b"a" * 2048 + b'"\n', 1000, 4096),
            ("rejected", b"x" * 2048 + b"\n", 1000, 4096),
            ("oversize_chunks", b"x" * 200000 + b"\n", 100000, 16),
        )
        original_scan = core._scan
        for label, raw, limit, line_limit in cases:
            with self.subTest(path=label):
                self.source.write_bytes(b"")
                target = self.root / label

                def injected_scan(_source, *args, raw=raw, **kwargs):
                    return original_scan(io.BytesIO(raw), *args, **kwargs)

                with (
                    _WriteAudit(self, limit) as audit,
                    patch.object(core, "_scan", side_effect=injected_scan),
                ):
                    self.quota_error(
                        lambda target=target, limit=limit, line_limit=line_limit: core.partition(
                            self.source,
                            target,
                            max_output_bytes=limit,
                            max_line_bytes=line_limit,
                        )
                    )
                if label == "oversize_chunks":
                    self.assertGreater(audit.bytes, 0, "A prior streamed chunk should fit")
                    self.assertEqual({p.name for p, _, _ in audit.events}, {"rejected.bin"})
                self.assertFalse(target.exists())
                self.assertEqual(self.source.read_bytes(), b"")

    def test_restore_chunk_writer_cannot_bypass_quota_if_reconstructor_overproduces(self):
        self.source.write_bytes(b"123456789\n")
        core.partition(self.source, self.bundle)
        before = {p.name: p.read_bytes() for p in self.bundle.iterdir()}

        def overproduce(_manifest, _handles, output):
            output.write(b"a" * 7)
            output.write(b"b" * 4)
            self.fail("Second write exceeded the cap but was accepted")

        with (
            _WriteAudit(self, 10) as audit,
            patch.object(core, "_reconstruct", side_effect=overproduce),
        ):
            self.quota_error(lambda: core.restore(self.bundle, self.output, max_output_bytes=10))
        self.assertEqual(audit.bytes, 7)
        self.assert_no_output()
        self.assertEqual({p.name: p.read_bytes() for p in self.bundle.iterdir()}, before)

    def test_restore_writer_counts_buffer_bytes_instead_of_buffer_elements(self):
        # File.write accepts buffer objects. Counting elements rather than bytes
        # would let a future reconstruction caller bypass the central boundary.
        self.source.write_bytes(b"123456789\n")
        core.partition(self.source, self.bundle)

        def overproduce_buffer(_manifest, _handles, output):
            output.write(memoryview(b"x" * 36).cast("I"))
            self.fail("A 36-byte buffer exceeded the ten-byte quota")

        with (
            _WriteAudit(self, 10) as audit,
            patch.object(core, "_reconstruct", side_effect=overproduce_buffer),
        ):
            with self.assertRaises(core.QuarantineError):
                core.restore(self.bundle, self.output, max_output_bytes=10)
        self.assertEqual(audit.bytes, 0)
        self.assert_no_output()

    def test_quota_rollback_preserves_foreign_replacement_of_owned_path(self):
        raw = b"{}\nbroken\n"
        _, sizes = self.probe(raw)
        limit = sum(sizes.values()) - 1
        original_json_line = core._json_line
        foreign = b"FOREIGN REPLACEMENT MUST SURVIVE"
        replacements = []

        def replace_before_manifest(value):
            if "artifacts" in value:
                accepted = self.bundle / "accepted.jsonl"
                # Keep the old inode alive: the foreign replacement must have
                # a distinct identity, independent of filesystem inode reuse.
                accepted.rename(self.root / "moved-owned-original")
                accepted.write_bytes(foreign)
                replacements.append(accepted)
            return original_json_line(value)

        with patch.object(core, "_json_line", side_effect=replace_before_manifest):
            self.quota_error(
                lambda: core.partition(self.source, self.bundle, max_output_bytes=limit)
            )
        self.assertEqual(replacements, [self.bundle / "accepted.jsonl"])
        self.assertEqual((self.bundle / "accepted.jsonl").read_bytes(), foreign)
        self.assertEqual({p.name for p in self.bundle.iterdir()}, {"accepted.jsonl"})
        self.assertEqual(self.source.read_bytes(), raw)

    def test_existing_output_and_foreign_markers_are_untouched(self):
        self.source.write_bytes(b"{}\n")
        self.bundle.mkdir()
        marker = self.bundle / "foreign.marker"
        marker.write_bytes(b"KEEP")
        with self.assertRaises(core.QuarantineError):
            core.partition(self.source, self.bundle, max_output_bytes=1)
        self.assertEqual(marker.read_bytes(), b"KEEP")
        actual = self.root / "actual"
        core.partition(self.source, actual)
        self.output.write_bytes(b"KEEP OUTPUT")
        with self.assertRaises(core.QuarantineError):
            core.restore(actual, self.output, max_output_bytes=1)
        self.assertEqual(self.output.read_bytes(), b"KEEP OUTPUT")

    def test_independently_constructed_v010_schema_bundle_still_restores(self):
        self.bundle.mkdir()
        lines = (b'{"old":true}\r\n', b"broken\n", b"null")
        streams = ("accepted", "rejected", "accepted")
        raw = b"".join(lines)
        accepted = lines[0] + lines[2]
        rejected = lines[1]
        offset = 0
        positions = {"accepted": 0, "rejected": 0}
        entries = []
        for line, (data, stream) in enumerate(zip(lines, streams, strict=True), 1):
            entries.append(
                {
                    "line": line,
                    "offset": offset,
                    "length": len(data),
                    "stream": stream,
                    "stream_offset": positions[stream],
                    "reason": "invalid_json" if stream == "rejected" else None,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            positions[stream] += len(data)
            offset += len(data)
        index = b"".join((json.dumps(entry) + "\n").encode() for entry in entries)
        contents = {"accepted.jsonl": accepted, "rejected.bin": rejected, "index.jsonl": index}
        for name, data in contents.items():
            (self.bundle / name).write_bytes(data)
        manifest = {
            "format_version": 1,
            "total_lines": 3,
            "accepted_lines": 2,
            "rejected_lines": 1,
            "input_bytes": len(raw),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "reason_counts": {"invalid_json": 1},
            "policy": {"max_line_bytes": 8388608, "objects_only": False},
            "artifacts": {
                name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in contents.items()
            },
        }
        (self.bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with _WriteAudit(self, len(raw)) as audit:
            core.restore(self.bundle, self.output, max_output_bytes=len(raw))
        self.assertEqual(audit.bytes, len(raw))
        self.assertEqual(self.output.read_bytes(), raw)


class OutputQuotaCLITests(OutputQuotaCase):
    def test_split_and_restore_exact_limits_and_quota_failure_exit_codes(self):
        raw = b'{"ok":1}\nPRIVATE_RECORD_SENTINEL\n'
        _, sizes = self.probe(raw)
        exact = sum(sizes.values())
        denied = self.cli(
            "split", self.source, self.bundle, "--max-output-bytes", exact - 1, "--json"
        )
        self.assertEqual(denied.returncode, 2, denied.stdout + denied.stderr)
        self.assertEqual(denied.stdout, "", "Quota failure must not emit success JSON")
        self.assertIn(QUOTA_MESSAGE, denied.stderr)
        self.assertNotIn("PRIVATE_RECORD_SENTINEL", denied.stdout + denied.stderr)
        self.assertNotIn("Traceback", denied.stderr)
        self.assertFalse(self.bundle.exists())
        allowed = self.cli("split", self.source, self.bundle, "--max-output-bytes", exact, "--json")
        self.assertEqual(allowed.returncode, 1, allowed.stdout + allowed.stderr)
        self.assertEqual(set(json.loads(allowed.stdout)), SUMMARY_FIELDS)
        denied = self.cli(
            "restore", self.bundle, self.output, "--max-output-bytes", len(raw) - 1, "--json"
        )
        self.assertEqual(denied.returncode, 2, denied.stdout + denied.stderr)
        self.assertEqual(denied.stdout, "")
        self.assertIn(QUOTA_MESSAGE, denied.stderr)
        self.assert_no_output()
        restored = self.cli(
            "restore", self.bundle, self.output, "--max-output-bytes", len(raw), "--json"
        )
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["bytes"], len(raw))
        self.assertEqual(self.output.read_bytes(), raw)

    def test_cli_defaults_help_and_no_unlimited_or_check_output_option(self):
        for command in ("split", "restore"):
            with self.subTest(command=command):
                result = self.cli(command, "--help")
                self.assertEqual(result.returncode, 0)
                self.assertIn("--max-output-bytes", result.stdout)
                self.assertIn("1073741824", result.stdout)
                self.assertNotIn("--unlimited", result.stdout)
        checked_help = self.cli("check", "--help")
        self.assertEqual(checked_help.returncode, 0)
        self.assertNotIn("--max-output-bytes", checked_help.stdout)
        self.source.write_bytes(b"{}\n")
        split = self.cli("split", self.source, self.bundle, "--json")
        self.assertEqual(split.returncode, 0, split.stdout + split.stderr)
        restored = self.cli("restore", self.bundle, self.output, "--json")
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual(self.output.read_bytes(), b"{}\n")

    def test_cli_rejects_invalid_limits_before_output_creation(self):
        self.source.write_bytes(b"{}\n")
        for value in ("0", "-1", "true", "1.0", str(2**63)):
            with self.subTest(value=value):
                result = self.cli(
                    "split", self.source, self.bundle, "--max-output-bytes", value, "--json"
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.bundle.exists())


if __name__ == "__main__":
    unittest.main()
