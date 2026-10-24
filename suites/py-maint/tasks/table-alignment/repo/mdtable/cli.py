"""mdtable on the command line: CSV on standard input, a Markdwon table on standard output.

    mdtable --align lrr < prices.csv
"""

import argparse
import csv
import sys

from .parse import parse_alignments
from .render import render


def main(argv=None, stdin=None, stdout=None):
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    parser = argparse.ArgumentParser(prog="mdtable", description="Turn CSV into a Markdown table.")
    parser.add_argument("--align", default="", help="one letter per column: l, r or c (default: all l)")
    args = parser.parse_args(argv)
    rows = list(csv.reader(stdin))
    if not rows:
        return 0
    try:
        align = parse_alignments(args.align) if args.align else None
        stdout.write(render(rows[0], rows[1:], align))
    except ValueError as exc:
        print(f"mdtable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
