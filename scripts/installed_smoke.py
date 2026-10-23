"""Verify a built wheel in a fresh venv, offline after Python's bundled pip bootstrap."""

import hashlib
import json
import os
import subprocess
import tempfile
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
    wheels = sorted((root / "dist").glob("jsonl_quarantine-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Build exactly one current wheel in dist/ first")
    with tempfile.TemporaryDirectory(prefix="jsonl-quarantine-smoke-") as directory:
        work = Path(directory)
        environment = work / "fresh-venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        command = scripts / ("jsonl-quarantine.exe" if os.name == "nt" else "jsonl-quarantine")
        run([python, "-m", "pip", "install", "--no-index", "--no-deps", wheels[0]], cwd=work)
        source = work / "synthetic.jsonl"
        raw = b'{"synthetic":true}\r\nPRIVATE_SYNTHETIC_BROKEN_ROW\nnull'
        source.write_bytes(raw)
        checked = run(
            [python, "-m", "jsonl_quarantine", "check", source, "--json"], cwd=work, expected=1
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
        output = work / "restored.jsonl"
        restored = run([command, "restore", bundle, output, "--json"], cwd=work)
        if output.read_bytes() != raw or source.read_bytes() != raw:
            raise RuntimeError("Byte-exact restoration or source preservation failed")
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
        run([python, "-m", "pip", "uninstall", "-y", "jsonl-quarantine"], cwd=work)
        probe = run(
            [
                python,
                "-c",
                "import importlib.util; print(importlib.util.find_spec('jsonl_quarantine'))",
            ],
            cwd=work,
        )
        if probe.stdout.strip() != "None":
            raise RuntimeError("Package remains importable after uninstall")
    print(
        "Fresh offline wheel install, module/entrypoint, quarantine, roundtrip and uninstall: PASS"
    )


if __name__ == "__main__":
    main()
