"""Independent requirements tests: raw-byte preservation and hostile metadata."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

from jsonl_roundtrip.core import QuarantineError, inspect, partition, restore


class ReversibleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "輸入 #100%.jsonl"
        self.bundle = self.root / "bundle"
        self.output = self.root / "restored.jsonl"

    def split(self, raw, **options):
        self.source.write_bytes(raw)
        summary = partition(self.source, self.bundle, **options)
        self.assertEqual(self.source.read_bytes(), raw)
        return summary

    def assert_roundtrip(self, raw):
        result = restore(self.bundle, self.output)
        self.assertEqual(self.output.read_bytes(), raw)
        self.assertEqual(result["bytes"], len(raw))
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())

    def test_mixed_corruption_preserves_every_original_byte_and_offset(self):
        raw = b'{"id":1}\r\nnull\nfalse\n42\n"\u2028"\n{"bad":\n\xff\n\n{"last":2}'
        summary = self.split(raw)
        self.assertEqual(
            (summary["total_lines"], summary["accepted_lines"], summary["rejected_lines"]),
            (9, 6, 3),
        )
        self.assertEqual(summary["input_bytes"], len(raw))
        self.assertEqual(summary["input_sha256"], hashlib.sha256(raw).hexdigest())
        accepted = (self.bundle / "accepted.jsonl").read_bytes()
        rejected = (self.bundle / "rejected.bin").read_bytes()
        records = [
            json.loads(line) for line in (self.bundle / "index.jsonl").read_text().splitlines()
        ]
        offsets = {"accepted": 0, "rejected": 0}
        position = 0
        for number, (original_line, entry) in enumerate(
            zip(io.BytesIO(raw), records, strict=True), 1
        ):
            self.assertEqual(entry["line"], number)
            self.assertEqual(entry["offset"], position)
            self.assertEqual(entry["length"], len(original_line))
            self.assertEqual(entry["stream_offset"], offsets[entry["stream"]])
            part = accepted if entry["stream"] == "accepted" else rejected
            self.assertEqual(
                part[entry["stream_offset"] : entry["stream_offset"] + entry["length"]],
                original_line,
            )
            self.assertEqual(entry["sha256"], hashlib.sha256(original_line).hexdigest())
            offsets[entry["stream"]] += entry["length"]
            position += len(original_line)
        self.assert_roundtrip(raw)

    def test_json_values_and_large_numbers_are_valid_without_numeric_conversion(self):
        raw = b'null\ntrue\nfalse\n123.5\n"text"\n[]\n{}\n' + b"9" * 6000 + b"\n1e99999999\n"
        summary = self.split(raw)
        self.assertEqual(summary["rejected_lines"], 0)
        self.assertEqual(summary["accepted_lines"], 9)
        self.assertEqual((self.bundle / "accepted.jsonl").read_bytes(), raw)
        self.assert_roundtrip(raw)

    def test_object_policy_is_explicit_and_does_not_change_bytes(self):
        raw = b'{}\nnull\n[]\n"text"\n1\n'
        summary = self.split(raw, objects_only=True)
        self.assertEqual((summary["accepted_lines"], summary["rejected_lines"]), (1, 4))
        self.assert_roundtrip(raw)

    def test_unicode_line_separators_inside_strings_are_not_record_delimiters(self):
        raw = '"line\u2028separator\u2029inside"\n{"x":"escaped\\nnewline"}\n'.encode()
        summary = self.split(raw)
        self.assertEqual(summary["total_lines"], 2)
        self.assertEqual(summary["rejected_lines"], 0)
        self.assert_roundtrip(raw)

    def test_rejects_bom_nonfinite_duplicate_keys_and_broken_utf8(self):
        raw = (
            b"\xef\xbb\xbf{}\nNaN\nInfinity\n-Infinity\n"
            b'{"private-key":1,"private-key":2}\n{"nested":{"a":1,"a":2}}\n'
            b'{"tail":"\xe2\x82'
        )
        summary = self.split(raw)
        self.assertEqual(summary["accepted_lines"], 0)
        self.assertEqual(summary["rejected_lines"], 7)
        self.assertNotIn("private-key", json.dumps(summary))
        self.assert_roundtrip(raw)

    def test_empty_input_is_a_valid_reversible_bundle(self):
        summary = self.split(b"")
        self.assertEqual(summary["total_lines"], 0)
        self.assertEqual(summary["reason_counts"], {})
        self.assert_roundtrip(b"")

    def test_restore_needs_only_bundle_when_original_is_unavailable(self):
        raw = b'{"kept":true}\r\nBROKEN_SYNTHETIC_RECORD\nnull'
        self.split(raw)
        self.source.unlink()
        self.assert_roundtrip(raw)
        self.assertFalse(self.source.exists())

    def test_last_record_does_not_need_a_newline(self):
        raw = b'{}\r\n{"final":true}'
        summary = self.split(raw)
        self.assertEqual(summary["rejected_lines"], 0)
        self.assert_roundtrip(raw)

    def test_blank_lines_and_bare_cr_do_not_create_invented_records(self):
        raw = b"\n\r\n1\r2"
        summary = self.split(raw)
        self.assertEqual((summary["total_lines"], summary["rejected_lines"]), (3, 3))
        self.assert_roundtrip(raw)

    def test_oversized_physical_line_is_streamed_with_bounded_memory(self):
        with self.source.open("wb") as stream:
            stream.write(b'{}\n"')
            for _ in range(384):
                stream.write(b"x" * 65536)
            stream.write(b'"\n{}')
        tracemalloc.start()
        try:
            summary = partition(self.source, self.bundle, max_line_bytes=64)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(
            (summary["total_lines"], summary["accepted_lines"], summary["rejected_lines"]),
            (3, 2, 1),
        )
        self.assertLess(peak, 8 * 1024 * 1024, "Oversized lines should not be loaded whole")
        restore(self.bundle, self.output)
        self.assertEqual(
            hashlib.sha256(self.source.read_bytes()).digest(),
            hashlib.sha256(self.output.read_bytes()).digest(),
        )

    def test_limit_includes_record_terminator(self):
        summary = self.split(b"123\n12\n", max_line_bytes=3)
        self.assertEqual((summary["accepted_lines"], summary["rejected_lines"]), (1, 1))
        self.assert_roundtrip(b"123\n12\n")

    def test_deep_input_is_quarantined_without_traceback(self):
        raw = b"[" * 2000 + b"0" + b"]" * 2000 + b"\n{}\n"
        summary = self.split(raw)
        self.assertEqual(summary["rejected_lines"], 1)
        self.assert_roundtrip(raw)

    def test_check_is_read_only_and_does_not_create_bundle_files(self):
        raw = b"{}\nPRIVATE_BROKEN_VALUE\n"
        self.source.write_bytes(raw)
        before = set(self.root.iterdir())
        summary = inspect(self.source)
        self.assertEqual(summary["rejected_lines"], 1)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertNotIn("PRIVATE_BROKEN_VALUE", json.dumps(summary))

    def test_existing_output_directories_and_files_are_never_overwritten(self):
        self.source.write_bytes(b"{}\n")
        self.bundle.mkdir()
        marker = self.bundle / "unrelated.txt"
        marker.write_text("KEEP")
        with self.assertRaises(QuarantineError):
            partition(self.source, self.bundle)
        self.assertEqual(marker.read_text(), "KEEP")
        other_bundle = self.root / "actual-bundle"
        partition(self.source, other_bundle)
        self.output.write_bytes(b"KEEP RESTORE OUTPUT")
        with self.assertRaises(QuarantineError):
            restore(other_bundle, self.output)
        self.assertEqual(self.output.read_bytes(), b"KEEP RESTORE OUTPUT")

    def test_invalid_options_are_errors(self):
        self.source.write_bytes(b"{}\n")
        for value in (0, -1, True, 2.5, "100", 64 * 1024 * 1024 + 1):
            with self.subTest(value=value), self.assertRaises(QuarantineError):
                inspect(self.source, max_line_bytes=value)
        with self.assertRaises(QuarantineError):
            inspect(self.source, objects_only="false")

    def test_cli_reports_counts_and_exit_codes_without_private_payload(self):
        self.source.write_bytes(b"{}\nPRIVATE_BROKEN_VALUE\n")
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)

        def command(*args):
            return subprocess.run(
                [sys.executable, "-m", "jsonl_roundtrip", *map(str, args)],
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )

        checked = command("check", self.source, "--json")
        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["rejected_lines"], 1)
        self.assertNotIn("PRIVATE_BROKEN_VALUE", checked.stdout + checked.stderr)
        split = command("split", self.source, self.bundle, "--json")
        self.assertEqual(split.returncode, 1, split.stdout + split.stderr)
        restored = command("restore", self.bundle, self.output, "--json")
        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
        self.assertEqual(self.source.read_bytes(), self.output.read_bytes())
        refused = command("restore", self.bundle, self.output, "--json")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertNotIn("Traceback", refused.stdout + refused.stderr)


class HostileBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.jsonl"
        self.source.write_bytes(b'{"a":1}\nBROKEN\n{"b":2}')
        self.bundle = self.root / "bundle"
        partition(self.source, self.bundle)
        self.output = self.root / "restored.jsonl"

    def manifest(self):
        return json.loads((self.bundle / "manifest.json").read_bytes())

    def write_manifest(self, manifest):
        (self.bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def replace_index(self, entries):
        raw = b"".join(json.dumps(entry).encode() + b"\n" for entry in entries)
        (self.bundle / "index.jsonl").write_bytes(raw)
        manifest = self.manifest()
        manifest["artifacts"]["index.jsonl"] = {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        self.write_manifest(manifest)

    def entries(self):
        return [
            json.loads(line) for line in (self.bundle / "index.jsonl").read_bytes().splitlines()
        ]

    def assert_refused(self):
        with self.assertRaises(QuarantineError) as context:
            restore(self.bundle, self.output)
        self.assertNotIn("BROKEN", str(context.exception))
        self.assertFalse(
            os.path.lexists(self.output), "Handled failures must not leave a partial output"
        )

    def test_raw_artifact_corruption_is_detected(self):
        (self.bundle / "accepted.jsonl").write_bytes(b"not the original bytes")
        self.assert_refused()

    def test_manifest_duplicate_keys_are_rejected(self):
        path = self.bundle / "manifest.json"
        raw = path.read_text(encoding="utf-8")
        path.write_text('{"format_version":1,' + raw[1:], encoding="utf-8")
        self.assert_refused()

    def test_manifest_types_and_versions_are_checked(self):
        original = self.manifest()
        for key, value in (
            ("format_version", True),
            ("format_version", 2),
            ("input_bytes", True),
            ("total_lines", -1),
            ("accepted_lines", 99),
            ("input_sha256", "not-a-hash"),
        ):
            with self.subTest(key=key, value=value):
                changed = json.loads(json.dumps(original))
                changed[key] = value
                self.write_manifest(changed)
                self.assert_refused()

    def test_artifact_manifest_cannot_choose_paths(self):
        manifest = self.manifest()
        manifest["artifacts"]["../../outside"] = manifest["artifacts"]["accepted.jsonl"]
        self.write_manifest(manifest)
        self.assert_refused()

    def test_index_gaps_and_overlaps_fail_even_with_recomputed_index_hash(self):
        entries = self.entries()
        original_manifest = self.manifest()
        cases = [
            (0, "offset", 1),
            (0, "stream_offset", 1),
            (0, "length", -1),
            (0, "length", True),
            (1, "line", 1),
            (2, "stream_offset", 0),
            (0, "length", 99999999999),
            (0, "sha256", "0" * 64),
            (0, "stream", "../../file"),
        ]
        for row, key, value in cases:
            with self.subTest(key=key, value=value):
                self.write_manifest(original_manifest)
                modified = json.loads(json.dumps(entries))
                modified[row][key] = value
                self.replace_index(modified)
                self.assert_refused()

    def test_omitted_record_and_trailing_partition_data_are_rejected(self):
        self.replace_index(self.entries()[:-1])
        self.assert_refused()

    def test_incomplete_bundle_has_no_completion_marker(self):
        (self.bundle / "manifest.json").unlink()
        self.assert_refused()

    def test_linked_artifact_is_rejected(self):
        path = self.bundle / "accepted.jsonl"
        target = self.root / "private-target"
        target.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest("Creating symbolic links is not permitted on this host")
        self.assert_refused()
        self.assertEqual(target.read_bytes(), b'{"a":1}\n{"b":2}')


if __name__ == "__main__":
    unittest.main()
