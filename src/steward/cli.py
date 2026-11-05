"""Command line.

  steward schema [-o FILE]          write the configuration JSON Schema (default: stdout)
  steward check CONFIG              load + validate a configuration; exit 1 on errors
  steward migrate CONFIG [-o FILE]  upgrade an older configuration format to the current schema_version

More commands (eval, demo, serve) arrive with their milestones (docs/PLAN.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from steward import __version__
from steward.errors import ConfigError


def _cmd_schema(args: argparse.Namespace) -> int:
    from steward.config import schema

    text = json.dumps(schema(), indent=1, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from steward.config import load_file, validate

    try:
        cfg, notes = load_file(args.config)
    except ConfigError as e:
        print(f"INVALID {args.config}\n{e}")
        return 1
    for n in notes:
        print(f"note: {n}")
    issues = validate(cfg)
    errors = [i for i in issues if i.severity == "error"]
    for i in issues:
        print(f"  {i}")
    print(f"{'INVALID' if errors else 'OK'} {args.config}: {len(errors)} errors, {len(issues) - len(errors)} warnings, "
          f"{len(cfg.journeys)} journeys, {len(cfg.policies)} policies, {len(cfg.templates)} templates")
    return 1 if errors else 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    from steward.config.migrate import migrate

    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out, notes = migrate(raw)
    for n in notes:
        print(f"note: {n}", file=sys.stderr)
    text = json.dumps(out, indent=1, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="steward", description="Steward: a compliant customer-service harness.")
    parser.add_argument("--version", action="version", version=f"steward {__version__}")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("schema", help="write the configuration JSON Schema")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=_cmd_schema)
    p = sub.add_parser("check", help="load and validate a configuration file")
    p.add_argument("config")
    p.set_defaults(fn=_cmd_check)
    p = sub.add_parser("migrate", help="upgrade an older configuration format")
    p.add_argument("config")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=_cmd_migrate)
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    return int(args.fn(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
