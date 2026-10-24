"""Verify a built wheel in a fresh venv, offline after Python's bundled pip bootstrap."""

import hashlib
import json
import os
import subprocess
import tempfile
import tomllib
import venv
from pathlib import Path


def run(arguments, *, cwd, expected=0):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        list(map(str, arguments)),
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if result.returncode != expected:
        raise RuntimeError(
            f"Smoke step returned {result.returncode}, expected {expected}: "
            + result.stdout
            + result.stderr
        )
    return result


def main():
    root = Path(__file__).resolve().parents[1]
    wheels = sorted((root / "dist").glob("jsonl_roundtrip-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one current wheel in dist/ first")
    with tempfile.TemporaryDirectory(prefix="jsonl-roundtrip-smoke-") as directory:
        work = Path(directory)
        environment = work / "fresh-venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        command = scripts / ("jsonl-roundtrip.exe" if os.name == "nt" else "jsonl-roundtrip")
        run([python, "-m", "pip", "install", "--no-index", "--no-deps", wheels[0]], cwd=work)
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        if run([command, "--version"], cwd=work).stdout.strip() != version:
            raise RuntimeError("Installed version disagrees with the source release")
        source = work / "synthetic.jsonl"
        raw = b'{"synthetic":true}\r\nPRIVATE_SYNTHETIC_BROKEN_ROW\nnull'
        source.write_bytes(raw)
        checked = run(
            [python, "-m", "jsonl_roundtrip", "check", source, "--json"], cwd=work, expected=1
        )
        if json.loads(checked.stdout)["rejected_lines"] != 1:
            raise RuntimeError("Check must identify the synthetic malformed line")
        bundle = work / "bundle"
        split = run([command, "split", source, bundle, "--json"], cwd=work, expected=1)
        result = json.loads(split.stdout)
        if (result["accepted_lines"], result["rejected_lines"]) != (2, 1):
            raise RuntimeError("Split counts disagree")
        if "PRIVATE_SYNTHETIC_BROKEN_ROW" in split.stdout + split.stderr:
            raise RuntimeError("Split disclosed a private record")
        budget = sum(path.stat().st_size for path in bundle.iterdir())
        for cap, name in ((budget, "exact"), (budget - 1, "short")):
            bounded = work / name
            result = run(
                [command, "split", source, bounded, "--max-output-bytes", cap, "--json"],
                cwd=work,
                expected=1 if cap == budget else 2,
            )
            if cap == budget:
                if sum(path.stat().st_size for path in bounded.iterdir()) != budget:
                    raise RuntimeError("Exact split quota changed the output bytes")
            elif (
                bounded.exists()
                or result.stdout
                or result.stderr.strip() != "jsonl-roundtrip: Total output byte quota exceeded"
            ):
                raise RuntimeError("Split quota failure leaked data or left output")
        denied = work / "denied.jsonl"
        failed = run(
            [command, "restore", bundle, denied, "--max-output-bytes", len(raw) - 1, "--json"],
            cwd=work,
            expected=2,
        )
        if denied.exists() or failed.stdout or list(work.glob(".jsonl-roundtrip-*.tmp")):
            raise RuntimeError("Restoration quota failure left output")
        if source.read_bytes() != raw:
            raise RuntimeError("Source preservation failed")
        source.unlink()
        output = work / "restored.jsonl"
        restored = run(
            [command, "restore", bundle, output, "--max-output-bytes", len(raw), "--json"],
            cwd=work,
        )
        if output.read_bytes() != raw or source.exists():
            raise RuntimeError("Bundle-only byte-exact restoration failed")
        if json.loads(restored.stdout)["sha256"] != hashlib.sha256(raw).hexdigest():
            raise RuntimeError("Restored digest disagrees")
        valid = work / "valid.jsonl"
        valid.write_bytes(b"true\n12345678901234567890\n")
        run([command, "check", valid, "--json"], cwd=work)
        demo = work / "demo.jsonl"
        demo_bundle = work / "demo-bundle"
        demo_restored = work / "demo-restored.jsonl"
        run([python, root / "examples" / "make_demo.py", demo], cwd=work)
        demo_split = run([command, "split", demo, demo_bundle, "--json"], cwd=work, expected=1)
        if json.loads(demo_split.stdout)["rejected_lines"] != 2:
            raise RuntimeError("README demonstration counts disagree")
        run([command, "restore", demo_bundle, demo_restored, "--json"], cwd=work)
        run([python, root / "examples" / "compare.py", demo, demo_restored], cwd=work)
        fixtures = root / "tests" / "fixtures"
        exports = sorted((fixtures / "exports").glob("*.ndjson"))
        if len(exports) != 4:
            raise RuntimeError("Expected four documented export fixtures")
        for index, fixture in enumerate(exports):
            exported = work / f"export-{index}.jsonl"
            exported.write_bytes(fixture.read_bytes())
            export_bundle = work / f"export-bundle-{index}"
            export_output = work / f"export-output-{index}.jsonl"
            run([command, "split", exported, export_bundle, "--objects-only", "--json"], cwd=work)
            exported.unlink()
            run([command, "restore", export_bundle, export_output, "--json"], cwd=work)
            if export_output.read_bytes() != fixture.read_bytes():
                raise RuntimeError("Installed CLI export fixture recovery failed")
        legacy = work / "legacy-restored.jsonl"
        run([command, "restore", fixtures / "legacy-v0.1.0", legacy, "--json"], cwd=work)
        if hashlib.sha256(legacy.read_bytes()).hexdigest() != (
            "1b9ff476e11a7de1adba6dbecceb2e8e7db4f8c00aa7b9cb22d4c4f27928a0db"
        ):
            raise RuntimeError("Installed CLI v0.1.0 bundle compatibility failed")
        run([python, "-m", "pip", "uninstall", "-y", "jsonl-roundtrip"], cwd=work)
        probe = run(
            [
                python,
                "-c",
                "import importlib.util; print(importlib.util.find_spec('jsonl_roundtrip'))",
            ],
            cwd=work,
        )
        if probe.stdout.strip() != "None":
            raise RuntimeError("Package remains importable after uninstall")
    print(
        "Fresh offline wheel install, exact/failed quotas, four export formats, "
        "legacy bundle, README demo, bundle-only roundtrip and uninstall: PASS"
    )


if __name__ == "__main__":
    main()
