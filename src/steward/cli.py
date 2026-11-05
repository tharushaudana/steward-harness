"""Command line: `steward --version`. More commands (check, schema, migrate, eval, demo, serve) arrive with their
milestones (docs/PLAN.md)."""

from __future__ import annotations

import argparse
import sys

from steward import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steward", description="Steward: a compliant customer-service harness.")
    parser.add_argument("--version", action="version", version=f"steward {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
