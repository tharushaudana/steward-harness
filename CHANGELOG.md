# Changelog

## 0.1.0.dev0 (unreleased)

- **M0 skeleton:**
  - packaging: hatchling, uv, ruff, pyright, Apache-2.0;
  - public types: `Message`, `Reply`, `ToolCall`, `ToolResult`, `TurnInfo`, `Receipt`;
  - error tree with fix-it messages: `Issue`, `ConfigError`, `UnknownTool`, …;
  - `Trace` / `Step` tracing, and an `EventBus` with an outbox;
  - async storage protocols and in-memory stores, with reusable contract tests;
  - a `wrap()` adapter for sync stores;
  - the `Provider` protocol and a scripted `FakeProvider`;
  - a banned-words guard (hashed) and CI.
