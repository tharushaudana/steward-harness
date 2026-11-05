# Changelog

## 0.1.0.dev0 (unreleased)

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
