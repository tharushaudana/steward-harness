# Steward: package architecture plan

> **Steward: a compliant customer-service harness, powered by DADR.**
> Distribution `steward-harness` (Apache-2.0), import name `steward`, Python ≥ 3.11.

Steward is a library that turns a knowledge base plus an admin-editable configuration into a grounded,
rule-compliant customer-service assistant. It provides:
- **knowledge answers** through the DADR retrieval engine (a decision model navigates a knowledge tree);
- **journeys** (lead capture, handover, any multi-step flow) as graphs of cards;
- **policies**, approved templates, client rules and an output composer;
- **tools** as host callbacks identified by string ids;
- **storage behind adapters** (memory, SQLite, MongoDB, Firestore) and **providers behind an interface** (OpenRouter built in).

This plan merges three planning reports. It was written from the DADR prototype (Phase 0/1) and inspired by
[google-research/harness2](https://github.com/google-research/harness2).

## 0. Fixed decisions (user)

| # | Decision |
|---|---|
| U1 | The DADR retrieval engine lives **in the package**, working against a `KnowledgeStore` interface. The core never hard-codes SQLite. |
| U2 | The knowledge **build** pipeline is an optional module, `steward.build`, writing through the store. Website crawling stays in `demo/`. |
| U3 | The admin UI and HTTP server are the optional extra `steward[server]`: a FastAPI router factory plus the admin page as a packaged static asset. |
| U4 | Providers are a pluggable interface (chat, structured JSON, decisions). A built-in OpenRouter provider covers the Jev decision model and chat models, with a cost ledger and spend cap. |
| U5 | Tools are callbacks registered by **string id**. Config uses a two-level form: tool instances in config point to an `impl` id, and `impl` defaults to the instance id. |
| U6 | **Async core with a sync wrapper**: `await bot.ahandle(msg)` / `bot.handle(msg)`. |
| U7 | Adapters in 0.1: **memory, generic SQLite, MongoDB, Firestore**. All of them pass the same contract tests. |
| U8 | Apache-2.0, `steward-harness` on PyPI. |
| U9 | **No private or client words** anywhere in the repo (except the gitignored `demo/`). Examples use a fictional business ("Northwind Bikes"). |
| U10 | Integration must be easy for **humans and coding agents**: README, `AGENTS.md`, `llms.txt`, typed API, a JSON Schema for config, and errors that say how to fix them. |

## 1. Borrowed from harness2 (and why)

- **Zero required runtime dependencies, with features as extras.** Steward's core only needs `pydantic` and `httpx`. `[server]`, `[mongo]` and `[firestore]` are opt-in.
- **`runtime_checkable` Protocols and frozen dataclasses** for extension points and records. Adapters implement the narrow Protocol that the core needs.
- **A demo that runs with no credentials.** `steward demo` and every test run on a scripted `FakeProvider` or a recorded cassette, so an agent can verify an integration for free.
- **The ledger is a core module.** Cost is part of the domain.
- **Fix-it errors.** `ConfigError` lists issues as `{path, node, port, code, msg, hint}`. `UnknownTool` suggests the closest id ("did you mean…").
- **Preflight and workflow CLI:** `steward check | schema | migrate | eval | demo | serve`, with `--dry-run` where it makes sense.
- **A banned-words guard,** like harness2's `BenchPrompts`: a CI test fails if client words appear in the package.
- **Vendored third-party assets** under their own license. The Drawflow canvas ships inside the package.
- **Not borrowed:** module-level config read at import time, setuptools, and the Linux-only stance.

## 2. Repository layout

```
steward-harness/
  pyproject.toml   README.md   AGENTS.md   llms.txt   LICENSE   CHANGELOG.md   .env.example   .gitignore
  schema/steward.schema.json          generated JSON Schema (committed); config files reference it via "$schema"
  src/steward/
    __init__.py        public API re-exports: Steward, Message, Reply, ToolCall, ToolResult, errors
    py.typed
    app.py             Steward facade: construction, registries, handle/ahandle, sessions, admin service, events
    types.py           Message, Reply, Status, TurnInfo, Receipt, ToolCall, ToolResult (frozen dataclasses)
    errors.py          StewardError tree (ConfigError, UnknownTool, StateConflict, RevisionConflict, BudgetExceeded, ...)
    events.py          Trace/Step, event bus + outbox (delivered after state commit)
    config/
      models.py        pydantic v2 models (extra="forbid"); cards as a discriminated union on "type"
      validate.py      graph + semantic checks -> list[Issue]; registry-aware (tools, cards, expr functions)
      migrate.py       schema_version chain; v1 = DADR format -> v2
      catalog.py       card catalog (ports, field schemas) shared by validation and the admin canvas
      loader.py        from file / dict / ConfigStore; ${env:NAME} secrets resolved at load, never written back
    knowledge/
      model.py         TreeNode, Section(authority: source|approved, marks), SupersedeMark, Value, Entity, Calculator,
                       Edge, SupersedeOverride, KBMeta, KnowledgeSnapshot
      store.py         KnowledgeStore / SearchableStore / KnowledgeWriter protocols
      index.py         KnowledgeIndex (the old KB class without SQL): authority, hidden, value(), text(), covers(), ...
      search.py        pure-Python BM25 (field weights 3.0/1.0, light stemmer, Lexicon); Searcher protocol
    engine/            DADR retrieval, domain-agnostic
      preprocess.py    condense (resolve-only rule), parts, translate, kind
      route.py         shortlist, route/select/gate/expand, about-fallback, soft floor, entity coverage, authority follow
      calc.py          safe AST eval + calculator binding (also used by build)
      generate.py      locked core prompts, number / language checks, rewrite
      plan_cache.py    signature + version(kb, ENGINE_V, thresholds digest); only confident plans cached
      settings.py      EngineSettings (today's TH thresholds) passed in code, not admin-edited
    harness/
      pipeline.py      ordered Stage list (load_state, gates, triage||understand, resume, match, short_circuit,
                       execute, compose, checks, commit); insert_before / replace / hooks
      turn.py          Turn context (env, outcomes, fired, receipts, segments, trace)
      decisions.py     DecisionBatch: questions ride on calls already being made (triage, route parts, late triggers)
      journeys.py      executor: limits, suspend/resume, reasks -> gave_up, invalid port, value_descriptions
      cards.py         CardRegistry; 9 built-in cards; hosts can add card types
      expr.py          safe expression language; registry for functions/roots
      slots.py         slot types, validators/normalizers (generic phone: unicode digits, country code, pattern)
      templates.py     {lang: text}, approval flags, requires_receipt, covers, counts_as_ask, filters, lint
      policies.py      template / inject / suppress_kb / exclusive / no_ask
      rules.py         check registry: forbidden, required wording, claims-without-receipt, emoji; fallbacks
      compose.py       prefix | body | suffix + flow (welcome / ask_contact / thank_you); once-per-reply
    tools/
      registry.py      ToolRegistry: decorator + register(); sync/async; timeouts; mock mode; idempotency; receipts
      builtins.py      records.append, state.get, state.set, knowledge.lookup, agents.available, http.request
    stores/
      base.py          Protocols: SessionStateStore (CAS), RecordStore (unique key + merge), RuntimeStore, ConfigStore
                       (draft/publish/history), LedgerStore, PlanCache, ChatLog; Stores bundle
      memory.py        all stores in memory (default; tests; demo)
      sqlite.py        generic SQLite (clean steward schema, one file) incl. KnowledgeStore + FTS5 search
      mongo.py         [mongo] pymongo async: versioned CAS, unique index on record key
      firestore.py     [firestore] transactions for CAS, create() for idempotent records
      sync.py          wraps sync implementations for the async protocols (to_thread)
    providers/
      base.py          Provider protocol: chat(messages, schema=?), decide(state, questions), estimate()
      openrouter.py    chat completions (strict json_schema, reasoning per model) + Jev /api/alpha/decisions
      emulated.py      EmulatedDecider: any chat model answers decision questions in one strict-JSON call
      metered.py       Metered(provider, ledger, cap_usd): reserve/record/release, BudgetExceeded
      cassette.py      record / replay / strict
      fake.py          scripted provider for tests and demos
    build/             optional: documents -> KnowledgeSnapshot through KnowledgeWriter
      documents.py     SourceDocument/SourceSection, split_markdown (from the crawler's splitter)
      schema.py  sections.py  tree.py  calculators.py  supersede.py  overrides.py  version.py  prompts.py  cache.py
      __init__.py      build(), overlay_approved(), set_override()
    evals/             scenarios (YAML), runner (fresh eval sessions, mock tools), invariants, calibrate, report
    testing.py         FakeProvider, tiny_kb(), run_journey() helpers, store contract test classes
    server/            [server] create_router(), mount_admin(), auth hook (required), static/admin.html, static/vendor/drawflow
    cli.py             steward check | schema | migrate | eval | demo | serve
  tests/               unit tests per module, contract tests per adapter, scenarios + cassettes, banned-words guard
  examples/            minimal.py, fastapi_app.py, django_view.py, custom_store.py, northwind/ (config, tools.py, kb.json, scenarios.yaml)
  docs/                PLAN.md, concepts.md, config-reference.md (generated), journeys.md, tools.md, stores.md,
                       providers.md, server.md, decisions.md
  demo/                GITIGNORED: legacy DADR SQLite adapter (kb_*.db), crawler, private configs, parity scripts
```

## 3. Public API

```python
from steward import Steward, Message, ToolCall, ToolResult
from steward.providers import OpenRouter
from steward.stores.sqlite import SqliteStores

bot = Steward.from_file(
    "northwind.steward.json",
    stores=SqliteStores("northwind.db"),            # knowledge, sessions, records, runtime, config, ledger, cache
    provider=OpenRouter.from_env(spend_cap_usd=5.0),
)

@bot.tool("crm.save_lead")                          # impl id; config tool instances point to it
async def save_lead(call: ToolCall) -> ToolResult:
    ref = await crm.upsert(call.args, key=call.idempotency_key)
    return ToolResult.ok({"id": ref}, receipt={"ref": ref})

@bot.tool("staff.available", side_effect=False)     # sync callables are fine
def available(call: ToolCall):
    return {"ok": True, "data": {"available": staffing.online() > 0}}

bot.on("handover", notify_slack)                    # events: handover, record, turn_end, error, budget_exceeded, ...
bot.check()                                         # fail fast: unknown tools/cards, invalid config

reply = bot.handle(Message(session="u-42", text="Do you have a 52 cm frame?", channel="dm"))
print(reply.text, reply.status, reply.trace.totals.cost_usd)
```

- **Async hosts:** `await bot.ahandle(...)`.
- **Sessions:** `bot.sessions.get / reset / release_handover(sid, by=...)`.
- **Admin:**
  - `bot.admin.draft()`, `save_draft(cfg, base_rev)` (raises `RevisionConflict`), `validate`, `publish(note)` (atomic hot-swap), `history`, `restore`, `discard`;
  - `bot.handle(msg, config="draft")` for the Test panel, so there is no thread-local draft mode.
- **Agents' quickstart:** `Steward.from_dict(cfg, stores=MemoryStores(knowledge=tiny_kb()), provider=FakeProvider.scripted(...))`, with no credentials.

## 4. Config (JSON + `$schema`, typed, migrated)

- **Config is what admins edit:** profile, prompt extras, settings, slots (incl. `value_descriptions`), validators, templates, rules, policies, channels, flow, journeys, tool instances (`impl`, `args`, `idempotency_key`, `side_effect`, `timeout_ms`, `mock`).
- **Code is what hosts own:** tool implementations, stores, providers, custom validators, expression functions and card types, engine thresholds (`EngineSettings`), and the locked core prompts (versioned by `ENGINE_V`).
- **Migration:** `schema_version` 2. `migrate.py` converts the DADR v1 format:
  - `{en, bn}` keys become `text: {lang: ...}`;
  - `impl: builtin:store.append` becomes `records.append`;
  - the country-specific phone validator becomes the generic `phone` validator with config;
  - `jev` becomes `decision` (with an alias).

## 5. Storage: typed adapters vs string-id tools

- **Typed Protocols** cover everything the engine calls on every turn and whose behaviour must be guaranteed:
  - the knowledge snapshot and search;
  - session state (true compare-and-set: no silent overwrite on conflict, which fixes a DADR bug);
  - the plan cache, ledger and config.
- **String-id tools** cover side effects that journeys choose in config: `records.append`, `agents.available`, `http.request`, `crm.*`, …
  - Built-ins wrap the typed `RecordStore` / `RuntimeStore`.
  - A host can override any built-in id to write into its own system.
- **Knowledge:** the full `KnowledgeSnapshot` is loaded and re-loaded when `store.version()` changes, which is fine up to roughly 5–10k sections.
  - **Search order:** the store's own search (SQLite FTS5) if it has one; else a host `Searcher` (including a tool-backed searcher such as `kb.search` for Elastic or Atlas); else the built-in BM25.
- **Test isolation:** `Message.is_test` → records flagged and idempotency keys prefixed `test:`.
- **Lead files:** the daily lead CSV becomes an optional `CsvRecordSink` event subscriber.

## 6. Providers and decisions

- **Interface:**
  ```python
  Provider.chat(messages, *, model, max_tokens, schema=None, purpose) -> ChatResult
  Provider.decide(state, questions: dict[str, Question]) -> Decisions
  ```
  `Question` types are `noul`, `choice` and `score`, all answered in one call.
- **Roles:** `Providers(decide=..., chat=..., extract=...)` maps each role to a provider and model.
- **`OpenRouter`** is built in: Jev for decide, any chat model, reasoning settings per model.
- **`EmulatedDecider`** lets any chat model answer decision batches. Per-model threshold offsets are calibrated with `steward eval calibrate`.
- **Wrappers:** `Metered` (ledger + cap), `Cassette` (record/replay), `Fake`.

## 7. Turn pipeline (ported behaviour, D40–D53)

The pipeline is a list of explicit stages, each recording trace steps:
1. **gates:** handover pause, template-only channels.
2. **triage:** decision batch with business context, run in parallel with *understand* (resolve-only rewrite, parts, kind).
3. **resume:** pending asks, reasks cap, `gave_up`.
4. **match:** exclusive / tap / takeover / policies / contact-only.
5. **chitchat or meta** short-circuit.
6. **execute:** journeys alongside knowledge, part claims, late triggers only when the triage was unsure.
7. **compose.**
8. **checks:** numbers, language, forbidden, required wording, claims without receipt → one rewrite → fallbacks.
9. **commit:** CAS save, then the event outbox.

Retrieval keeps every accuracy rule:
- dual-view relevance plus the about-view;
- gate and expand;
- about-fallback, soft floor and entity coverage;
- approved authority and supersede marks;
- calculators;
- the confident-only plan cache.

## 8. Server extra

- **Wiring:** `create_router(bot_or_resolver, auth=..., prefix="/steward")` and `mount_admin(app)`.
- **Auth:** an `auth(request) -> Principal{roles: viewer|editor|publisher}` callable is **required**. Running without one needs an explicit `insecure_local=True`.
- **Endpoints**, ported from DADR's `server.py`:
  - `chat` (channel, draft);
  - `config` with `base_rev` → 409, validate, publish, discard, history, restore;
  - `catalog`, prompt preview;
  - session state / reset / release, agents status, records + CSV;
  - journey templates, approved knowledge (only if the store supports writing).
- **Generic admin page:**
  - N language columns, taken from the config;
  - "decision model" instead of "Jev";
  - journey templates from JSON;
  - vendored Drawflow;
  - tabs hidden when their capability is absent.

## 9. Evaluation

`steward eval scenarios.yaml --record|--replay|--strict`:
- **Multi-turn scenarios** with per-turn expectations (journeys, policies, tools, slots, status, require/forbid regexes, max cost, max LLM calls) plus global invariants.
- **Runs** in fresh `eval_*` sessions with mock tools, into a resumable `runs/<ts>/` directory.
- **`calibrate`** suggests trigger thresholds.
- The DADR regression sets and the user-reported conversations (D49–D53) become scenarios in `demo/` (private) and generic Northwind scenarios in `examples/`.

## 10. Neutralisation list (DADR → steward)

- **Phone and digits:**
  - Script-specific digits and the country prefix become `unicodedata.digit` folding plus a configurable `country_code`.
  - The country phone validator becomes the generic `phone` validator.
  - The `slots.name_comma` hack becomes template filters.
- **Globals:**
  - `llm.KB_NAME`, `db()` and the thread-local draft mode become instance stores and an explicit `config="draft"`.
  - The `test_` session prefix convention becomes `Message.is_test`.
- **Prompts and wording:**
  - "website" in prompts becomes "knowledge sources".
  - Hard-coded claim lines move into locked core prompts (generic).
  - The `script_lang` map becomes a `lang_detect` hook.
  - The fixed `dm`/`comment` channels are now driven by `channels` config only.
  - The `phone_refused` / `r_refuse` names become generic, keyed to `contact_slot`.
- **State:** the handover flags become `state.handover{active, epoch}`.
- **Admin page:** remove the fixed EN/BN columns, Jev labels, the phone placeholder and the KB dropdown.
- **Tests and examples:** names and numbers become Northwind data (`+1 555 …`).
- **CI guard:** a banned-words test over `src/`, `tests/`, `examples/` and `docs/`, using generic patterns plus an untracked private list.

## 11. Milestones

| # | Milestone | Main sources |
|---|---|---|
| M0 | Skeleton: pyproject (hatchling, uv, ruff, pyright), types, errors, events, memory stores, FakeProvider, CI, banned-words test, AGENTS.md stub | – |
| M1 | Config: models, JSON Schema, validate (+catalog), migrate v1→v2, Northwind example config | journeys.validate_harness, CATALOG, harness_*.json shape |
| M2 | Providers: Provider protocol, OpenRouter, Metered + LedgerStore, EmulatedDecider, Cassette | llm.py |
| M3 | Knowledge + engine: model, KnowledgeIndex, BM25, preprocess/route/calc/generate/plan cache; tiny_kb tests; demo SQLite adapter + parity replay against DADR traces | dadr.py (KB, route, calc, generate), crawl.split_passages |
| M4 | Harness runtime: pipeline stages, journeys, cards, expr, slots, templates, policies, rules, compose; tests ported from test_harness.py (generic) | journeys.py, dadr.answer_harness/_compose/checks |
| M5 | Stores: SQLite, MongoDB, Firestore + contract test suite; Steward facade (sync/async, sessions, admin service, events) | journeys state/records, server.py |
| M6 | Server extra: router, auth, generic admin page (vendored Drawflow) | server.py, harness.html |
| M7 | steward.build: passes S/U/B/D, approved overlay, supersede (3-step), overrides, build cache | build_kb.py |
| M8 | Evals + docs: scenario runner, calibrate, README, AGENTS.md, llms.txt, generated config reference; 0.1.0 | eval.py, test_harness.py |

**Definition of done for 0.1:**
- every module has unit tests;
- all four adapters pass the contract suite (Mongo and Firestore against emulators or containers);
- the Northwind demo runs without credentials;
- the parity replay against DADR traces matches selected units on the private demo KBs;
- the banned-words guard is green.
