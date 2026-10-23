"""CLI with aggregate-only diagnostics and stable exit categories."""

import argparse
import json

from . import __version__
from .core import (
    DEFAULT_MAX_LINE_BYTES,
    MAX_LINE_BYTES,
    QuarantineError,
    inspect,
    partition,
    restore,
)


def _line_limit(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "expected a line-byte limit from 1 through 67108864"
        ) from None
    if not 1 <= value <= MAX_LINE_BYTES:
        raise argparse.ArgumentTypeError("expected a line-byte limit from 1 through 67108864")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reversible, byte-exact JSON Lines quarantine.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "split"):
        child = commands.add_parser(
            command, help="check records" if command == "check" else "create a reversible bundle"
        )
        child.add_argument("source", help="closed, stable input file")
        if command == "split":
            child.add_argument("output_dir", help="new, nonexisting bundle directory")
        child.add_argument(
            "--max-line-bytes",
            type=_line_limit,
            default=DEFAULT_MAX_LINE_BYTES,
            help="record byte limit including LF/CRLF (default: 8388608, max: 67108864)",
        )
        child.add_argument(
            "--objects-only", action="store_true", help="quarantine nonobject JSON values too"
        )
        child.add_argument("--json", action="store_true", help="emit an aggregate JSON summary")
    child = commands.add_parser("restore", help="verify and reconstruct the original bytes")
    child.add_argument("bundle_dir")
    child.add_argument("output_path", help="new, nonexisting output file")
    child.add_argument("--json", action="store_true", help="emit byte count and digest as JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "restore":
            summary = restore(args.bundle_dir, args.output_path)
        else:
            options = {"max_line_bytes": args.max_line_bytes, "objects_only": args.objects_only}
            summary = (
                partition(args.source, args.output_dir, **options)
                if args.command == "split"
                else inspect(args.source, **options)
            )
    except QuarantineError as exc:
        parser.exit(2, "jsonl-quarantine: " + str(exc) + "\n")
    if args.json:
        print(json.dumps(summary, sort_keys=True, ensure_ascii=True))
    elif args.command == "restore":
        print(f"Restored {summary['bytes']} bytes; SHA-256 {summary['sha256']}")
    else:
        print(
            f"{summary['total_lines']} lines: {summary['accepted_lines']} accepted, "
            f"{summary['rejected_lines']} quarantined"
        )
        print("Input SHA-256: " + summary["input_sha256"])
    return 1 if summary.get("rejected_lines", 0) else 0
