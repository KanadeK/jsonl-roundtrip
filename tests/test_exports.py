"""Independent byte-preservation regressions for documented export dialects.

Fixtures distinguish actual pinned tool outputs from source-derived synthetic
format examples. These tests require no optional exporter packages.
"""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from jsonl_roundtrip.core import inspect, partition, restore

FIXTURES = Path(__file__).parent / "fixtures" / "exports"
PRIVATE_SENTINEL = "SYNTHETIC_PRIVATE_EXPORT_SENTINEL_94c2"
EXPECTED_RECORDS = {
    "duckdb-copy.ndjson": 3,
    "pandas-records.ndjson": 3,
    "elasticsearch-bulk.ndjson": 5,
    "mongodb-extended.ndjson": 3,
}


def decoded_records(raw):
    """Only LF delimits records; U+2028/U+2029 inside strings remain data."""
    return [json.loads(line) for line in io.BytesIO(raw)]


class ExportRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def assert_bundle_only_roundtrip(self, raw, *, accepted, rejected, objects_only=True):
        """Assert the external workflow, not the implementation's classification."""
        directory = self.root / str(len(list(self.root.iterdir())))
        directory.mkdir()
        source = directory / "synthetic-export.ndjson"
        source.write_bytes(raw)
        before_hash = hashlib.sha256(raw).hexdigest()
        checked = inspect(source, objects_only=objects_only)
        bundle = directory / "bundle"
        summary = partition(source, bundle, objects_only=objects_only)
        self.assertEqual(summary, checked)
        self.assertEqual(summary["accepted_lines"], accepted)
        self.assertEqual(summary["rejected_lines"], rejected)
        self.assertEqual(summary["total_lines"], accepted + rejected)
        self.assertEqual(summary["input_bytes"], len(raw))
        self.assertEqual(summary["input_sha256"], before_hash)
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(summary))
        self.assertEqual(source.read_bytes(), raw)

        accepted_raw = (bundle / "accepted.jsonl").read_bytes()
        rejected_raw = (bundle / "rejected.bin").read_bytes()
        entries = [
            json.loads(line) for line in (bundle / "index.jsonl").read_bytes().split(b"\n") if line
        ]
        cursor = 0
        stream_cursors = {"accepted": 0, "rejected": 0}
        for number, (physical_record, entry) in enumerate(
            zip(io.BytesIO(raw), entries, strict=True), 1
        ):
            self.assertEqual(entry["line"], number)
            self.assertEqual(entry["offset"], cursor)
            self.assertEqual(entry["length"], len(physical_record))
            self.assertEqual(entry["sha256"], hashlib.sha256(physical_record).hexdigest())
            stream = entry["stream"]
            self.assertIn(stream, stream_cursors)
            self.assertEqual(entry["stream_offset"], stream_cursors[stream])
            partition_raw = accepted_raw if stream == "accepted" else rejected_raw
            self.assertEqual(
                partition_raw[entry["stream_offset"] : entry["stream_offset"] + entry["length"]],
                physical_record,
            )
            stream_cursors[stream] += len(physical_record)
            cursor += len(physical_record)
        self.assertEqual(cursor, len(raw))
        self.assertEqual(stream_cursors["accepted"], len(accepted_raw))
        self.assertEqual(stream_cursors["rejected"], len(rejected_raw))

        # Remove the source and move the bundle: neither path may be a hidden
        # recovery dependency. Restore has access only to the bundle artifacts.
        source.unlink()
        relocated = directory / "relocated-bundle"
        bundle.rename(relocated)
        output = directory / "restored.ndjson"
        recovered = restore(relocated, output)
        self.assertFalse(source.exists())
        self.assertEqual(output.read_bytes(), raw)
        self.assertEqual(recovered["bytes"], len(raw))
        self.assertEqual(recovered["sha256"], before_hash)
        return accepted_raw, rejected_raw

    def test_four_documented_export_dialects_roundtrip_without_producer_dependencies(self):
        for name, count in EXPECTED_RECORDS.items():
            with self.subTest(fixture=name):
                raw = (FIXTURES / name).read_bytes()
                kept, quarantined = self.assert_bundle_only_roundtrip(
                    raw, accepted=count, rejected=0
                )
                self.assertEqual(kept, raw)
                self.assertEqual(quarantined, b"")

    def test_provenance_hashes_and_record_counts_match_committed_bytes(self):
        provenance = json.loads((FIXTURES / "provenance.json").read_bytes())
        self.assertEqual(provenance["version"], 1)
        self.assertEqual({item["file"] for item in provenance["fixtures"]}, set(EXPECTED_RECORDS))
        for item in provenance["fixtures"]:
            with self.subTest(fixture=item["file"]):
                raw = (FIXTURES / item["file"]).read_bytes()
                self.assertEqual(item["records"], EXPECTED_RECORDS[item["file"]])
                self.assertEqual(item["bytes"], len(raw))
                self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
                self.assertTrue(all(source.startswith("https://") for source in item["sources"]))
                if item["producer"] is None:
                    self.assertEqual(item["provenance"], "source-derived-hand-authored-synthetic")
                    self.assertIsNone(item["producer_version"])
                else:
                    self.assertEqual(item["provenance"], "real-tool-generated-synthetic")
                    self.assertIn(item["producer"], {"duckdb", "pandas"})

    def test_actual_exporters_escape_record_newlines_preserving_unicode_nested_and_nulls(self):
        for name in ("duckdb-copy.ndjson", "pandas-records.ndjson"):
            with self.subTest(fixture=name):
                raw = (FIXTURES / name).read_bytes()
                self.assertEqual(raw.count(b"\n"), 3)
                rows = decoded_records(raw)
                self.assertEqual(rows[0]["message"], "first line\nsecond line " + PRIVATE_SENTINEL)
                self.assertEqual(rows[0]["label"], "雪🙂")
                self.assertIsNone(rows[0]["optional"])
                self.assertEqual(rows[0]["nested"], {"values": [1, None, True]})
                self.assertEqual(rows[1]["message"], 'quote " and slash \\')
                self.assertEqual(rows[2]["message"], "CR\rLF\nend")
                self.assertEqual(rows[2]["label"], "LS\u2028PS\u2029")

    def test_actual_duckdb_hugeint_and_pandas_int64_digits_are_unchanged(self):
        duck_raw = (FIXTURES / "duckdb-copy.ndjson").read_bytes()
        duck = decoded_records(duck_raw)
        self.assertEqual(duck[0]["large_number"], 170141183460469231731687303715884105727)
        self.assertEqual(duck[1]["large_number"], -170141183460469231731687303715884105728)
        self.assertIn(b'"large_number":170141183460469231731687303715884105727', duck_raw)
        pandas = decoded_records((FIXTURES / "pandas-records.ndjson").read_bytes())
        self.assertEqual(
            [row["large_number"] for row in pandas],
            [9007199254740993, 9223372036854775807, -9223372036854775808],
        )

    def test_actual_exported_records_with_injected_truncation_and_invalid_utf8(self):
        for name in ("duckdb-copy.ndjson", "pandas-records.ndjson"):
            with self.subTest(fixture=name):
                lines = (FIXTURES / name).read_bytes().split(b"\n")[:-1]
                malformed = b'{"payload":"' + PRIVATE_SENTINEL.encode()
                damaged = b"\r\n".join((lines[0], malformed, b"\xff", lines[1], lines[2]))
                accepted, rejected = self.assert_bundle_only_roundtrip(
                    damaged, accepted=3, rejected=2
                )
                self.assertEqual(accepted, b"\r\n".join((lines[0], lines[1], lines[2])))
                self.assertEqual(rejected, malformed + b"\r\n\xff\r\n")

    def test_transport_crlf_and_missing_final_lf_keep_export_record_bytes(self):
        for name in ("duckdb-copy.ndjson", "pandas-records.ndjson"):
            raw = (FIXTURES / name).read_bytes()
            for variant in (raw.replace(b"\n", b"\r\n"), raw.removesuffix(b"\n")):
                with self.subTest(fixture=name, ends_with_lf=variant.endswith(b"\n")):
                    self.assert_bundle_only_roundtrip(variant, accepted=3, rejected=0)

    def test_elasticsearch_bulk_records_are_not_action_counts(self):
        raw = (FIXTURES / "elasticsearch-bulk.ndjson").read_bytes()
        self.assertEqual(raw.count(b"\r\n"), 5)
        rows = decoded_records(raw)
        self.assertIn("index", rows[0])
        self.assertIn("delete", rows[2])
        self.assertIn("create", rows[3])
        self.assertEqual(rows[1]["message"], "line one\nline two")
        self.assertEqual(rows[1]["label"], "雪")
        self.assertEqual(rows[4]["large_id"], 9007199254740993)
        self.assert_bundle_only_roundtrip(raw, accepted=5, rejected=0)

    def test_elasticsearch_quarantine_does_not_repair_action_document_pairs(self):
        lines = (FIXTURES / "elasticsearch-bulk.ndjson").read_bytes().split(b"\r\n")[:-1]
        lines[1] = b'{"truncated_document":'
        raw = b"\r\n".join(lines) + b"\r\n"
        accepted, rejected = self.assert_bundle_only_roundtrip(raw, accepted=4, rejected=1)
        self.assertEqual(rejected, lines[1] + b"\r\n")
        accepted_rows = decoded_records(accepted)
        # An index action now immediately precedes a delete action. The tool
        # preserves valid JSON records; it does not claim a replayable Bulk job.
        self.assertIn("index", accepted_rows[0])
        self.assertIn("delete", accepted_rows[1])

    def test_elasticsearch_missing_final_lf_is_jsonl_not_protocol_validation(self):
        raw = (FIXTURES / "elasticsearch-bulk.ndjson").read_bytes().removesuffix(b"\r\n")
        self.assert_bundle_only_roundtrip(raw, accepted=5, rejected=0)

    def test_mongodb_canonical_relaxed_and_nonfinite_strings_are_ordinary_json(self):
        raw = (FIXTURES / "mongodb-extended.ndjson").read_bytes()
        rows = decoded_records(raw)
        self.assertEqual(rows[0]["sequence"]["$numberLong"], "9223372036854775807")
        self.assertEqual(rows[0]["amount"]["$numberDecimal"], "1234567890.12345678901234567890")
        self.assertEqual(rows[0]["at"]["$date"], {"$numberLong": "1767225600000"})
        self.assertEqual(rows[1]["at"]["$date"], "2026-01-02T03:04:05.000Z")
        self.assertEqual(rows[1]["counter"], 9007199254740993)
        self.assertEqual(rows[1]["label"], "你好🙂")
        self.assertIsNone(rows[1]["payload"])
        self.assertEqual(rows[2]["value"], {"$numberDouble": "NaN"})
        self.assertEqual(rows[2]["limit"], {"$numberDouble": "Infinity"})
        self.assert_bundle_only_roundtrip(raw, accepted=3, rejected=0)

    def test_mongodb_wrapped_strings_remain_valid_while_bare_nonfinite_literals_reject(self):
        raw = (FIXTURES / "mongodb-extended.ndjson").read_bytes()
        damaged = raw + b'{"value":NaN}\n{"value":Infinity}\n{"truncated":'
        accepted, rejected = self.assert_bundle_only_roundtrip(damaged, accepted=3, rejected=3)
        self.assertEqual(accepted, raw)
        self.assertEqual(rejected, b'{"value":NaN}\n{"value":Infinity}\n{"truncated":')


if __name__ == "__main__":
    unittest.main()
