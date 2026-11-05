# AGENTS.md: guide for coding agents

This file is for AI coding agents that work **on** steward or integrate it **into** another system.

## Working on this repository
- **Plan:** read `docs/PLAN.md` first. It is the source of truth for architecture and milestones.
- **Layout:** `src/steward/` is the package. Tests are in `tests/`. `demo/` is private and gitignored; never reference it from the package.
- **Never add private client or brand words** to the repo. `tests/test_banned_words.py` enforces this with hashed words. Examples use the fictional "Northwind Bikes".
- **Core dependencies stay at `pydantic` + `httpx`.** Anything else goes into an optional extra (`[server]`, `[mongo]`, `[firestore]`).
- **The core is async.** Store and provider protocols are async. Tools may be sync or async.
- **Storage access goes through the protocols** in `steward/stores/base.py`. A new adapter must pass the contract suites in `steward/testing/contracts.py`.
- **Tests need no network and no API keys.** Use `steward.providers.fake.FakeProvider` and the in-memory stores.

Run before every commit:

```bash
uv run ruff check src tests
uv run pytest -q
uv run pyright
```

## Integrating steward into a host system
This section is filled in as milestones land (see docs/PLAN.md §3). In short:
1. Write a config file (JSON, validated by `schema/steward.schema.json`).
2. Pick or implement stores (`steward.stores`).
3. Pick a provider (`steward.providers`).
4. Register tools by id with `@bot.tool("id")`.
5. Call `bot.handle(Message(...))` or `await bot.ahandle(...)`.
