# Changelog

## 0.1.0.dev0 (unreleased)

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
