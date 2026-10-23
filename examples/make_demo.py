"""Create only synthetic demonstration data, refusing to overwrite a file."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    args = parser.parse_args()
    raw = b'{"demo":1}\r\nnot-json\nnull\n\xff\n{"last":true}'
    with Path(args.output).open("xb") as handle:
        handle.write(raw)
    print("Created five synthetic physical records: three valid, two invalid.")


if __name__ == "__main__":
    main()
