"""Compare demonstration files byte-for-byte and print only count and SHA-256."""

import argparse
import hashlib
from pathlib import Path


def compare(first_path, second_path):
    digest = hashlib.sha256()
    count = 0
    with Path(first_path).open("rb") as first, Path(second_path).open("rb") as second:
        while True:
            chunk = first.read(65536)
            if chunk != second.read(65536):
                return None
            if not chunk:
                break
            count += len(chunk)
            digest.update(chunk)
    return count, digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original")
    parser.add_argument("restored")
    args = parser.parse_args()
    result = compare(args.original, args.restored)
    if result is None:
        parser.exit(1, "Files differ\n")
    print(f"Equal bytes ({result[0]}) and SHA-256: {result[1]}")


if __name__ == "__main__":
    main()
