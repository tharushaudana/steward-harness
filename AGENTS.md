# AGENTS.md: guide for coding agents

This file is for AI coding agents that integrate steward **into** a host system, or that work **on** this repository.

## Integrating steward (a checklist)

1. **Install.** `pip install steward-harness`. Add `[server]` for the admin page, and `[mongo]` or `[firestore]` for those stores.
2. **Start from the example config.** Copy `examples/northwind/northwind.steward.json` and replace the profile, templates, policies, slots and journeys. Validate the result with `steward check FILE`. Each problem comes back as an `Issue` with a path, a code and a hint on how to fix it. Field-by-field documentation is in `docs/config-reference.md`, and the JSON Schema is `schema/steward.schema.json` (`$id` `urn:steward:config:2`).
3. **Pick the stores.** Choose one:
   - `SqliteStores(path)` for a single process;
   - `await MongoStores.create(db)` (PyMongo async database);
   - `FirestoreStores(client)` (`google.cloud.firestore.AsyncClient`);
   - your own adapter (see `docs/stores.md`).

   Pass knowledge separately with `knowledge=` if it lives elsewhere.
4. **Pick a provider.** `OpenRouter.from_env(spend_cap_usd=...)` reads `OPENROUTER_API_KEY`. In tests, use `steward.testing.FakeProvider` (scripted, no network).
5. **Register tools by id.** Every config tool instance has an `impl` id. When `impl` is omitted, it is the instance's own id.
   - Register the implementation with `@bot.tool("impl.id")`. The function receives a `ToolCall` and returns `ok(data, receipt=...)`, `fail("why")`, a dict, or `None`.
   - Built-ins already registered: `records.append`, `agents.available`, `http.request`. Register the same id to replace one.
6. **Call `bot.check()` at startup.** It raises `ConfigError` for an invalid config or an unregistered tool implementation. `UnknownTool` suggests the closest id.
7. **Handle messages.**
   - Sync hosts: `bot.handle(Message(session, text, channel=...))`.
   - Async hosts: `await bot.ahandle(...)`. Calling the sync facade inside a running event loop raises an error.
8. **React to events** with `bot.on("handover" | "record" | "tool_call" | "journey_end" | "ask_abandoned" | "turn_end" | "error" | "budget_exceeded" | "release" | "config_published", fn)`.
9. **Mount the admin page.** `app.include_router(create_router(bot, auth=your_auth), prefix="/steward")`. `auth(request)` returns a `Principal(id, roles={"viewer", "editor", "publisher"})` or raises an HTTP error.
10. **Write scenarios and run `steward eval`.** Do this before shipping and after every config change. Record with `--record` once, then replay with `--replay` in CI.

### Minimal, offline, copy-paste

```python
from steward import Message, Steward, ToolCall, ok
from steward.knowledge import MemoryKnowledgeStore
from steward.testing import FakeProvider, memory_stores, northwind_config, tiny_kb

config = northwind_config()
config["tools"]["save_lead"]["impl"] = "crm.save_lead"  # point the tool instance at your implementation
fake = FakeProvider().on_decide("j_lead_capture", {"noul": 0.95}).on_chat_json("extract", {"name": "Ada", "phone": "202 555 0147"})
bot = Steward(config, provider=fake, stores=memory_stores(), knowledge=MemoryKnowledgeStore(tiny_kb()))

@bot.tool("crm.save_lead")
def save_lead(call: ToolCall):
    return ok({"id": "L1"}, receipt={"crm_id": "L1"})

bot.check()
reply = bot.handle(Message(session="s1", text="I'm Ada, 202 555 0147", is_test=True))
assert reply.journeys[0]["journey"] == "lead_capture" and "Thank you, Ada" in reply.text
```

### Facts that prevent common mistakes
- **The core is async.** `Steward.handle()` runs the async core on a private background loop. Create one `Steward` per process, not one per request.
- **The configuration lives in the ConfigStore.** The dict or file you pass only seeds it on the very first start. Later changes come through `bot.admin` (draft, publish) or the admin page. To force a new config on an existing store, use `await bot.admin.asave_draft(cfg, (await bot.admin.adraft()).rev)` and then `await bot.admin.apublish("note")`.
- **Session state is saved with compare-and-set.** Concurrent messages in one session are retried, not lost.
- **Test conversations are isolated.** Pass `Message(..., is_test=True)`. Idempotency keys get a `test:` prefix, and record listings hide those records unless `include_test=True`.
- **Mock mode keeps side effects out.** `settings.tools_mode = "mock"` makes tools return their mock result: the config instance's `mock`, then the implementation's `mock=`. Read-only tools (`side_effect: false`) still run for real.
- **The engine never hard-codes client words.** Anything domain-specific goes into the config or the knowledge base.
- **Spend is capped.** Every paid call goes through `Metered` (ledger plus spend cap). `BudgetExceeded` turns into a `budget_exceeded` reply, not a crash.

### Recipes
- **Your own knowledge database:** see `examples/custom_knowledge_store.py`. It needs two async methods, `version()` and `load()`. Add `search()` if the database has full-text search.
- **Your own store adapter:** implement the protocols in `steward/stores/base.py`, then run the contract suites (`steward.testing.contracts`) against it in pytest.
- **A custom expression function** for `condition` cards: `@bot.expr_function("open_now")`.
- **A custom card type:** `steward.config.catalog.register_card(CardInfo(...))` plus a runtime handler (advanced).
- **Another LLM vendor:** implement `Provider` (`chat`, `decide`, `estimate`). With no native decision model, wrap a chat provider in `EmulatedDecider`.

## Working on this repository
- **Plan:** `docs/PLAN.md` is the source of truth for the architecture.
- **Layout:** `src/steward/` is the package and `tests/` holds the tests. `demo/` is private and gitignored; never reference it from the package.
- **Never add private client or brand words** to the repo. `tests/test_banned_words.py` enforces this with hashed words. Examples use the fictional "Northwind Bikes".
- **Core dependencies stay at `pydantic` + `httpx`.** Anything else goes into an optional extra (`[server]`, `[mongo]`, `[firestore]`).
- **Storage access goes through the protocols** in `steward/stores/base.py`. A new adapter must pass the contract suites.
- **Tests need no network and no API keys.** Use `FakeProvider` and the in-memory stores.
- **Generated files stay in sync.** Tests fail when they drift. Regenerate them with:
  - `uv run steward schema -o schema/steward.schema.json`
  - `uv run steward schema --markdown -o docs/config-reference.md`
  - `examples/northwind/northwind.steward.json` must equal `src/steward/testing/northwind.steward.json`.
- **README figures** (`assets/`) are rendered from React sources in `figures/` with Remotion: `cd figures && npm ci && node render.mjs [LogoBanner|Architecture|Dadr|KnowledgeRadial|KnowledgeStrikes|LogoIcon]`. The knowledge graph uses a fictional knowledge base (`figures/src/data/northwindKb.ts`); keep it fictional. If the pipeline or the engine changes, update `figures/src/Architecture.tsx` and `figures/src/Dadr.tsx` too.

Run before every commit:

```bash
uv run ruff check src tests examples
uv run pytest -q
uv run pyright
```
