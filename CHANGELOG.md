# Changelog

## 0.1.0 (2026-11-05)

The first release. Everything below is new in 0.1.0.

Known limitations:
- The rewrite step can split a very short follow-up ("try again") into parts. Journeys still behave, but the knowledge path does unnecessary work.
- The MongoDB adapter is tested against mongomock in CI. Set `STEWARD_TEST_MONGO_URI` to run the suite against a real server.
- A configuration migrated from the prototype format cannot know the phone country code. Set the validator's `country_code` step after migrating.
- `EmulatedDecider` thresholds are not calibrated per model. Use `steward eval --calibrate`.

- **M8 evals and docs:**
  - `steward.evals`: multi-turn scenario files (JSON or YAML) with expectations per turn on status, text, regexes, journeys, policies, tools, slots, the pending ask and the turn budget. A turn can set runtime switches, and a scenario can check record counts at the end.
  - invariants checked on every reply: not empty, no unrendered placeholder, no emoji when they are off, no error status, no repeated sentence.
  - the runner gives each scenario a fresh bot, sends `is_test` messages, can use mock tools, saves `results.json` and `report.md`, and can resume a run.
  - `calibrate` suggests trigger thresholds from labelled turns.
  - CLI: `steward eval` (`--offline | --record | --replay`, `--mock-tools`, `--only`, `--tags`, `--calibrate`).
  - Northwind scenarios (`examples/northwind/scenarios.json`) pass offline.
  - docs: README, AGENTS.md (integration checklist), `llms.txt`, concepts, tools, stores, providers, server, evals, build, and a generated config reference (`steward schema --markdown`, kept in sync by a test).
  - examples (`minimal.py`, `fastapi_app.py`, `django_view.py`, `custom_knowledge_store.py`), tested where they can run offline.
  - README figures (`assets/`), rendered with Remotion from `figures/`, each in light and dark: the logo, the architecture of one turn, a four-step DADR explainer (route, select, dependencies, compute and verify), and the knowledge graph (still and animated) with answers striking to their sections. README section "How DADR works"; DADR is now consistently expanded as dependency-aware decision retrieval.
  - **mock mode:** the built-in tools have mock results; a config instance's `mock` outranks the implementation's; read-only tools run for real; a mocked `records.append` emits no `record` event.
- **M7 knowledge build** (`steward.build`, optional):
  - markdown documents become section-sized units;
  - passes S (schema), U (units and verified values), B (topic tree and assignment) and D (calculators validated in code);
  - every model call is cached under a hash of its inputs, and a per-run budget limits spend;
  - `overlay_approved` ranks approved information first and runs the three-step supersede review (decision, evidence verified verbatim, per-line decision);
  - admin overrides survive re-runs (`set_override`);
  - `KnowledgeAdmin` adds the approved-knowledge endpoints for the admin page;
  - CLI: `steward build`, and `steward serve --approved-admin`.
- **M6 admin server** (`steward[server]`):
  - a FastAPI router with viewer, editor and publisher roles;
  - endpoints for config draft, validate, publish, discard, history and restore, plus catalog, prompt preview, test chat, sessions, the agents switch, and records with CSV export;
  - the admin page, ported to be domain-free, with the Drawflow canvas vendored;
  - `steward demo` (offline Northwind) and `steward serve`.
- **M5 storage adapters:**
  - SQLite (one file, WAL, FTS5 knowledge search);
  - MongoDB (PyMongo async, conditional updates, unique key index, generation-switched knowledge);
  - Firestore (precondition writes for compare-and-set, hashed record ids, merge transactions);
  - all of them pass the contract suites (MongoDB via mongomock or a real server, Firestore against the emulator in CI).
- **M4 harness runtime:**
  - the `Steward` facade (async core, sync bridge on a private loop, `check()`, events, `sessions`, `admin` draft and publish);
  - the turn pipeline: gates, triage in parallel with understand, resume with a cap on re-asks, journeys and late triggers, the knowledge path, compose, rules check and fix, compare-and-set commit with retry, then the event outbox;
  - journeys with all built-in cards;
  - a tool registry with timeouts, mock mode, receipts and `test:` idempotency keys;
  - built-in tools `records.append`, `agents.available` and `http.request`;
  - claims-without-receipt protection.

- **M3 knowledge and engine:**
  - **knowledge model:** typed and storage-independent (`steward.knowledge`): `KnowledgeSnapshot`, sections with authority and supersede marks, values, entities, calculators.
  - **store interfaces:** `KnowledgeStore` / `SearchableStore` / `KnowledgeWriter`, plus `MemoryKnowledgeStore`.
  - **built-in search:** pure-Python BM25 for stores without text search.
  - **`KnowledgeIndex`:** applies authority, removes outdated lines, hides fully superseded sections, picks values approved-first, and checks entity coverage.
  - **the DADR engine** (`steward.engine`, async):
    - the resolve-only rewrite, parts and translation;
    - routing with decision batches: route, select, gate, expand, about-fallback, soft floor, entity coverage, authority follow-up;
    - parallel routing per part;
    - calculators with a whitelisted AST;
    - grounded generation with number, language and client-rule checks plus one rewrite;
    - a plan cache keyed by knowledge version, engine version and a thresholds digest;
    - a keyword-RAG baseline.
  - **behaviour change:** entity coverage no longer adds a section for an entity that a selected calculator already computes with.
  - **test fixture:** `steward.testing.tiny_kb()`, the fictional Northwind knowledge base (also in `examples/northwind/kb.json`).
  - **parity:** checked privately against the prototype's regression sets with real models: 12/12 on one, 18/19 on the other (the one miss is model variance).
- **M2 providers:**
  - `OpenRouter`: async chat with strict JSON schema and reasoning per model, Jev decision batches, retries, and cost from usage or the price list;
  - `Metered`: ledger plus a hard spend cap with reservations;
  - `EmulatedDecider`: decision batches from any chat model;
  - `Cassette`: record / replay / auto.

- **M1 configuration:**
  - typed pydantic v2 models (`steward.config.models`, schema_version 2, `extra="forbid"`). Journey cards are a discriminated union, with `CustomNode` for host card types. The `jev` and `from` aliases are accepted.
  - the JSON Schema is generated into `schema/steward.schema.json`.
  - semantic validation (`steward.config.validate`) returns `Issue`s with JSON paths, codes and did-you-mean hints. It covers graph checks, references, safe expressions, idempotency keys and registered tool implementations.
  - the card catalog with ports (`steward.config.catalog`).
  - migration from the prototype format (v1 → v2).
  - the safe expression language (`steward.harness.expr`), with registrable functions and roots.
  - generic slot validation (`steward.harness.slots`): digits from any script, country codes, contact capture.
  - CLI: `steward schema | check | migrate`.
  - the fictional Northwind Bikes example config (English and Spanish).

- **M0 skeleton:**
  - packaging: hatchling, uv, ruff, pyright, Apache-2.0;
  - public types: `Message`, `Reply`, `ToolCall`, `ToolResult`, `TurnInfo`, `Receipt`;
  - error tree with fix-it messages: `Issue`, `ConfigError`, `UnknownTool`, …;
  - `Trace` / `Step` tracing, and an `EventBus` with an outbox;
  - async storage protocols and in-memory stores, with reusable contract tests;
  - a `wrap()` adapter for sync stores;
  - the `Provider` protocol and a scripted `FakeProvider`;
  - a banned-words guard (hashed) and CI.
