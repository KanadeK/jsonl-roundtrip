"""Adversarial, requirements-derived tests beyond the acceptance workflow."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonl_quarantine import core
from jsonl_quarantine.core import QuarantineError, inspect, partition, restore


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class BundleCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "输入 %& #.jsonl"
        self.bundle = self.root / "quarantine"
        self.output = self.root / "restored.jsonl"

    def split(self, raw=b"{}\nnull\n", **options):
        self.source.write_bytes(raw)
        return partition(self.source, self.bundle, **options)

    def manifest(self):
        return json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, value):
        (self.bundle / "manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def rewrite_index(self, raw, **changes):
        (self.bundle / "index.jsonl").write_bytes(raw)
        value = self.manifest()
        value["artifacts"]["index.jsonl"] = {"bytes": len(raw), "sha256": sha(raw)}
        value.update(changes)
        self.write_manifest(value)

    def invalid_bundle(self):
        before = set(self.root.iterdir())
        with self.assertRaises(QuarantineError):
            restore(self.bundle, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(
            set(self.root.iterdir()), before, "Validation must not leak temporary files"
        )


class RecordPolicyTests(BundleCase):
    def test_escaped_duplicate_keys_are_quarantined_without_disclosing_them(self):
        private = b"PRIVATE_DUPLICATE_KEY_f8e18"
        raw = b'{"' + private + b'":1,"' + private + b'":2}\n{"\\u0061":1,"a":2}\n'
        result = self.split(raw)
        self.assertEqual(result["reason_counts"], {"duplicate_key": 2})
        self.assertNotIn(private.decode(), json.dumps(result))
        restore(self.bundle, self.output)
        self.assertEqual(self.output.read_bytes(), raw)

    def test_quotes_escaped_brackets_and_unicode_separators_are_data(self):
        raw = ('{"text":"' + "[" * 3000 + '\\"quoted\\"' + "]" * 3000 + '  "}\n').encode()
        result = self.split(raw)
        self.assertEqual(result["accepted_lines"], 1)
        restore(self.bundle, self.output)
        self.assertEqual(self.output.read_bytes(), raw)

    def test_numeric_syntax_never_converts_or_rounds_values(self):
        raw = ("1" * 10000 + "\n1e999999999999999999999\n-0.0000000000000000001\n").encode()
        self.assertEqual(self.split(raw)["rejected_lines"], 0)
        self.assertEqual((self.bundle / "accepted.jsonl").read_bytes(), raw)
        restore(self.bundle, self.output)
        self.assertEqual(self.output.read_bytes(), raw)

    def test_invalid_numeric_grammar_is_rejected(self):
        raw = b"01\n+1\n1.\n.2\n1e\n0x10\n"
        result = self.split(raw)
        self.assertEqual((result["accepted_lines"], result["rejected_lines"]), (0, 6))

    def test_chunk_boundary_lf_does_not_invent_empty_records(self):
        raw = b"0\n" + b"x" * 65536 + b"\ntrue\r\n"
        result = self.split(raw, max_line_bytes=65536)
        self.assertEqual((result["total_lines"], result["rejected_lines"]), (3, 1))
        self.assertEqual((self.bundle / "rejected.bin").read_bytes(), b"x" * 65536 + b"\n")
        restore(self.bundle, self.output)
        self.assertEqual(self.output.read_bytes(), raw)

    def test_high_nesting_is_a_documented_limit_not_a_process_failure(self):
        raw = b"[" * 257 + b"0" + b"]" * 257
        result = self.split(raw)
        self.assertEqual(result["reason_counts"], {"nesting_limit": 1})

    def test_all_rejected_input_and_empty_accepted_artifact_roundtrip(self):
        raw = b"\xff\r\n\n{unfinished"
        result = self.split(raw)
        self.assertEqual(result["accepted_lines"], 0)
        self.assertEqual((self.bundle / "accepted.jsonl").read_bytes(), b"")
        restore(self.bundle, self.output)
        self.assertEqual(self.output.read_bytes(), raw)

    def test_object_option_type_is_checked(self):
        self.source.write_bytes(b"{}\n")
        for value in (1, "yes", None):
            with self.subTest(value=value):
                with self.assertRaises(QuarantineError):
                    partition(self.source, self.bundle, objects_only=value)
                self.assertFalse(self.bundle.exists())


class FilesystemAndRecoveryTests(BundleCase):
    def test_missing_source_does_not_create_output_directory(self):
        with self.assertRaises(QuarantineError):
            partition(self.source, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_directory_input_is_rejected(self):
        with self.assertRaises(QuarantineError):
            inspect(self.root)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO test")
    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        os.mkfifo(self.source)
        result = subprocess.run(
            [sys.executable, "-m", "jsonl_quarantine", "check", str(self.source), "--json"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 2)

    def test_input_change_removes_only_new_bundle(self):
        self.source.write_bytes(b"{}\n")
        marker = self.root / "unrelated.txt"
        marker.write_bytes(b"keep")
        scan = core._scan

        def change_after_scan(*args, **kwargs):
            result = scan(*args, **kwargs)
            self.source.write_bytes(b"true\n")
            return result

        with patch.object(core, "_scan", side_effect=change_after_scan):
            with self.assertRaises(QuarantineError):
                partition(self.source, self.bundle)
        self.assertFalse(self.bundle.exists())
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_same_size_content_change_is_detected_even_if_signature_is_stale(self):
        self.source.write_bytes(b"{}\n")
        scan = core._scan

        def change_after_scan(*args, **kwargs):
            result = scan(*args, **kwargs)
            self.source.write_bytes(b"[]\n")
            return result

        with (
            patch.object(core, "_scan", side_effect=change_after_scan),
            patch.object(core, "_unchanged"),
        ):
            with self.assertRaises(QuarantineError):
                inspect(self.source)

    def test_failed_manifest_publication_does_not_leave_completed_bundle(self):
        self.source.write_bytes(b"{}\n")
        with patch.object(core.os, "link", side_effect=OSError("PRIVATE_FILESYSTEM_ERROR")):
            with self.assertRaises(QuarantineError) as caught:
                partition(self.source, self.bundle)
        self.assertNotIn("PRIVATE_FILESYSTEM_ERROR", str(caught.exception))
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.source.read_bytes(), b"{}\n")

    def test_failed_restore_publication_has_no_final_or_temporary_file(self):
        self.split()
        before = set(self.root.iterdir())
        with patch.object(core.os, "link", side_effect=OSError("PRIVATE_FILESYSTEM_ERROR")):
            with self.assertRaises(QuarantineError):
                restore(self.bundle, self.output)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_restore_rejects_output_inside_bundle_before_temp_creation(self):
        raw = b"{}\nbroken\n"
        self.split(raw)
        target = self.bundle / "recovered.jsonl"
        before = set(self.bundle.iterdir())
        with self.assertRaises(QuarantineError):
            restore(self.bundle, target)
        self.assertEqual(set(self.bundle.iterdir()), before)

    def test_existing_nonempty_bundle_is_untouched(self):
        self.source.write_bytes(b"{}\n")
        self.bundle.mkdir()
        marker = self.bundle / "unrelated.txt"
        marker.write_bytes(b"existing")
        with self.assertRaises(QuarantineError):
            partition(self.source, self.bundle)
        self.assertEqual(list(self.bundle.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b"existing")

    def test_nonexistent_output_parent_does_not_modify_bundle(self):
        self.split()
        before = {p.name: p.read_bytes() for p in self.bundle.iterdir()}
        with self.assertRaises(QuarantineError):
            restore(self.bundle, self.root / "missing-parent" / "result.jsonl")
        self.assertEqual({p.name: p.read_bytes() for p in self.bundle.iterdir()}, before)


class MetadataValidationTests(BundleCase):
    def test_record_digest_is_checked_even_when_artifact_hashes_match(self):
        self.split()
        entries = [
            json.loads(line) for line in (self.bundle / "index.jsonl").read_bytes().splitlines()
        ]
        entries[0]["sha256"] = "0" * 64
        self.rewrite_index(b"".join((json.dumps(entry) + "\n").encode() for entry in entries))
        self.invalid_bundle()

    def test_complete_original_hash_is_checked(self):
        self.split()
        value = self.manifest()
        value["input_sha256"] = "f" * 64
        self.write_manifest(value)
        self.invalid_bundle()

    def test_arbitrary_record_splits_are_not_accepted_as_physical_lines(self):
        raw = b"{}\nnull\n"
        self.split(raw)
        entries = [
            {
                "line": 1,
                "offset": 0,
                "length": 1,
                "stream": "accepted",
                "stream_offset": 0,
                "reason": None,
                "sha256": sha(raw[:1]),
            },
            {
                "line": 2,
                "offset": 1,
                "length": len(raw) - 1,
                "stream": "accepted",
                "stream_offset": 1,
                "reason": None,
                "sha256": sha(raw[1:]),
            },
        ]
        self.rewrite_index(b"".join((json.dumps(entry) + "\n").encode() for entry in entries))
        self.invalid_bundle()

    def test_merged_physical_lines_are_rejected_even_with_matching_hashes(self):
        raw = b"{}\nnull\n"
        self.split(raw)
        entry = {
            "line": 1,
            "offset": 0,
            "length": len(raw),
            "stream": "accepted",
            "stream_offset": 0,
            "reason": None,
            "sha256": sha(raw),
        }
        self.rewrite_index((json.dumps(entry) + "\n").encode(), total_lines=1, accepted_lines=1)
        self.invalid_bundle()

    def test_duplicate_index_keys_do_not_override_values(self):
        self.split()
        raw = (
            (self.bundle / "index.jsonl").read_bytes().replace(b'"line":1', b'"line":1,"line":1', 1)
        )
        self.rewrite_index(raw)
        self.invalid_bundle()

    def test_index_types_reasons_and_digests_are_checked(self):
        mutations = [
            {"length": True},
            {"offset": -1},
            {"stream_offset": 1},
            {"stream": "../outside"},
            {"sha256": "NOT_A_DIGEST"},
            {"reason": "PRIVATE_KEY"},
            {"stream": "rejected", "reason": None},
        ]
        for index, changes in enumerate(mutations):
            with self.subTest(case=index):
                bundle = self.root / f"case-{index}"
                self.source.write_bytes(b"{}\n")
                partition(self.source, bundle)
                self.bundle = bundle
                entry = json.loads((bundle / "index.jsonl").read_bytes())
                entry.update(changes)
                self.rewrite_index((json.dumps(entry) + "\n").encode())
                self.invalid_bundle()

    def test_rejection_reason_aggregate_must_match_index(self):
        self.split(b"x\n")
        value = self.manifest()
        value["reason_counts"] = {"blank_line": 1}
        self.write_manifest(value)
        self.invalid_bundle()

    def test_metadata_encoding_nonfinite_and_nesting_are_bounded(self):
        documents = [b"\xff", b"NaN", b"[" * 100 + b"0" + b"]" * 100, b" " * 65537]
        for index, document in enumerate(documents):
            with self.subTest(case=index):
                bundle = self.root / f"case-{index}"
                self.source.write_bytes(b"{}\n")
                partition(self.source, bundle)
                self.bundle = bundle
                (bundle / "manifest.json").write_bytes(document)
                self.invalid_bundle()

    def test_nonregular_artifact_is_rejected(self):
        self.split()
        artifact = self.bundle / "accepted.jsonl"
        artifact.unlink()
        artifact.mkdir()
        self.invalid_bundle()

    def test_missing_artifact_is_rejected(self):
        self.split()
        (self.bundle / "rejected.bin").unlink()
        self.invalid_bundle()

    def test_manifest_counts_and_reason_types_are_checked(self):
        mutations = [
            {"input_bytes": -1},
            {"input_sha256": "A" * 64},
            {"total_lines": True},
            {"accepted_lines": 9},
            {"reason_counts": {"unknown": 1}},
            {"reason_counts": []},
            {"reason_counts": {"invalid_json": True}},
            {"policy": {"max_line_bytes": 0, "objects_only": False}},
        ]
        for index, changes in enumerate(mutations):
            with self.subTest(case=index):
                bundle = self.root / f"case-{index}"
                self.source.write_bytes(b"x\n")
                partition(self.source, bundle)
                self.bundle = bundle
                value = self.manifest()
                value.update(changes)
                self.write_manifest(value)
                self.invalid_bundle()


if __name__ == "__main__":
    unittest.main()
