"""Read an actual v0.1.0-produced bundle without the original source file."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsonl_roundtrip.core import OutputQuotaExceeded, partition, restore

LEGACY = Path(__file__).parent / "fixtures" / "legacy-v0.1.0"
ORIGINAL = b'{"export":"legacy","id":1}\r\nBROKEN_SYNTHETIC\n{"tail":null}'


class LegacyBundleTests(unittest.TestCase):
    def test_actual_old_bundle_restores_without_source_under_exact_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "original.jsonl"
            summary = restore(LEGACY, output, max_output_bytes=len(ORIGINAL))
            self.assertEqual(output.read_bytes(), ORIGINAL)
            self.assertEqual(summary["sha256"], hashlib.sha256(ORIGINAL).hexdigest())

    def test_quota_also_applies_to_preexisting_old_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "original.jsonl"
            with self.assertRaises(OutputQuotaExceeded):
                restore(LEGACY, output, max_output_bytes=len(ORIGINAL) - 1)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_resource_quota_does_not_change_version_one_manifest_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            source.write_bytes(ORIGINAL)
            bundle = root / "bundle"
            partition(source, bundle, max_output_bytes=16384)
            old = json.loads((LEGACY / "manifest.json").read_bytes())
            new = json.loads((bundle / "manifest.json").read_bytes())
            self.assertEqual(new, old)


if __name__ == "__main__":
    unittest.main()
