"""Summarize local HTML files without parsing or network access."""

import argparse
from pathlib import Path


def main() -> None:
    """Print deterministic counts for an offline input directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    files = sorted(arguments.directory.glob("*.html"))
    print(f"Found {len(files)} local HTML file(s) in {arguments.directory.resolve()}.")
    for path in files:
        print(f"- {path.name}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
