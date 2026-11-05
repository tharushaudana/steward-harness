"""Command line.

  steward schema [-o FILE]          write the configuration JSON Schema (default: stdout)
  steward check CONFIG              load + validate a configuration; exit 1 on errors
  steward migrate CONFIG [-o FILE]  upgrade an older configuration format to the current schema_version

  steward demo [--port 8765]        the fictional Northwind assistant + admin page (offline unless OPENROUTER_API_KEY)
  steward serve CONFIG --sqlite F   serve a configuration (OpenRouter from the environment) with the admin page
  steward build DOCS.md... -o kb.json [--approved A.md]   build a knowledge base (paid model calls, cached)
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


def _load_env_file() -> None:
    """Read KEY=VALUE lines from ./.env into the environment (without overriding existing variables)."""
    import os

    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _cmd_build(args: argparse.Namespace) -> int:
    import asyncio

    from steward.build import SourceDocument, build, overlay_approved
    from steward.knowledge import MemoryKnowledgeStore
    from steward.providers import OpenRouter

    _load_env_file()
    docs = [SourceDocument.from_markdown(Path(f).read_text(encoding="utf-8"), doc_id=Path(f).stem, id_prefix=f"P{i + 1:02d}")
            for i, f in enumerate(args.files)]
    approved = [SourceDocument.from_markdown(Path(f).read_text(encoding="utf-8"), doc_id=Path(f).stem, kind="approved",
                                             id_prefix=f"A{i + 1:02d}") for i, f in enumerate(args.approved or [])]
    provider = OpenRouter.from_env(spend_cap_usd=args.budget)

    async def run() -> object:
        store = MemoryKnowledgeStore()
        report = await build(docs, provider, writer=store, description=args.description, cache_dir=args.cache,
                             budget_usd=args.budget)
        if approved:
            report = await overlay_approved(store, approved, provider, cache_dir=args.cache, budget_usd=args.budget)
        return report

    report = asyncio.run(run())
    for n in report.notes:  # type: ignore[attr-defined]
        print(n)
    Path(args.output).write_text(json.dumps(report.snapshot.to_dict(), ensure_ascii=False, indent=1),  # type: ignore[attr-defined]
                                 encoding="utf-8")
    print(f"wrote {args.output} (cost ${report.cost_usd:.4f})")  # type: ignore[attr-defined]
    return 0


def _serve(bot: object, host: str, port: int, insecure_local: bool, knowledge_admin: object = None) -> int:
    try:
        import uvicorn

        from steward.server import create_app
    except ImportError:
        print('the server needs: pip install "steward-harness[server]"', file=sys.stderr)
        return 1
    if insecure_local and host not in ("127.0.0.1", "localhost", "::1"):
        print("refusing to serve the unauthenticated admin on a non-local address", file=sys.stderr)
        return 1
    print(f"admin page: http://{host}:{port}/steward/admin")
    uvicorn.run(create_app(bot, insecure_local=insecure_local, prefix="/steward", knowledge_admin=knowledge_admin),  # type: ignore[arg-type]
                host=host, port=port)
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    import os

    from steward import Steward
    from steward.knowledge import MemoryKnowledgeStore
    from steward.testing import demo_provider, northwind_config, tiny_kb

    _load_env_file()
    if os.environ.get("OPENROUTER_API_KEY"):
        from steward.providers import OpenRouter

        provider = OpenRouter.from_env(spend_cap_usd=args.spend_cap)
        print(f"using OpenRouter (spend cap ${args.spend_cap:.2f})")
    else:
        provider = demo_provider()
        print("offline demo (scripted decisions; set OPENROUTER_API_KEY for real answers)")
    bot = Steward(northwind_config(), provider=provider, knowledge=MemoryKnowledgeStore(tiny_kb()))
    return _serve(bot, args.host, args.port, insecure_local=True)


def _cmd_serve(args: argparse.Namespace) -> int:
    from steward import Steward
    from steward.providers import OpenRouter
    from steward.stores.sqlite import SqliteStores

    _load_env_file()
    raw = json.loads(Path(args.config).read_text(encoding="utf-8"))
    stores = SqliteStores(args.sqlite)
    if args.knowledge:
        import asyncio

        from steward.knowledge import KnowledgeSnapshot

        snap = KnowledgeSnapshot.from_dict(json.loads(Path(args.knowledge).read_text(encoding="utf-8")))
        asyncio.run(stores.knowledge.write_snapshot(snap))
        print(f"loaded knowledge base {snap.meta.version} ({len(snap.sections)} sections) into {args.sqlite}")
    provider = OpenRouter.from_env(spend_cap_usd=args.spend_cap)
    bot = Steward(raw, provider=provider, stores=stores)
    admin = None
    if args.approved_admin:
        from steward.build import KnowledgeAdmin

        admin = KnowledgeAdmin(bot, provider=provider, cache_dir=args.build_cache)
    return _serve(bot, args.host, args.port, insecure_local=args.insecure_local, knowledge_admin=admin)


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
    p = sub.add_parser("demo", help="run the fictional Northwind assistant with the admin page (local only)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--spend-cap", type=float, default=0.5)
    p.set_defaults(fn=_cmd_demo)
    p = sub.add_parser("serve", help="serve a configuration with SQLite storage and the admin page")
    p.add_argument("config")
    p.add_argument("--sqlite", default="steward.db")
    p.add_argument("--knowledge", help="a knowledge base JSON file (KnowledgeSnapshot) to load first")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--spend-cap", type=float, default=5.0)
    p.add_argument("--insecure-local", action="store_true", help="admin without login (only on 127.0.0.1)")
    p.add_argument("--approved-admin", action="store_true", help="enable the approved-knowledge tab (steward.build)")
    p.add_argument("--build-cache", default=".steward-build-cache")
    p.set_defaults(fn=_cmd_serve)
    p = sub.add_parser("build", help="build a knowledge base JSON from markdown documents (paid model calls, cached)")
    p.add_argument("files", nargs="+", help="source markdown files")
    p.add_argument("--approved", nargs="*", help="approved markdown files that outrank the sources")
    p.add_argument("--description", default="", help="one line on what the documents are about")
    p.add_argument("-o", "--output", default="kb.json")
    p.add_argument("--cache", default=".steward-build-cache")
    p.add_argument("--budget", type=float, default=0.25, help="max spend in USD for this run")
    p.set_defaults(fn=_cmd_build)
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    return int(args.fn(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
